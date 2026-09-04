#!/usr/bin/env bun
/**
 * Scribe Channel v3.2 — Full institutional observer.
 *
 * Five event sources:
 *   1. PostToolUse hook → brain files, governance files, TANDEM_BOARD
 *   2. Notify script → deploys, manual messages
 *   3. Slack #tandem → push (replaces polling)
 *   4. Slack #common-room → push (replaces polling)
 *   5. Direct POST → any event
 *
 * Three reliability guarantees:
 *   1. EVENT QUEUE — Events written to append-only source files first.
 *   2. DELIVERY JOURNAL — Exact pending → delivered → acknowledged lifecycle.
 *   3. REPLY VALIDATION — Server rejects bad verdicts.
 *
 * Usage:
 *   claude --dangerously-load-development-channels server:scribe-channel
 */

import {
  Server,
  StdioServerTransport,
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "./closed_mcp.ts";
import { existsSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { createConnection } from "node:net";
import { resolve, dirname, isAbsolute } from "node:path";
import {
  cleanupDeadCursors,
  drainQueue as drainDurableQueue,
  type DrainResult,
} from "../shared/durable_inbox.ts";
import {
  DurableDeliveryStore,
  appendInboundEvent,
  deliverWithJournal,
  normalizeInboundEvent,
} from "./durable_delivery.ts";

const SCRIBE_PORT = 8789;
// Push delivery via mcp.notification() is durable and retryable:
// - Top-level launcher sessions (--dangerously-load-development-channels) subscribe
//   to the experimental claude/channel capability and receive notifications directly.
// - If notification() rejects, the event remains pending and its source cursor
//   does not advance. The same stable event_id is retried; failure is never
//   converted into apparent delivery.
// A closed-graph import probe performs no file or network effects.
if (process.env.SCRIBE_OFFLINE_PROBE === "1") {
  console.log("offline-bun-ok");
  process.exit(0);
}
function requiredDirectory(name: string): string {
  const value = process.env[name] || "";
  if (!isAbsolute(value) || resolve(value) !== value) {
    console.error(`[scribe-channel] REFUSED: ${name} must be an explicit absolute path`);
    process.exit(78);
  }
  return value;
}
const expectedParent = Number(process.env.SCRIBE_EXPECTED_PARENT_PID || "0");
if (process.env.MISE_SCRIBE_ADMITTED !== "1" ||
    !Number.isSafeInteger(expectedParent) || expectedParent <= 1 || process.ppid !== expectedParent) {
  console.error("[scribe-channel] REFUSED: exact admitted runtime parent is required");
  process.exit(78);
}
const CHANNEL_DIR = dirname(resolve(import.meta.path));
const CHANNEL_STATE_DIR = requiredDirectory("SCRIBE_CHANNEL_STATE_DIR");
const QUEUE_FILE = resolve(CHANNEL_STATE_DIR, "event_queue.jsonl");
const AUDIT_LOG = resolve(CHANNEL_STATE_DIR, "audit_log.jsonl");
const DELIVERY_STATE_DIR = requiredDirectory("SCRIBE_DELIVERY_STATE_DIR");
const DELIVERY_JOURNAL = resolve(DELIVERY_STATE_DIR, "delivery_journal.jsonl");
const LOCAL_CURSOR = resolve(
  DELIVERY_STATE_DIR,
  `cursor_scribe-local_${process.pid}.json`
);
const SLACK_CURSOR = resolve(
  DELIVERY_STATE_DIR,
  `cursor_scribe-shared_${process.pid}.json`
);
const admittedRuntime = process.env.MISE_SCRIBE_ADMITTED === "1";

// --- Shared Slack poller integration ----------------------------------------
// Scribe reads from its own queue file written by channels/shared/slack_poller.ts
// No direct Slack API calls from this server — the shared poller handles it.
// The poller fans out broadcast messages to each consumer's queue at write time,
// so Scribe only needs to drain ONE file (no separate broadcast queue).
const configuredSharedDir = requiredDirectory("SCRIBE_CHANNEL_SHARED_DIR");
if (
  admittedRuntime &&
  (
    !isAbsolute(configuredSharedDir) ||
    resolve(configuredSharedDir) !== configuredSharedDir
  )
) {
  console.error(
    "[scribe-channel] REFUSED: admitted runtime requires one exact external channel bus"
  );
  process.exit(78);
}
const WORKSPACE_ROOT = process.env.SCRIBE_WORKSPACE_ROOT
  ? resolve(process.env.SCRIBE_WORKSPACE_ROOT)
  : resolve(CHANNEL_DIR, "../..");
const SHARED_DIR = configuredSharedDir
  ? resolve(configuredSharedDir)
  : resolve(WORKSPACE_ROOT, "channels/shared");
const SLACK_ROLE_QUEUE = resolve(SHARED_DIR, "queue_scribe.jsonl");
const CHANNEL_EFFECT_SOCKET = process.env.SCRIBE_CHANNEL_EFFECT_SOCKET || "";

// The package verifier imports the fully bundled graph with installation
// disabled, a blank cache, and no workspace. Exit before opening files/ports.


async function callChannelEffect(
  name: "channel_send" | "channel_reply",
  args: Record<string, unknown>
): Promise<Record<string, unknown>> {
  if (!CHANNEL_EFFECT_SOCKET.startsWith("/")) {
    throw new Error("Company Scribe channel effect socket is unavailable");
  }
  return await new Promise<Record<string, unknown>>((accept, reject) => {
    const identifier = randomUUID();
    const socket = createConnection(CHANNEL_EFFECT_SOCKET);
    let buffered = "";
    let settled = false;
    const timer = setTimeout(() => {
      socket.destroy();
      reject(new Error("Company Scribe channel authorization timed out"));
    }, 5000);
    const finish = (error?: Error, value?: Record<string, unknown>) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.destroy();
      if (error) reject(error);
      else if (value === undefined) reject(new Error("Company Scribe channel effect returned no result"));
      else accept(value);
    };
    socket.on("connect", () => {
      socket.write(JSON.stringify({
        jsonrpc: "2.0",
        id: identifier,
        method: "tools/call",
        params: { name, arguments: args },
      }) + "\n");
    });
    socket.on("data", (chunk) => {
      buffered += chunk.toString("utf8");
      if (Buffer.byteLength(buffered, "utf8") > 1024 * 1024) {
        finish(new Error("Company Scribe channel effect response exceeded 1 MiB"));
        return;
      }
      const newline = buffered.indexOf("\n");
      if (newline < 0) return;
      try {
        const response = JSON.parse(buffered.slice(0, newline)) as {
          id?: string;
          result?: {
            isError?: boolean;
            content?: Array<{ type?: string; text?: string }>;
          };
        };
        const text = response.result?.content?.[0]?.text;
        if (
          response.id !== identifier ||
          response.result?.isError ||
          typeof text !== "string"
        ) {
          finish(new Error("Company Scribe channel effect was refused"));
          return;
        }
        const value = JSON.parse(text) as unknown;
        if (value === null || typeof value !== "object" || Array.isArray(value)) {
          finish(new Error("Company Scribe channel effect result was malformed"));
          return;
        }
        finish(undefined, value as Record<string, unknown>);
      } catch {
        finish(new Error("Company Scribe channel effect response was malformed"));
      }
    });
    socket.on("error", (error) => finish(error));
    socket.on("end", () => finish(
      new Error("Company Scribe channel effect closed without a receipt")
    ));
  });
}

// --- OIL-012: Channel Send Primitive v1 (Scribe = fleet comms hub, unrestricted
// across roles — the 2026-05-25 Q3 audit-only restriction was PURGED 2026-07-02) --
const VALID_ROLES_SCRIBE = new Set([
  "ccto", "ccpo", "ccro", "ccfo", "ccmo", "cclo", "ccgo", "ccco",
  "ccde", "cos", "scribe", "utility",
]);
const VALID_EVENT_TYPES_SCRIBE = new Set([
  "agent_direct", "task_handoff", "audit_request", "reply_classification",
]);
interface ScribeSendArgs {
  effect_id: string;
  target_role: string;
  content: string;
  event_type?: string;
  meta?: Record<string, unknown>;
}

async function handleScribeSend(
  args: ScribeSendArgs
): Promise<{ success: true; send_id: string } | { success: false; error: string }> {
  const {
    effect_id,
    target_role,
    content,
    event_type = "audit_request",
    meta = {},
  } = args;

  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(effect_id || "")) {
    return { success: false, error: "A stable effect_id is required" };
  }

  // Q4: enum validation
  if (!VALID_EVENT_TYPES_SCRIBE.has(event_type)) {
    return {
      success: false,
      error: `Unknown event_type "${event_type}". Valid: ${[...VALID_EVENT_TYPES_SCRIBE].join(", ")}`,
    };
  }

  // Scribe is the fleet COMMS HUB — unrestricted across VALID_ROLES_SCRIBE
  // (founder-ratified 2026-07-02, "everything goes through the Scribe"). The
  // old Q3 audit-only restriction is PURGED: it forbade Scribe from relaying
  // dispositions/handoffs BACK to Mise exec lanes, which silently forced
  // agents to park founder-asks in-window and idle. Any VALID_EVENT_TYPES_SCRIBE
  // to any VALID_ROLES_SCRIBE is allowed; the Workloop venture wall is enforced
  // on the wl-* SENDER side (relay daemon), not here.

  // Target role validation
  if (!VALID_ROLES_SCRIBE.has(target_role)) {
    return {
      success: false,
      error: `Unknown target_role "${target_role}". Valid: ${[...VALID_ROLES_SCRIBE].join(", ")}`,
    };
  }

  try {
    const result = await callChannelEffect("channel_send", {
      effect_id,
      target_role,
      content,
      event_type,
      meta,
    });
    if (result.state !== "sent" || typeof result.send_id !== "string") {
      throw new Error("broker did not return a durable send receipt");
    }
    console.error(
      `[scribe-channel] SEND ${result.send_id}: scribe→${target_role} ` +
      `[${event_type}] ${content.substring(0, 60)}`
    );
    return { success: true, send_id: result.send_id };
  } catch (error) {
    return {
      success: false,
      error: `SEND FAILED — broker did not durably deliver to queue_${target_role}: ${error}`,
    };
  }
}

