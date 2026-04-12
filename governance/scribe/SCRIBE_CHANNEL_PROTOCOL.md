# Scribe Channel Protocol — Independent Audit with Verdict Validation

**Status:** Active (CC-Suite™ v2 — Phase 2 Channels)
**Authority:** Layer 6 (Governance Spec). Specialization of `governance/CHANNEL_PROTOCOL.md` for the Scribe role.
**Scope:** Deployments that run a Scribe (independent auditor) agent and need mechanical enforcement of audit verdicts.
**Related:** `governance/CHANNEL_PROTOCOL.md`, `governance/scribe/EVENT_TYPES.md`, `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`, `GOVERNANCE.md` — "The Scribe (Independent Auditor)" section.

---

## Purpose

The Scribe is the institutional auditor defined in `GOVERNANCE.md`. It operates outside the executive hierarchy, cannot be overridden by any governed agent, and reports directly to the human authority. Its job is to record, verify, and flag — never to execute, decide, or modify state.

In a polling-era governance system, the Scribe audits by reading log files and brain files on request. In v2's push-based channel architecture, the Scribe receives a live stream of institutional events — brain file creations, deploys, canon changes, tandem board updates, verification results — and must produce a verdict on each one. The Scribe Channel Protocol specializes the generic Channel Protocol with two extra mechanical enforcements that make those verdicts auditable:

1. **Typed-event verdict validation.** The reply tool REJECTS any acknowledgment that does not contain a required verdict phrase for the event type. The Scribe cannot rationalize, hand-wave, or short-circuit the mechanical checklist — the server refuses to accept a reply that doesn't state the verdict.
2. **Append-only audit log.** Every accepted verdict is appended to a permanent log file. Entries are never modified or removed. The log is the canonical record of which events the Scribe processed and how it ruled.

Together, these two enforcements close the gap v1 left open: v1 defined the Scribe role but provided no mechanical way to ensure the Scribe actually ran the audit before declaring "done."

---

## Typed Events vs Untyped Events

The Scribe channel distinguishes two classes of events.

### Typed events

Events with a predefined audit workflow AND a required verdict phrase. The reply tool validates the ACK text against a per-type regex pattern. If the pattern does not match, the reply is REJECTED and the Scribe must retry with the correct phrase.

Current typed events (see `governance/scribe/EVENT_TYPES.md` for the canonical registry):

- `brain_file_created`
- `deploy_completed`
- `canon_changed`
- `tandem_board_updated`
- `verification_result`

Each typed event has a corresponding mechanical checklist in `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`. The Scribe runs the checklist, produces a verdict from the allowed set, and replies with a string that contains the verdict phrase.

### Untyped events

Events that carry information but do not require a specific audit outcome. No verdict validation is performed — any reply is accepted. Examples:

- `slack_message` / `message` — human-originated or generic messages routed by the bus poller
- `hook_event` — informational code hook events (e.g., `browser_closed`, `deploy_detected`, `browser_fresh_violation`)

Untyped events still land in the audit log when acknowledged, but the verdict column contains free-form text rather than a validated phrase.

---

## Verdict Validation

When the agent calls the reply tool on a typed event, the channel server:

1. Looks up the event type from the pending events map by `event_id`
2. Retrieves the required verdict patterns for that event type from the canonical registry (`governance/scribe/EVENT_TYPES.md` equivalent in code)
3. Tests the reply text against each pattern (case-insensitive substring or regex match)
4. If at least one pattern matches: ACCEPT — append to audit log, remove from pending events map, return "acknowledged"
5. If none match: REJECT — return a message stating the required verdicts, keep the event in pending, do NOT append to audit log

The Scribe's next action on a REJECTED reply is to re-run the checklist, select a valid verdict, and reply again. The pending events map preserves the event until a valid verdict lands.

**Rationale:** this makes the audit mechanical. An agent cannot write "looks fine" or "noted" on a typed event — the server refuses. The only way to clear a typed event is to state one of the required verdict phrases, which means the Scribe has at least nominally traversed the checklist that ends in those phrases.

---

## Audit Log

Every accepted verdict is appended to an append-only audit log (`audit_log.jsonl` in the Mise reference implementation; equivalent in other deployments). One JSON object per line. Entries are never modified, reordered, or removed.

