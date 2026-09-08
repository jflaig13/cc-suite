#!/usr/bin/env bun
// @bun

// closed_mcp.ts
var ListToolsRequestSchema = { method: "tools/list" };
var CallToolRequestSchema = { method: "tools/call" };
function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

class StdioServerTransport {
  buffered = "";
  receiver = null;
  async start(receiver) {
    if (this.receiver !== null) {
      throw new Error("stdio MCP transport already started");
    }
    this.receiver = receiver;
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      this.buffered += chunk;
      if (Buffer.byteLength(this.buffered, "utf8") > 1024 * 1024) {
        this.send({
          jsonrpc: "2.0",
          id: null,
          error: { code: -32700, message: "MCP input exceeded 1 MiB" }
        }).finally(() => process.exit(78));
        return;
      }
      while (true) {
        const newline = this.buffered.indexOf(`
`);
        if (newline < 0)
          break;
        const raw = this.buffered.slice(0, newline);
        this.buffered = this.buffered.slice(newline + 1);
        if (!raw.trim())
          continue;
        let message;
        try {
          message = JSON.parse(raw);
        } catch {
          this.send({
            jsonrpc: "2.0",
            id: null,
            error: { code: -32700, message: "Parse error" }
          });
          continue;
        }
        receiver(message).catch((error) => {
          process.stderr.write(`MCP receive failed: ${error}
`);
          process.exit(78);
        });
      }
    });
    process.stdin.on("end", () => process.exit(0));
    process.stdin.resume();
  }
  async send(message) {
    const payload = JSON.stringify(message) + `
`;
    await new Promise((accept, reject) => {
      process.stdout.write(payload, (error) => {
        if (error)
          reject(error);
        else
          accept();
      });
    });
  }
}

class Server {
  serverInfo;
  options;
  handlers = new Map;
  transport = null;
  initializeResponded = false;
  initialized = false;
  initializedResolve = null;
  initializedPromise;
  constructor(serverInfo, options) {
    this.serverInfo = serverInfo;
    this.options = options;
    this.initializedPromise = new Promise((accept) => {
      this.initializedResolve = accept;
    });
  }
  setRequestHandler(schema, handler) {
    if (this.handlers.has(schema.method)) {
      throw new Error(`duplicate MCP handler: ${schema.method}`);
    }
    this.handlers.set(schema.method, handler);
  }
  async connect(transport) {
    if (this.transport !== null) {
      throw new Error("MCP server already connected");
    }
    this.transport = transport;
    await transport.start(async (message) => {
      await this.receive(message);
    });
  }
  async waitUntilInitialized() {
    await this.initializedPromise;
  }
  async notification(message) {
    if (!this.initialized || this.transport === null) {
      throw new Error("MCP client is not initialized");
    }
    await this.transport.send({
      jsonrpc: "2.0",
      method: message.method,
      ...message.params === undefined ? {} : { params: message.params }
    });
  }
  async respond(id, result, error) {
    if (this.transport === null)
      throw new Error("MCP transport is absent");
    await this.transport.send({
      jsonrpc: "2.0",
      id,
      ...error === undefined ? { result } : { error }
    });
  }
  async receive(message) {
    if (!isObject(message) || message.jsonrpc !== "2.0" || typeof message.method !== "string") {
      await this.respond(null, undefined, {
        code: -32600,
        message: "Invalid Request"
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
            message: "Initialize request is invalid"
          });
        }
        return;
      }
      const requestedVersion = typeof params.protocolVersion === "string" ? params.protocolVersion : "2025-06-18";
      await this.respond(id, {
        protocolVersion: requestedVersion,
        capabilities: this.options.capabilities,
        serverInfo: this.serverInfo,
        ...this.options.instructions === undefined ? {} : { instructions: this.options.instructions }
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
    if (method === "notifications/cancelled")
      return;
    if (!hasId)
      return;
    if (!this.initialized) {
      await this.respond(id, undefined, {
        code: -32002,
        message: "Server not initialized"
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
        message: "Method not found"
      });
      return;
    }
    try {
      const result = await handler({ params });
      await this.respond(id, result);
    } catch (error) {
      await this.respond(id, undefined, {
        code: -32603,
        message: `Internal error: ${error}`
      });
    }
  }
}

// webhook.ts
import { existsSync as existsSync3 } from "fs";
import { randomUUID } from "crypto";
import { createConnection } from "net";
import { resolve as resolve2, dirname as dirname3, isAbsolute } from "path";

// ../shared/durable_inbox.ts
import { existsSync, readdirSync, readFileSync, writeFileSync, unlinkSync } from "fs";
import { dirname, resolve } from "path";
import { createHash } from "crypto";
function loadCursor(cursorFile) {
  try {
    const raw = readFileSync(cursorFile, "utf-8");
    const parsed = JSON.parse(raw);
    return {
      line: typeof parsed.line === "number" ? parsed.line : 0,
      pid: typeof parsed.pid === "number" ? parsed.pid : 0,
      role: typeof parsed.role === "string" ? parsed.role : "",
      anchor: typeof parsed.anchor === "string" ? parsed.anchor : ""
    };
  } catch {
    return { line: 0, pid: 0, role: "", anchor: "" };
  }
}
function saveCursor(cursorFile, line, pid, role, anchor = "") {
  try {
    writeFileSync(cursorFile, JSON.stringify({ line, pid, role, anchor }));
  } catch {}
}
async function completeLines(inboxFile) {
  if (!existsSync(inboxFile))
    return [];
  const raw = await Bun.file(inboxFile).text();
  const safe = raw.endsWith(`
`) ? raw : raw.slice(0, raw.lastIndexOf(`
`) + 1);
  return safe.split(`
`).filter((l) => l.trim().length > 0);
}
function anchorOf(line) {
  return createHash("sha1").update(line, "utf-8").digest("hex").slice(0, 16);
}
function roleCheckpointFile(sharedDir, role) {
  return resolve(sharedDir, `cursor_${role}_role.json`);
}
function loadRoleCheckpoint(file) {
  try {
    const parsed = JSON.parse(readFileSync(file, "utf-8"));
    return typeof parsed.line === "number" ? parsed.line : 0;
  } catch {
    return 0;
  }
}
function saveRoleCheckpoint(file, line, force = false) {
  try {
    if (force || line > loadRoleCheckpoint(file))
      writeFileSync(file, JSON.stringify({ line }));
  } catch {}
}
function cleanupDeadCursors(sharedDir, role, myPid) {
  try {
    const pattern = new RegExp(`^cursor_${role}_(\\d+)\\.json$`);
    const files = readdirSync(sharedDir).filter((f) => pattern.test(f));
    for (const f of files) {
      const match = f.match(/(\d+)\.json$/);
      if (!match)
        continue;
      const pid = parseInt(match[1], 10);
      if (pid === myPid)
        continue;
      try {
        process.kill(pid, 0);
      } catch {
        try {
          unlinkSync(resolve(sharedDir, f));
        } catch {}
      }
    }
  } catch {}
}
async function drainQueue(inboxFile, cursorFile, pid, role, onEvent) {
  const roleCpFile = roleCheckpointFile(dirname(cursorFile), role);
  const ownCursor = existsSync(cursorFile) ? loadCursor(cursorFile) : null;
  let startLine;
  if (ownCursor !== null) {
    startLine = ownCursor.line;
  } else if (existsSync(roleCpFile)) {
    startLine = loadRoleCheckpoint(roleCpFile);
  } else {
    startLine = 0;
  }
  const allLines = await completeLines(inboxFile);
  const total = allLines.length;
  let desync = startLine > total;
  if (!desync && ownCursor !== null && ownCursor.anchor && startLine > 0) {
    const prev = startLine - 1 < total ? allLines[startLine - 1] : undefined;
    if (prev === undefined || anchorOf(prev) !== ownCursor.anchor)
      desync = true;
  }
  if (desync) {
    console.error(`[durable_inbox] CURSOR DESYNC role=${role}: cursor ${startLine} vs qlen ${total} \u2014 queue truncated/rotated (content-anchor or line-count); resyncing from 0 so pending events deliver`);
    startLine = 0;
    saveRoleCheckpoint(roleCpFile, 0, true);
  }
  const lines = allLines.slice(startLine);
  if (lines.length === 0) {
    const anchor = startLine > 0 && startLine - 1 < total ? anchorOf(allLines[startLine - 1]) : "";
    saveCursor(cursorFile, startLine, pid, role, anchor);
    saveRoleCheckpoint(roleCpFile, startLine);
    return { processed: 0, held: false };
  }
  let processed = 0;
  let held = false;
  for (const line of lines) {
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      processed++;
      continue;
    }
    try {
      await onEvent(event.event_type || "slack_message", event.content || "", event.meta || {});
      processed++;
    } catch {
      held = true;
      break;
    }
  }
  const newLine = startLine + processed;
  const newAnchor = newLine > 0 && newLine - 1 < allLines.length ? anchorOf(allLines[newLine - 1]) : "";
  saveCursor(cursorFile, newLine, pid, role, newAnchor);
  saveRoleCheckpoint(roleCpFile, newLine);
  return { processed, held };
}

