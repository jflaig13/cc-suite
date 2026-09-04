type JsonObject = Record<string, unknown>;
type RequestHandler = (
  request: { params: JsonObject }
) => Promise<unknown> | unknown;

type ServerOptions = {
  capabilities: JsonObject;
  instructions?: string;
};

export const ListToolsRequestSchema = { method: "tools/list" } as const;
export const CallToolRequestSchema = { method: "tools/call" } as const;

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export class StdioServerTransport {
  private buffered = "";
  private receiver: ((message: unknown) => Promise<void>) | null = null;

  async start(receiver: (message: unknown) => Promise<void>): Promise<void> {
    if (this.receiver !== null) {
      throw new Error("stdio MCP transport already started");
    }
    this.receiver = receiver;
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk: string) => {
      this.buffered += chunk;
      if (Buffer.byteLength(this.buffered, "utf8") > 1024 * 1024) {
        void this.send({
          jsonrpc: "2.0",
          id: null,
          error: { code: -32700, message: "MCP input exceeded 1 MiB" },
        }).finally(() => process.exit(78));
        return;
      }
      while (true) {
        const newline = this.buffered.indexOf("\n");
        if (newline < 0) break;
        const raw = this.buffered.slice(0, newline);
        this.buffered = this.buffered.slice(newline + 1);
        if (!raw.trim()) continue;
        let message: unknown;
        try {
          message = JSON.parse(raw);
        } catch {
          void this.send({
            jsonrpc: "2.0",
            id: null,
            error: { code: -32700, message: "Parse error" },
          });
          continue;
        }
        void receiver(message).catch((error) => {
          process.stderr.write(`MCP receive failed: ${error}\n`);
          process.exit(78);
        });
      }
    });
    process.stdin.on("end", () => process.exit(0));
    process.stdin.resume();
  }

  async send(message: JsonObject): Promise<void> {
    const payload = JSON.stringify(message) + "\n";
    await new Promise<void>((accept, reject) => {
      process.stdout.write(payload, (error?: Error | null) => {
        if (error) reject(error);
        else accept();
      });
    });
  }
}

export class Server {
  private readonly handlers = new Map<string, RequestHandler>();
  private transport: StdioServerTransport | null = null;
  private initializeResponded = false;
  private initialized = false;
  private initializedResolve: (() => void) | null = null;
  private readonly initializedPromise: Promise<void>;

  constructor(
    private readonly serverInfo: { name: string; version: string },
    private readonly options: ServerOptions
  ) {
    this.initializedPromise = new Promise<void>((accept) => {
      this.initializedResolve = accept;
    });
  }

  setRequestHandler(
    schema: { method: string },
    handler: RequestHandler
  ): void {
    if (this.handlers.has(schema.method)) {
      throw new Error(`duplicate MCP handler: ${schema.method}`);
    }
    this.handlers.set(schema.method, handler);
  }

  async connect(transport: StdioServerTransport): Promise<void> {
    if (this.transport !== null) {
      throw new Error("MCP server already connected");
    }
    this.transport = transport;
    await transport.start(async (message) => {
      await this.receive(message);
    });
  }

  async waitUntilInitialized(): Promise<void> {
    await this.initializedPromise;
  }

  async notification(message: {
    method: string;
    params?: JsonObject;
  }): Promise<void> {
    if (!this.initialized || this.transport === null) {
      throw new Error("MCP client is not initialized");
    }
    await this.transport.send({
      jsonrpc: "2.0",
      method: message.method,
      ...(message.params === undefined ? {} : { params: message.params }),
    });
  }

  private async respond(
    id: unknown,
    result?: unknown,
    error?: { code: number; message: string }
  ): Promise<void> {
    if (this.transport === null) throw new Error("MCP transport is absent");
    await this.transport.send({
      jsonrpc: "2.0",
      id,
      ...(error === undefined ? { result } : { error }),
    });
  }

  private async receive(message: unknown): Promise<void> {
    if (
      !isObject(message) ||
      message.jsonrpc !== "2.0" ||
      typeof message.method !== "string"
    ) {
      await this.respond(null, undefined, {
        code: -32600,
        message: "Invalid Request",
      });
      return;
    }
    const method = message.method;
    const hasId = Object.prototype.hasOwnProperty.call(message, "id");
    const id = message.id;
    const params = isObject(message.params) ? message.params : {};

    if (method === "initialize") {
      if (!hasId || this.initializeResponded) {
        if (hasId) {
          await this.respond(id, undefined, {
            code: -32600,
            message: "Initialize request is invalid",
          });
        }
        return;
      }
      const requestedVersion =
        typeof params.protocolVersion === "string"
          ? params.protocolVersion
          : "2025-06-18";
      await this.respond(id, {
        protocolVersion: requestedVersion,
        capabilities: this.options.capabilities,
        serverInfo: this.serverInfo,
        ...(this.options.instructions === undefined
          ? {}
          : { instructions: this.options.instructions }),
      });
      this.initializeResponded = true;
      return;
    }

    if (method === "notifications/initialized") {
      if (this.initializeResponded && !this.initialized) {
        this.initialized = true;
        this.initializedResolve?.();
        this.initializedResolve = null;
      }
      return;
    }
    if (method === "notifications/cancelled") return;
    if (!hasId) return;
    if (!this.initialized) {
      await this.respond(id, undefined, {
        code: -32002,
        message: "Server not initialized",
      });
      return;
    }
    if (method === "ping") {
      await this.respond(id, {});
      return;
    }
    const handler = this.handlers.get(method);
    if (handler === undefined) {
      await this.respond(id, undefined, {
        code: -32601,
        message: "Method not found",
      });
      return;
    }
    try {
      const result = await handler({ params });
      await this.respond(id, result);
    } catch (error) {
      await this.respond(id, undefined, {
        code: -32603,
        message: `Internal error: ${error}`,
      });
    }
  }
}
