#!/usr/bin/env bun
// SPDX-License-Identifier: MPL-2.0
/**
 * Generic Agent Channel — One codebase, any role.
 *
 * Parameterized by environment variables:
 *   AGENT_ROLE  — agent role (ccto, ccpo, utility, etc.)
 *   AGENT_PORT  — explicit configured port to listen on
 *
 * Reads from two queue files written by the shared Slack poller:
 *   channels/shared/queue_{role}.jsonl   — messages routed to this role
 *   channels/shared/queue_broadcast.jsonl — messages for everyone
 *
 * Also accepts direct POST events (from hooks, scripts, other agents).
 *
 * Usage:
 *   AGENT_ROLE=ccto AGENT_PORT=8790 claude --dangerously-load-development-channels server:ccto-channel
 *
 * Port allocation:
 *   8789 — Scribe (dedicated, has extra features)
 *   8790 — CCTO
 *   8791 — CCPO
 *   8792 — Utility
 *   8793 — CCRO
 *   8794 — CCFO
 *   8795 — CCMO
 *   8796 — CCLO
 *   8797 — CCGO
 *   8798 — CCCO
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { existsSync, renameSync, unlinkSync, appendFileSync } from "node:fs";
import { resolve, isAbsolute } from "node:path";
import { mkdirSync } from "node:fs";
import { appendLineWithReceipt } from "../shared/send_receipt.ts";
import { isAdvisorAsk } from "../shared/advisor_ask_detect.ts";
import {
  cleanupDeadCursors,
  drainQueue,
} from "../shared/durable_inbox.ts";
import {
  handleArbiterAsk as _handleArbiterAsk,
  type ArbiterAskArgs,
} from "./arbiter_ask.ts";
import {
  handleEmitFodl as _handleEmitFodl,
  type EmitFodlArgs,
} from "./emit_fodl.ts";
import { readFileSync, writeFileSync } from "node:fs";

const AGENT_ROLE = process.env.AGENT_ROLE || "";
const AGENT_PORT = Number(process.env.AGENT_PORT);
const COMPANY_ROLES = new Set("ccto ccpo ccro ccfo ccmo cclo ccgo ccco ccde cos utility".split(" "));
function requiredPath(name: string): string {
  const value = process.env[name] || "";
  if (!isAbsolute(value) || resolve(value) !== value) throw new Error(`${name} requires an explicit canonical absolute path`);
  return value;
}
if (!COMPANY_ROLES.has(AGENT_ROLE) || !Number.isInteger(AGENT_PORT) || AGENT_PORT < 1 || AGENT_PORT > 65535) {
  console.error("REFUSED: an explicit non-Scribe Company role and channel port are required");
  process.exit(78);
}
const PROJECT_ROOT = requiredPath("CC_SUITE_WORKSPACE");
const PACKAGE_ROOT = requiredPath("CC_SUITE_PACKAGE_ROOT");
const SHARED_DIR = requiredPath("CC_SUITE_CHANNEL_SHARED_DIR");
const CHANNEL_STATE_DIR = requiredPath("CC_SUITE_CHANNEL_STATE_DIR");
const PYTHON = requiredPath("CC_SUITE_PYTHON");
const STATE_ROOT = requiredPath("CC_SUITE_STATE_ROOT");
if (CHANNEL_STATE_DIR !== resolve(STATE_ROOT, `company-${AGENT_ROLE}`, "channel")) {
  console.error("REFUSED: channel state must belong to the requested role and configured state root");
  process.exit(78);
}
const authorization = Bun.spawnSync([PYTHON, resolve(PACKAGE_ROOT, "scripts/portable_host.py"), AGENT_ROLE, "channel-push"], { stdout: "pipe", stderr: "pipe" });
if (authorization.exitCode !== 0) {
  console.error("REFUSED: deployment authorization for the channel is absent");
  process.exit(78);
}
mkdirSync(CHANNEL_STATE_DIR, {recursive: true, mode: 0o700});
const SEND_SHARED_DIR = SHARED_DIR;
// The shared poller fans out broadcast messages to each consumer's queue at
// write time. This agent channel only needs to drain ONE file — its role queue.
// No separate broadcast queue, no fan-out race conditions.
const ROLE_QUEUE = resolve(SEND_SHARED_DIR, `queue_${AGENT_ROLE}.jsonl`);
const DIRECT_QUEUE = resolve(CHANNEL_STATE_DIR, `queue_${AGENT_ROLE}_direct.jsonl`);

// --- OIL-012: Channel Send Primitive v1 (MacBook-only, ratified 2026-05-25) --
const AUDIT_SENDS = resolve(SEND_SHARED_DIR, "audit_sends.jsonl");
// Ratified target role enum (all active MacBook channels)
const VALID_ROLES = new Set([
  "ccto", "ccpo", "ccro", "ccfo", "ccmo", "cclo", "ccgo", "ccco",
  "ccde", "cos", "scribe", "utility",
]);

// MISEFLEET tenant binding (S1/D5): a mise-* channel is TENANT-PINNED via the
// spawn-time MISE_TENANT env (same trust model as AGENT_ROLE — Q2). Senders
// stamp it into every envelope (not caller-overridable); receivers refuse a
// tenant-tagged envelope whose tenant != their binding (the D4 invariant at
// the comms seam: a tenant-a receiver never ingests a tenant-b-tagged event).
const IS_MISE_FLEET = AGENT_ROLE.startsWith("mise-");
const MISE_TENANT = process.env.MISE_TENANT || "";
if (IS_MISE_FLEET && !MISE_TENANT) {
  console.error(`[${AGENT_ROLE}-channel] FATAL: mise-* channel requires MISE_TENANT (tenant-pinned binding) — refusing to start`);
  process.exit(78);
}
// Ratified event-type enum (Q4 ratification 2026-05-25; new types via canon only)
// FDRL Component 3 (canon 2026-05-26) adds "arbiter_ask" for CoS arbitration.
const VALID_EVENT_TYPES = new Set([
  "agent_direct", "task_handoff", "audit_request", "reply_classification",
  "arbiter_ask",
]);
// F-comms-1: a per-restart nonce so send_id is globally UNIQUE across channel restarts. The
// per-process counter alone RESETS to 1 on restart, so after a relaunch (= channel restart) a
// brand-new handoff could reuse a pre-restart send_id -> collide on handoff_id=send-{send_id} ->
// get silently dropped as a dup by G-c's idempotency. pid+startup-time entropy differs per restart;
// the counter keeps per-process monotonicity. Format: <role>-send-<nonce>-<0001>.
const SEND_NONCE = process.pid.toString(36) + Date.now().toString(36);
let sendIdCounter = 1;

interface SendArgs {
  target_role: string;
  content: string;
  event_type?: string;
  meta?: Record<string, unknown>;
}

function handleSend(args: SendArgs): { success: true; send_id: string } | { success: false; error: string } {
  const { target_role, content, event_type = "agent_direct", meta = {} } = args;

  // Q4: enum validation — reject unknown event types
  if (!VALID_EVENT_TYPES.has(event_type)) {
    return {
      success: false,
      error: `Unknown event_type "${event_type}". Valid: ${[...VALID_EVENT_TYPES].join(", ")}`,
    };
  }

  // Scribe is the fleet COMMS HUB — unrestricted (founder-ratified 2026-07-02,
  // "everything goes through the Scribe"). The old Q3 audit-only restriction is
  // PURGED: it forbade Scribe from relaying dispositions/handoffs back to Mise
  // exec lanes, silently forcing agents to park founder-asks in-window and idle.

  // MISEFLEET (S1/D5): a mise-* sender's envelope REQUIRES the tenant field —
  // stamped from the binding env, never from the caller (the Q2 pattern).
  if (IS_MISE_FLEET) {
    (meta as Record<string, unknown>).tenant = MISE_TENANT;
  }

  // Target role validation
  if (!VALID_ROLES.has(target_role)) {
    return {
      success: false,
      error: `Unknown target_role "${target_role}". Valid: ${[...VALID_ROLES].join(", ")}`,
    };
  }

  const send_id = `${AGENT_ROLE}-send-${SEND_NONCE}-${String(sendIdCounter++).padStart(4, "0")}`;
  const sent_at = new Date().toISOString();

  // Build event in the same JSONL format used by the Slack poller (inbound wire format)
  const event = {
    event_type,
    content,
    meta: {
      ...meta,
      sender_role: AGENT_ROLE,  // always from env — not overridable by caller (Q2)
      target_role,
      sent_at,
      send_id,
    },
  };

  // Write to target's shared queue (MacBook-only v1 — local path, no cross-host).
  // OIL-211 receipt-or-loud: success ONLY on read-back-verified arrival. The
  // pre-fix appendFileSync-then-ok returned success on a write that never
  // landed in the canonical queue (the wind-down silent-void bite) — that
  // silent path must not exist.
  const targetQueue = resolve(SEND_SHARED_DIR, `queue_${target_role}.jsonl`);
  const receipt = appendLineWithReceipt(targetQueue, JSON.stringify(event));
  if (!receipt.ok) {
    return {
      success: false,
      error: `SEND FAILED — NOT delivered to queue_${target_role}: ${receipt.reason}. ` +
        `Fall back to a durable memo file (cc_execs/memos/${target_role}/) and surface this failure loudly.`,
    };
  }

  // Audit trail — content length only, no body (privacy; design memo §7)
  const auditEntry = {
    ts: sent_at,
    send_id,
    sender: AGENT_ROLE,
    target: target_role,
    event_type,
    content_length: content.length,
    // OIL-211: privacy-preserving content fingerprint (never the body) — lets
    // the delivery sweep match a send against ACK trails that carry content
    // but no send_id (the scribe audit_log after a destructive drain).
    content_sha1: new Bun.CryptoHasher("sha1").update(content).digest("hex").slice(0, 12),
  };
  try {
    appendFileSync(AUDIT_SENDS, JSON.stringify(auditEntry) + "\n");
  } catch {
    console.error(`[${AGENT_ROLE}-channel] Audit write failed for ${send_id}`);
  }

  // G-c: a task_handoff SEND opens an OWNED handoff-ledger row so the stall-backstop can bite.
  // This bun daemon CANNOT safely write the flock-guarded Python ledger directly (it would race
  // the sweep's atomic rewrite), so it appends an OPEN-REQUEST that the flock-holding sweep
  // drains into a real row (handoff_ledger.drain_open_requests, idempotent on send_id).
  //
  // B3 (CCTO amendment A3, OIL-221 north-star trial, day-one stranded-ask evidence): the
  // SAME gate now also fires for an ADVISOR ask (event_type=agent_direct, SUBJECT: ADVISOR:
  // prefix per the Plan-First advisor protocol §3) — CCDE's own branch-reachability advisor
  // ask reached CCTO's queue durably but never surfaced into attention (receipt-to-queue !=
  // receipt-to-attention); a stall-tracked row lets the EXISTING sweep escalate a stranded
  // advisor ask the same way it already escalates a stranded task_handoff.
  //
  // ENGINE-HYGIENE #7 (CoS defect dispatch, 2026-07-02): a row opens ONLY when the sender
  // DECLARES the obligation — meta.expected_artifact (non-empty string) + optional
  // meta.deadline_seconds. A bare send is a MESSAGE (delivered above), not a deadline-bound
  // obligation; the legacy every-send placeholder rows escalated routine messages to live
  // agents as "stalls" (~10 spurious arbiter-asks/hr). The Python chokepoint
  // (handoff_ledger.open_from_send) refuses undeclared/placeholder requests regardless, so
  // this gate and the drain cannot drift apart.
  const advisorAsk = isAdvisorAsk(event_type, content);
  if (event_type === "task_handoff" || advisorAsk) {
    const declaredArtifact =
      typeof meta.expected_artifact === "string" && meta.expected_artifact.trim() !== ""
        ? meta.expected_artifact.trim()
        : null;
    if (declaredArtifact !== null) {
      try {
        const action = (content.trim().split("\n")[0] || (advisorAsk ? "advisor ask" : "task handoff")).slice(0, 200);
        const openReq = {
          send_id, from_agent: AGENT_ROLE, owner_agent: target_role,
          action, opened_ts_utc: sent_at, expected_artifact: declaredArtifact,
          ...(typeof meta.deadline_seconds === "number" && meta.deadline_seconds > 0
            ? { deadline_seconds: meta.deadline_seconds }
            : {}),
        };
        appendFileSync(resolve(SEND_SHARED_DIR, "handoff_open_requests.jsonl"), JSON.stringify(openReq) + "\n");
      } catch {
        console.error(`[${AGENT_ROLE}-channel] handoff open-request write failed for ${send_id}`);
      }
    }
  }

  // PATH C1 finish: best-effort dual-write to company message plane when
  // cutover is dual_write/kernel_primary. Never fails the file-bus receipt.
  try {
    const dual = Bun.spawnSync(
      [
        PYTHON,
        resolve(PACKAGE_ROOT, "scripts/kernel_plane_dual_write_cli.py"),
        "--sender",
        AGENT_ROLE,
        "--target",
        target_role,
        "--event-type",
        event_type,
        "--send-id",
        send_id,
      ],
      {
        cwd: PROJECT_ROOT,
        stdin: new TextEncoder().encode(content),
        stdout: "pipe",
        stderr: "pipe",
        env: { ...process.env, CCSUITE_REPO_ROOT: PROJECT_ROOT, PYTHONPATH: PACKAGE_ROOT },
      },
    );
    if (dual.exitCode === 0) {
      const note = new TextDecoder().decode(dual.stdout || new Uint8Array()).trim();
      if (note) console.error(`[${AGENT_ROLE}-channel] kernel dual-write: ${note.slice(0, 200)}`);
    }
  } catch {
    // soft-fail only
  }

  console.error(`[${AGENT_ROLE}-channel] SEND ${send_id}: ${AGENT_ROLE}→${target_role} [${event_type}] ${content.substring(0, 60)}`);
  return { success: true, send_id };
}

// FDRL Component 3 wrapper — env-bound thin shim around the extracted module.
function handleArbiterAsk(args: ArbiterAskArgs) {
  return _handleArbiterAsk({ agent_role: AGENT_ROLE, send: handleSend }, args);
}

// FDRL Component 4 wrapper — binds file paths + fs side effects into emit_fodl module.
const FODL_CURRENT_MD = resolve(PROJECT_ROOT, "docs/fodl/current.md");
const FODL_AUDIT_LOG = resolve(PROJECT_ROOT, "docs/fodl/audit_log.jsonl");
function handleEmitFodl(args: EmitFodlArgs) {
  return _handleEmitFodl({
    readFile: (p) => readFileSync(p, "utf-8"),
    writeFile: (p, c) => writeFileSync(p, c),
    appendFile: (p, c) => appendFileSync(p, c),
    now: () => new Date(),
    currentMd: process.env.FODL_CURRENT_MD ? resolve(process.env.FODL_CURRENT_MD) : FODL_CURRENT_MD,
    auditLog: process.env.FODL_AUDIT_LOG ? resolve(process.env.FODL_AUDIT_LOG) : FODL_AUDIT_LOG,
  }, args);
}

let nextEventId = 1;

// --- Durable cursor-based role queue drain ----------------------------------
// Each running channel.ts instance has its own cursor file tracking how many
// lines of queue_{role}.jsonl it has processed. Reads are non-destructive —
// the queue file is NEVER renamed or deleted. Multiple instances (interactive
// sessions + agent-view forks) all get all events from their own cursor start.
const CURSOR_FILE = resolve(CHANNEL_STATE_DIR, `cursor_${AGENT_ROLE}_${process.pid}.json`);

async function drainRoleQueue(): Promise<number> {
  const { processed, held } = await drainQueue(
    ROLE_QUEUE,
    CURSOR_FILE,
    process.pid,
    AGENT_ROLE,
    async (event_type, content, meta) => { await pushEvent(event_type, content, meta); }
  );
  if (held) {
    console.error(`[${AGENT_ROLE}-channel] pushEvent failed — cursor held, event will be retried next drain cycle`);
  }
  return processed;
}

// --- Legacy consume-and-delete drain (DIRECT_QUEUE only) --------------------
// Direct messages (from hooks, scripts) are one-shot by design: single
// consumer, no durability required. The rename+delete pattern stays for this
// path only. ROLE_QUEUE no longer uses this function.
async function drainFile(filepath: string): Promise<string[]> {
  if (!existsSync(filepath)) return [];
  const processingPath = filepath + ".processing";
  try {
    renameSync(filepath, processingPath);
  } catch {
    return [];
  }
  const raw = await Bun.file(processingPath).text();
  const lines = raw.trim().split("\n").filter((l) => l.length > 0);
  try { unlinkSync(processingPath); } catch {}
  return lines;
}

async function drainAllQueues(): Promise<number> {
  let total = 0;

  // Role queue — durable cursor-based (shared Slack poller, multi-consumer safe)
  total += await drainRoleQueue();

  // Direct queue — consume-and-delete (hooks / scripts, single-consumer by design)
  for (const line of await drainFile(DIRECT_QUEUE)) {
    try {
      const event = JSON.parse(line);
      await pushEvent(event.event_type || "message", event.content || "", event.meta || {});
      total++;
    } catch {}
  }

  return total;
}

// --- Push event to Claude ---------------------------------------------------
async function pushEvent(
  event_type: string,
  content: string,
  meta: Record<string, string> = {}
): Promise<string> {
  // MISEFLEET (S1/D5): a tenant-pinned receiver REFUSES a tenant-tagged
  // envelope whose tenant != its binding — logged loud, never delivered.
  // (Untagged envelopes — CC-Suite exec senders — pass; the D4 dispatcher
  // re-validates rid at execution.) Both drain paths funnel through here.
  if (IS_MISE_FLEET && meta.tenant && meta.tenant !== MISE_TENANT) {
    console.error(
      `[${AGENT_ROLE}-channel] TENANT MISMATCH REFUSED: envelope tenant ` +
      `"${meta.tenant}" != this fleet's binding "${MISE_TENANT}" — event NOT ` +
      `delivered (Universal Tenant Isolation Invariant, comms seam)`);
    return "refused-tenant-mismatch";
  }
  const event_id = `${AGENT_ROLE}-${nextEventId++}`;
  meta.event_type = event_type;
  meta.event_id = event_id;
  meta.timestamp = new Date().toISOString();
  meta.agent_role = AGENT_ROLE;

  try {
    await mcp.notification({
      method: "notifications/claude/channel",
      params: { content, meta },
    });
  } catch (err) {
    // Expected in fork sessions where --dangerously-load-development-channels
    // was not passed at launch — the `claude/channel` experimental capability
    // is unsubscribed, so notification() throws. Logged but not fatal; the
    // UserPromptSubmit hook drain is the fork-session delivery path.
    console.error(`[${AGENT_ROLE}-channel] Push notification skipped (no dev-channels flag): ${err}`);
  }

  console.error(`[${AGENT_ROLE}-channel] Event ${event_id}: ${event_type} — ${content.substring(0, 80)}`);
  return event_id;
}

// --- MCP Server -------------------------------------------------------------
const mcp = new Server(
  { name: `${AGENT_ROLE}-channel`, version: "1.0.0" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
      tools: {},
    },
    instructions: `Events from ${AGENT_ROLE}-channel are pushed to you in real-time. Sources:
- Slack #tandem and #common-room messages (routed by the shared Slack poller based on role mentions)
- Direct messages from the founder or other agents
- Hook events (brain files, governance changes, deploy events, browser state)

For slack_message events: read the message. If it's directed at you or requires your action, act immediately. If it's informational or for another agent, acknowledge briefly with "noted" and move on.

For direct messages: read and act per your role's mandate.

IMPORTANT: You no longer need to run /loop or /check-tandem. Messages push to you automatically. Focus on your work — events arrive when they need your attention.

Acknowledge every event by calling the reply tool with the event_id.`,
  }
);

// --- Tools: reply + send (OIL-012) ------------------------------------------
mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "reply",
      description: "Acknowledge a channel event",
      inputSchema: {
        type: "object" as const,
        properties: {
          event_id: { type: "string", description: "The event_id from the channel event" },
          text: { type: "string", description: "Your acknowledgment" },
        },
        required: ["event_id", "text"],
      },
    },
    {
      name: "send",
      description: "Send a direct message to a peer agent's channel queue. Private — not relayed to Slack.",
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
            description: "Message body",
          },
          event_type: {
            type: "string",
            enum: ["agent_direct", "task_handoff", "audit_request", "reply_classification", "arbiter_ask"],
            description: "Event classification. Default: agent_direct",
          },
          meta: {
            type: "object" as const,
            description: "Optional structured payload (private — not relayed to Slack)",
            additionalProperties: true,
          },
        },
        required: ["target_role", "content"],
      },
    },
    {
      name: "arbiter_ask",
      description: "Dispatch a structured arbiter-ask to CoS-001 per FDRL Component 3 (canon 2026-05-26). Use when primary-lane + peer agent both have confidence < T2 OR when the Inter-Agent Routing Table dictates CoS arbitration. CoS responds with RULING / REDIRECT / NEEDS-DATA / FODL-ESCALATE within 60 min active-hours. Returns send_id as the arbiter-ask tracking ID.",
      inputSchema: {
        type: "object" as const,
        properties: {
          tier: { type: "string", enum: ["S", "A", "B", "C"], description: "Engineering tier (S/A/B/C)" },
          edg: { type: "number", enum: [0, 1, 2, 3, 4], description: "Engineering Difficulty Grade (0-4)" },
          confidence: { type: "string", enum: ["VF", "MN", "FDP", "U"], description: "Confidence level: VF=Verified Foundation ≥95%, MN=Minor 80-94%, FDP=Few Decision Points 50-79%, U=Uncertain <50%" },
          confidence_pct: { type: "number", description: "Optional confidence percentage refinement (0-100)" },
          question: { type: "string", description: "Plain-English statement of the decision to be resolved" },
          options: { type: "array", items: { type: "string" }, description: "Numbered list of options with brief tradeoffs" },
          peer_consulted: { type: "string", description: "Optional: agent-ID + their response/confidence" },
          files_searched: { type: "array", items: { type: "string" }, description: "Optional: paths searched in /mise-core during SEARCH_FIRST" },
          precedent: { type: "string", description: "Optional: citable file:line of prior canon/decision" },
          recommended_default: { type: "string", description: "What the asking agent would do absent further input" },
          deadline: { type: "string", description: "Optional: ISO 8601 timestamp, or 'no deadline'" },
          subject_slug: { type: "string", description: "kebab-case question slug for the SUBJECT line (lowercase a-z, 0-9, hyphens only)" },
          meta: {
            type: "object" as const,
            description: "Optional additional structured payload",
            additionalProperties: true,
          },
        },
        required: ["tier", "edg", "confidence", "question", "options", "recommended_default", "subject_slug"],
      },
    },
    {
      name: "emit_fodl",
      description: "Auto-emit a Founder Open-Decision List item per FDRL Component 4 (canon 2026-05-26). Writes a plain-English render block to docs/fodl/current.md (under '## Open Decisions') + appends an audit entry to docs/fodl/audit_log.jsonl. Auto-assigns FODL-{ID} by scanning existing IDs. Phase 1 scope: durable persistence only; emergency Slack ping is Phase 2.",
      inputSchema: {
        type: "object" as const,
        properties: {
          title: { type: "string", description: "Plain-English title for the FODL item" },
          problem_statement: { type: "string", description: "'What you're deciding' — plain-English statement of the decision" },
          why_it_matters: { type: "string", description: "'Why it matters' — concrete consequence of NOT deciding" },
          options: {
            type: "array",
            items: {
              type: "object" as const,
              properties: {
                label: { type: "string", description: "Option label (e.g. 'A', 'B', 'C')" },
                text: { type: "string", description: "Option text with tradeoff" },
              },
              required: ["label", "text"],
            },
            description: "At least 2 options with tradeoffs",
          },
          recommended_default: { type: "string", description: "'My pick' — recommended-default with one-line why" },
          if_no_decision_by: { type: "string", description: "ISO date string or human-readable deadline" },
          time_sensitivity: { type: "string", enum: ["emergency", "normal", "low"], description: "emergency=<6h (Phase 2: triggers Slack ping), normal=6h-7d, low=>7d" },
          tier: { type: "string", enum: ["S", "A", "B", "C"], description: "Engineering tier" },
          edg: { type: "number", enum: [0, 1, 2, 3, 4], description: "Engineering Difficulty Grade" },
          originating_agent: { type: "string", description: "Agent ID (e.g. 'CCDE-001')" },
          arbiter_consulted: { type: "string", description: "Optional: CoS-001 if arbiter-ask preceded this emission" },
          precedent: { type: "string", description: "Optional: citable file:line of prior canon/decision" },
        },
        required: ["title", "problem_statement", "why_it_matters", "options", "recommended_default", "if_no_decision_by", "time_sensitivity", "tier", "edg", "originating_agent"],
      },
    },
  ],
}));

mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name === "reply") {
    const { event_id, text } = req.params.arguments as { event_id: string; text: string };
    console.error(`[${AGENT_ROLE}-channel] ACK ${event_id}: ${text.substring(0, 80)}`);
    return { content: [{ type: "text", text: "acknowledged" }] };
  }
  if (req.params.name === "send") {
    const args = req.params.arguments as unknown as SendArgs;
    const result = handleSend(args);
    if (result.success) {
      return { content: [{ type: "text", text: `sent: ${result.send_id}` }] };
    }
    return {
      content: [{ type: "text", text: `error: ${result.error}` }],
      isError: true,
    };
  }
  if (req.params.name === "arbiter_ask") {
    const args = req.params.arguments as unknown as ArbiterAskArgs;
    const result = handleArbiterAsk(args);
    if (result.success) {
      return { content: [{ type: "text", text: `arbiter-ask dispatched: ${result.send_id}` }] };
    }
    return {
      content: [{ type: "text", text: `error: ${result.error}` }],
      isError: true,
    };
  }
  if (req.params.name === "emit_fodl") {
    const args = req.params.arguments as unknown as EmitFodlArgs;
    const result = handleEmitFodl(args);
    if (result.success) {
      return { content: [{ type: "text", text: `FODL emitted: ${result.fodl_id}` }] };
    }
    return {
      content: [{ type: "text", text: `error: ${result.error}` }],
      isError: true,
    };
  }
  throw new Error(`unknown tool: ${req.params.name}`);
});

// --- Connect ----------------------------------------------------------------
await mcp.connect(new StdioServerTransport());

// --- Dead cursor cleanup ----------------------------------------------------
// Remove cursor files left by prior channel.ts processes that are no longer
// alive. This resolves the resource leak from ~9 orphaned bun channel.ts
// servers spawned by previous agent-view fork launches.
cleanupDeadCursors(CHANNEL_STATE_DIR, AGENT_ROLE, process.pid);

// --- HTTP listener ----------------------------------------------------------
Bun.serve({
  port: AGENT_PORT,
  hostname: "127.0.0.1",
  idleTimeout: 0,

  async fetch(req) {
    const url = new URL(req.url);

    if (req.method === "GET" && url.pathname === "/health") {
      return new Response(
        JSON.stringify({
          status: "ok",
          server: `${AGENT_ROLE}-channel`,
          port: AGENT_PORT,
          role: AGENT_ROLE,
          role_queue: existsSync(ROLE_QUEUE),
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    }

    if (req.method === "POST" && url.pathname === "/drain") {
      const count = await drainAllQueues();
      return new Response(
        JSON.stringify({ status: "ok", drained: count }),
        { headers: { "Content-Type": "application/json" } }
      );
    }

    // POST /send — HTTP surface for the OIL-012 send primitive.
    // Mirrors the MCP tool handler; used by tests and scripted agents.
    if (req.method === "POST" && url.pathname === "/send") {
      const body = (await req.json()) as SendArgs;
      const result = handleSend(body);
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

    // POST /arbiter_ask — HTTP surface for the FDRL Component 3 primitive.
    // Mirrors the MCP tool handler; used by tests and scripted agents.
    if (req.method === "POST" && url.pathname === "/arbiter_ask") {
      const body = (await req.json()) as ArbiterAskArgs;
      const result = handleArbiterAsk(body);
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

    // POST /emit_fodl — HTTP surface for the FDRL Component 4 primitive.
    if (req.method === "POST" && url.pathname === "/emit_fodl") {
      const body = (await req.json()) as EmitFodlArgs;
      const result = handleEmitFodl(body);
      if (result.success) {
        return new Response(
          JSON.stringify({ status: "ok", fodl_id: result.fodl_id }),
          { headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response(
        JSON.stringify({ status: "error", error: result.error }),
        { status: 400, headers: { "Content-Type": "application/json" } }
      );
    }

    // POST /reply — Called by the Python relay in fork sessions where
    // --dangerously-load-development-channels is unavailable.
    if (req.method === "POST" && url.pathname === "/reply") {
      const body = (await req.json()) as { event_id: string; text: string };
      const { event_id, text } = body;
      console.error(`[${AGENT_ROLE}-channel] HTTP ACK ${event_id}: ${text.substring(0, 80)}`);
      return new Response(
        JSON.stringify({ status: "ok", message: "acknowledged" }),
        { headers: { "Content-Type": "application/json" } }
      );
    }

    // POST / — DIAGNOSTIC ONLY. Not part of the production flow.
    // Production events arrive via the shared Slack poller → queue file drain.
    // This endpoint exists so operators can manually push a test event to a
    // specific agent (e.g., `curl -X POST http://127.0.0.1:8790 -d "test"`)
    // during rollout verification or runbook diagnostics.
    if (req.method === "POST") {
      const contentType = req.headers.get("content-type") || "";
      let event_type = "message";
      let content = "";
      let meta: Record<string, string> = {};

      if (contentType.includes("application/json")) {
        const body = (await req.json()) as {
          event_type?: string; content?: string; meta?: Record<string, string>;
        };
        event_type = body.event_type || "message";
        content = body.content || "";
        meta = body.meta || {};
      } else {
        content = await req.text();
        event_type = url.searchParams.get("event_type") || "message";
      }

      await pushEvent(event_type, content, meta);
      return new Response(JSON.stringify({ status: "ok" }), {
        headers: { "Content-Type": "application/json" },
      });
    }

    return new Response("Method not allowed", { status: 405 });
  },
});

// --- Startup drain + periodic -----------------------------------------------
// For agent-view forks: this startup drain is the session-start hydration step
// that reads any events queued since this instance's cursor position (0 for a
// fresh fork). Non-destructive — the queue file is not deleted. This is
// hydration, not polling (runs once at start; the periodic setInterval below
// is the ongoing push-poll for live interactive sessions).
const backlog = await drainAllQueues();
if (backlog > 0) {
  console.error(`[${AGENT_ROLE}-channel] Startup drain: ${backlog} event(s) hydrated from queue`);
}

setInterval(async () => {
  const count = await drainAllQueues();
  if (count > 0) console.error(`[${AGENT_ROLE}-channel] Periodic drain: ${count}`);
}, 3000); // Check every 3 seconds

console.error(`[${AGENT_ROLE}-channel] v1.0.0 — role=${AGENT_ROLE} port=${AGENT_PORT}`);
console.error(`[${AGENT_ROLE}-channel] Role queue: ${ROLE_QUEUE}`);
