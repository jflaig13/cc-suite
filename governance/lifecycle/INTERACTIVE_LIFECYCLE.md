# Interactive Agent Lifecycle

**Status:** Active (CC-Suite™ v2 — Phase 2 Channels)
**Authority:** Layer 6 (Governance Spec). Specialization of `governance/LIFECYCLE.md` for long-running interactive agents.
**Scope:** Any agent that is human-launched, persists as a long-running session, and must receive institutional events in real time through the channel architecture.
**Related:** `governance/LIFECYCLE.md` (base lifecycle), `governance/CHANNEL_PROTOCOL.md`, `governance/lifecycle/EPHEMERAL_LIFECYCLE.md`.

---

## Definition

An **interactive agent** is an agent session that is:

- **Human-launched** — a person opens a terminal, runs a launch command, and the agent session comes up under their direct supervision
- **Long-running** — the session persists for hours (sometimes days), not seconds or minutes
- **Channel-connected** — the session is wired to a channel server at launch time, receives institutional events via push, and acknowledges them via the reply tool
- **Real-time responsive** — the session is expected to process events within minutes (often seconds) of their arrival, not on a batch schedule

Interactive agents are the primary shape of the fleet in a v2 deployment. They are the sessions the human authority talks to, the sessions that do tandem work, the sessions that carry the load of day-to-day operations.

Contrast with **ephemeral agents** (`governance/lifecycle/EPHEMERAL_LIFECYCLE.md`), which are cron-triggered, spawn-execute-exit, and have no persistent consumer for pushed events.

---

## Lifecycle Phases

The six phases from `governance/LIFECYCLE.md` apply unchanged, but several phases have additional requirements for interactive agents. This file lists the deltas.

### HIRE

No changes from the base lifecycle. Issue the next sequential Employee ID from `governance/ID_REGISTRY.md`, create the personnel record from `governance/performance/PERSONNEL_TEMPLATE.md`, create the strike log from `governance/strikes/STRIKE_LOG_TEMPLATE.md`.

### ONBOARD

**Interactive-specific additions on top of the base onboarding reads:**

1. **Channel server setup.** Before the first launch, the operator must:
   - Confirm the role has a per-role channel config file (e.g., `channels/configs/role-{role}.mcp.json` in the Mise reference implementation)
   - Confirm the role has a reserved port in the deployment port table
   - Confirm the role is in the central poller's routing map
   - Confirm the central poller is running and healthy
2. **Launch wrapper alias.** The role must have a shell alias in the deployment's wrapper (e.g., `scripts/launch_claude.sh {role}` behind a one-word shortcut). The alias file must be sourced in the user's shell startup so every new terminal has the alias available.
3. **First launch through the wrapper.** The agent's first session MUST be launched through the wrapper (never via plain `claude` or equivalent). Plain launches do not load the channel server and result in a silent communication failure that is hard to detect. Document this in the onboarding packet.
4. **Dual-ACK canon read.** The agent must read `governance/CHANNEL_PROTOCOL.md` — "Dual-ACK Requirement" section — in full during onboarding. This is a canon, not a suggestion.
5. **Audit log access.** If the role is a Scribe-class independent auditor, additionally read `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md`, `governance/scribe/EVENT_TYPES.md`, and `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`.

### OPERATE

**Interactive-specific ongoing requirements:**

