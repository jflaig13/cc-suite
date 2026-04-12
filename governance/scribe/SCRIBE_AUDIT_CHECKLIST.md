# Scribe Audit Checklist — Mechanical Per-Event-Type Process

**Status:** Active (CC-Suite™ v2 — Phase 2 Channels)
**Authority:** Layer 6 (Governance Spec). Extends `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md`.
**Scope:** The exact mechanical checklist the Scribe runs on every typed event. Produces the verdict phrase the channel server validates.
**Related:** `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md`, `governance/scribe/EVENT_TYPES.md`, `governance/CHANNEL_PROTOCOL.md`, `governance/TANDEM_PROTOCOL.md` — Step 9.

---

## Purpose

The Scribe's job is to record, verify, and flag — never to execute, decide, or modify state. Verification that relies on judgment is fragile: agents rationalize, skim, and declare "looks good" without actually checking. The Scribe channel server's verdict pattern validation closes half of that gap by refusing to accept malformed acknowledgments. This checklist closes the other half by making the verdict itself the output of a mechanical process rather than a judgment call.

Every typed event follows the same four-step structure:

1. **STEP 1 — EXTRACT.** Read the source material. Transcribe the relevant data into a structured form without commentary.
2. **STEP 2 — COMPARE.** Check the extracted data against the canonical reference(s) for that event type.
3. **STEP 3 — VERDICT.** The verdict is a formula output: a specific condition produces a specific phrase from the allowed set.
4. **STEP 4 — ACKNOWLEDGE.** Call the reply tool with the verdict phrase. For bus-sourced events, also post the external ACK per the dual-ACK canon.

**Nuclear rule:** the Scribe finds problems, the human authority decides whether they matter. "By design" and "suspicious but fine" are not verdict options.

---

## Event: `brain_file_created`

**Trigger:** a new brain file (institutional memory, rule, canon, or spec) has been created or meaningfully edited.

### STEP 1 — EXTRACT

Read the brain file completely. Transcribe the following into a flat table:

- **Title** — the canonical title of the brain file
- **Status** — e.g., CANON / DESIGN / HISTORICAL / SUPERSEDED
- **Purpose** — the one-sentence reason the file exists
- **Core assertion** — the rule, principle, or fact the file establishes (one paragraph max)
- **Scope** — who the rule applies to (all agents, a specific role, a specific domain)
- **Supersession claim** — does this file supersede an older file? Which one?

### STEP 2 — COMPARE

Run the Wiring Checklist. For each target, mark WIRED / UNWIRED / N/A.

| Target | Wired? | Why |
|---|---|---|
| Top-level CLAUDE.md (or equivalent session-init file) — Institutional Rules section | ☐ | Is this a rule every agent in every session must know? If yes and not referenced → UNWIRED. |
| Memory file (cross-session persistence) | ☐ | Does this rule affect future session behavior? If yes and no memory entry → UNWIRED. |
| Role-specific init files / registries | ☐ | Is this rule role-specific? Does the relevant role's init reference it? |
| Master spec / company context document | ☐ | Is this a company-level fact or architecture change? Is the master spec updated? |
| Related brain files (supersession check) | ☐ | Does this file claim to supersede another? Is the older file marked as superseded? Are there silent contradictions with other brain files? |

### STEP 3 — VERDICT

- **WIRING COMPLETE** — all applicable targets are wired. No contradictions with existing files.
- **WIRING INCOMPLETE** — the brain file is written but at least one downstream target needs to be updated before the rule takes effect. List the specific target(s).
- **CONFLICT DETECTED** — the brain file contradicts an existing rule in another brain file or canon document without explicitly superseding it. List the conflict.

### STEP 4 — ACKNOWLEDGE

Call `reply(event_id, text)` with text containing the verdict phrase plus evidence (specific file paths, the wiring gaps or conflicts).

**Dual-ACK:** typically NOT required — `brain_file_created` is usually hook-sourced (fires on a PostToolUse file write). If the event metadata shows it was bus-sourced instead, apply the dual-ACK canon.

---

## Event: `deploy_completed`

**Trigger:** a production deploy has landed OR a human announced a deploy in the bus.

### STEP 1 — EXTRACT

- Read `git log --oneline -10` (or equivalent) to see the commits included in the deploy.
- For each commit, extract:
  - Commit hash
  - Commit message (one line)
  - Files changed (from `git diff --stat HEAD~1..HEAD` or equivalent)

Transcribe into a table. No commentary.

### STEP 2 — COMPARE

Cross-reference each commit against the canonical documentation sources. For each documentation file that could be affected by the commit, check whether the file was updated or is now stale.

| Documentation source | Updated by this deploy? | Stale now? |
|---|---|---|
| Master spec / company context | ☐ | ☐ |
| Memory file | ☐ | ☐ |
| Relevant brain files | ☐ | ☐ |
| Top-level init file | ☐ | ☐ |
| Role registries | ☐ | ☐ |