// durable_delivery.ts
import {
  existsSync as existsSync2,
  mkdirSync,
  readFileSync as readFileSync2
} from "fs";
import { createHash as createHash2 } from "crypto";
import { dirname as dirname2 } from "path";

// ../shared/send_receipt.ts
import {
  openSync,
  readSync,
  writeSync,
  closeSync,
  fstatSync,
  fsyncSync
} from "fs";
var RACE_SCAN_SLACK_BYTES = 4 * 1024 * 1024;
function readBackAtOffset(path, offset, expectedLine) {
  const lineBytes = Buffer.byteLength(expectedLine, "utf-8");
  let fd = null;
  try {
    fd = openSync(path, "r");
    const exact = Buffer.alloc(lineBytes + 1);
    const got = readSync(fd, exact, 0, lineBytes + 1, offset);
    if (got === lineBytes + 1 && exact.toString("utf-8") === expectedLine + `
`) {
      return true;
    }
    const scanLen = lineBytes + RACE_SCAN_SLACK_BYTES;
    const buf = Buffer.alloc(scanLen);
    const n = readSync(fd, buf, 0, scanLen, offset);
    if (n <= 0)
      return false;
    const text = buf.toString("utf-8", 0, n);
    for (const candidate of text.split(`
`)) {
      if (candidate === expectedLine)
        return true;
    }
    return false;
  } catch {
    return false;
  } finally {
    if (fd !== null) {
      try {
        closeSync(fd);
      } catch {}
    }
  }
}
function appendLineWithReceipt(queuePath, line, verifier = readBackAtOffset) {
  if (!line || line.includes(`
`)) {
    return { ok: false, reason: "invalid line: empty or embedded newline" };
  }
  try {
    JSON.parse(line);
  } catch {
    return { ok: false, reason: "invalid line: not valid JSON (queue rows are JSONL)" };
  }
  let fd = null;
  let offset;
  try {
    fd = openSync(queuePath, "a+");
    const size = fstatSync(fd).size;
    let prefix = "";
    if (size > 0) {
      const last = Buffer.alloc(1);
      if (readSync(fd, last, 0, 1, size - 1) === 1 && last.toString("utf-8") !== `
`) {
        prefix = `
`;
      }
    }
    offset = size + Buffer.byteLength(prefix, "utf-8");
    const payload = Buffer.from(prefix + line + `
`, "utf-8");
    writeSync(fd, payload);
    fsyncSync(fd);
  } catch (err) {
    return { ok: false, reason: `append failed: ${err}` };
  } finally {
    if (fd !== null) {
      try {
        closeSync(fd);
      } catch {}
    }
  }
  if (verifier(queuePath, offset, line)) {
    return { ok: true, reason: "receipt: offset-anchored read-back verified" };
  }
  return { ok: false, reason: "RECEIPT MISSING \u2014 appended line not found on disk read-back" };
}

