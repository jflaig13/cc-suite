# Session Handoff — {AGENT_ID} — {DATE}

**Status:** Handoff document. Self-contained. A fresh instance should be able to pick up work from this file alone.

**Written at:** {TIMESTAMP}
**Trigger:** {What the human authority said — e.g., "shutting down", "updating"}
**Agent ID:** {ROLE-NNN}
**Session duration:** {approximate time the session was active}

---

## Active Work (Exact State at Close)

*What you were doing the moment the close signal came in. Be specific — file paths, line numbers, V-Loop step numbers, test status, deploy status.*

- **Current ticket:** {Ticket ID or short description, or "None"}
- **V-Loop step:** {1-9, or "N/A"}
- **Mid-edit:** {File path and line number if you were editing something, or "No"}
- **Test status:** {Passing / Failing / Not run / Which test}
- **Deploy status:** {Not deployed / Deploy in flight / Deployed but unverified / Verified}
- **Blocked on:** {What you were waiting for, if anything}

---

## Completed This Session

*Numbered list of what was finished. Include enough detail that the successor does not redo this work.*

1. {Completed item 1 — what changed, where, why}
2. {Completed item 2}
3. {Completed item 3}

---

## Code Changes (Uncommitted)

*Every file modified during this session. State commit and deploy status for each.*

| File | What Changed | Committed? | Deployed? |
|------|--------------|------------|-----------|
| {path/to/file.py} | {One-line description of the change} | {Yes / No / Partial} | {Yes / No / Pending} |
| {path/to/other.py} | {...} | {...} | {...} |

---

## Open Items (Priority Order)

*What still needs to happen, in priority order.*

### P0 — Drop everything
1. {Most urgent item}
2. {Next most urgent}

### P1 — Next session's main work
1. {Item}
2. {Item}

### P2+ — Queue
1. {Item}
2. {Item}

---

## Strike Count

**{AGENT_ID}: {current} / 3**

*If you received strikes during this session, note the reason and whether any recent strike was "same class" as the prior one (back-to-back rule risk).*

---

## Pickup Instructions

*Exact first action the successor should take. Be mechanical. No ambiguity.*

1. {First action — e.g., "Open `billing/calculate.py` at line 128 and read the current implementation"}
2. {Second action — e.g., "Run `pytest tests/test_billing.py::test_rounding -v` and confirm it fails as documented"}
3. {Third action — e.g., "Check `#tandem` channel for Reviewer status on V-Loop Step 4"}
4. {Continue numbering as needed}

---

## Context Notes (Optional)

*Anything the successor needs to know that does not fit the sections above. Cross-agent dependencies, recent human-authority decisions, tolerances added during the session, new brain files, etc.*

- {Note}
- {Note}

---

## Self-Check Before Posting

Before writing this handoff, confirm:

- [ ] A fresh instance with no prior context could pick up from this file alone
- [ ] Every file modified is listed in "Code Changes"
- [ ] Every open item is in priority order
- [ ] Pickup Instructions are mechanical (specific files, specific commands, specific next actions)
- [ ] Strike count is current
- [ ] Nothing important was left in chat history that is not captured here