1. **Channel-driven event processing.** The agent processes incoming channel events as they arrive. Events are pushed into the session as `<channel source="...">` system reminders (or the deployment's equivalent structured notification).
2. **Dual-ACK canon on every bus-sourced event.** For every channel event that originated from a human on the message bus, the agent MUST:
   - Call the reply tool with the verdict (internal ACK)
   - Post back to the originating bus channel with the four required elements (see `governance/CHANNEL_PROTOCOL.md` — "Dual-ACK Requirement")
3. **Typed-event verdict compliance.** For events with typed verdict requirements (see `governance/scribe/EVENT_TYPES.md`), the reply tool will REJECT malformed verdicts. The agent must run the mechanical checklist for the event type (`governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`) and produce a verdict phrase from the allowed set.
4. **Instant handoff on session close.** When the human authority signals a session close ("shutting down," "updating," "new windows" — see `governance/INSTANT_HANDOFF_PROTOCOL.md` in Phase 1), the agent writes a structured handoff immediately before the session terminates. This is non-optional — the channel architecture requires sessions to close cleanly so their ports release for the successor.
5. **Wrapper re-launch on communication failure.** If the agent notices its channel tools are not in its toolbelt, its events stop arriving, or the channel server disconnects, the agent flags the issue and requests a wrapper re-launch from the human authority. It does NOT attempt to patch its own session.

### REVIEW

Standard review cycle from the base lifecycle, with two additional inputs:

1. **Channel audit log entries.** Review the agent's audit log entries (`governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md` audit log) for the period. Look for verdict quality, turnaround time, rejected replies, and untracked ACKs.
2. **Dual-ACK compliance.** Cross-reference the audit log against the bus-side channel history. Every bus-sourced internal ACK should have a corresponding bus post within ~60 seconds. Missing bus posts are dual-ACK canon violations.

### TERMINATE

Standard termination. The terminated agent's channel port is released when the session closes; the successor reuses the same port via the wrapper.

Additional termination tasks for an interactive agent:

1. Archive the agent's audit log entries (or add a marker in the log indicating the termination boundary for future introspection).
2. Note the terminated agent's dual-ACK compliance record in the termination packet.
3. If the termination is due to a channel protocol violation (e.g., repeated dual-ACK failures), cite the specific canon violation and audit log evidence.

### REHIRE

Standard rehire cycle. The successor:

1. Receives a new Employee ID (permanent, never reused)
2. Reads ALL predecessor termination packets including any that cited channel protocol violations
3. Confirms wrapper launch on first session
4. Reads `governance/CHANNEL_PROTOCOL.md`, `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md` (if Scribe-class), and the dual-ACK canon as part of onboarding

---

## Required Infrastructure

For a deployment to run interactive agents at all, the following infrastructure must be in place:

| Component | Purpose | Must exist before first interactive hire |
|---|---|---|
| Central poller | Reads the message bus and routes to per-role queues | Yes |
| Process supervision for the poller | Singleton enforcement, auto-restart on crash | Yes |
| Per-role channel server codebase | The MCP (or equivalent) server that drains queues and pushes to agent sessions | Yes |
| Per-role config files | One config per role; ensures session isolation | One per role before that role's first launch |
| Port table | Documented port allocation per role | Yes |
| Launch wrapper | Role-aware launcher with config + flag + pre-flight checks | Yes |
| Shell aliases | One alias per role, auto-sourced from user shell startup | Yes |
| Health check script | Poller heartbeat + per-channel `/health` + audit log growth | Yes |
| Rollback script | Emergency revert that stops everything and restores polling | Yes |
| Dual-ACK canon in session init | Agents must read the canon on every session start | Yes |

If ANY of the above is missing, interactive hires are not yet possible. Either complete the infrastructure first or hire ephemeral agents (see `governance/lifecycle/EPHEMERAL_LIFECYCLE.md`) instead.

---

## Concurrency and Multiple Sessions

A single role can have only ONE active interactive session at a time (one port per role). If a second session is launched for the same role, the wrapper's port pre-flight check MUST refuse to launch — either because the port is held by the live first session, or because the orphan cleanup is ambiguous.

**When you need parallel work for a single role:**

- Use the tandem protocol: one Builder and one Reviewer, each in their own role, coordinated via the tandem board (`governance/TANDEM_PROTOCOL.md`). Two sessions, two roles, two channels.
- If you need multiple instances of the same role, define distinct roles in the role registry (e.g., `builder-a`, `builder-b`) with separate IDs, configs, and ports. Each is an independent hire.
- Do NOT share a channel between two agent sessions. The reply tool state is per-session and sharing would corrupt the pending events map.

---

## Session Close Behavior

Interactive agents close in three ways:

1. **Clean close.** The human authority signals session close and the agent runs the Instant Handoff Protocol (`governance/INSTANT_HANDOFF_PROTOCOL.md` in Phase 1), writing a self-contained handoff document. The channel server subprocess exits cleanly and releases its port.
2. **Unclean close.** The agent session terminates without running the handoff protocol (crash, forced quit, runtime error). The channel subprocess may orphan and hold its port. The launch wrapper's port pre-flight will detect the orphan and clean it up on the next launch, but any pending events in the server's memory are lost.
3. **Termination.** The agent receives enough strikes to hit one of the termination thresholds (standard 3-strike, PERP 2-OLD-strike, back-to-back repeat per `BACK_TO_BACK_TERMINATION_RULE.md` in Phase 1). The agent writes a final handoff, the termination packet is authored, and the successor picks up at the next hire.

For case 2 (unclean close), the Scribe should note the lost events in its audit log so they can be re-requested or re-pushed.

---

## Reference Implementation

The Mise reference implementation runs 10 interactive agents (one Scribe plus nine CC Execs / Utility), each on its own channel port (8789–8798), launched via `scripts/launch_claude.sh {role}` behind role aliases (`scribe`, `ccto`, `ccpo`, etc.) auto-sourced from `~/.zshrc`. The central poller is `channels/shared/slack_poller.ts` supervised by launchd. Dual-ACK is enforced behaviorally with Scribe audit layered on top of the raw channel architecture.

Full Mise spec: `docs/brain/041026__channel-architecture-rollout.md`. Dual-ACK canon: `docs/brain/041126__channel-dual-ack-canon.md`. Ephemeral vs interactive distinction: `docs/brain/041126__mini-fleet-channel-architecture-eval.md`.

None of Mise's specific platform choices (Slack, Bun, Claude Code, macOS launchd) are load-bearing for the interactive lifecycle pattern. Any comparable substitutes work.

---

## Related Files

- `governance/LIFECYCLE.md` — Base lifecycle that this file specializes.
- `governance/lifecycle/EPHEMERAL_LIFECYCLE.md` — The alternate lifecycle for batch-triggered agents.
- `governance/CHANNEL_PROTOCOL.md` — Channel architecture spec; required reading for every interactive hire.
- `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md` — Scribe-specific channel; required reading for Scribe-class interactive hires.
- `governance/INSTANT_HANDOFF_PROTOCOL.md` (Phase 1, if present) — Session-close handoff requirements.
- `BACK_TO_BACK_TERMINATION_RULE.md` (Phase 1, if present) — Back-to-back repeat termination threshold.
- `governance/TANDEM_PROTOCOL.md` — V-Loop for coordinated work between two interactive agents.

---

## Changelog

- **v1.0 (2026-04-11):** Initial specialization. Extracted from the Mise reference implementation's MacBook fleet operation model. Integrates the dual-ACK canon from 2026-04-11.
