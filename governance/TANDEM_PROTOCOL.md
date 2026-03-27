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

A shared state file (`handoffs/TANDEM_BOARD.md`) that both agents read before doing anything and update after doing anything.

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

### Step 6: Reviewer — Verify the Fix Works
- Open a fresh environment. Navigate to the affected area.
- Follow the reproduction sequence — the exact steps to trigger the original issue
- Loading a page and seeing no error is NOT verification. You must walk through the full user flow.
- Log: "FIX VERIFIED" or "FIX FAILED" with evidence and reproduction steps performed
- If FAILED: Builder goes back to Step 2 (investigate, don't just patch)

### Step 7: Reviewer — Bug Scan
- Scan the surrounding area for OTHER issues
- Check adjacent functionality, related pages, edge cases
- Log: "SCAN COMPLETE" — verdict: **CLEAN** or issues found

### Step 8: Dual Declaration
- Both agents must independently declare "VERIFIED"
- Neither can close the ticket alone
- Both declarations logged in the Verification Log

### Step 9: Audit (Async)
- If a Scribe/auditor exists, it reviews the V-Loop after completion
- Checks: were all steps followed? Were any skipped? Is the verification genuine?

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
