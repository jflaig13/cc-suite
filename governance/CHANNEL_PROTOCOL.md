# Channel Protocol — Push-Based Event Delivery for Long-Running Agents

**Status:** Active (CC-Suite™ v2 — Phase 2 Channels)
**Authority:** Layer 6 (Governance Spec). Does not override Values, Reasoning Standard, Search Protocol, Agent Policy, or company context documents.
**Scope:** Any deployment of The CC-Suite™ that runs long-running interactive agent sessions which must receive institutional events in real time.
**Related:** `governance/CHANNEL_TEMPLATE.md`, `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md`, `governance/scribe/EVENT_TYPES.md`, `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`, `governance/lifecycle/INTERACTIVE_LIFECYCLE.md`

---

## Purpose

When you need real-time event delivery to long-running agent sessions, use this pattern.

The Channel Protocol exists because long-running agents cannot be asked to poll for messages without burning their context window. Polling loops (e.g., "check the shared board every 2 minutes") consume tokens continuously, drift out of sync with reality, and produce silent failure modes where a missed check looks identical to "nothing happened." Push-based delivery inverts the relationship: events arrive at the agent as structured system reminders the moment they land on the shared message bus, and the agent acknowledges each one through a mechanical reply tool that produces an auditable record.

This protocol is **optional** — CC-Suite deployments that do not run long-running interactive sessions (e.g., purely batch or single-turn invocations) have no consumer for pushed events and should use the `governance/lifecycle/EPHEMERAL_LIFECYCLE.md` variant with the one-way digest bridge instead.

---

## The Problem It Solves

Polling burns context. Every `/check-board` call reads the same file, produces the same "nothing new" output, and reduces the tokens available for actual work. For a long-running agent active for eight hours, a 2-minute polling loop costs 240 tool calls, and 238 of them return no new information. The cost is real. The detection is impossible — a missed poll looks the same as an empty poll.

Channel push events flip the model. Instead of the agent asking "anything new?", the infrastructure asks the agent "here is a new thing" — only when there actually is one. Events arrive as structured system reminders the agent is guaranteed to see. Zero polling overhead. No silent misses.

The protocol also produces an audit trail. Because every event is acknowledged through a reply tool that logs to a permanent append-only audit log, the human authority can retroactively prove which agent received which event and how they responded.

---

## Architecture — Platform-Agnostic Model

```
┌──────────────────┐
│  Shared Message  │  Slack, Discord, Matrix, Telegram,
│       Bus        │  custom HTTP, in-process bus —
│                  │  implementation-specific.
└────────┬─────────┘
         │ polls on interval
         ▼
┌──────────────────────────────────────┐
│ Central Poller                       │
│ - Single always-on process           │
│ - Reads new messages from the bus    │
│ - Routes by role mention / broadcast │
│ - Writes per-role queue files        │
│ - Emits heartbeat per cycle          │
└────────┬─────────────────────────────┘
         │ writes role-routed events
         ▼
┌──────────────────────────────────────┐
│ Per-Role Queue Files                 │
│ - One queue per consumer role        │
│ - Single-writer (poller)             │
│ - Single-reader (that role's server) │
│ - Append-only JSONL or equivalent    │
└────────┬─────────────────────────────┘
         │ drained periodically
         ▼
┌──────────────────────────────────────┐
│ Per-Role Channel Servers             │
│ - One server per role                │
│ - Drains its queue on interval       │
│ - Pushes events into the agent       │
│   session as system reminders        │
│ - Validates ACK replies              │
│ - Appends verdicts to audit log      │
└────────┬─────────────────────────────┘
         │ system reminder notifications
         ▼
┌──────────────────────────────────────┐
│ Long-Running Agent Session           │
│ - Receives <channel source="...">    │
│   system reminders in real time      │
│ - Processes each event               │
│ - Acknowledges via reply tool        │
└──────────────────────────────────────┘
```

### Central poller

One process that polls the shared message bus on a fixed interval. Reads new messages since the last checkpoint. Routes each message to zero or more role queues based on the routing rules below. Emits a heartbeat file on every cycle so health monitors can detect stalls. Runs under process supervision (launchd, systemd, supervisord, or equivalent) so a crash auto-restarts.

**Critical invariant:** there is exactly ONE poller process. Multiple pollers race on queue writes and cause duplicate delivery. Supervision must enforce singleton.

### Per-role queue files