// --- Durable delivery authority + in-process pending projection -------------
const deliveryStore = new DurableDeliveryStore(DELIVERY_JOURNAL);
const pendingEvents = new Map<
  string,
  { event_type: string; content: string; timestamp: string }
>();
for (const snapshot of deliveryStore.unacknowledged()) {
  pendingEvents.set(snapshot.event.event_id, {
    event_type: snapshot.event.event_type,
    content: snapshot.event.content,
    timestamp: snapshot.event.timestamp,
  });
}

function refreshPendingProjection(): void {
  deliveryStore.refreshFromDisk();
  pendingEvents.clear();
  for (const snapshot of deliveryStore.unacknowledged()) {
    pendingEvents.set(snapshot.event.event_id, {
      event_type: snapshot.event.event_type,
      content: snapshot.event.content,
      timestamp: snapshot.event.timestamp,
    });
  }
}

cleanupDeadCursors(DELIVERY_STATE_DIR, "scribe-local", process.pid);
cleanupDeadCursors(DELIVERY_STATE_DIR, "scribe-shared", process.pid);

// Prevent duplicate notifications when the same stable event appears in more
// than one source or is encountered again after startup rehydration.
const notifiedThisProcess = new Set<string>();
const deliveryInFlight = new Map<string, Promise<string>>();