### STEP 3 — VERDICT

- **DOC SYNC CLEAN** — every piece of deployed code is reflected in the documentation, and no documentation file is now stale.
- **DRIFT FOUND** — at least one commit is not reflected in the documentation, OR a documentation file that was consistent with the previous state is now stale. List the specific drift.

### STEP 4 — ACKNOWLEDGE

Call `reply(event_id, text)` with the verdict phrase plus the specific drift items (if any).

**Dual-ACK:** required if the event was bus-sourced (a human posted "deployed X" in the bus). Not required if the event was pushed by a script.

---

## Event: `canon_changed`

**Trigger:** a canon file (values file, reasoning standard, search protocol, agent policy, or session init file) has been edited.

### STEP 1 — EXTRACT

- Read the changed canon file completely.
- If possible, read `git diff` of the change to see exactly what was added/removed/modified.
- Transcribe into a table:
  - File path
  - Section(s) edited
  - Nature of the edit (addition / deletion / modification)
  - Text of the change

### STEP 2 — COMPARE

For each change, check:

| Check | Result |
|---|---|
| Authority hierarchy conflict? | ☐ Does this change contradict a higher-authority file? The authority hierarchy in `GOVERNANCE.md` is: Values → Reasoning Standard → Search Protocol → Agent Policy → Company Context → Domain Specs → Governance → Roles → Skills → Codebase → External. Changes to a lower-authority file cannot override a higher-authority file. |
| Downstream documents stale? | ☐ Are there brain files, init files, or role registries that reference the old text and must be updated? |
| Contradicts memory or spec? | ☐ Does the change contradict a statement in the memory file or master spec? |
| Hook or reference broken? | ☐ Does the change break a script, hook, or cross-reference that depended on the old text? |

### STEP 3 — VERDICT

- **CANON CHANGE CLEAN** — the change is consistent with all higher-authority files, no downstream updates are needed, and nothing is broken.
- **AUTHORITY CONFLICT** — the change contradicts a higher-authority file. List the conflict. This is a high-severity flag — higher-authority files cannot be overridden by lower ones.
- **DOWNSTREAM UPDATES NEEDED** — the change is internally consistent but downstream documents or references need updates. List the specific updates.

### STEP 4 — ACKNOWLEDGE

Call `reply(event_id, text)` with the verdict phrase plus specifics.

**Dual-ACK:** typically NOT required — `canon_changed` is usually hook-sourced.

---

## Event: `tandem_board_updated`

**Trigger:** the shared tandem coordination board (see `governance/handoffs/TANDEM_BOARD_TEMPLATE.md`) has been edited.

### STEP 1 — EXTRACT

- Read the tandem board completely. Identify:
  - Which ticket(s) the update touched
  - Which section(s) were edited (CURRENT TICKET / BUILDER STATUS / REVIEWER STATUS / VERIFICATION LOG / BUG HUNT QUEUE)
  - Which V-Loop step(s) the update corresponds to

### STEP 2 — COMPARE

Run the V-Loop Compliance Checklist from `governance/TANDEM_PROTOCOL.md`:

| Check | Result |
|---|---|
| Are all V-Loop steps present for the ticket up to the current step? | ☐ |
| Is the step sequence correct (no skipped steps, no out-of-order steps)? | ☐ |
| Does each logged step have specific evidence (not "N/A" or "looks good")? | ☐ |
| For Step 6 (Reviewer verifies the fix): was the reproduction sequence actually executed, or did the Reviewer just open a page and declare verified? |  ☐ |
| Was the Reviewer's verification independent of the Builder (fresh session, not cached/stale)? | ☐ |
| For Step 8 (Dual declaration): are BOTH agent declarations logged, or did one agent try to close alone? | ☐ |
| Did the Builder verify its own work anywhere in the ticket? (Violation if yes.) | ☐ |

### STEP 3 — VERDICT

- **V-LOOP STEP VALID** — the specific step that was just added is present, sequenced correctly, and has concrete evidence.
- **V-LOOP VIOLATION** — a step was skipped, miss-sequenced, missing evidence, or the Builder tried to self-verify. List the specific violation.
- **TANDEM AUDIT CLEAN** — the full ticket up through the latest update is compliant; no violations anywhere in the log.

Use `V-LOOP STEP VALID` for incremental step updates, `TANDEM AUDIT CLEAN` for ticket-close audits, `V-LOOP VIOLATION` for any failure.

### STEP 4 — ACKNOWLEDGE

Call `reply(event_id, text)` with the verdict phrase plus specifics.

**Dual-ACK:** typically NOT required — `tandem_board_updated` is usually hook-sourced on board file writes.

---

## Event: `verification_result`

**Trigger:** an automated verification script or agent has produced a structured verification output (typically a per-field MATCH/MISMATCH grid from a mechanical pipeline, per `governance/ATOMIC_VERIFICATION_PROTOCOL.md` in Phase 1).

