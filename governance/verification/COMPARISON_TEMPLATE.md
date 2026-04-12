# Comparison Template (Phase 2)

**Status:** Template. Part of the Atomic Verification Protocol.

**Purpose:** The Comparison Template is the side-by-side grid format for Phase 2 of Atomic Verification. Each row from the Phase 1 extraction gets a comparison entry against the canonical source of truth. Every row receives exactly one verdict: **MATCH**, **WITHIN TOLERANCE**, or **MISMATCH**.

**Who builds this grid:** The comparator script builds it, not the agent. The agent reads the output. Manually building the grid is acceptable ONLY if no pipeline script exists for the domain — and in that case, the agent follows the format below exactly and does not rationalize any row.

---

## Rules

1. **Every row from Phase 1 gets a comparison entry.** No rows may be skipped.
2. **Each row gets exactly one verdict.** MATCH, WITHIN TOLERANCE, or MISMATCH. No "kind of a match." No "probably fine."
3. **Tolerances are pre-declared.** The comparator applies tolerances from the Verification Manifest. Fields not covered by a declared tolerance have ZERO tolerance.
4. **MISMATCH is MISMATCH.** If a value is unexpected, unusual, or raises any question and is NOT covered by a pre-declared tolerance, the verdict is MISMATCH. Not "suspicious." Not "by design." MISMATCH.

---

## Format

```markdown
## COMPARISON: [Artifact Label] ([Date / Identifier])

**Source:** [URL, file path, API endpoint — where the extraction came from]
**Canon source:** [What canonical data is being compared against]
**Comparator:** [Script name, or "manual" if no pipeline]

### Section 1: [Name]

| # | Field | Source Shows | Canon Says | Verdict |
|---|-------|--------------|------------|---------|
| 1 | [Field name] | [Extracted value] | [Canonical value] | MATCH / WITHIN TOLERANCE / MISMATCH |
| 2 | [Field name] | [Extracted value] | [Canonical value] | [Verdict] |
| 3 | [Field name] | [Extracted value] | [Canonical value] | [Verdict] |

### Section 2: [Name]

| # | Field | Source Shows | Canon Says | Verdict |
|---|-------|--------------|------------|---------|
| ... | ... | ... | ... | ... |

### Summary

- Total rows: [N]
- MATCH: [count]
- WITHIN TOLERANCE: [count]
- MISMATCH: [count]
```

---

## Example

*Names, dates, dollar amounts, and URLs in this example are fictional. They illustrate the shape of a comparison grid for a payroll-shift artifact. Your reference-impl-specific paths and POS-specific field names will differ.*

```markdown
## COMPARISON: Friday Evening Shift (2026-03-30)

**Source:** https://app.example.com/payroll/period/2026-03-30/shift/FRI-EVE
**Canon source:** proposals/FRI-EVE.json (pos_data + _cached_food_sales)
**Comparator:** scripts/verification_pipeline.py compare

### Servers

| # | Field | Source Shows | Canon Says | Verdict |
|---|-------|--------------|------------|---------|
| 1 | Alice Example.name | Alice Example | Alice Example | MATCH |
| 2 | Alice Example.clock_in | 5:00 PM | 5:00 PM | MATCH |
| 3 | Alice Example.clock_out | 11:30 PM | 11:30 PM | MATCH |
| 4 | Alice Example.hours | 6.50 | 6.50 | MATCH |
| 5 | Alice Example.cc_tips | $0.00 | $250.00 | **MISMATCH** |
| 6 | Alice Example.food_sales | $1,800.00 | $1,820.50 | WITHIN TOLERANCE ($20.50 ≤ $100 food sales tolerance) |

### Summary

- Total rows: 28
- MATCH: 26
- WITHIN TOLERANCE: 1
- **MISMATCH: 1**
```

---

## Applying Tolerances

When a field has a pre-declared tolerance (from `VERIFICATION_MANIFEST_TEMPLATE.md`), the comparator marks the verdict as `WITHIN TOLERANCE ({difference} within {tolerance_type})` and does NOT count it toward FAILURES FOUND. Example format:

- `MATCH` — exact match (down to the precision specified by the field type)
- `WITHIN TOLERANCE ($20.67 within $100 food sales tolerance)` — within a declared tolerance, accepted
- `MISMATCH` — outside all declared tolerances

Mark tolerance applications explicitly so the audit log can verify the comparator applied only the tolerances approved by the human authority.

---

## What NOT to Write in the Verdict Column

Forbidden verdict values:
- "Kind of a match"
- "Close enough"
- "Probably fine"
- "By design (mismatch)"
- "Suspicious"
- "Known gap"
- "I'll follow up"
- "N/A" (unless the canon source literally has no value for this field — use "NO CANON" in that case)
- Empty cells

Only MATCH / WITHIN TOLERANCE / MISMATCH / NO CANON are valid. Everything else is a protocol violation.

---

## Reference Implementation

Mise's comparator is `scripts/verification_pipeline.py` in the `compare` mode. It reads the filled extraction JSON, loads the canonical proposal JSON for the shift, iterates every field, applies the declared tolerances (food sales ≤ $100 per server, clock times ≤ 1 minute, dollar amounts < $0.01), and prints the grid in the exact format above. Mismatches are marked `<<<< MISMATCH` in the output for visual scanning. The script exits non-zero if any MISMATCH rows exist so it can be used in automated pipelines.
