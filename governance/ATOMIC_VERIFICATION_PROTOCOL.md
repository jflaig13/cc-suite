# Atomic Verification Protocol

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** Every verification step performed by any governed agent. Not a special mode. Not triggered by a command. This is how verification works — always.

---

## Purpose

Atomic Verification is a three-phase mechanical standard for verifying any artifact (a web page, a data export, a deployment state, a generated document, a database snapshot, an API response — anything an agent must compare against a source of truth).

The protocol exists because free-form verification fails. An agent left to its own judgment will look at a page, notice an anomaly, rationalize it as "suspicious but probably fine," and declare the artifact CLEAN. More written rules do not fix this. The protocol fixes it by **separating extraction from judgment and making the verdict a derived formula, not a free-form declaration**.

It is structurally impossible under Atomic Verification to declare CLEAN while holding evidence of a MISMATCH — because the verdict is computed from the comparison grid, not chosen.

---

## The Three Phases

### Phase 1: EXTRACT (Judgment-Free)

The agent transcribes every data element from the artifact into a structured table. No annotations. No judgment. No commentary.

**Rules:**
- Pure transcription. Type exactly what the source shows.
- Use your domain's Verification Manifest (see `verification/VERIFICATION_MANIFEST_TEMPLATE.md`) as the checklist of required fields.
- Every field in the manifest gets a row in the extraction table.
- If a field is blank or not displayed on the source, write "BLANK" or "NOT DISPLAYED" — do not guess and do not omit.
- If a field shows an unexpected value, write the value. Do NOT explain it.

Forbidden extraction annotations: "looks good," "suspicious," "by design," "probably fine," "known gap," "within tolerance," "I'll double-check this later." None of these belong in Phase 1.

Format: see `verification/EXTRACTION_TEMPLATE.md`.

### Phase 2: COMPARE (Mechanical)

The agent (or the verification pipeline, if one exists) places each extracted value side-by-side with the corresponding canonical value.

**Rules:**
- Every row from Phase 1 gets a comparison entry. No rows may be skipped.
- Each row receives exactly one verdict: **MATCH**, **WITHIN TOLERANCE**, or **MISMATCH**.
- Tolerances must be pre-declared at protocol-setup time (see next section). They are domain-specific and immutable during a verification pass — the agent does not negotiate tolerances mid-verification.
- If a value is unexpected, unusual, or raises any question and is NOT covered by a pre-declared tolerance, it is **MISMATCH**. Not "suspicious." Not "by design." MISMATCH. Period.

Format: see `verification/COMPARISON_TEMPLATE.md`.

### Phase 3: VERDICT (Derived — Not Declared)

The verdict is a formula. The agent does not choose it.

```
CLEAN         = zero MISMATCH rows in the comparison grid
FAILURES FOUND = one or more MISMATCH rows in the comparison grid
```

There is no third option. There is no override. There is no "MISMATCH but it's actually fine so CLEAN." If one row says MISMATCH, the verdict is FAILURES FOUND.

The agent posts the verdict in the format defined in `verification/VERDICT_DECLARATION_TEMPLATE.md`. The extraction and comparison tables ARE the evidence — if they do not exist, the verification did not happen. A verdict without the supporting tables is invalid.

---

## The Nuclear Rule

**The agent's job is to find problems. The human authority decides if problems matter.**

If the agent encounters any value that is unexpected, unusual, confusing, or that the agent feels the need to explain — that value is MISMATCH. The human authority is the only party that can rule a MISMATCH acceptable.

Not valid verdicts:
- "By design"
- "Suspicious but probably fine"
- "Known gap"
- "Within our usual range"
- "I'll flag it for follow-up but mark CLEAN"

If the agent is in doubt: MISMATCH. Let the human authority sort it out.

---

## Tolerances (Pre-Declared, Domain-Specific)

Some domains have legitimate, founder-approved tolerances — rounding differences, timestamp resolution limits, check-transfer attribution quirks, known cosmetic drift. These must be declared **before verification begins**, not invented mid-pass.

**Format for declaring a tolerance:**

| Field type | Tolerance | Rationale | Approved by | Approved on |
|---|---|---|---|---|
| [e.g., dollar amount per line item] | [e.g., ≤ $0.01] | [e.g., floating-point rounding] | [authority name] | [date] |
| [e.g., timestamp] | [e.g., ≤ 1 minute] | [e.g., upstream clock resolution] | [authority name] | [date] |
| [Everything else] | **ZERO** | Default | — | — |

In Phase 2 comparison, a field that falls within its pre-declared tolerance is marked `ACCEPTED (tolerance: {type})` and does NOT count toward FAILURES FOUND. If a field has no declared tolerance, its tolerance is ZERO.

**Only the human authority can add tolerances.** When the agent encounters a MISMATCH and the human authority rules it acceptable, the rule is added to the tolerance table and becomes permanent for future verification passes.

---

## Entering the Protocol (Structural Pipeline Pattern)

The v2 enforcement model is **"the agent enters the pipeline — the pipeline does the right thing."** Instead of asking the agent to remember a nine-step checklist, the agent invokes a skill or command that mechanically runs each step. Each step cannot be skipped because the next step depends on the output of the previous one.

**The skeleton of a verification pipeline:**