// durable_delivery.ts
var DELIVERY_SCHEMA = "mise.scribe-delivery-transition.v1";
function canonicalize(value) {
  if (Array.isArray(value))
    return value.map(canonicalize);
  if (value !== null && typeof value === "object") {
    const source = value;
    const sorted = {};
    for (const key of Object.keys(source).sort()) {
      sorted[key] = canonicalize(source[key]);
    }
    return sorted;
  }
  return value;
}
function canonicalJson(value) {
  return JSON.stringify(canonicalize(value));
}
function sha256(value) {
  return createHash2("sha256").update(value, "utf-8").digest("hex");
}
function cloneEvent(event) {
  return {
    event_id: event.event_id,
    event_type: event.event_type,
    content: event.content,
    meta: { ...event.meta },
    timestamp: event.timestamp
  };
}
function cloneSnapshot(snapshot) {
  return {
    event: cloneEvent(snapshot.event),
    event_sha256: snapshot.event_sha256,
    state: snapshot.state,
    ...snapshot.verdict === undefined ? {} : { verdict: snapshot.verdict },
    ...snapshot.acknowledged_at === undefined ? {} : { acknowledged_at: snapshot.acknowledged_at }
  };
}
function isSafeExplicitEventId(value) {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value);
}
function stableEventId(event_type, content, meta = {}) {
  if (isSafeExplicitEventId(meta.event_id)) {
    if (event_type === "slack_message" && isSafeExplicitEventId(meta.slack_channel_id)) {
      const scoped = `${meta.event_id}:${meta.slack_channel_id}`;
      if (isSafeExplicitEventId(scoped))
        return scoped;
      return `${meta.event_id.slice(0, 150)}:` + sha256(meta.slack_channel_id).slice(0, 32);
    }
    return meta.event_id;
  }
  const identityMeta = { ...meta };
  delete identityMeta.event_id;
  delete identityMeta.event_type;
  const digest = sha256(canonicalJson({
    event_type,
    content,
    meta: identityMeta
  }));
  return `scribe-event-${digest.slice(0, 32)}`;
}
function normalizeInboundEvent(event_type, content, meta = {}, now = () => new Date().toISOString()) {
  const eventId = stableEventId(event_type, content, meta);
  const timestamp = typeof meta.timestamp === "string" && meta.timestamp || typeof meta.queued_at === "string" && meta.queued_at || typeof meta.sent_at === "string" && meta.sent_at || now();
  const sourceEventId = typeof meta.event_id === "string" && meta.event_id !== eventId ? meta.event_id : undefined;
  return {
    event_id: eventId,
    event_type,
    content,
    meta: {
      ...meta,
      ...sourceEventId === undefined ? {} : { source_event_id: sourceEventId },
      event_type,
      event_id: eventId,
      timestamp
    },
    timestamp
  };
}
function eventFingerprint(event) {
  const identityMeta = { ...event.meta };
  delete identityMeta.event_id;
  delete identityMeta.event_type;
  delete identityMeta.timestamp;
  return sha256(canonicalJson({
    event_type: event.event_type,
    content: event.content,
    meta: identityMeta
  }));
}
function assertTransitionRecord(value, lineNumber) {
  if (value === null || typeof value !== "object") {
    throw new Error(`delivery journal line ${lineNumber}: record is not an object`);
  }
  const record = value;
  if (record.schema !== DELIVERY_SCHEMA) {
    throw new Error(`delivery journal line ${lineNumber}: unsupported schema`);
  }
  if (!isSafeExplicitEventId(record.event_id)) {
    throw new Error(`delivery journal line ${lineNumber}: invalid event_id`);
  }
  if (typeof record.event_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(record.event_sha256)) {
    throw new Error(`delivery journal line ${lineNumber}: invalid event_sha256`);
  }
  if (record.state !== "pending" && record.state !== "delivered" && record.state !== "acknowledged") {
    throw new Error(`delivery journal line ${lineNumber}: invalid state`);
  }
  if (typeof record.transitioned_at !== "string" || !record.transitioned_at) {
    throw new Error(`delivery journal line ${lineNumber}: invalid transitioned_at`);
  }
  if (record.state === "pending") {
    const event = record.event;
    if (event === undefined || event.event_id !== record.event_id || typeof event.event_type !== "string" || typeof event.content !== "string" || typeof event.timestamp !== "string" || event.meta === null || typeof event.meta !== "object" || Array.isArray(event.meta)) {
      throw new Error(`delivery journal line ${lineNumber}: invalid pending event`);
    }
  }
  if (record.state === "acknowledged" && typeof record.verdict !== "string") {
    throw new Error(`delivery journal line ${lineNumber}: missing verdict`);
  }
  return record;
}

class DurableDeliveryStore {
  journalPath;
  now;
  snapshots = new Map;
  constructor(journalPath, now = () => new Date().toISOString()) {
    this.journalPath = journalPath;
    this.now = now;
    mkdirSync(dirname2(journalPath), { recursive: true, mode: 448 });
    this.refreshFromDisk();
  }
  refreshFromDisk() {
    const previous = this.snapshots;
    this.snapshots = new Map;
    try {
      this.load();
    } catch (error) {
      this.snapshots = previous;
      throw error;
    }
  }
  load() {
    if (!existsSync2(this.journalPath))
      return;
    const raw = readFileSync2(this.journalPath, "utf-8");
    const complete = raw.endsWith(`
`) ? raw : raw.slice(0, raw.lastIndexOf(`
`) + 1);
    const lines = complete.split(`
`).filter((line) => line.trim().length > 0);
    for (const [index, line] of lines.entries()) {
      let decoded;
      try {
        decoded = JSON.parse(line);
      } catch {
        throw new Error(`delivery journal line ${index + 1}: invalid JSON`);
      }
      const record = assertTransitionRecord(decoded, index + 1);
      const current = this.snapshots.get(record.event_id);
      if (record.state === "pending") {
        if (current !== undefined || record.event === undefined) {
          throw new Error(`delivery journal line ${index + 1}: pending is not the first transition`);
        }
        const event = cloneEvent(record.event);
        if (eventFingerprint(event) !== record.event_sha256) {
          throw new Error(`delivery journal line ${index + 1}: pending fingerprint mismatch`);
        }
        this.snapshots.set(record.event_id, {
          event,
          event_sha256: record.event_sha256,
          state: "pending"
        });
        continue;
      }
      if (current === undefined || current.event_sha256 !== record.event_sha256) {
        throw new Error(`delivery journal line ${index + 1}: transition has no matching pending event`);
      }
      if (record.state === "delivered") {
        if (current.state !== "pending") {
          throw new Error(`delivery journal line ${index + 1}: delivered does not follow pending`);
        }
        current.state = "delivered";
        continue;
      }
      if (current.state !== "delivered") {
        throw new Error(`delivery journal line ${index + 1}: acknowledged does not follow delivered`);
      }
      current.state = "acknowledged";
      current.verdict = record.verdict;
      current.acknowledged_at = record.transitioned_at;
    }
  }
  assertCompleteTail() {
    if (!existsSync2(this.journalPath))
      return;
    const raw = readFileSync2(this.journalPath);
    if (raw.length > 0 && raw[raw.length - 1] !== 10) {
      throw new Error("delivery journal has a torn final record; refusing to append behind it");
    }
  }
  appendTransition(record) {
    this.assertCompleteTail();
    const receipt = appendLineWithReceipt(this.journalPath, JSON.stringify(record));
    if (!receipt.ok) {
      throw new Error(`delivery journal append failed: ${receipt.reason}`);
    }
  }
  get(eventId) {
    const snapshot = this.snapshots.get(eventId);
    return snapshot === undefined ? undefined : cloneSnapshot(snapshot);
  }
  getBySendId(sendId) {
    if (!sendId)
      return;
    for (const snapshot of this.snapshots.values()) {
      if (snapshot.event.meta.send_id === sendId) {
        return cloneSnapshot(snapshot);
      }
    }
    return;
  }
  ensurePending(event) {
    const normalized = cloneEvent(event);
    const fingerprint = eventFingerprint(normalized);
    const current = this.snapshots.get(normalized.event_id);
    if (current !== undefined) {
      if (current.event_sha256 !== fingerprint) {
        throw new Error(`event_id conflict: ${normalized.event_id} identifies different content`);
      }
      return { created: false, snapshot: cloneSnapshot(current) };
    }
    const transitionedAt = this.now();
    this.appendTransition({
      schema: DELIVERY_SCHEMA,
      event_id: normalized.event_id,
      event_sha256: fingerprint,
      state: "pending",
      transitioned_at: transitionedAt,
      event: normalized
    });
    const snapshot = {
      event: normalized,
      event_sha256: fingerprint,
      state: "pending"
    };
    this.snapshots.set(normalized.event_id, snapshot);
    return { created: true, snapshot: cloneSnapshot(snapshot) };
  }
  markDelivered(eventId) {
    const current = this.snapshots.get(eventId);
    if (current === undefined) {
      throw new Error(`cannot mark unknown event delivered: ${eventId}`);
    }
    if (current.state === "delivered" || current.state === "acknowledged") {
      return cloneSnapshot(current);
    }
    this.appendTransition({
      schema: DELIVERY_SCHEMA,
      event_id: eventId,
      event_sha256: current.event_sha256,
      state: "delivered",
      transitioned_at: this.now()
    });
    current.state = "delivered";
    return cloneSnapshot(current);
  }
  acknowledge(eventId, verdict) {
    const current = this.snapshots.get(eventId);
    if (current === undefined)
      return { kind: "unknown" };
    if (current.state === "pending") {
      return { kind: "not_delivered", snapshot: cloneSnapshot(current) };
    }
    if (current.state === "acknowledged") {
      return {
        kind: current.verdict === verdict ? "duplicate" : "conflict",
        snapshot: cloneSnapshot(current)
      };
    }
    const acknowledgedAt = this.now();
    this.appendTransition({
      schema: DELIVERY_SCHEMA,
      event_id: eventId,
      event_sha256: current.event_sha256,
      state: "acknowledged",
      transitioned_at: acknowledgedAt,
      verdict
    });
    current.state = "acknowledged";
    current.verdict = verdict;
    current.acknowledged_at = acknowledgedAt;
    return { kind: "acknowledged", snapshot: cloneSnapshot(current) };
  }
  unacknowledged() {
    return [...this.snapshots.values()].filter((snapshot) => snapshot.state !== "acknowledged").map(cloneSnapshot);
  }
  acknowledgedAuditEntries() {
    return [...this.snapshots.values()].filter((snapshot) => snapshot.state === "acknowledged" && snapshot.verdict !== undefined && snapshot.acknowledged_at !== undefined).map((snapshot) => ({
      event_id: snapshot.event.event_id,
      event_type: snapshot.event.event_type,
      content: snapshot.event.content,
      verdict: snapshot.verdict,
      timestamp_event: snapshot.event.timestamp,
      timestamp_ack: snapshot.acknowledged_at
    }));
  }
}
async function deliverWithJournal(store, event, notify) {
  const pending = store.ensurePending(event).snapshot;
  if (pending.state === "acknowledged") {
    return { notification_accepted: false, snapshot: pending };
  }
  await notify(pending.event);
  return {
    notification_accepted: true,
    snapshot: store.markDelivered(pending.event.event_id)
  };
}
function appendInboundEvent(queuePath, event) {
  mkdirSync(dirname2(queuePath), { recursive: true, mode: 448 });
  return appendLineWithReceipt(queuePath, JSON.stringify({
    event_type: event.event_type,
    content: event.content,
    meta: event.meta
  }));
}