// --- Metrics counters (cumulative since process start) ----------------------
const metricsServerStartTime = Date.now();
let metricsEventsReceived = pendingEvents.size; // rehydrated + newly admitted
let metricsAcksAccepted = 0;       // new durable acknowledged transitions
let metricsAcksRejected = 0;       // total rejected ACKs (verdict mismatch)
let metricsAcksUntracked = 0;      // total ACKs for unknown durable event IDs

// --- Required verdict patterns per event type -------------------------------
const VERDICT_PATTERNS: Record<string, RegExp[]> = {
  brain_file_created: [
    /WIRING COMPLETE/i,
    /WIRING INCOMPLETE/i,
    /CONFLICT DETECTED/i,
  ],
  deploy_completed: [/DOC SYNC CLEAN/i, /DRIFT FOUND/i],
  canon_changed: [
    /CANON CHANGE CLEAN/i,
    /AUTHORITY CONFLICT/i,
    /DOWNSTREAM UPDATES NEEDED/i,
  ],
  tandem_board_updated: [
    /V-LOOP STEP VALID/i,
    /V-LOOP VIOLATION/i,
    /TANDEM AUDIT CLEAN/i,
  ],
  verification_result: [
    /ALL FIELDS MATCH/i,
    /MISMATCH.*FOUND/i,
    /VERIFICATION LOGGED/i,
  ],
  // slack_message, message, handoff_created, browser_*, deploy_detected,
  // verification_incomplete — no verdict required (informational)
};

function hasValidVerdict(event_type: string, text: string): boolean {
  const patterns = VERDICT_PATTERNS[event_type];
  if (!patterns) return true;
  return patterns.some((p) => p.test(text));
}

interface AckOutcome {
  ok: boolean;
  status: number;
  kind:
    | "acknowledged"
    | "duplicate"
    | "conflict"
    | "not_delivered"
    | "unknown"
    | "invalid_request"
    | "invalid_verdict"
    | "persistence_failed";
  message: string;
}

