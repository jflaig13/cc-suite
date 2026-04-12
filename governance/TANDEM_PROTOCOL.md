# Tandem Protocol — Two-Agent Verification (V-Loop)

**Purpose:** Prevent bugs from reaching the human authority. When two agents operate in tandem, every critical change goes through a Verification Loop that neither agent can skip alone.

**When required:**
- All Tier S or Tier A changes (any EDG)
- Any EDG-2+ change (any Tier)

---

## Roles

| Agent | Owns | Does NOT Do |
|-------|------|-------------|
| **Builder** | Code, implementation, server, tests | Verification, browser testing |
| **Reviewer** | Code review, browser verification, bug scanning | Implementation, deployment |

The Builder can never verify its own work. The Reviewer must independently confirm.

---

## The Coordination Board

A shared state file that both agents read before doing anything and update after doing anything. Create it from `handoffs/TANDEM_BOARD_TEMPLATE.md` when starting a tandem session.

**Sections:**
- **CURRENT TICKET** — One item at a time. Description, classification, V-Loop step, next action owner.
- **BUILDER STATUS** — Updated by Builder only.
- **REVIEWER STATUS** — Updated by Reviewer only.
- **VERIFICATION LOG** — Append-only. Neither agent removes entries. This is the audit trail.
- **BUG HUNT QUEUE** — Issues the Reviewer finds proactively.

**Rules:**
- No agent edits another agent's section
- Browser belongs to the Reviewer. Builder never uses the browser in tandem mode.
- If the other agent is silent, they are WORKING. Do not escalate silence. Wait.

---

## The V-Loop (9 Steps)

### Step 1: Builder — Classify and Announce
- State Tier/EDG classification on the board
- One-line description of what changes and why
- List files to be modified
- Log: "TICKET OPENED" in Verification Log

### Step 2: Builder — Investigate Data First
- Before writing ANY code, investigate the actual data (API responses, database values, edge cases)
- Document at least 2 concrete data examples
- Log: "DATA INVESTIGATED" with findings

### Step 3: Builder — Implement
- Write the fix. ONE implementation.
- **If about to change the same code a second time, STOP.** Go back to Step 2. Something was missed.
- Run tests. All must pass.
- Log: "IMPLEMENTED + TESTS PASS" with count
- Hand off to Reviewer with comprehensive details: files changed, what each change does, the data that drove the fix, what to verify

### Step 4: Reviewer — Code Review Against Canon
- Read the diff
- Check against all relevant canonical documents
- Independently verify data claims from Builder
- Check for: regressions, edge cases, values violations, scope creep
- Log: "CODE REVIEWED" — verdict: **PASS**, **BLOCK** (with specifics), or **CONCERN**
- If BLOCK: Builder addresses issues, returns to Step 3

### Step 5: Builder — Deploy + Fresh Environment
- Restart the server/environment
- Reviewer opens a FRESH session (new browser, new context — not cached/stale)
- Log: "DEPLOYED + FRESH ENVIRONMENT"

### Step 6: Reviewer — Verify the Fix Works (Atomic Verification)
- Open a fresh environment. Navigate to the affected area.
- Follow the reproduction sequence — the exact steps to trigger the original issue
- Loading a page and seeing no error is NOT verification. You must walk through the full user flow.
- **Run the Atomic Verification Protocol.** Step 6 uses the three-phase mechanical format defined in `ATOMIC_VERIFICATION_PROTOCOL.md`:
  - **EXTRACT** — transcribe every field from the post-fix artifact into the extraction template
  - **COMPARE** — side-by-side grid against canonical source, every row verdicted MATCH / WITHIN TOLERANCE / MISMATCH
  - **VERDICT** — derived formula: zero MISMATCH rows = CLEAN, otherwise FAILURES FOUND
