# Verdict Declaration Template (Phase 3)

**Status:** Template. Part of the Atomic Verification Protocol.

**Purpose:** The Verdict Declaration Template is the final output of Atomic Verification. The verdict is **derived** from the Phase 2 comparison grid, not declared freely by the agent.

---

## The Formula

```
CLEAN          = zero MISMATCH rows in the comparison grid
FAILURES FOUND = one or more MISMATCH rows in the comparison grid
```

There is no third option. There is no override. Rows marked WITHIN TOLERANCE do not count toward MISMATCH. The verdict is computed, not chosen.

---

## Format: CLEAN

```markdown
## VERDICT: [Artifact Label] — CLEAN

**Comparisons:** [N] total
**Mismatches:** 0
**Verdict:** CLEAN

**Evidence:**
- Extraction: [file path, URL, or location of Phase 1 table]
- Comparison: [file path, URL, or location of Phase 2 grid]

**Verified by:** [agent ID]
**Verified at:** [timestamp]
```

---

## Format: FAILURES FOUND

```markdown
## VERDICT: [Artifact Label] — FAILURES FOUND

**Comparisons:** [N] total
**Mismatches:** [X]
**Verdict:** FAILURES FOUND

**Mismatches:**
- [Field name]: source=[value], canon=[value]
- [Field name]: source=[value], canon=[value]
- [Field name]: source=[value], canon=[value]

**Evidence:**
- Extraction: [file path, URL, or location of Phase 1 table]
- Comparison: [file path, URL, or location of Phase 2 grid]

**Verified by:** [agent ID]
**Verified at:** [timestamp]

**Escalation:** Per the Nuclear Rule, mismatches are reported to the human authority for decision. Agent does NOT override.
```

---

## Example: CLEAN

```markdown
## VERDICT: Monday Friday Matinée — CLEAN

**Comparisons:** 28 total
**Mismatches:** 0
**Verdict:** CLEAN

**Evidence:**
- Extraction: /tmp/extraction_FAM.json
- Comparison: comparator output (scripts/verification_pipeline.py compare --shift FAM)

**Verified by:** REVIEWER-002
**Verified at:** 2026-04-05T10:22:14-04:00
```

---

## Example: FAILURES FOUND

```markdown
## VERDICT: Monday Friday Matinée — FAILURES FOUND

**Comparisons:** 28 total
**Mismatches:** 2
**Verdict:** FAILURES FOUND

**Mismatches:**
- Chase Vinson.cc_tips: source=$0.00, canon=$232.67
- Kevin Worley.clock_out: source="Still in", canon="4:30 PM"

**Evidence:**
- Extraction: /tmp/extraction_FAM.json
- Comparison: comparator output (scripts/verification_pipeline.py compare --shift FAM)

**Verified by:** REVIEWER-002
**Verified at:** 2026-04-05T10:22:14-04:00

**Escalation:** Per the Nuclear Rule, mismatches are reported to the human authority for decision. Agent does NOT override.
```

---

## Rules

1. **The agent does not choose the verdict.** The verdict is computed from the Phase 2 grid via the formula above.
2. **CLEAN requires ALL rows to be MATCH or WITHIN TOLERANCE.** If even one row is MISMATCH, the verdict is FAILURES FOUND.
3. **The extraction and comparison are the evidence.** A verdict without the supporting tables is invalid. The agent MUST link to them.
4. **Mismatches are reported verbatim.** The agent does NOT rephrase, minimize, or explain them away.
5. **Nuclear Rule.** The agent's job is to find problems. The human authority decides if they matter. Never override a MISMATCH.

---

## If Your Deployment Uses Typed Events

If your coordination surface uses typed events with required verdict phrases (e.g., a Scribe channel that validates acknowledgments), the verdict text above must include the exact verdict phrase the channel expects. Common patterns:

- Verification event → required phrase: `CLEAN (0 mismatches in N comparisons)` or `FAILURES FOUND (X mismatches)`
- Deployment verification → required phrase: `DOC SYNC CLEAN` or `DRIFT FOUND`
- Canon change verification → required phrase: `CANON CHANGE CLEAN` or `AUTHORITY CONFLICT` or `DOWNSTREAM UPDATES NEEDED`

The channel server rejects acknowledgments that do not contain the required phrase. Check your event type definitions before posting.

---

## Reference Implementation

Mise posts verdict declarations to the `#tandem` Slack channel, which is routed into the Scribe's channel server. The Scribe webhook (`channels/scribe/webhook.ts`) validates each verdict against the `VERDICT_PATTERNS` map per event type — invalid verdicts are REJECTED at the tool level and never reach the audit log. Every accepted verdict appends a line to `channels/scribe/audit_log.jsonl`, producing a permanent, append-only record of every verification decision across the fleet.
