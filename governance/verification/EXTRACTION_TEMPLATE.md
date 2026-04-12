# Extraction Template (Phase 1)

**Status:** Template. Part of the Atomic Verification Protocol.

**Purpose:** The Extraction Template is the structured transcription format for Phase 1 of Atomic Verification. Pure transcription — no judgment, no annotations, no commentary. Every field from the Verification Manifest gets a row.

---

## Rules

1. **Pure transcription.** Type exactly what the source shows.
2. **No annotations.** Never write "looks good," "suspicious," "by design," "probably fine," "within range," or any other judgment in this phase.
3. **No omissions.** If a field is blank on the source, write "BLANK" or "NOT DISPLAYED." Do not skip the row.
4. **Unexpected values get transcribed verbatim.** Do not explain them. Do not reformat them. Write what the source shows.
5. **The template validator rejects blanks.** If you leave a field empty, validation fails and you cannot proceed to Phase 2.

---

## Format

```markdown
## EXTRACTION: [Artifact Label] ([Date / Identifier])

**Source:** [URL, file path, API endpoint — where the data came from]
**Captured at:** [timestamp]
**Captured by:** [agent ID]

### Section 1: [Name — e.g., "Per Server"]

| # | Field | Value |
|---|-------|-------|
| 1 | [Field name from manifest] | [Exact value from source] |
| 2 | [Field name] | [Exact value] |
| 3 | [Field name] | [Exact value] |

### Section 2: [Name — e.g., "Per Support Staff"]

| # | Field | Value |
|---|-------|-------|
| 4 | [Field name] | [Exact value] |
| 5 | [Field name] | [Exact value] |

### Section 3: [Name — e.g., "Totals"]

| # | Field | Value |
|---|-------|-------|
| 6 | [Total] | [Exact value] |
| 7 | [Total] | [Exact value] |

### Section 4: [Name — e.g., "Independent Calculations"]

Independently computed by the agent and compared against what the artifact displays.

| # | Check | Computed | Displayed |
|---|-------|----------|-----------|
| 8 | [Check name] | [What the agent calculated] | [What the source shows] |
```

---

## Example

*Names, dates, dollar amounts, and URLs in this example are fictional. They illustrate the shape and density of an extraction table for a payroll-shift artifact. Replace with your domain when adapting.*

```markdown
## EXTRACTION: Friday Evening Shift (2026-03-30)

**Source:** https://app.example.com/payroll/period/2026-03-30/shift/FRI-EVE
**Captured at:** 2026-03-30T17:30:00-04:00
**Captured by:** REVIEWER-001

### Servers

| # | Field | Value |
|---|-------|-------|
| 1 | Server 1 Name | Alice Example |
| 2 | Server 1 Clock-In | 5:00 PM |
| 3 | Server 1 Clock-Out | 11:30 PM |
| 4 | Server 1 Hours | 6.50 |
| 5 | Server 1 CC Tips | $250.00 |
| 6 | Server 1 Cash | $50.00 |
| 7 | Server 1 Food Sales | $1,800.00 |
| 8 | Server 1 Shift % | 100% |
```

---

## What NOT to Write in Phase 1

Forbidden in the extraction table:
- "Looks correct" — that's a judgment, not a transcription
- "Within usual range" — judgment
- "$0.00 (suspicious)" — parenthetical is an annotation, remove it
- "Still in (by design)" — parenthetical is an annotation, remove it
- "Value missing, probably fine" — judgment + speculation
- "$1,847 (approximately)" — the source shows an exact value; write the exact value
- Empty cells — leave nothing blank; write "BLANK" or "NOT DISPLAYED"

All judgment happens in Phase 2 (COMPARE) via the mechanical comparator and in Phase 3 (VERDICT) via the formula. Phase 1 is transcription only.

---

## Reference Implementation

Mise's verification pipeline generates blank extraction templates from canonical Toast API data. The agent fills the template in JSON format; the validator rejects any JSON with blank fields before the comparator will run. See `scripts/verification_pipeline.py` in the Mise reference implementation — `generate_template` mode produces the blank template, `validate` mode rejects blanks.