One queue per consumer role. Single-writer (the poller), single-reader (that role's channel server). This prevents race conditions — you never have two processes trying to drain the same file, and you never have two processes trying to write the same file.

Queue files are append-only within a poll cycle. The channel server drains each file atomically via a rename-read-unlink pattern so new writes from the poller can't interleave with in-progress reads.

When a message is broadcast to all roles, the poller writes it once to every role's queue. There is no shared broadcast file. Fanning out at write time eliminates the broadcast fan-out race condition.

### Per-role channel servers

One MCP server (or equivalent protocol server) per role. Parameterized by role name — one codebase, N instances. Each server:

1. Runs as a subprocess of the agent session's runtime
2. Drains its role queue on a short interval (every 2–3 seconds is typical)
3. Pushes each drained event into the agent session as a structured system reminder
4. Exposes a `reply` tool the agent calls to acknowledge each event
5. Validates the reply text against required verdict patterns (for typed events)
6. Appends every accepted verdict to an append-only audit log

The channel server binds a localhost HTTP port (for direct POST events from scripts and hooks) in addition to the queue-drain path. Both feed into the same event-push function.

### Agent receives

The long-running agent session receives each event as a structured system reminder with a fixed shape:

```
<channel source="{role}-channel"
         event_type="{type}"
         event_id="{role}-N"
         timestamp="{iso-8601}"
         [source-bus metadata]>
{event content}
</channel>
```

The agent processes the event content, formulates a response, and calls the channel server's reply tool with the event_id and verdict text. The server either accepts (typed verdicts pass pattern validation) or rejects (typed verdicts fail validation — agent must retry with correct phrase).

---

## Required Metadata on Every Channel Event

Every event pushed through the channel protocol MUST carry the following metadata fields. These are the minimum set required for audit, correlation, and dual-ACK compliance.

| Field | Type | Required | Purpose |
|---|---|---|---|
| `source` | string | yes | Which channel delivered this event (e.g., `scribe-channel`, `builder-channel`). |
| `event_type` | string | yes | The typed event category. Determines which verdict patterns (if any) the reply tool will enforce. See `governance/scribe/EVENT_TYPES.md`. |
| `event_id` | string | yes | Unique per role, sequential. Format: `{role}-{N}`. Used to correlate ACKs with the audit log. |
| `timestamp` | string (ISO 8601) | yes | When the event was pushed to the agent session. |
| `routed_to` | string (comma-separated roles) | yes when routed from a broadcast path | Which roles received this event. Lets the agent tell whether this was a direct mention or a fleet-wide broadcast. |
| `slack_channel` (or equivalent) | string | yes when sourced from the message bus | Which bus channel the message came from (e.g., `tandem`, `common-room`). Populated by the poller. Null when the event came from a code hook or direct POST. |
| `slack_ts` (or equivalent) | string | yes when sourced from the message bus | The bus-side timestamp of the originating message. Required for dual-ACK correlation (see below). Null when the event came from a code hook. |
| `slack_user` (or equivalent) | string | when available | Which human posted the originating message, if any. |
| `hook` | string | yes when sourced from a code hook | Which hook fired this event (e.g., `deploy_verify_hook`, `brain_file_write_hook`). Mutually exclusive with `slack_ts` — an event is either from the bus or from a hook, never both. |

**Rule of thumb:** if the event has a `slack_ts` (or equivalent bus timestamp), it originated from a human on the bus. If it has a `hook` field, it originated from code. This distinction determines whether dual-ACK applies (see below).

---

## Routing Rules

The central poller decides which role queues receive each inbound message. Three rules, evaluated in order:

### Rule 1 — Broadcast marker

If the message text contains the literal substring `[[BROADCAST]]` (case-sensitive), the poller fans out to ALL consumer roles. This rule is evaluated first and short-circuits the other rules.

**Purpose:** When a sender needs to guarantee fleet-wide delivery regardless of which role names the message happens to mention, they prepend `[[BROADCAST]]`. Example: a nightly digest that summarizes several agents' output should reach every active agent, not just the ones whose names appear in the summary body.

**Case sensitivity:** strict. `[[BROADCAST]]` matches; `[[broadcast]]` does not. This prevents accidental triggering.

### Rule 2 — Role regex match

The poller maintains a map of role → regex. For each role, if the regex matches the message text, that role's queue receives the event. A single message can match multiple roles (multi-mention → multi-route). Matching is case-insensitive.

**Canonical regex shape:** `\b{ROLE_NAME}\b` (word-boundary anchored). This matches `CCTO`, `@CCTO`, and `ccto: please check` but does not match `ccto` embedded in an unrelated word.

**Adding a new role** is a one-line change to the poller's regex map. The same map derives the consumer set (see Rule 3).

### Rule 3 — No-match fall-through

If a message matches neither the broadcast marker nor any role regex, the poller fans out to ALL consumer roles. This is the default — when in doubt, deliver to everyone.

**Rationale:** A message that mentions no specific role is ambient context. Dropping it is worse than delivering it. The cost of a small amount of noise in every agent's channel is far less than the cost of a message that was meant for everyone silently disappearing.

---

## Broadcast Marker Pattern

When a sender needs to guarantee a message reaches every agent in the fleet regardless of content, they prepend `[[BROADCAST]]` (case-sensitive, literal substring) to the message text.

**When to use:**

- A nightly summary that digests multiple agents' output
- A fleet-wide announcement ("all agents: the deploy window opens at 18:00")
- A one-way push from an ephemeral worker that cannot itself deliver to individual agents (see `governance/DIGEST_BRIDGE_PROTOCOL.md`, Phase 3)
- Emergency alerts that must reach every active session

**When NOT to use:**

- A message directed at a specific role (just mention the role by name; the regex routing handles it)
- A private message between two agents (use the bus's DM or thread primitives, not a broadcast)

The marker is intentionally ugly so it's visible in normal prose. Senders see it and understand they're fanning out to the entire fleet.

---

## Dual-ACK Requirement (CRITICAL — from 2026-04-11 Canon)

**When an agent receives a channel event that originated from a human message on the bus, the agent MUST acknowledge via TWO paths.**

This is canon. It is not optional. It closes the silent-response gap where internal ACKs are invisible to humans watching the bus in real time.

### The two paths

1. **Internal ACK** — The agent calls the channel server's reply tool (e.g., `mcp__{role}-channel__reply` in the Claude Code reference implementation) with the event_id and verdict text. This clears the pending events map, logs to the audit log, and enforces verdict validation for typed events.

2. **External ACK** — The agent posts a visible reply back to the originating bus channel. This is NOT handled by the channel server; the agent makes a direct tool call to the bus's post-message API (e.g., the Slack Web API in the Mise reference implementation). The human sender can see the reply immediately in the channel they posted to.

**Both are required. Internal-only is a violation. External-only is a violation.**

### Required elements in every external ACK

The external (bus) post must contain four strict elements. Wording around them is flexible; presence is mandatory.

| # | Element | Example | Why required |
|---|---|---|---|
| 1 | Originating message timestamp (ts) | `ts 1775879960.056839` | Lets the human trace the ACK back to the exact message they sent. |
| 2 | Internal reply tool name | `mcp__{role}-channel__reply` | Proves the internal ACK was invoked; identifies which channel handled the event. |
| 3 | Channel event_id | `{role}-N` | Uniquely identifies the event in the audit log. Allows correlation between the bus post and the audit trail. |
| 4 | Verdict phrase | `closed`, `noted`, or a typed-event verdict | States what happened to the event. For typed events, this MUST match one of the required verdict patterns (see `governance/scribe/EVENT_TYPES.md`). |

### Canonical template

The following template was founder-validated in the reference implementation on 2026-04-11:

> *"Posted to #{channel} (ts {bus_ts}). Both ACK'd internally via `mcp__{role}-channel__reply` and confirmed externally in the channel so you can see it. Channel event {event_id} closed. [Optional follow-up.]"*

### Hook-sourced events are EXEMPT from dual-ACK

Events whose origin is a code hook (metadata has a `hook` field, no `slack_ts`) do NOT require an external ACK. Internal ACK via the reply tool is sufficient.

**Why:**

1. No human is actively watching a bus channel for the ACK. The event came from code, not from a person.
2. Posting every hook event back to the bus would flood the channel with machine chatter and degrade its usefulness for actual human-agent coordination.
3. The verdict still lands in the audit log, so the Scribe (if present) can audit any event's lifecycle by reading the audit log without needing a bus post.

**Rule of thumb:** if the event has a `slack_ts` (or equivalent bus timestamp), dual-ACK applies. If it has a `hook` field instead, only internal ACK is required.

### Enforcement

This is a **behavioral canon, not a mechanical enforcement**. The channel server cannot directly verify a bus post occurred — that's a separate tool invocation outside the server's view. Parsing ACK text for "Posted to #" patterns is fragile and unreliable.

Enforcement comes from three layers:

1. **Scribe audit** — The Scribe (see `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md`) periodically reviews the audit log and flags any Slack-sourced (bus-sourced) event whose internal ACK has no corresponding bus post visible in the originating channel within a short window (~60 seconds).
2. **Human observation** — The human authority watches the bus channels in real time. A missing bus ACK from an agent that SHOULD have received an event is immediately visible. This is the fastest detection path.
3. **Canon pressure + strike consequences** — Violations are strike-able under the deployment's governance rules (e.g., Type C — Negligence, per `GOVERNANCE.md`). Repeat violations escalate per the standard and back-to-back termination thresholds.

See `BACK_TO_BACK_TERMINATION_RULE.md` (Phase 1) for back-to-back repeat handling.

---

## Agent Launch Requirements

Long-running agent sessions must be launched with the channel server active. The exact launch mechanism is platform-specific, but the requirements are universal:

1. **One channel server per session.** Each agent session spawns exactly one channel subprocess. Multiple sessions on the same host spawn multiple subprocesses, each on its own port.

2. **Per-role isolation.** The channel server configuration must be role-specific so that different agent sessions do not compete for the same port or subprocess. In the Mise reference implementation, this is done via a per-role MCP config file loaded at launch time via `--mcp-config channels/configs/role-{name}.mcp.json`.

3. **Dev-mode flag (if the runtime requires one).** Some agent runtimes gate push notification handlers behind a development-mode allowlist. If yours does, the channel server must be explicitly loaded via that flag at launch time. In Claude Code, the flag is `--dangerously-load-development-channels server:{role}-channel`.

4. **Launch wrapper.** Because the launch command typically involves several flags that are easy to forget, wrap it in a script or shell alias per role. The wrapper should:
   - Validate that the per-role config exists
   - Pre-flight the port (kill any orphaned subprocesses, refuse if a live session holds the port)
   - Spawn the agent runtime with all required flags
   - Be invocable via a short alias for every role (e.g., `builder`, `reviewer`, `scribe`)

5. **Auto-sourced aliases.** Wrapper aliases should be sourced from the user's shell startup file so every new terminal has them available without manual activation.

See `governance/lifecycle/INTERACTIVE_LIFECYCLE.md` for the onboarding sequence that ensures every new agent hire is launched through the wrapper.

---

## Port Allocation

Each channel server binds a unique localhost port. Ports are reserved per role in a deployment-wide port table.

**Guidelines:**

- Reserve a contiguous block (e.g., 8789–8798 in the Mise reference implementation — 10 ports for 10 roles).
- One port per role. Never reuse ports across roles.
- Document the table in a visible location (e.g., `channels/PORTS.md`).
- The launch wrapper should read the port from the config, not hard-code it, so new roles require only a config file and a port table entry.

**Port collision handling:** When an agent session closes uncleanly, its channel subprocess may orphan and hold its port. The launch wrapper must pre-flight-check the role's port and either kill the orphan (if its parent agent process is dead) or refuse to launch (if a live agent session holds the port — the user must close that session first).

---

## Failure Modes

| Failure | Detection | Recovery |
|---|---|---|
| Central poller crash | Heartbeat file stale beyond threshold | Supervision auto-restart (launchd/systemd/etc.) |
| Poller bus-side auth failure | Poller stderr reports auth error | Rotate credentials, restart poller |
| Port squatted by orphaned subprocess | Launch wrapper `lsof` pre-flight | Wrapper kills orphan and retries bind |
| Channel server subprocess crash | Agent session marks its channel disconnected | Reconnect command or agent relaunch |
| Dev-mode flag forgotten at launch | Push events accumulate in server memory, never delivered to agent | Relaunch via the wrapper |
| Queue file corruption (bad JSON) | Server logs "bad queue entry" on drain | Skip corrupt lines, alert Scribe |
| Queue file growth unbounded | Health check size threshold | Investigate agent backpressure — agent is not draining |
| Session lost during runtime compaction | Agent context refresh loses MCP state | Reconnect command; channel server re-pushes pending events |
| Poller missing a required role queue | Role regex matches but queue file does not exist | Wrapper creates queue files at launch; health check flags missing files |
| Bus rate limit exceeded | Poller stderr reports rate limit | Increase poll interval; scale down if pathological |

See also the Operations Runbook in your deployment's Mise-equivalent brain file (`docs/brain/041026__channel-architecture-rollout.md` in the reference implementation).

---

## Relationship to Other Protocols

- **`governance/CHANNEL_TEMPLATE.md`** — Short template for defining a new channel for a specific role. Use this when adding a new role to the fleet.
- **`governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md`** — Specialization of this protocol for the Scribe role. Adds typed-event verdict validation and the audit-log append requirement. The Scribe's channel is the canonical reference implementation of verdict validation.
- **`governance/scribe/EVENT_TYPES.md`** — Canonical registry of event types and required verdict phrases. Update this whenever you add a new typed event.
- **`governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`** — Mechanical checklist the Scribe runs on every event type. Extracted from this protocol to keep Scribe responsibilities auditable.
- **`governance/lifecycle/INTERACTIVE_LIFECYCLE.md`** — Lifecycle variant for long-running agents that consume channel events. Interactive agents MUST be channel-connected.
- **`governance/lifecycle/EPHEMERAL_LIFECYCLE.md`** — Lifecycle variant for batch-triggered ephemeral agents. Ephemeral agents do NOT use channels because they have no persistent consumer; see `governance/DIGEST_BRIDGE_PROTOCOL.md` (Phase 3) for their one-way output pattern.
- **`governance/TANDEM_PROTOCOL.md`** — V-Loop Step 9 (Audit) references the Scribe audit checklist for mechanical per-event-type enforcement.
- **`HARNESS_CORE.md`** (Phase 1, repo root) — Compressed init that may reference this protocol in its "Communication" section.
- **`BACK_TO_BACK_TERMINATION_RULE.md`** (Phase 1) — Governs repeat violations of dual-ACK canon and other channel-protocol rules.

---

## Reference Implementation

The Mise reference implementation lives in the main `mise-core` repository and implements this protocol with:

- **Shared poller:** `channels/shared/slack_poller.ts` — single always-on process, polls Slack `#tandem` and `#common-room` every 10s, routes by regex, writes per-role JSONL queues, emits `poller.heartbeat`.
- **Generic agent channel:** `channels/agent/channel.ts` — parameterized by `AGENT_ROLE` env var, drains its role queue every 3s, pushes via `mcp.notification({method: "notifications/claude/channel"})`, exposes `reply` tool.
- **Scribe channel:** `channels/scribe/webhook.ts` — extends the generic agent channel with verdict validation against the VERDICT_PATTERNS map and append-only audit log at `channels/scribe/audit_log.jsonl`.
- **Per-role MCP configs:** `channels/configs/role-{name}.mcp.json` — one config file per role, each containing exactly ONE channel server. Loaded at launch via `--mcp-config`. This per-role isolation is the architectural fix that allows multiple simultaneous agent sessions without port collision.
- **Launch wrapper:** `scripts/launch_claude.sh` — role-aware launcher that validates the config, pre-flights the port, and passes the dev flag.
- **Shell aliases:** `scripts/dev/claude_aliases.zsh` — auto-sourced by `~/.zshrc`, one alias per role.
- **Process supervision:** `launchd/com.mise.slack-poller.plist.template` — macOS launchctl singleton enforcement with auto-restart.
- **Health check:** `scripts/check_push_health.sh` — heartbeat freshness, process liveness, per-channel `/health` endpoint, scribe `/metrics` summary.
- **Rollback:** `scripts/rollback_push_architecture.sh` — emergency revert that stops the poller, kills channel subprocesses, and reverts the cutover commit.
- **Ports:** 8789 (scribe) + 8790–8798 (nine agent channels).

Full Mise spec: `docs/brain/041026__channel-architecture-rollout.md`. The dual-ACK canon: `docs/brain/041126__channel-dual-ack-canon.md`.

The Mise implementation uses Slack as the message bus, Bun as the runtime, Claude Code as the agent runtime, and macOS launchd as the process supervisor. None of those choices are load-bearing for the protocol itself — any comparable substitutes (Discord + Cursor + systemd, Telegram + custom-agent + supervisord, in-process queue + test harness) implement the same abstract pattern.

---

## Changelog

- **v1.0 (2026-04-11):** Extracted from Mise reference implementation into CC-Suite™ v2 Phase 2 governance source. Mandates dual-ACK requirement per canon ratified 2026-04-11.