// webhook.ts
/*! SPDX-License-Identifier: MPL-2.0
 * Source: https://github.com/jflaig13/cc-suite
 * License: https://mozilla.org/MPL/2.0/
 */
var SCRIBE_PORT = 8789;
if (process.env.SCRIBE_OFFLINE_PROBE === "1") {
  console.log("offline-bun-ok");
  process.exit(0);
}
function requiredDirectory(name) {
  const value = process.env[name] || "";
  if (!isAbsolute(value) || resolve2(value) !== value) {
    console.error(`[scribe-channel] REFUSED: ${name} must be an explicit absolute path`);
    process.exit(78);
  }
  return value;
}
var expectedParent = Number(process.env.SCRIBE_EXPECTED_PARENT_PID || "0");
if (process.env.MISE_SCRIBE_ADMITTED !== "1" || !Number.isSafeInteger(expectedParent) || expectedParent <= 1 || process.ppid !== expectedParent) {
  console.error("[scribe-channel] REFUSED: exact admitted runtime parent is required");
  process.exit(78);
}
var CHANNEL_DIR = dirname3(resolve2(import.meta.path));
var CHANNEL_STATE_DIR = requiredDirectory("SCRIBE_CHANNEL_STATE_DIR");
var QUEUE_FILE = resolve2(CHANNEL_STATE_DIR, "event_queue.jsonl");
var AUDIT_LOG = resolve2(CHANNEL_STATE_DIR, "audit_log.jsonl");
var DELIVERY_STATE_DIR = requiredDirectory("SCRIBE_DELIVERY_STATE_DIR");
var DELIVERY_JOURNAL = resolve2(DELIVERY_STATE_DIR, "delivery_journal.jsonl");
var LOCAL_CURSOR = resolve2(DELIVERY_STATE_DIR, `cursor_scribe-local_${process.pid}.json`);
var SLACK_CURSOR = resolve2(DELIVERY_STATE_DIR, `cursor_scribe-shared_${process.pid}.json`);
var admittedRuntime = process.env.MISE_SCRIBE_ADMITTED === "1";
var configuredSharedDir = requiredDirectory("SCRIBE_CHANNEL_SHARED_DIR");
if (admittedRuntime && (!isAbsolute(configuredSharedDir) || resolve2(configuredSharedDir) !== configuredSharedDir)) {
  console.error("[scribe-channel] REFUSED: admitted runtime requires one exact external channel bus");
  process.exit(78);
}
var WORKSPACE_ROOT = process.env.SCRIBE_WORKSPACE_ROOT ? resolve2(process.env.SCRIBE_WORKSPACE_ROOT) : resolve2(CHANNEL_DIR, "../..");
var SHARED_DIR = configuredSharedDir ? resolve2(configuredSharedDir) : resolve2(WORKSPACE_ROOT, "channels/shared");
var SLACK_ROLE_QUEUE = resolve2(SHARED_DIR, "queue_scribe.jsonl");
var CHANNEL_EFFECT_SOCKET = process.env.SCRIBE_CHANNEL_EFFECT_SOCKET || "";
async function callChannelEffect(name, args) {
  if (!CHANNEL_EFFECT_SOCKET.startsWith("/")) {
    throw new Error("Company Scribe channel effect socket is unavailable");
  }
  return await new Promise((accept, reject) => {
    const identifier = randomUUID();
    const socket = createConnection(CHANNEL_EFFECT_SOCKET);
    let buffered = "";
    let settled = false;
    const timer = setTimeout(() => {
      socket.destroy();
      reject(new Error("Company Scribe channel authorization timed out"));
    }, 5000);
    const finish = (error, value) => {
      if (settled)
        return;
      settled = true;
      clearTimeout(timer);
      socket.destroy();
      if (error)
        reject(error);
      else if (value === undefined)
        reject(new Error("Company Scribe channel effect returned no result"));
      else
        accept(value);
    };
    socket.on("connect", () => {
      socket.write(JSON.stringify({
        jsonrpc: "2.0",
        id: identifier,
        method: "tools/call",
        params: { name, arguments: args }
      }) + `
`);
    });
    socket.on("data", (chunk) => {
      buffered += chunk.toString("utf8");
      if (Buffer.byteLength(buffered, "utf8") > 1024 * 1024) {
        finish(new Error("Company Scribe channel effect response exceeded 1 MiB"));
        return;
      }
      const newline = buffered.indexOf(`
`);
      if (newline < 0)
        return;
      try {
        const response = JSON.parse(buffered.slice(0, newline));
        const text = response.result?.content?.[0]?.text;
        if (response.id !== identifier || response.result?.isError || typeof text !== "string") {
          finish(new Error("Company Scribe channel effect was refused"));
          return;
        }
        const value = JSON.parse(text);
        if (value === null || typeof value !== "object" || Array.isArray(value)) {
          finish(new Error("Company Scribe channel effect result was malformed"));
          return;
        }
        finish(undefined, value);
      } catch {
        finish(new Error("Company Scribe channel effect response was malformed"));
      }
    });
    socket.on("error", (error) => finish(error));
    socket.on("end", () => finish(new Error("Company Scribe channel effect closed without a receipt")));
  });
}
var VALID_ROLES_SCRIBE = new Set([
  "ccto",
  "ccpo",
  "ccro",
  "ccfo",
  "ccmo",
  "cclo",
  "ccgo",
  "ccco",
  "ccde",
  "cos",
  "scribe",
  "utility"
]);
var VALID_EVENT_TYPES_SCRIBE = new Set([
  "agent_direct",
  "task_handoff",
  "audit_request",
  "reply_classification"
]);
async function handleScribeSend(args) {
  const {
    effect_id,
    target_role,
    content,
    event_type = "audit_request",
    meta = {}
  } = args;
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(effect_id || "")) {
    return { success: false, error: "A stable effect_id is required" };
  }
  if (!VALID_EVENT_TYPES_SCRIBE.has(event_type)) {
    return {
      success: false,
      error: `Unknown event_type "${event_type}". Valid: ${[...VALID_EVENT_TYPES_SCRIBE].join(", ")}`
    };
  }
  if (!VALID_ROLES_SCRIBE.has(target_role)) {
    return {
      success: false,
      error: `Unknown target_role "${target_role}". Valid: ${[...VALID_ROLES_SCRIBE].join(", ")}`
    };
  }
  try {
    const result = await callChannelEffect("channel_send", {
      effect_id,
      target_role,
      content,
      event_type,
      meta
    });
    if (result.state !== "sent" || typeof result.send_id !== "string") {
      throw new Error("broker did not return a durable send receipt");
    }
    console.error(`[scribe-channel] SEND ${result.send_id}: scribe\u2192${target_role} ` + `[${event_type}] ${content.substring(0, 60)}`);
    return { success: true, send_id: result.send_id };
  } catch (error) {
    return {
      success: false,
      error: `SEND FAILED \u2014 broker did not durably deliver to queue_${target_role}: ${error}`
    };
  }
}
var deliveryStore = new DurableDeliveryStore(DELIVERY_JOURNAL);
var pendingEvents = new Map;
for (const snapshot of deliveryStore.unacknowledged()) {
  pendingEvents.set(snapshot.event.event_id, {
    event_type: snapshot.event.event_type,
    content: snapshot.event.content,
    timestamp: snapshot.event.timestamp
  });
}
function refreshPendingProjection() {
  deliveryStore.refreshFromDisk();
  pendingEvents.clear();
  for (const snapshot of deliveryStore.unacknowledged()) {
    pendingEvents.set(snapshot.event.event_id, {
      event_type: snapshot.event.event_type,
      content: snapshot.event.content,
      timestamp: snapshot.event.timestamp
    });
  }
}
cleanupDeadCursors(DELIVERY_STATE_DIR, "scribe-local", process.pid);
cleanupDeadCursors(DELIVERY_STATE_DIR, "scribe-shared", process.pid);
var notifiedThisProcess = new Set;
var deliveryInFlight = new Map;
var metricsServerStartTime = Date.now();
var metricsEventsReceived = pendingEvents.size;
var metricsAcksAccepted = 0;
var metricsAcksRejected = 0;
var metricsAcksUntracked = 0;
var VERDICT_PATTERNS = {
  brain_file_created: [
    /WIRING COMPLETE/i,
    /WIRING INCOMPLETE/i,
    /CONFLICT DETECTED/i
  ],
  deploy_completed: [/DOC SYNC CLEAN/i, /DRIFT FOUND/i],
  canon_changed: [
    /CANON CHANGE CLEAN/i,
    /AUTHORITY CONFLICT/i,
    /DOWNSTREAM UPDATES NEEDED/i
  ],
  tandem_board_updated: [
    /V-LOOP STEP VALID/i,
    /V-LOOP VIOLATION/i,
    /TANDEM AUDIT CLEAN/i
  ],
  verification_result: [
    /ALL FIELDS MATCH/i,
    /MISMATCH.*FOUND/i,
    /VERIFICATION LOGGED/i
  ]
};
function hasValidVerdict(event_type, text) {
  const patterns = VERDICT_PATTERNS[event_type];
  if (!patterns)
    return true;
  return patterns.some((p) => p.test(text));
}
async function acknowledgeEvent(effectId, eventId, text) {
  if (typeof effectId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(effectId) || typeof eventId !== "string" || eventId.length === 0 || typeof text !== "string" || text.length === 0) {
    metricsAcksRejected++;
    return {
      ok: false,
      status: 400,
      kind: "invalid_request",
      message: "effect_id, event_id, and text must be valid non-empty strings"
    };
  }
  try {
    refreshPendingProjection();
  } catch (error) {
    metricsAcksRejected++;
    return {
      ok: false,
      status: 503,
      kind: "persistence_failed",
      message: `Delivery projection could not be refreshed: ${error}`
    };
  }
  let durable = deliveryStore.get(eventId);
  if (durable === undefined) {
    durable = deliveryStore.getBySendId(eventId);
    if (durable !== undefined) {
      eventId = durable.event.event_id;
    }
  }
  if (durable === undefined) {
    metricsAcksUntracked++;
    return {
      ok: false,
      status: 404,
      kind: "unknown",
      message: `Unknown event_id: ${eventId}`
    };
  }
  const wasAcknowledged = durable.state === "acknowledged";
  if (durable.state === "acknowledged") {
    if (durable.verdict !== text) {
      metricsAcksRejected++;
      return {
        ok: false,
        status: 409,
        kind: "conflict",
        message: `Conflicting acknowledgment for ${eventId}`
      };
    }
  }
  if (durable.state === "pending") {
    metricsAcksRejected++;
    return {
      ok: false,
      status: 409,
      kind: "not_delivered",
      message: `Event ${eventId} has not reached delivered state`
    };
  }
  if (durable.state !== "acknowledged" && !hasValidVerdict(durable.event.event_type, text)) {
    metricsAcksRejected++;
    const patterns = VERDICT_PATTERNS[durable.event.event_type];
    const required = patterns ? patterns.map((pattern) => pattern.source).join(" OR ") : "none";
    return {
      ok: false,
      status: 422,
      kind: "invalid_verdict",
      message: `Event ${eventId} (${durable.event.event_type}) requires verdict: ` + `${required}. Run the full checklist, then reply again.`
    };
  }
  let kind;
  try {
    const result = await callChannelEffect("channel_reply", {
      effect_id: effectId,
      event_id: eventId,
      text
    });
    if (result.kind !== "acknowledged" && result.kind !== "duplicate") {
      throw new Error("broker returned an invalid acknowledgment receipt");
    }
    kind = result.kind;
    refreshPendingProjection();
    const persisted = deliveryStore.get(durable.event.event_id);
    if (persisted?.state !== "acknowledged" || persisted.verdict !== text) {
      throw new Error("broker receipt is absent from the durable journal");
    }
  } catch (err) {
    try {
      refreshPendingProjection();
    } catch {}
    metricsAcksRejected++;
    return {
      ok: false,
      status: 503,
      kind: "persistence_failed",
      message: `Acknowledgment was not persisted: ${err}`
    };
  }
  pendingEvents.delete(durable.event.event_id);
  if (!wasAcknowledged)
    metricsAcksAccepted++;
  console.error(`[scribe-channel] VERIFIED ${eventId}: ${text.substring(0, 80)}`);
  return {
    ok: true,
    status: 200,
    kind,
    message: kind === "duplicate" ? "acknowledged (idempotent replay)" : "acknowledged"
  };
}
async function pushEvent(event_type, content, meta = {}) {
  const event = normalizeInboundEvent(event_type, content, meta);
  const admitted = deliveryStore.ensurePending(event);
  const storedEvent = admitted.snapshot.event;
  const eventId = storedEvent.event_id;
  if (admitted.created)
    metricsEventsReceived++;
  if (admitted.snapshot.state === "acknowledged") {
    pendingEvents.delete(eventId);
    return eventId;
  }
  pendingEvents.set(eventId, {
    event_type: storedEvent.event_type,
    content: storedEvent.content,
    timestamp: storedEvent.timestamp
  });
  if (notifiedThisProcess.has(eventId))
    return eventId;
  const active = deliveryInFlight.get(eventId);
  if (active !== undefined)
    return active;
  const attempt = (async () => {
    try {
      await deliverWithJournal(deliveryStore, storedEvent, async (durableEvent) => {
        await mcp.notification({
          method: "notifications/claude/channel",
          params: {
            content: durableEvent.content,
            meta: durableEvent.meta
          }
        });
      });
      notifiedThisProcess.add(eventId);
      console.error(`[scribe-channel] Event ${eventId}: ${storedEvent.event_type} \u2014 ${storedEvent.content.substring(0, 100)}`);
      return eventId;
    } catch (err) {
      console.error(`[scribe-channel] Notification failed for ${eventId}: ${err}`);
      throw err;
    } finally {
      deliveryInFlight.delete(eventId);
    }
  })();
  deliveryInFlight.set(eventId, attempt);
  return attempt;
}
var localDrainInFlight = null;
var slackDrainInFlight = null;
function drainLocalQueue() {
  if (localDrainInFlight !== null)
    return localDrainInFlight;
  const drain = drainQueue(QUEUE_FILE, LOCAL_CURSOR, process.pid, "scribe-local", async (eventType, eventContent, eventMeta) => {
    await pushEvent(eventType || "message", eventContent, eventMeta);
  }).finally(() => {
    localDrainInFlight = null;
  });
  localDrainInFlight = drain;
  return drain;
}
function drainSlackQueues() {
  if (slackDrainInFlight !== null)
    return slackDrainInFlight;
  const drain = drainQueue(SLACK_ROLE_QUEUE, SLACK_CURSOR, process.pid, "scribe-shared", async (eventType, eventContent, eventMeta) => {
    await pushEvent(eventType || "slack_message", eventContent, eventMeta);
  }).finally(() => {
    slackDrainInFlight = null;
  });
  slackDrainInFlight = drain;
  return drain;
}
async function retryUnacknowledged() {
  let accepted = 0;
  for (const snapshot of deliveryStore.unacknowledged()) {
    try {
      await pushEvent(snapshot.event.event_type, snapshot.event.content, snapshot.event.meta);
      accepted++;
    } catch {}
  }
  return accepted;
}
var mcp = new Server({ name: "scribe-channel", version: "3.2.0" }, {
  capabilities: {
    experimental: { "claude/channel": {} },
    tools: {}
  },
  instructions: `You are the Scribe \u2014 Keeper of the Mise Bible. Events from scribe-channel are institutional signals. You MUST follow the mechanical checklist for each event type. No shortcuts. No skipping steps.

HARD CONSTRAINT: You NEVER make code changes from channel events. You record, verify, and flag.

CRITICAL: The reply tool VALIDATES your verdict. If your acknowledgment does not contain the required verdict phrase, the server REJECTS it. Required verdicts:
  - brain_file_created: "WIRING COMPLETE" or "WIRING INCOMPLETE" or "CONFLICT DETECTED"
  - deploy_completed: "DOC SYNC CLEAN" or "DRIFT FOUND"
  - canon_changed: "CANON CHANGE CLEAN" or "AUTHORITY CONFLICT" or "DOWNSTREAM UPDATES NEEDED"
  - tandem_board_updated: "V-LOOP STEP VALID" or "V-LOOP VIOLATION" or "TANDEM AUDIT CLEAN"
  - slack_message / message: no specific verdict required

Your verdict is permanently logged to the append-only delivery journal.

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
EVENT: brain_file_created
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

STEP 1 \u2014 EXTRACT: Page the brain file through workspace_read until total_lines is exhausted. Output:
  - Title / Status / Purpose / Core assertion

STEP 2 \u2014 COMPARE (Wiring Checklist): Check each, mark WIRED / UNWIRED / N/A:
  [ ] CLAUDE.md "Institutional Rules" \u2014 rule all agents must know?
  [ ] Canonical workspace memory projection \u2014 affects cross-session behavior? (host memory is unavailable)
  [ ] CC Exec registries \u2014 role-specific?
  [ ] MISE_MASTER_SPEC.md \u2014 company fact?
  [ ] Supersedes existing? \u2014 conflicts with other brain files?

STEP 3 \u2014 VERDICT: WIRING COMPLETE / WIRING INCOMPLETE / CONFLICT DETECTED

STEP 4 \u2014 ACKNOWLEDGE: reply tool with event_id + verdict.

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
EVENT: deploy_completed
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

STEP 1 \u2014 EXTRACT: use the event-provided deploy revision and externally delivered Git-history inventory; report GIT HISTORY INVENTORY UNAVAILABLE if absent
STEP 2 \u2014 COMPARE: MISE_MASTER_SPEC / MEMORY.md / brain files / CLAUDE.md
STEP 3 \u2014 VERDICT: DOC SYNC CLEAN / DRIFT FOUND
STEP 4 \u2014 ACKNOWLEDGE: reply tool with event_id + verdict.

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
EVENT: canon_changed
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

STEP 1 \u2014 EXTRACT: page the changed file through workspace_read and use the event-provided or externally delivered diff; report the evidence gap if absent.
STEP 2 \u2014 COMPARE: Authority hierarchy conflict? Downstream docs stale?
STEP 3 \u2014 VERDICT: CANON CHANGE CLEAN / AUTHORITY CONFLICT / DOWNSTREAM UPDATES NEEDED
STEP 4 \u2014 ACKNOWLEDGE: reply tool with event_id + verdict.

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
EVENT: tandem_board_updated
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

STEP 1 \u2014 EXTRACT: Read TANDEM_BOARD.md. Identify which ticket/step changed.
STEP 2 \u2014 COMPARE: V-Loop compliance check:
  [ ] Are all required V-Loop steps present for the ticket?
  [ ] Is the step sequence correct (no skipped steps)?
  [ ] Does evidence column have specifics (not "N/A" or "looks good")?
  [ ] Was CCPO verification done BEFORE CCTO declared verified?
STEP 3 \u2014 VERDICT: V-LOOP STEP VALID / V-LOOP VIOLATION / TANDEM AUDIT CLEAN
STEP 4 \u2014 ACKNOWLEDGE: reply tool with event_id + verdict.

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
EVENT: slack_message
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

A message from #tandem or #common-room pushed in real-time.
Attributes: slack_channel (tandem or common-room), slack_user, slack_ts.

Read the message. If it requires Scribe action (handoff, directive, brain file request, audit request), act immediately. If informational, acknowledge briefly.

ACKNOWLEDGE: reply tool with event_id + what you did or "noted."

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
EVENT: verification_result
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

Automated verification script results. Contains structured per-field MATCH/MISMATCH data.
This is MECHANICAL \u2014 no agent ran these checks, a Python script did.

Content is JSON with: shift, total_checks, matches, mismatches, verdict, details.

If ALL FIELDS MATCH \u2192 acknowledge with "VERIFICATION LOGGED. ALL FIELDS MATCH."
If MISMATCHES FOUND \u2192 flag to founder. List every mismatch. This is a data integrity issue.

ACKNOWLEDGE: reply tool with "ALL FIELDS MATCH" or "MISMATCH(ES) FOUND" + details.

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
EVENT: browser_fresh_violation / browser_closed / deploy_detected / verification_incomplete
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

Real-time events from verification agent hooks (any agent, not just CCPO).
These are the Scribe's eyes into whether verification agents are following protocol.

- browser_fresh_violation: Agent took a snapshot without closing browser first. Flag it.
- browser_closed: Agent closed browser (good \u2014 fresh browser marker set). Note it.
- deploy_detected: Agent's window detected a deploy. Verification should follow.
- verification_incomplete: Agent declared VERIFIED/CLEAN without EXTRACTION + COMPARISON tables. Flag it.

For violations (browser_fresh_violation, verification_incomplete): log and escalate to founder.
For informational (browser_closed, deploy_detected): acknowledge briefly.

\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
EVENT: message
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

Direct message from founder or agent. Route to appropriate Scribe operation.
ACKNOWLEDGE: reply tool confirming what you did.`
});
mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "reply",
      description: "Acknowledge a scribe-channel event. MUST include the required verdict phrase or the server rejects it.",
      inputSchema: {
        type: "object",
        properties: {
          event_id: {
            type: "string",
            description: "The channel event_id, or the coworker send_id on that row."
          },
          text: {
            type: "string",
            description: "Your verdict and acknowledgment."
          },
          effect_id: {
            type: "string",
            description: "Stable caller-generated idempotency key for this exact reply."
          }
        },
        required: ["effect_id", "event_id", "text"]
      }
    },
    {
      name: "send",
      description: "Send a direct message to a peer agent's channel queue. Scribe is the fleet comms hub (founder-ratified 2026-07-02) \u2014 relays dispositions, handoffs, and audits to any lane.",
      inputSchema: {
        type: "object",
        properties: {
          target_role: {
            type: "string",
            enum: [
              "ccto",
              "ccpo",
              "ccro",
              "ccfo",
              "ccmo",
              "cclo",
              "ccgo",
              "ccco",
              "ccde",
              "cos",
              "scribe",
              "utility"
            ],
            description: "Receiving agent role"
          },
          content: {
            type: "string",
            description: "Message body (FROM/TO/SUBJECT envelope for handoffs)"
          },
          event_type: {
            type: "string",
            enum: ["agent_direct", "task_handoff", "audit_request", "reply_classification"],
            description: "Default: audit_request"
          },
          meta: {
            type: "object",
            description: "Optional structured payload (expected_artifact opens a stall-tracked handoff row)",
            additionalProperties: true
          },
          effect_id: {
            type: "string",
            description: "Stable caller-generated idempotency key for this exact send."
          }
        },
        required: ["effect_id", "target_role", "content"]
      }
    }
  ]
}));
mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name === "send") {
    const args = req.params.arguments;
    const result = await handleScribeSend(args);
    if (result.success) {
      return { content: [{ type: "text", text: `sent: ${result.send_id}` }] };
    }
    return {
      content: [{ type: "text", text: `error: ${result.error}` }],
      isError: true
    };
  }
  if (req.params.name === "reply") {
    const { effect_id, event_id, text } = req.params.arguments;
    const outcome = await acknowledgeEvent(effect_id, event_id, text);
    return {
      content: [{ type: "text", text: outcome.message }],
      ...outcome.ok ? {} : { isError: true }
    };
  }
  throw new Error(`unknown tool: ${req.params.name}`);
});
await mcp.connect(new StdioServerTransport);
await mcp.waitUntilInitialized();
if (!admittedRuntime)
  Bun.serve({
    port: SCRIBE_PORT,
    hostname: "127.0.0.1",
    idleTimeout: 0,
    async fetch(req) {
      const url = new URL(req.url);
      if (req.method === "GET" && url.pathname === "/health") {
        return new Response(JSON.stringify({
          status: "ok",
          server: "scribe-channel",
          version: "3.2.0",
          port: SCRIBE_PORT,
          pending_events: pendingEvents.size,
          queue_exists: existsSync3(QUEUE_FILE),
          slack_source: "shared_poller",
          slack_role_queue: existsSync3(SLACK_ROLE_QUEUE),
          delivery_journal: DELIVERY_JOURNAL
        }), { headers: { "Content-Type": "application/json" } });
      }
      if (req.method === "GET" && url.pathname === "/metrics") {
        const uptimeSecs = Math.floor((Date.now() - metricsServerStartTime) / 1000);
        const auditLogLines = existsSync3(AUDIT_LOG) ? (await Bun.file(AUDIT_LOG).text()).trim().split(`
`).filter((l) => l.length > 0).length : 0;
        return new Response(JSON.stringify({
          server: "scribe-channel",
          version: "3.2.0",
          uptime_seconds: uptimeSecs,
          uptime_human: `${Math.floor(uptimeSecs / 3600)}h ${Math.floor(uptimeSecs % 3600 / 60)}m ${uptimeSecs % 60}s`,
          events_received_total: metricsEventsReceived,
          acks_accepted_total: metricsAcksAccepted,
          acks_rejected_total: metricsAcksRejected,
          acks_untracked_total: metricsAcksUntracked,
          pending_events_current: pendingEvents.size,
          audit_log_size: auditLogLines,
          durable_acknowledgments: deliveryStore.acknowledgedAuditEntries().length,
          delivery_rate_pct: metricsEventsReceived > 0 ? Math.round(metricsAcksAccepted / metricsEventsReceived * 100) : null
        }), { headers: { "Content-Type": "application/json" } });
      }
      if (req.method === "GET" && url.pathname === "/audit") {
        const legacyEntries = existsSync3(AUDIT_LOG) ? (await Bun.file(AUDIT_LOG).text()).trim().split(`
`).filter((line) => line.length > 0).map((line) => {
          try {
            return JSON.parse(line);
          } catch {
            return null;
          }
        }).filter(Boolean) : [];
        const entries = [
          ...legacyEntries,
          ...deliveryStore.acknowledgedAuditEntries()
        ];
        return new Response(JSON.stringify({ entries }), {
          headers: { "Content-Type": "application/json" }
        });
      }
      if (req.method === "POST" && url.pathname === "/send") {
        const body = await req.json();
        const result = await handleScribeSend(body);
        if (result.success) {
          return new Response(JSON.stringify({ status: "ok", send_id: result.send_id }), { headers: { "Content-Type": "application/json" } });
        }
        return new Response(JSON.stringify({ status: "error", error: result.error }), { status: 400, headers: { "Content-Type": "application/json" } });
      }
      if (req.method === "POST" && url.pathname === "/reply") {
        const decoded = await req.json();
        const body = decoded !== null && typeof decoded === "object" && !Array.isArray(decoded) ? decoded : {};
        const outcome = await acknowledgeEvent(body.effect_id, body.event_id, body.text);
        return new Response(JSON.stringify({
          status: outcome.ok ? "ok" : "rejected",
          kind: outcome.kind,
          message: outcome.message
        }), {
          status: outcome.status,
          headers: { "Content-Type": "application/json" }
        });
      }
      if (req.method === "POST" && url.pathname === "/drain") {
        const result = await drainLocalQueue();
        return new Response(JSON.stringify({
          status: result.held ? "held" : "ok",
          drained: result.processed,
          held: result.held
        }), { headers: { "Content-Type": "application/json" } });
      }
      if (req.method === "POST") {
        const contentType = req.headers.get("content-type") || "";
        let event_type = "message";
        let content = "";
        let meta = {};
        if (contentType.includes("application/json")) {
          const decoded = await req.json();
          if (decoded === null || typeof decoded !== "object" || Array.isArray(decoded)) {
            return new Response(JSON.stringify({
              status: "error",
              message: "JSON body must be an object"
            }), { status: 400, headers: { "Content-Type": "application/json" } });
          }
          const body = decoded;
          if (body.event_type !== undefined && typeof body.event_type !== "string" || body.content !== undefined && typeof body.content !== "string" || body.meta !== undefined && (body.meta === null || typeof body.meta !== "object" || Array.isArray(body.meta) || Object.values(body.meta).some((value) => typeof value !== "string"))) {
            return new Response(JSON.stringify({
              status: "error",
              message: "event_type and content must be strings; meta values must be strings"
            }), { status: 400, headers: { "Content-Type": "application/json" } });
          }
          event_type = body.event_type || "message";
          content = body.content || "";
          meta = body.meta || {};
        } else {
          content = await req.text();
          event_type = url.searchParams.get("event_type") || "message";
        }
        if (typeof meta.event_id !== "string" || meta.event_id.length === 0) {
          meta = { ...meta, event_id: `scribe-http-${randomUUID()}` };
        }
        const event = normalizeInboundEvent(event_type, content, meta);
        const receipt = appendInboundEvent(QUEUE_FILE, event);
        if (!receipt.ok) {
          return new Response(JSON.stringify({
            status: "error",
            message: `Event was not durably queued: ${receipt.reason}`
          }), { status: 503, headers: { "Content-Type": "application/json" } });
        }
        const result = await drainLocalQueue();
        const durable = deliveryStore.get(event.event_id);
        const delivered = durable?.state === "delivered" || durable?.state === "acknowledged";
        return new Response(JSON.stringify({
          status: delivered ? "ok" : "queued",
          event_id: event.event_id,
          delivery_state: durable?.state || "source_queued",
          drain_held: result.held
        }), {
          status: delivered ? 200 : 202,
          headers: { "Content-Type": "application/json" }
        });
      }
      return new Response("Method not allowed", { status: 405 });
    }
  });