Minimum required fields per entry:

```json
{
  "event_id": "scribe-42",
  "event_type": "brain_file_created",
  "content": "<the original event content>",
  "verdict": "WIRING COMPLETE. Added to CLAUDE.md Institutional Rules; MEMORY.md unchanged (brain-file-only).",
  "timestamp_event": "2026-04-11T04:15:30Z",
  "timestamp_ack": "2026-04-11T04:15:52Z"
}
```

Additional fields MAY be included (e.g., the agent's employee ID, the bus-side originating timestamp, reviewer cross-references) but the minimum set above is the canonical shape.

**Retention:** permanent. The audit log is the institutional memory of Scribe rulings. Truncating or rotating it destroys the record.

**Access:** the audit log SHOULD be exposed via a read-only endpoint (e.g., `GET /audit`) so the Scribe can introspect its own history and cross-reference prior rulings.

---

## Dual-ACK Enforcement at the Scribe Level

The dual-ACK canon (`governance/CHANNEL_PROTOCOL.md` — "Dual-ACK Requirement") applies to the Scribe exactly as it applies to every other channel-connected agent. The Scribe is not exempt.

**Scribe's own dual-ACK behavior MUST be exemplary.** Every bus-sourced event (events with a bus-side timestamp such as `slack_ts`) the Scribe receives requires BOTH:

1. Internal ACK via `reply` tool with the required verdict phrase for typed events
2. External ACK posted back to the originating bus channel with the four required elements (bus timestamp, reply tool name, event_id, verdict phrase)

If the Scribe receives a Slack-sourced `brain_file_created` event, it runs the wiring checklist, calls `reply` with "WIRING COMPLETE" (or the appropriate verdict), then posts to the originating bus channel following the canonical template.

### Scribe is the enforcer of dual-ACK across the fleet

Beyond its own compliance, the Scribe is responsible for AUDITING every other agent's dual-ACK behavior. This is one of the Scribe's unique duties under the v2 protocol.

**How the Scribe audits dual-ACK:**

1. Periodically (on a schedule or in response to an explicit request), the Scribe reads the audit log entries for bus-sourced events across all channels.
2. For each entry, the Scribe checks whether a corresponding bus post exists in the originating channel within a reasonable window (~60 seconds) after the internal ACK timestamp.
3. If the internal ACK is logged but no bus post is visible, the Scribe flags the violation to the human authority with:
   - Agent employee ID
   - Event ID and type
   - Audit log entry
   - The specific channel and window searched
4. The flagged violation is a candidate for a strike under the deployment's governance rules (typically Type C — Negligence).

**Rationale:** the channel server cannot verify a bus post occurred because that post happens through a separate tool call outside the server's view. The only actor positioned to verify dual-ACK compliance is one that can read both the audit log and the bus-side channel history — the Scribe. This is why dual-ACK enforcement is the Scribe's job, not the channel server's.

---

## How the Scribe Uses Its Own Channel

The Scribe consumes its own channel to receive institutional signals in real time. In the Mise reference implementation, the Scribe channel is on port `8789` (reserved — the only non-generic channel in the fleet) and receives events from:

1. **Post-tool-use hooks** — code hooks that fire on specific file writes (e.g., `brain_file_created` on writes into the brain files directory, `canon_changed` on writes into canon files, `tandem_board_updated` on writes into the tandem board).
2. **Deploy notifications** — either from humans posting `deploy_completed` to the bus, or from scripts pushing directly to the Scribe's channel HTTP endpoint.
3. **Verification results** — structured JSON output from automated verification scripts pushed via direct POST.
4. **Bus messages** — any bus message routed to the Scribe by the central poller (typically because the message mentions "scribe" or is a broadcast).
5. **Direct POSTs** — any script or hook can push an arbitrary event to the Scribe's channel via an HTTP endpoint.

For each of these, the Scribe runs the corresponding mechanical checklist from `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`, produces a verdict, and acknowledges via the reply tool (with dual-ACK if the event was bus-sourced).

---

## Event Type Handling Summary

The following table cross-references the Scribe's event handling with the canonical registry and the checklist. This is a summary — consult `governance/scribe/EVENT_TYPES.md` and `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md` for the authoritative rules.

| Event type | Typical origin | Verdict phrases | Dual-ACK? |
|---|---|---|---|
| `brain_file_created` | Code hook on brain-file write | `WIRING COMPLETE` / `WIRING INCOMPLETE` / `CONFLICT DETECTED` | No (hook-sourced) |
| `deploy_completed` | Human (bus) or script | `DOC SYNC CLEAN` / `DRIFT FOUND` | Yes if bus-sourced; no if script-sourced |
| `canon_changed` | Code hook on canon-file write | `CANON CHANGE CLEAN` / `AUTHORITY CONFLICT` / `DOWNSTREAM UPDATES NEEDED` | No (hook-sourced) |
| `tandem_board_updated` | Code hook on tandem-board write | `V-LOOP STEP VALID` / `V-LOOP VIOLATION` / `TANDEM AUDIT CLEAN` | No (hook-sourced) |
| `verification_result` | Script or agent | `ALL FIELDS MATCH` / `MISMATCH FOUND` / `VERIFICATION LOGGED` | Depends on origin |
| `slack_message` / `message` | Human (bus) or generic | Informational — no required phrase | Yes if bus-sourced |
| `browser_closed` / `deploy_detected` / `browser_fresh_violation` / `verification_incomplete` | Code hook | Informational — no required phrase | No (hook-sourced) |
| `handoff_created` | Script or agent | Informational — no required phrase | Depends on origin |

---

## Relationship to the Generic Channel Protocol

The Scribe channel is a specialization of the generic channel defined in `governance/CHANNEL_PROTOCOL.md`. Everything in the generic protocol applies:

- Central poller routes bus messages to the scribe queue
- Single-writer/single-reader queue file
- Channel server drains the queue and pushes events as system reminders
- Reply tool acknowledges events
- Dual-ACK canon applies to bus-sourced events

The Scribe channel adds:

- Typed-event verdict pattern validation (the server rejects malformed verdicts)
- Append-only audit log on every accepted verdict
- Dedicated port separate from the generic agent channels
- Dual-ACK cross-fleet audit responsibility

If you need a channel that does NOT enforce verdict validation (e.g., for a Builder or Reviewer role), use the generic `governance/CHANNEL_TEMPLATE.md` pattern instead. Only the Scribe (and any other independent-auditor-class roles your deployment defines) should use this specialization.

---

## Reference Implementation

The Mise reference implementation of the Scribe channel lives at:

- **Scribe channel server:** `channels/scribe/webhook.ts` — implements the `VERDICT_PATTERNS` map, the `reply` tool with validation logic, the append-only audit log at `channels/scribe/audit_log.jsonl`, and exposes HTTP endpoints (`/health`, `/metrics`, `/audit`, `/drain`, and direct POST).
- **Brain-file hook:** `scripts/scribe_channel_brain_hook.sh` — PostToolUse hook that fires `brain_file_created` events when a brain file is written.
- **Verdict pattern registry in code:** the `VERDICT_PATTERNS: Record<string, RegExp[]>` map inside `webhook.ts`. Keep this map in sync with `governance/scribe/EVENT_TYPES.md`.
- **Audit log inspection:** `curl -s http://127.0.0.1:8789/audit | jq .` returns the full audit log as a JSON array.
- **Metrics endpoint:** `curl -s http://127.0.0.1:8789/metrics | jq .` returns uptime, event counters, delivery rate, and pending event count.

Full Mise spec: `docs/brain/041026__channel-architecture-rollout.md`. Dual-ACK canon: `docs/brain/041126__channel-dual-ack-canon.md`.

The Mise implementation uses TypeScript + Bun + the Model Context Protocol SDK. Equivalent implementations in other languages / runtimes should preserve the three load-bearing behaviors: verdict pattern validation in the reply tool, append-only audit log on every accepted verdict, and dual-ACK audit responsibility across the fleet.

---

## Changelog

- **v1.0 (2026-04-11):** Extracted from Mise reference implementation into CC-Suite™ v2 Phase 2 governance source. Integrates dual-ACK canon from 2026-04-11.