1. **Close any existing session / browser / connection.** Atomic, non-negotiable. This step is why the pipeline exists — fresh context is inside the pipeline, not a step the agent remembers.
2. **Open a fresh context.** New browser, new session, new authentication. Navigate to the artifact.
3. **Capture the artifact state.** Screenshot, export, dump — whatever is appropriate for the domain.
4. **Generate a blank extraction template.** The template is sized to the domain (e.g., N rows for N line items) and every field starts blank.
5. **Fill the template.** Agent transcribes every field from the artifact.
6. **Validate.** A validator script REJECTS any template with blanks. Agent cannot proceed until every field is filled.
7. **Compare.** A comparator script reads the filled template and the canonical source, applies pre-declared tolerances mechanically, and outputs the comparison grid.
8. **Compute verdict.** The comparator derives CLEAN or FAILURES FOUND from the grid. No agent judgment.
9. **Report.** Agent reads the output and reports it to the coordination surface (handoff, channel, log, etc.). Mismatches are reported verbatim. The agent does NOT rationalize.

A verification flow that depends on the agent remembering steps is not acceptable for any Tier S or Tier A work. Build the pipeline.

---

## Three Enforcement Layers

The v2 protocol relies on four mechanical enforcement layers working together. Any one of them alone is bypassable; together they are not.

1. **Pipeline skill.** The skill is the ONLY path to valid verification evidence. Using any tool outside the skill during a verification pass is a protocol violation.
2. **Validator rejects blanks.** The extraction template validator refuses to accept any filled template with blank fields. The agent cannot proceed to comparison until the extraction is complete.
3. **Comparator computes the verdict.** The agent does not build the grid and does not declare CLEAN. The comparator reads the filled template, applies tolerances, and outputs the verdict.
4. **Audit log verdict validation (where applicable).** If you run a Scribe / auditor with typed-event acknowledgments, the auditor validates verdict phrases against per-event-type regex patterns. An acknowledgment that does not contain the required verdict phrase is REJECTED at the tool level. The audit log records every verdict permanently.

---

## Relationship to V-Loop Steps 6 and 6b

When two agents operate under the V-Loop (`governance/TANDEM_PROTOCOL.md`), the Reviewer runs Atomic Verification at V-Loop Step 6 (verify the fix works) and Step 6b (bug scan of the surrounding area). Both steps use the three-phase format defined here.

**V-Loop Step 6** — Reviewer extracts every field from the post-deployment artifact into the extraction template, runs the validator, runs the comparator, and posts the derived verdict to the tandem board. A free-form "looks good" is NOT a valid Step 6 outcome.

**V-Loop Step 6b** — Reviewer scans the surrounding area (adjacent functionality, related artifacts) using the same three-phase format. Any MISMATCH found in the scan queues a new ticket — it does NOT block the current ticket's verdict.

See `governance/TANDEM_PROTOCOL.md` for the full V-Loop.

---

## Scribe / Auditor Checklist

If your deployment has an independent Scribe or auditor, it verifies the following for every completed verification pass:

1. Does the extraction table exist? → If not, verification is incomplete.
2. Does the comparison grid exist? → If not, verification is incomplete.
3. Does the comparison grid have a row for every field in the Verification Manifest? → If not, verification is incomplete.
4. Are there any MISMATCH rows in the grid? → If yes, the posted verdict MUST be FAILURES FOUND.
5. Does the posted verdict match the grid? → If the grid has MISMATCH but the posted verdict says CLEAN, the verification is a **protocol violation** and the agent receives a strike.

Rule 5 is the key enforcement: **the auditor can mechanically verify the verdict is consistent with the evidence**. This was impossible under free-form verification.

---

## What Atomic Verification Replaces

| Old (dead) | Atomic Verification |
|---|---|
| Free-form prose ("looks good, numbers match") | Structured extraction → computed comparison → derived verdict |
| Optional checklists (honor system) | Mandatory manifest with every field accounted for |
| Agent decides if anomalies matter | Agent reports anomalies; authority decides if they matter |
| "CLEAN" is a declaration | "CLEAN" is a formula output (zero mismatches) |
| Evidence quality checked by async audit | Evidence completeness checkable mechanically |

Any pre-v2 checklist or manifest remains valid as a SUPPLEMENTARY reference. Atomic Verification is THE standard going forward.

---

## Related Files

- `verification/VERIFICATION_MANIFEST_TEMPLATE.md` — the domain-specific checklist of fields to verify
- `verification/EXTRACTION_TEMPLATE.md` — structured transcription format
- `verification/COMPARISON_TEMPLATE.md` — side-by-side grid format
- `verification/VERDICT_DECLARATION_TEMPLATE.md` — CLEAN / FAILURES FOUND declaration format
- `GOVERNANCE.md` — master governance spec
- `TANDEM_PROTOCOL.md` — V-Loop (Step 6 and 6b reference this protocol)
- `BACK_TO_BACK_TERMINATION_RULE.md` — two consecutive verification failures = termination

---

## Reference Implementation

Mise Inc. runs the reference implementation of Atomic Verification as a structural pipeline:

- **Spec:** `docs/brain/032826__atomic-verification-protocol.md` (established 2026-03-28 after multiple verification failures demonstrated that written rules alone were insufficient)
- **Skill:** `.claude/skills/verify-shift/SKILL.md` (145 lines) — the nine-step pipeline an agent invokes via `/verify-shift {SHIFT_CODE}`
- **Pipeline script:** `scripts/verification_pipeline.py` (464 lines) — three modes: `template` (generates blank extraction), `validate` (rejects blanks), `compare` (computes the grid and derives the verdict)
- **Batch wrapper:** `scripts/verify_shift_automated.py` — runs the pipeline over every shift in a period
- **Audit-log verdict validation:** `channels/scribe/webhook.ts` — Scribe channel server validates acknowledgment verdicts against per-event-type regex patterns; rejects invalid verdicts at the tool level

Mise's 28-field shift verification manifest (per-server fields, per-support-staff fields, totals, and independent recomputed calculations) is the concrete example this protocol templates. Your verification manifest will have different fields — but the three-phase structure and the pipeline enforcement model transfer directly.
