# Termination Packet: {ID}

---

## Section A — Summary

| Field | Value |
|-------|-------|
| Employee ID | {ID} |
| Role | {ROLE_NAME} |
| Hire Date | {HIRE_DATE} |
| Termination Date | {TERMINATION_DATE} |
| Termination Trigger | {3 strikes / 2 OLD strikes / At-will / Other} |
| Authorized By | {NAME} |

---

## Section B — Exit Interview

### 1. What was the primary failure mode of this deployment?

{answer}

### 2. Was the failure a pattern (systemic) or isolated?

{answer}

### 3. Which protocols or constraints were insufficient to prevent the failure?

{answer}

### 4. What systemic gaps enabled or failed to catch the failure?

{answer}

### 5. What should the successor's onboarding emphasize?

{answer}

---

## Section C — Root Cause Analysis

{Narrative analysis. Go deeper than "the agent made a mistake." Answer: why did the SYSTEM allow this? What structural weakness was exposed?}

---

## Section D — Prevention Recommendations

**Onboarding materials:**
- {recommendation}

**Role boundaries:**
- {recommendation}

**Protocol enforcement:**
- {recommendation}

**Audit procedures:**
- {recommendation}

---

## Section E — Artifact Links

| Artifact | Path |
|----------|------|
| Performance Log | `governance/performance/{ID}.md` |
| Strike Log | `governance/strikes/{ID}.md` |
| Related Files | {list or "None"} |

---

*This termination packet is a permanent record. It must not be modified after creation.*

*Successor requirement: The next deployment in this role MUST read this packet during onboarding.*