async function acknowledgeEvent(
  effectId: unknown,
  eventId: unknown,
  text: unknown
): Promise<AckOutcome> {
  if (
    typeof effectId !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(effectId) ||
    typeof eventId !== "string" ||
    eventId.length === 0 ||
    typeof text !== "string" ||
    text.length === 0
  ) {
    metricsAcksRejected++;
    return {
      ok: false,
      status: 400,
      kind: "invalid_request",
      message: "effect_id, event_id, and text must be valid non-empty strings",
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
      message: `Delivery projection could not be refreshed: ${error}`,
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
      message: `Unknown event_id: ${eventId}`,
    };
  }
  const wasAcknowledged = durable.state === "acknowledged";

  // An acknowledged local projection is only validation. The broker still
  // owns exact replay/conflict classification and every durable mutation.
  if (durable.state === "acknowledged") {
    if (durable.verdict !== text) {
      metricsAcksRejected++;
      return {
        ok: false,
        status: 409,
        kind: "conflict",
        message: `Conflicting acknowledgment for ${eventId}`,
      };
    }
  }

  if (durable.state === "pending") {
    metricsAcksRejected++;
    return {
      ok: false,
      status: 409,
      kind: "not_delivered",
      message: `Event ${eventId} has not reached delivered state`,
    };
  }

  if (durable.state !== "acknowledged" && !hasValidVerdict(durable.event.event_type, text)) {
    metricsAcksRejected++;
    const patterns = VERDICT_PATTERNS[durable.event.event_type];
    const required = patterns
      ? patterns.map((pattern) => pattern.source).join(" OR ")
      : "none";
    return {
      ok: false,
      status: 422,
      kind: "invalid_verdict",
      message:
        `Event ${eventId} (${durable.event.event_type}) requires verdict: ` +
        `${required}. Run the full checklist, then reply again.`,
    };
  }

  let kind: "acknowledged" | "duplicate";
  try {
    const result = await callChannelEffect("channel_reply", {
      effect_id: effectId,
      event_id: eventId,
      text,
    });
    if (result.kind !== "acknowledged" && result.kind !== "duplicate") {
      throw new Error("broker returned an invalid acknowledgment receipt");
    }
    kind = result.kind;
    // Reload only after the broker's fsync/read-back receipt. The in-memory
    // projection can never get ahead of canonical durable state.
    refreshPendingProjection();
    const persisted = deliveryStore.get(durable.event.event_id);
    if (
      persisted?.state !== "acknowledged" ||
      persisted.verdict !== text
    ) {
      throw new Error("broker receipt is absent from the durable journal");
    }
  } catch (err) {
    try {
      // A disconnected response may still follow a committed broker effect.
      // Reconcile visibility, but remain loud so the caller retries the exact
      // same effect_id and receives the durable replay receipt.
      refreshPendingProjection();
    } catch {
      // Preserve the original broker failure below.
    }
    metricsAcksRejected++;
    return {
      ok: false,
      status: 503,
      kind: "persistence_failed",
      message: `Acknowledgment was not persisted: ${err}`,
    };
  }

  // The durable acknowledged transition is already fsync'd at this point.
  pendingEvents.delete(durable.event.event_id);
  if (!wasAcknowledged) metricsAcksAccepted++;
  console.error(`[scribe-channel] VERIFIED ${eventId}: ${text.substring(0, 80)}`);
  return {
    ok: true,
    status: 200,
    kind,
    message: kind === "duplicate"
      ? "acknowledged (idempotent replay)"
      : "acknowledged",
  };
}

// --- Push event to Claude through the durable lifecycle ---------------------
async function pushEvent(
  event_type: string,
  content: string,
  meta: Record<string, string> = {}
): Promise<string> {
  const event = normalizeInboundEvent(event_type, content, meta);
  // This synchronous journal append is the admission point. Nothing can reach
  // the notification transport before the pending row has an fsync'd receipt.
  const admitted = deliveryStore.ensurePending(event);
  const storedEvent = admitted.snapshot.event;
  const eventId = storedEvent.event_id;
  if (admitted.created) metricsEventsReceived++;

  if (admitted.snapshot.state === "acknowledged") {
    pendingEvents.delete(eventId);
    return eventId;
  }

  pendingEvents.set(eventId, {
    event_type: storedEvent.event_type,
    content: storedEvent.content,
    timestamp: storedEvent.timestamp,
  });

  if (notifiedThisProcess.has(eventId)) return eventId;
  const active = deliveryInFlight.get(eventId);
  if (active !== undefined) return active;

  const attempt = (async (): Promise<string> => {
    try {
      await deliverWithJournal(deliveryStore, storedEvent, async (durableEvent) => {
        await mcp.notification({
          method: "notifications/claude/channel",
          params: {
            content: durableEvent.content,
            meta: durableEvent.meta,
          },
        });
      });
      notifiedThisProcess.add(eventId);
      console.error(
        `[scribe-channel] Event ${eventId}: ${storedEvent.event_type} — ${storedEvent.content.substring(0, 100)}`
      );
      return eventId;
    } catch (err) {
      // A rejected notification is not delivery. The journal stays pending
      // (or at its prior delivered state), and the source cursor is held so
      // the exact same stable event_id is retried.
      console.error(`[scribe-channel] Notification failed for ${eventId}: ${err}`);
      throw err;
    } finally {
      deliveryInFlight.delete(eventId);
    }
  })();
  deliveryInFlight.set(eventId, attempt);
  return attempt;
}

// --- Non-destructive source queue drains -----------------------------------
let localDrainInFlight: Promise<DrainResult> | null = null;
let slackDrainInFlight: Promise<DrainResult> | null = null;

function drainLocalQueue(): Promise<DrainResult> {
  if (localDrainInFlight !== null) return localDrainInFlight;
  const drain = drainDurableQueue(
    QUEUE_FILE,
    LOCAL_CURSOR,
    process.pid,
    "scribe-local",
    async (eventType, eventContent, eventMeta) => {
      await pushEvent(eventType || "message", eventContent, eventMeta);
    }
  ).finally(() => {
    localDrainInFlight = null;
  });
  localDrainInFlight = drain;
  return drain;
}

