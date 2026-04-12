# Scribe Event Types — Canonical Registry

**Status:** Active (CC-Suite™ v2 — Phase 2 Channels)
**Authority:** Layer 6 (Governance Spec). Extends `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md`.
**Scope:** Canonical registry of event types the Scribe can receive, with required verdict phrases and origin rules.
**Related:** `governance/CHANNEL_PROTOCOL.md`, `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md`, `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`.

---

## Purpose

This is the single source of truth for Scribe event types. It defines:

1. Every event type the Scribe channel accepts
2. Where each type originates (human via bus, code hook, script, or direct POST)
3. The required verdict phrases (for typed events) or "informational" status (for untyped events)
4. The reply tool's behavior when a required verdict is missing
5. The dual-ACK scope per event type

Keep this registry in sync with your implementation's verdict patterns map. If the two drift, the Scribe will either accept verdicts the registry says are invalid or reject verdicts the registry says are valid. The implementation's pattern map is the runtime authority; this registry is the human-readable authority. Both must agree.

---

## The Registry

| Event type | Origin | Required verdict phrase | Behavior when missing | Dual-ACK scope |
|---|---|---|---|---|
| `brain_file_created` | Code hook (PostToolUse on writes into the brain files directory) | `WIRING COMPLETE` OR `WIRING INCOMPLETE` OR `CONFLICT DETECTED` | Reply REJECTED by channel server | Hook-sourced — internal ACK only |
| `deploy_completed` | Human (bus post) OR script | `DOC SYNC CLEAN` OR `DRIFT FOUND` | Reply REJECTED by channel server | Yes if bus-sourced (has bus timestamp); internal-only if script-sourced |
| `canon_changed` | Code hook (PostToolUse on writes into canon files — VALUES, GOVERNANCE, CLAUDE.md, AUTHORITY, etc.) | `CANON CHANGE CLEAN` OR `AUTHORITY CONFLICT` OR `DOWNSTREAM UPDATES NEEDED` | Reply REJECTED by channel server | Hook-sourced — internal ACK only |
| `tandem_board_updated` | Code hook (PostToolUse on writes into the tandem coordination board) | `V-LOOP STEP VALID` OR `V-LOOP VIOLATION` OR `TANDEM AUDIT CLEAN` | Reply REJECTED by channel server | Hook-sourced — internal ACK only |
| `verification_result` | Script (automated verification output) OR agent (manual verification push) | `ALL FIELDS MATCH` OR `MISMATCH FOUND` OR `VERIFICATION LOGGED` | Reply REJECTED by channel server | Depends on origin — dual-ACK if bus-sourced or originating from a human request, internal-only if purely machine-originated |
| `slack_message` | Human (bus post routed by the central poller) | Informational — no required phrase | Reply accepted as-is | Yes — bus-sourced by definition |
| `message` | Any (direct POST, generic) | Informational — no required phrase | Reply accepted as-is | Yes if bus-sourced (has bus timestamp); internal-only otherwise |
| `hook_event` (generic) | Any code hook | Informational — no required phrase | Reply accepted as-is | Hook-sourced — internal ACK only |
| `handoff_created` | Script or agent | Informational — no required phrase | Reply accepted as-is | Depends on origin |
| `browser_closed` | Code hook (browser-close marker) | Informational — no required phrase | Reply accepted as-is | Hook-sourced — internal ACK only |
| `browser_fresh_violation` | Code hook (fires when a browser snapshot/screenshot is taken without a recent browser close) | Informational — no required phrase | Reply accepted as-is (but the Scribe should escalate to human authority) | Hook-sourced — internal ACK only |
| `deploy_detected` | Code hook (detects deploy patterns in shell commands) | Informational — no required phrase | Reply accepted as-is | Hook-sourced — internal ACK only |
| `verification_incomplete` | Code hook (fires when an agent declares VERIFIED without running the mechanical checklist) | Informational — no required phrase | Reply accepted as-is (but the Scribe should escalate) | Hook-sourced — internal ACK only |

---

## Why Typed Events Require Verdicts

The typed events (`brain_file_created`, `deploy_completed`, `canon_changed`, `tandem_board_updated`, `verification_result`) are the events where an agent is most tempted to rationalize, skim, or short-circuit. They correspond to the protocol failures v2 was designed to prevent:

- **`brain_file_created`** catches the "new rule written but never wired into the init files" failure mode. Without mechanical enforcement, an agent writes a brain file, says "done," and moves on — and the rule never reaches any future session because no downstream files reference it. The required verdict forces the Scribe to state WIRING COMPLETE (downstream files updated), WIRING INCOMPLETE (they need updates), or CONFLICT DETECTED (the new rule contradicts an existing one). There is no "noted" verdict because "noted" is what fails.
- **`deploy_completed`** catches the "deployed code drifted from documentation" failure mode. Requires DOC SYNC CLEAN or DRIFT FOUND. "Looks good" is not an option.
- **`canon_changed`** catches the "edited the top of the authority hierarchy without checking downstream" failure mode. Requires CANON CHANGE CLEAN, AUTHORITY CONFLICT, or DOWNSTREAM UPDATES NEEDED.
- **`tandem_board_updated`** catches the "agents skipped V-Loop steps but declared VERIFIED anyway" failure mode. Requires V-LOOP STEP VALID, V-LOOP VIOLATION, or TANDEM AUDIT CLEAN.
- **`verification_result`** catches the "agent ran the pipeline but didn't actually check the output" failure mode. Requires ALL FIELDS MATCH, MISMATCH FOUND, or VERIFICATION LOGGED.