var rehydrated = await retryUnacknowledged();
if (rehydrated > 0) {
  console.error(`[scribe-channel] Retried ${rehydrated} unacknowledged journal event(s)`);
}
var backlog = await drainLocalQueue();
if (backlog.processed > 0) {
  console.error(`[scribe-channel] Drained ${backlog.processed} queued event(s) from backlog`);
}
if (backlog.held) {
  console.error("[scribe-channel] Local source cursor held for delivery retry");
}
setInterval(async () => {
  if (existsSync3(QUEUE_FILE)) {
    const result = await drainLocalQueue();
    if (result.processed > 0) {
      console.error(`[scribe-channel] Periodic drain: ${result.processed}`);
    }
  }
}, 2000);
setInterval(async () => {
  const result = await drainSlackQueues();
  if (result.processed > 0) {
    console.error(`[scribe-channel] Slack queue drain: ${result.processed} message(s)`);
  }
}, 3000);
var slackBacklog = await drainSlackQueues();
if (slackBacklog.processed > 0) {
  console.error(`[scribe-channel] Drained ${slackBacklog.processed} Slack message(s) from backlog`);
}
if (slackBacklog.held) {
  console.error("[scribe-channel] Shared source cursor held for delivery retry");
}
console.error(admittedRuntime ? "[scribe-channel] v3.2.0 \u2014 admitted MCP-only runtime; HTTP disabled" : `[scribe-channel] v3.2.0 \u2014 Listening on http://127.0.0.1:${SCRIBE_PORT}`);
console.error(`[scribe-channel] Slack source: shared poller queues at ${SHARED_DIR}`);
console.error(`[scribe-channel] Queue: ${QUEUE_FILE}`);
console.error(`[scribe-channel] Delivery journal: ${DELIVERY_JOURNAL}`);
console.error(`[scribe-channel] Legacy audit: ${AUDIT_LOG}`);
console.error(`[scribe-channel] NOTE: Shared Slack poller must be running: nohup bun run channels/shared/slack_poller.ts &`);