function drainSlackQueues(): Promise<DrainResult> {
  if (slackDrainInFlight !== null) return slackDrainInFlight;
  const drain = drainDurableQueue(
    SLACK_ROLE_QUEUE,
    SLACK_CURSOR,
    process.pid,
    "scribe-shared",
    async (eventType, eventContent, eventMeta) => {
      await pushEvent(eventType || "slack_message", eventContent, eventMeta);
    }
  ).finally(() => {
    slackDrainInFlight = null;
  });
  slackDrainInFlight = drain;
  return drain;
}

async function retryUnacknowledged(): Promise<number> {
  let accepted = 0;
  for (const snapshot of deliveryStore.unacknowledged()) {
    try {
      await pushEvent(
        snapshot.event.event_type,
        snapshot.event.content,
        snapshot.event.meta
      );
      accepted++;
    } catch {
      // Each failure remains durable and retryable. Continue so one blocked
      // transport event does not suppress independent rehydrated events.
    }
  }
  return accepted;
}

// --- MCP Server -------------------------------------------------------------
const mcp = new Server(
  { name: "scribe-channel", version: "3.2.0" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
      tools: {},
    },
    instructions: `You are the Scribe — Keeper of the Mise Bible. Events from scribe-channel are institutional signals. You MUST follow the mechanical checklist for each event type. No shortcuts. No skipping steps.

HARD CONSTRAINT: You NEVER make code changes from channel events. You record, verify, and flag.

CRITICAL: The reply tool VALIDATES your verdict. If your acknowledgment does not contain the required verdict phrase, the server REJECTS it. Required verdicts:
  - brain_file_created: "WIRING COMPLETE" or "WIRING INCOMPLETE" or "CONFLICT DETECTED"
  - deploy_completed: "DOC SYNC CLEAN" or "DRIFT FOUND"
  - canon_changed: "CANON CHANGE CLEAN" or "AUTHORITY CONFLICT" or "DOWNSTREAM UPDATES NEEDED"
  - tandem_board_updated: "V-LOOP STEP VALID" or "V-LOOP VIOLATION" or "TANDEM AUDIT CLEAN"
  - slack_message / message: no specific verdict required

Your verdict is permanently logged to the append-only delivery journal.

═══════════════════════════════════════════════════════════
EVENT: brain_file_created
═══════════════════════════════════════════════════════════

STEP 1 — EXTRACT: Page the brain file through workspace_read until total_lines is exhausted. Output:
  - Title / Status / Purpose / Core assertion

STEP 2 — COMPARE (Wiring Checklist): Check each, mark WIRED / UNWIRED / N/A:
  [ ] CLAUDE.md "Institutional Rules" — rule all agents must know?
  [ ] Canonical workspace memory projection — affects cross-session behavior? (host memory is unavailable)
  [ ] CC Exec registries — role-specific?
  [ ] MISE_MASTER_SPEC.md — company fact?
  [ ] Supersedes existing? — conflicts with other brain files?

STEP 3 — VERDICT: WIRING COMPLETE / WIRING INCOMPLETE / CONFLICT DETECTED

STEP 4 — ACKNOWLEDGE: reply tool with event_id + verdict.

═══════════════════════════════════════════════════════════
EVENT: deploy_completed
═══════════════════════════════════════════════════════════

STEP 1 — EXTRACT: use the event-provided deploy revision and externally delivered Git-history inventory; report GIT HISTORY INVENTORY UNAVAILABLE if absent
STEP 2 — COMPARE: MISE_MASTER_SPEC / MEMORY.md / brain files / CLAUDE.md
STEP 3 — VERDICT: DOC SYNC CLEAN / DRIFT FOUND
STEP 4 — ACKNOWLEDGE: reply tool with event_id + verdict.

═══════════════════════════════════════════════════════════
EVENT: canon_changed
═══════════════════════════════════════════════════════════

STEP 1 — EXTRACT: page the changed file through workspace_read and use the event-provided or externally delivered diff; report the evidence gap if absent.
STEP 2 — COMPARE: Authority hierarchy conflict? Downstream docs stale?
STEP 3 — VERDICT: CANON CHANGE CLEAN / AUTHORITY CONFLICT / DOWNSTREAM UPDATES NEEDED
STEP 4 — ACKNOWLEDGE: reply tool with event_id + verdict.

═══════════════════════════════════════════════════════════
EVENT: tandem_board_updated
═══════════════════════════════════════════════════════════

STEP 1 — EXTRACT: Read TANDEM_BOARD.md. Identify which ticket/step changed.
STEP 2 — COMPARE: V-Loop compliance check:
  [ ] Are all required V-Loop steps present for the ticket?
  [ ] Is the step sequence correct (no skipped steps)?
  [ ] Does evidence column have specifics (not "N/A" or "looks good")?
  [ ] Was CCPO verification done BEFORE CCTO declared verified?
STEP 3 — VERDICT: V-LOOP STEP VALID / V-LOOP VIOLATION / TANDEM AUDIT CLEAN
STEP 4 — ACKNOWLEDGE: reply tool with event_id + verdict.

═══════════════════════════════════════════════════════════
EVENT: slack_message
═══════════════════════════════════════════════════════════

A message from #tandem or #common-room pushed in real-time.
Attributes: slack_channel (tandem or common-room), slack_user, slack_ts.

Read the message. If it requires Scribe action (handoff, directive, brain file request, audit request), act immediately. If informational, acknowledge briefly.

ACKNOWLEDGE: reply tool with event_id + what you did or "noted."

═══════════════════════════════════════════════════════════
EVENT: verification_result
═══════════════════════════════════════════════════════════

Automated verification script results. Contains structured per-field MATCH/MISMATCH data.
This is MECHANICAL — no agent ran these checks, a Python script did.

Content is JSON with: shift, total_checks, matches, mismatches, verdict, details.

If ALL FIELDS MATCH → acknowledge with "VERIFICATION LOGGED. ALL FIELDS MATCH."
If MISMATCHES FOUND → flag to founder. List every mismatch. This is a data integrity issue.

ACKNOWLEDGE: reply tool with "ALL FIELDS MATCH" or "MISMATCH(ES) FOUND" + details.

═══════════════════════════════════════════════════════════
EVENT: browser_fresh_violation / browser_closed / deploy_detected / verification_incomplete
═══════════════════════════════════════════════════════════

Real-time events from verification agent hooks (any agent, not just CCPO).
These are the Scribe's eyes into whether verification agents are following protocol.

- browser_fresh_violation: Agent took a snapshot without closing browser first. Flag it.
- browser_closed: Agent closed browser (good — fresh browser marker set). Note it.
- deploy_detected: Agent's window detected a deploy. Verification should follow.
- verification_incomplete: Agent declared VERIFIED/CLEAN without EXTRACTION + COMPARISON tables. Flag it.

For violations (browser_fresh_violation, verification_incomplete): log and escalate to founder.
For informational (browser_closed, deploy_detected): acknowledge briefly.

═══════════════════════════════════════════════════════════
EVENT: message
═══════════════════════════════════════════════════════════

Direct message from founder or agent. Route to appropriate Scribe operation.
ACKNOWLEDGE: reply tool confirming what you did.`,
  }
);