### STEP 1 — EXTRACT

The event content is structured data (JSON or equivalent). Extract:

- The subject of the verification (what was verified)
- Total number of fields checked
- Number of matches
- Number of mismatches
- The per-field verdict grid (field name, "page shows", "canon says", "verdict")
- The script's own verdict (e.g., CLEAN / FAILURES FOUND)

### STEP 2 — COMPARE

- Cross-check the per-field grid: does the count of MATCH/MISMATCH verdicts equal the totals in the summary?
- For each MISMATCH: is the "page shows" / "canon says" / "verdict" triple populated with specific values (not "N/A" or blank)?
- Is the script's verdict consistent with the grid? (CLEAN should mean zero mismatches; FAILURES FOUND should mean at least one.)

### STEP 3 — VERDICT

- **ALL FIELDS MATCH** — every field in the grid matches. The script's summary confirms zero mismatches.
- **MISMATCH FOUND** — at least one field has a MISMATCH verdict. List every mismatch with the specific field, expected value, and actual value. This flag goes to the human authority — the Scribe does not judge whether the mismatch "matters."
- **VERIFICATION LOGGED** — the verification output was recorded but the grid was malformed (missing fields, blank verdicts, or internal inconsistency). Log the verification event but explicitly note that the output format was invalid and a re-run is needed.

### STEP 4 — ACKNOWLEDGE

Call `reply(event_id, text)` with the verdict phrase plus the specific mismatches or format issues.

**Dual-ACK:** depends on origin. Typically internal-only (script-sourced). If the event came from a human request via the bus, apply the dual-ACK canon.

---

## Untyped Events (Informational Acknowledgment)

For `slack_message`, `message`, `hook_event`, `handoff_created`, `browser_closed`, `deploy_detected`, `browser_fresh_violation`, and `verification_incomplete`:

### STEP 1 — EXTRACT

Read the event content. Note the metadata (source, originating channel if any, any hook identifier, any bus-side timestamp).

### STEP 2 — COMPARE

There is no mechanical checklist for untyped events because there is no typed verdict. However, the Scribe still must decide:

- Is this event informational only, or does it require action?
- Is this event a signal of a protocol violation that the Scribe should escalate?
- Is this event bus-sourced (requires dual-ACK) or hook-sourced (internal only)?

### STEP 3 — VERDICT

No required phrase. Use free-form text stating what the Scribe did or decided. Typical patterns:

- `noted — <brief summary of the event and what (if anything) was recorded>`
- `flagged — <summary of why this event is an escalation candidate, plus what was sent to the human authority>`

**Escalation triggers (must flag, not just note):**

- `browser_fresh_violation` — an agent took a browser snapshot without closing the browser first. This is a verification protocol violation.
- `verification_incomplete` — an agent declared VERIFIED without running the mechanical checklist.
- Any `slack_message` containing "shutting down" or "updating" from the human authority — triggers the Instant Handoff Protocol (Phase 1, see `governance/INSTANT_HANDOFF_PROTOCOL.md`).

### STEP 4 — ACKNOWLEDGE

Call `reply(event_id, text)` with free-form text.

**Dual-ACK:** required if bus-sourced. Not required if hook-sourced.

---

## Dual-ACK Reminder (Applies to All Steps Above)

Whenever STEP 4 — ACKNOWLEDGE applies to a bus-sourced event (the event metadata contains a bus-side timestamp such as `slack_ts`):

1. Call the reply tool with the verdict (internal ACK).
2. **Immediately after,** post back to the originating bus channel using the deployment's bus-post tool. Include all four required elements:
   - Originating bus timestamp
   - Reply tool name (`mcp__{role}-channel__reply` or equivalent)
   - Event ID
   - Verdict phrase

See `governance/CHANNEL_PROTOCOL.md` — "Dual-ACK Requirement" for the full canon and template.

**Failure to dual-ACK on a bus-sourced event is a canon violation.** The Scribe's own compliance must be exemplary because the Scribe is also the enforcer of dual-ACK across the fleet (per `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md`).

---

## Related Files

- `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md` — Scribe channel specification, including the audit log and verdict validation.
- `governance/scribe/EVENT_TYPES.md` — Canonical event registry with required verdict phrases.
- `governance/CHANNEL_PROTOCOL.md` — Abstract channel protocol and dual-ACK canon.
- `governance/TANDEM_PROTOCOL.md` — V-Loop protocol; Step 9 (Audit) invokes this checklist.
- `governance/ATOMIC_VERIFICATION_PROTOCOL.md` (Phase 1, if present) — The structural verification pipeline that produces `verification_result` events.
- `GOVERNANCE.md` — "The Scribe (Independent Auditor)" section defining the role.

---

## Changelog

- **v1.0 (2026-04-11):** Initial checklist. Extracted from the Mise reference implementation's scribe channel instructions (`channels/scribe/webhook.ts` `instructions` field).