- A free-form "looks good" is NOT a valid Step 6 outcome. The extraction table and comparison grid ARE the evidence — without them, Step 6 is incomplete.
- Log: "FIX VERIFIED (CLEAN, N comparisons)" or "FIX FAILED (X mismatches, see comparison grid)" with evidence and reproduction steps performed
- If FAILED: Builder goes back to Step 2 (investigate, don't just patch). Reviewer does NOT rationalize mismatches — Nuclear Rule applies (find problems, authority decides).

### Step 7: Reviewer — Bug Scan (Atomic Verification, Adjacent Surface)

*(Also referred to as "Step 6b" in some CC-Suite deployments — same semantics.)*

- Scan the surrounding area for OTHER issues using the same three-phase format from `ATOMIC_VERIFICATION_PROTOCOL.md`
- Check adjacent functionality, related pages, edge cases — apply EXTRACT → COMPARE → VERDICT to each
- Any MISMATCH found in the bug scan queues a new ticket — it does NOT block the current ticket's verdict
- Log: "SCAN COMPLETE" — verdict: **CLEAN** (no adjacent issues) or **FINDINGS** (list of new ticket IDs queued)

### Step 8: Dual Declaration
- Both agents must independently declare "VERIFIED"
- Neither can close the ticket alone
- Both declarations logged in the Verification Log

### Step 9: Audit (Async)
- If a Scribe/auditor exists, it reviews the V-Loop after completion
- Checks: were all steps followed? Were any skipped? Is the verification genuine?

**Mechanical audit path.** When the deployment runs the v2 channel architecture (`governance/CHANNEL_PROTOCOL.md`) and the tandem coordination board is under a code hook that fires `tandem_board_updated` events, the Scribe processes each update through the mechanical checklist in `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md` — specifically the `tandem_board_updated` event type. The Scribe extracts which step changed, runs the V-Loop Compliance Checklist (were all required steps present for the current ticket? was the sequence correct? does each logged step have specific evidence rather than "N/A" or "looks good"? did the Reviewer verify independently in a fresh environment? did the Builder avoid self-verification anywhere in the ticket? are dual declarations logged?), and produces a verdict from the allowed set: **V-LOOP STEP VALID**, **V-LOOP VIOLATION**, or **TANDEM AUDIT CLEAN**. The channel server's reply tool REJECTS any acknowledgment that does not contain one of these phrases, which prevents the Scribe from rationalizing or hand-waving the audit. The verdict is appended permanently to the Scribe's append-only audit log.

See:
- `governance/scribe/SCRIBE_AUDIT_CHECKLIST.md` — the `tandem_board_updated` checklist (STEP 1 EXTRACT → STEP 2 COMPARE → STEP 3 VERDICT → STEP 4 ACKNOWLEDGE)
- `governance/scribe/EVENT_TYPES.md` — canonical verdict phrases for `tandem_board_updated`
- `governance/scribe/SCRIBE_CHANNEL_PROTOCOL.md` — the channel server's verdict validation behavior
- `governance/CHANNEL_PROTOCOL.md` — the push-based event delivery spec

For deployments without the v2 channel architecture, Step 9 is still performed by the Scribe reviewing the tandem board on request and producing the same verdict set — just without the mechanical reply-tool enforcement. The mechanical path is preferred because it prevents silent audit failures where "looks fine" stands in for a genuine checklist run.

---

## Fast Lane (Low-Risk Only)

For Tier B/C x EDG-0 or EDG-1 ONLY:

| Step | Owner | What |
|------|-------|------|
| 1 | Builder | Classify + implement + run tests |
| 2 | Reviewer | Quick review (does change match request? obvious issues?) |
| 3 | Builder | Deploy if needed |
| 4 | Both | Dual declaration |

If the Reviewer finds ANYTHING concerning, upgrade to the full V-Loop.

---

## BLOCK Escalation

If the Reviewer BLOCKs and the Builder's second attempt is also BLOCKed, escalate to the human authority. Two consecutive BLOCK cycles means the agents cannot resolve it alone.