// --- Tools: reply + send (OIL-012) ------------------------------------------
mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "reply",
      description:
        "Acknowledge a scribe-channel event. MUST include the required verdict phrase or the server rejects it.",
      inputSchema: {
        type: "object" as const,
        properties: {
          event_id: {
            type: "string",
            description:
              "The channel event_id, or the coworker send_id on that row.",
          },
          text: {
            type: "string",
            description: "Your verdict and acknowledgment.",
          },
          effect_id: {
            type: "string",
            description: "Stable caller-generated idempotency key for this exact reply.",
          },
        },
        required: ["effect_id", "event_id", "text"],
      },
    },
    {
      name: "send",
      description: "Send a direct message to a peer agent's channel queue. Scribe is the fleet comms hub (founder-ratified 2026-07-02) — relays dispositions, handoffs, and audits to any lane.",
      inputSchema: {
        type: "object" as const,
        properties: {
          target_role: {
            type: "string",
            enum: ["ccto", "ccpo", "ccro", "ccfo", "ccmo", "cclo", "ccgo", "ccco",
                   "ccde", "cos", "scribe", "utility"],
            description: "Receiving agent role",
          },
          content: {
            type: "string",
            description: "Message body (FROM/TO/SUBJECT envelope for handoffs)",
          },
          event_type: {
            type: "string",
            enum: ["agent_direct", "task_handoff", "audit_request", "reply_classification"],
            description: "Default: audit_request",
          },
          meta: {
            type: "object" as const,
            description: "Optional structured payload (expected_artifact opens a stall-tracked handoff row)",
            additionalProperties: true,
          },
          effect_id: {
            type: "string",
            description: "Stable caller-generated idempotency key for this exact send.",
          },
        },
        required: ["effect_id", "target_role", "content"],
      },
    },
  ],
}));

mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name === "send") {
    const args = req.params.arguments as unknown as ScribeSendArgs;
    const result = await handleScribeSend(args);
    if (result.success) {
      return { content: [{ type: "text", text: `sent: ${result.send_id}` }] };
    }
    return {
      content: [{ type: "text", text: `error: ${result.error}` }],
      isError: true,
    };
  }
  if (req.params.name === "reply") {
    const { effect_id, event_id, text } = req.params.arguments as {
      effect_id: string;
      event_id: string;
      text: string;
    };
    const outcome = await acknowledgeEvent(effect_id, event_id, text);
    return {
      content: [{ type: "text", text: outcome.message }],
      ...(outcome.ok ? {} : { isError: true }),
    };
  }
  throw new Error(`unknown tool: ${req.params.name}`);
});

