# Verification Manifest — [Your Domain]

**Status:** Template. Customize for your domain.

**Purpose:** The Verification Manifest is the canonical list of every data field that must appear in every extraction table for a given artifact type. When the agent enters Atomic Verification on this artifact type, every field listed below MUST receive a row in the extraction table. No field may be skipped.

**Authority:** This manifest is approved by the human authority and cannot be reduced without explicit approval. Fields may be added, but existing fields may only be removed with documented cause.

---

## Artifact Type

**Name:** [e.g., "Payroll Shift Review Page", "Invoice Detail Page", "Deployment Status Dashboard"]

**Source of truth:** [What system / file / API is the canonical comparison against — e.g., "Toast API shift data", "Stripe invoice record", "GitHub Actions run log"]

**Where this artifact appears:** [e.g., "app.example.com/payroll/period/{period}/shift/{code}", "console.stripe.com/invoices/{id}"]

---

## Required Fields

Group your fields into logical sections. Each section lists the fields that must be extracted. Add or remove rows as needed for your domain.

### Section 1: [Name — e.g., "Per Server"]

| # | Field | Source on Page | Notes |
|---|-------|----------------|-------|
| 1 | [Field name — e.g., "Name"] | [Where it appears on the artifact] | |
| 2 | [Field name] | | |
| 3 | [Field name] | | |

### Section 2: [Name — e.g., "Per Support Staff"]

| # | Field | Source on Page | Notes |
|---|-------|----------------|-------|
| 4 | [Field name] | | |
| 5 | [Field name] | | |

### Section 3: [Name — e.g., "Totals & Summary"]

| # | Field | Source on Page | Notes |
|---|-------|----------------|-------|
| 6 | [Total field] | | |
| 7 | [Total field] | | |

### Section 4: [Name — e.g., "Independent Calculations"]

Fields the agent must **compute independently** and compare against what the artifact displays. If the math does not match, it is MISMATCH.

| # | Check | Formula | Notes |
|---|-------|---------|-------|
| 8 | [Check name — e.g., "Sum of line items equals total"] | [The formula — e.g., "sum(item.price) == displayed_total"] | |
| 9 | [Check name] | [Formula] | |

---

## Tolerances

Pre-declared, domain-specific tolerances. Any field not listed has ZERO tolerance.

| Field type | Tolerance | Rationale | Approved by | Approved on |
|------------|-----------|-----------|-------------|-------------|
| [e.g., "Dollar amount per line item"] | [e.g., "≤ $0.01"] | [e.g., "Floating-point rounding between systems"] | [Authority name] | [Date] |
| [e.g., "Timestamp"] | [e.g., "≤ 1 minute"] | [e.g., "Upstream clock resolution"] | [Authority name] | [Date] |
| Everything else | **ZERO** | Default | — | — |

Only the human authority can add tolerances. The agent does NOT negotiate tolerances mid-verification.

---

## Example: Mise Shift Verification Manifest (28 Fields)

For reference, Mise's live shift verification manifest covers 28 fields grouped into:

- **Per server (fields 1-8):** name, clock-in, clock-out, hours worked, CC tips, cash, food sales, shift percentage
- **Per support staff (fields 9-16):** name, role label, tipout percentage, clock-in, clock-out, hours worked, tipout dollar amount, partial shift percentage
- **Shift totals (fields 17-22):** total food sales, total CC tips, total tipouts, pool after tipouts, per-server payout, shift total
- **Independent calculations (fields 23-28):** food sales sum check, per-tipout math check, total tipouts sum check, pool math check, per-server payout check, shift total check

Your manifest will look different because your domain is different. The shape — sections, numbered fields, independent calculation rows, pre-declared tolerances — transfers.

See `scripts/verification_pipeline.py` in the Mise reference implementation for how the manifest is encoded in code (the `generate_template` function reads canonical data and emits a JSON template sized to the manifest).