Each required verdict set is designed so that the Scribe cannot acknowledge the event without having at least nominally traversed the mechanical checklist. The checklist outputs are the verdicts, not vice versa. If an agent writes "WIRING COMPLETE" without running the wiring checklist, it is lying — and when the failure surfaces, the audit log will show the lie.

**The verdict is a formula output, not a judgment call.** This is the same principle as the atomic verification protocol (`governance/ATOMIC_VERIFICATION_PROTOCOL.md` in Phase 1): verification verdicts are computed mechanically from extracted data and comparison grids, not produced by the agent's judgment.

---

## How to Add a New Event Type

1. **Decide whether the event is typed or untyped.** A typed event requires mechanical enforcement (the agent could rationalize its way out without the verdict). An untyped event is informational (the agent's acknowledgment is enough).
2. **If typed:** Define the verdict phrase set. Design it so each phrase corresponds to a distinct outcome of a mechanical checklist. Add the checklist to `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md`.
3. **Add the row to this registry.** Include origin, verdict phrases, behavior-when-missing, and dual-ACK scope.
4. **Update the implementation's verdict patterns map.** In the Mise reference implementation, this is the `VERDICT_PATTERNS` constant in `channels/scribe/webhook.ts`. Use one regex per allowed phrase, case-insensitive.
5. **Test.** Post a test event of the new type, verify the channel server rejects an invalid verdict, verify it accepts a valid one, verify the entry lands in the audit log with the correct fields.
6. **Document the origin.** If the event is fired by a code hook, add the hook to the deployment's hook registry. If it comes from a script, document the script. If from a human, document the expected bus format.
7. **Run the dual-ACK scope decision.** Follow the rule from `governance/CHANNEL_PROTOCOL.md`:
   - Event has a bus-side timestamp (e.g., `slack_ts`) → dual-ACK required
   - Event has a `hook` field and no bus timestamp → internal ACK only
   - Mixed origin (some instances bus-sourced, some hook-sourced) → apply the rule per-instance based on metadata, not per-type

---

## Scope of Dual-ACK per Event Type

The dual-ACK canon (`governance/CHANNEL_PROTOCOL.md` — "Dual-ACK Requirement") distinguishes bus-sourced events from hook-sourced events. The rule is per-event-instance based on metadata, not per-event-type. However, the table below shows the TYPICAL dual-ACK scope for each event type as a shorthand.

| Event type | Typical origin | Typical dual-ACK scope |
|---|---|---|
| `brain_file_created` | Code hook | Hook-sourced — internal ACK only |
| `deploy_completed` | Mixed (human + script) | Dual-ACK if human-sourced |
| `canon_changed` | Code hook | Hook-sourced — internal ACK only |
| `tandem_board_updated` | Code hook | Hook-sourced — internal ACK only |
| `verification_result` | Script | Typically internal-only |
| `slack_message` | Human (bus) | Dual-ACK required |
| `message` | Mixed | Dual-ACK if bus-sourced |
| `hook_event` | Code hook | Hook-sourced — internal ACK only |
| `handoff_created` | Mixed | Dual-ACK if bus-sourced |
| `browser_closed` / `deploy_detected` / `browser_fresh_violation` / `verification_incomplete` | Code hook | Hook-sourced — internal ACK only |

**When in doubt:** check the event's metadata. If there is a bus timestamp field, dual-ACK. If there is only a `hook` field, internal-only. The metadata is the authoritative source.

---

## Implementation Notes

- **Regex case-sensitivity.** Verdict phrase patterns should be case-insensitive (`/i` flag). The Scribe should not be penalized for capitalization choices.
- **Substring match is sufficient.** The pattern matches if the verdict phrase appears anywhere in the reply text. The Scribe can include context, explanation, and evidence before or after the phrase.
- **Multiple phrases.** A reply may contain multiple verdict phrases (e.g., "WIRING COMPLETE for the `CLAUDE.md` entry but DOWNSTREAM UPDATES NEEDED for the role-specific init files"). The implementation should accept if any one of the required phrases matches — the Scribe's free-form text carries the nuance.
- **Event type spelling.** Exact spelling must match between the event producer (hook, script, poller) and the verdict patterns map. Typos cause silent validation bypass (no pattern → no enforcement). Test with a known-bad verdict to confirm the pattern is actually firing.

---

## Related Files

- `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md` — The Scribe channel specialization that consumes these event types.
- `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md` — The mechanical checklist the Scribe runs to produce each verdict.
- `governance/CHANNEL_PROTOCOL.md` — The abstract channel protocol and dual-ACK canon.
- `governance/ATOMIC_VERIFICATION_PROTOCOL.md` (Phase 1, if present) — The verification protocol that produces `verification_result` events.
- Your deployment's hook registry (e.g., `docs/brain/hooks.md`) — Lists which hooks fire which event types.
- The implementation's verdict patterns map — the runtime enforcement (e.g., `channels/scribe/webhook.ts` in the Mise reference).

---

## Changelog

- **v1.0 (2026-04-11):** Initial canonical registry. Derived from the Mise reference implementation's `VERDICT_PATTERNS` map.