// --- Connect to Claude Code -------------------------------------------------
// Parent identity was checked before opening delivery state.
await mcp.connect(new StdioServerTransport());
await mcp.waitUntilInitialized();

// --- HTTP listener ----------------------------------------------------------
if (!admittedRuntime) Bun.serve({
  port: SCRIBE_PORT,
  hostname: "127.0.0.1",
  idleTimeout: 0,

  async fetch(req) {
    const url = new URL(req.url);

    // GET /health
    if (req.method === "GET" && url.pathname === "/health") {
      return new Response(
        JSON.stringify({
          status: "ok",
          server: "scribe-channel",
          version: "3.2.0",
          port: SCRIBE_PORT,
          pending_events: pendingEvents.size,
          queue_exists: existsSync(QUEUE_FILE),
          slack_source: "shared_poller",
          slack_role_queue: existsSync(SLACK_ROLE_QUEUE),
          delivery_journal: DELIVERY_JOURNAL,
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    }

    // GET /metrics — comprehensive counters since process start
    if (req.method === "GET" && url.pathname === "/metrics") {
      const uptimeSecs = Math.floor((Date.now() - metricsServerStartTime) / 1000);
      const auditLogLines = existsSync(AUDIT_LOG)
        ? (await Bun.file(AUDIT_LOG).text()).trim().split("\n").filter((l) => l.length > 0).length
        : 0;
      return new Response(
        JSON.stringify({
          server: "scribe-channel",
          version: "3.2.0",
          uptime_seconds: uptimeSecs,
          uptime_human: `${Math.floor(uptimeSecs / 3600)}h ${Math.floor((uptimeSecs % 3600) / 60)}m ${uptimeSecs % 60}s`,
          events_received_total: metricsEventsReceived,
          acks_accepted_total: metricsAcksAccepted,
          acks_rejected_total: metricsAcksRejected,
          acks_untracked_total: metricsAcksUntracked,
          pending_events_current: pendingEvents.size,
          audit_log_size: auditLogLines,
          durable_acknowledgments:
            deliveryStore.acknowledgedAuditEntries().length,
          delivery_rate_pct: metricsEventsReceived > 0
            ? Math.round((metricsAcksAccepted / metricsEventsReceived) * 100)
            : null,
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    }

    // GET /audit
    if (req.method === "GET" && url.pathname === "/audit") {
      const legacyEntries = existsSync(AUDIT_LOG)
        ? (await Bun.file(AUDIT_LOG).text())
            .trim()
            .split("\n")
            .filter((line) => line.length > 0)
            .map((line) => {
              try {
                return JSON.parse(line);
              } catch {
                return null;
              }
            })
            .filter(Boolean)
        : [];
      const entries = [
        ...legacyEntries,
        ...deliveryStore.acknowledgedAuditEntries(),
      ];
      return new Response(JSON.stringify({ entries }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    // POST /send — OIL-012 send primitive (Scribe = comms hub; Q3 purged 2026-07-02)
    if (req.method === "POST" && url.pathname === "/send") {
      const body = (await req.json()) as ScribeSendArgs;
      const result = await handleScribeSend(body);
      if (result.success) {
        return new Response(
          JSON.stringify({ status: "ok", send_id: result.send_id }),
          { headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response(
        JSON.stringify({ status: "error", error: result.error }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    // POST /reply — Called by the Python relay in fork sessions where
    // --dangerously-load-development-channels is unavailable. This is the same
    // durable acknowledgment state machine as the MCP reply tool.
    if (req.method === "POST" && url.pathname === "/reply") {
      const decoded = await req.json() as unknown;
      const body =
        decoded !== null && typeof decoded === "object" && !Array.isArray(decoded)
          ? decoded as Record<string, unknown>
          : {};
      const outcome = await acknowledgeEvent(
        body.effect_id,
        body.event_id,
        body.text
      );
      return new Response(
        JSON.stringify({
          status: outcome.ok ? "ok" : "rejected",
          kind: outcome.kind,
          message: outcome.message,
        }),
        {
          status: outcome.status,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    // POST /drain
    if (req.method === "POST" && url.pathname === "/drain") {
      const result = await drainLocalQueue();
      return new Response(
        JSON.stringify({
          status: result.held ? "held" : "ok",
          drained: result.processed,
          held: result.held,
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    }

    // POST /: direct event push
    if (req.method === "POST") {
      const contentType = req.headers.get("content-type") || "";
      let event_type = "message";
      let content = "";
      let meta: Record<string, string> = {};

      if (contentType.includes("application/json")) {
        const decoded = await req.json() as unknown;
        if (
          decoded === null ||
          typeof decoded !== "object" ||
          Array.isArray(decoded)
        ) {
          return new Response(
            JSON.stringify({
              status: "error",
              message: "JSON body must be an object",
            }),
            { status: 400, headers: { "Content-Type": "application/json" } }
          );
        }
        const body = decoded as Record<string, unknown>;
        if (
          (body.event_type !== undefined &&
            typeof body.event_type !== "string") ||
          (body.content !== undefined && typeof body.content !== "string") ||
          (body.meta !== undefined &&
            (body.meta === null ||
              typeof body.meta !== "object" ||
              Array.isArray(body.meta) ||
              Object.values(body.meta).some(
                (value) => typeof value !== "string"
              )))
        ) {
          return new Response(
            JSON.stringify({
              status: "error",
              message:
                "event_type and content must be strings; meta values must be strings",
            }),
            { status: 400, headers: { "Content-Type": "application/json" } }
          );
        }
        event_type =
          (body.event_type as string | undefined) || "message";
        content = (body.content as string | undefined) || "";
        meta = (body.meta as Record<string, string> | undefined) || {};
      } else {
        content = await req.text();
        event_type = url.searchParams.get("event_type") || "message";
      }

      // Direct input is first fsync'd into the immutable source queue. Only
      // then may the normal cursor drain attempt notification delivery.
      // Each accepted HTTP request is one logical event even when two callers
      // send byte-identical bodies. Queue retries retain this minted ID.
      if (
        typeof meta.event_id !== "string" ||
        meta.event_id.length === 0
      ) {
        meta = { ...meta, event_id: `scribe-http-${randomUUID()}` };
      }
      const event = normalizeInboundEvent(event_type, content, meta);
      const receipt = appendInboundEvent(QUEUE_FILE, event);
      if (!receipt.ok) {
        return new Response(
          JSON.stringify({
            status: "error",
            message: `Event was not durably queued: ${receipt.reason}`,
          }),
          { status: 503, headers: { "Content-Type": "application/json" } }
        );
      }

      const result = await drainLocalQueue();
      const durable = deliveryStore.get(event.event_id);
      const delivered =
        durable?.state === "delivered" || durable?.state === "acknowledged";
      return new Response(
        JSON.stringify({
          status: delivered ? "ok" : "queued",
          event_id: event.event_id,
          delivery_state: durable?.state || "source_queued",
          drain_held: result.held,
        }),
        {
          status: delivered ? 200 : 202,
          headers: { "Content-Type": "application/json" },
        }
      );
    }

    return new Response("Method not allowed", { status: 405 });
  },
});

// --- Startup: rehydrate journal, then advance immutable source cursors -------
const rehydrated = await retryUnacknowledged();
if (rehydrated > 0) {
  console.error(
    `[scribe-channel] Retried ${rehydrated} unacknowledged journal event(s)`
  );
}

const backlog = await drainLocalQueue();
if (backlog.processed > 0) {
  console.error(
    `[scribe-channel] Drained ${backlog.processed} queued event(s) from backlog`
  );
}
if (backlog.held) {
  console.error("[scribe-channel] Local source cursor held for delivery retry");
}

// --- Periodic: own queue drain (every 2s) + shared Slack queue drain (every 3s) ---
setInterval(async () => {
  if (existsSync(QUEUE_FILE)) {
    const result = await drainLocalQueue();
    if (result.processed > 0) {
      console.error(`[scribe-channel] Periodic drain: ${result.processed}`);
    }
  }
}, 2000);

// Drain shared Slack queues (written by channels/shared/slack_poller.ts)
setInterval(async () => {
  const result = await drainSlackQueues();
  if (result.processed > 0) {
    console.error(
      `[scribe-channel] Slack queue drain: ${result.processed} message(s)`
    );
  }
}, 3000);

// Drain any backlog in shared queues on startup
const slackBacklog = await drainSlackQueues();
if (slackBacklog.processed > 0) {
  console.error(
    `[scribe-channel] Drained ${slackBacklog.processed} Slack message(s) from backlog`
  );
}
if (slackBacklog.held) {
  console.error("[scribe-channel] Shared source cursor held for delivery retry");
}

console.error(
  admittedRuntime
    ? "[scribe-channel] v3.2.0 — admitted MCP-only runtime; HTTP disabled"
    : `[scribe-channel] v3.2.0 — Listening on http://127.0.0.1:${SCRIBE_PORT}`
);
console.error(`[scribe-channel] Slack source: shared poller queues at ${SHARED_DIR}`);
console.error(`[scribe-channel] Queue: ${QUEUE_FILE}`);
console.error(`[scribe-channel] Delivery journal: ${DELIVERY_JOURNAL}`);
console.error(`[scribe-channel] Legacy audit: ${AUDIT_LOG}`);
console.error(`[scribe-channel] NOTE: Shared Slack poller must be running: nohup bun run channels/shared/slack_poller.ts &`);
