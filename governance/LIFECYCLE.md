# Agent Lifecycle Protocol

```
HIRE → ONBOARD → OPERATE → REVIEW → [TERMINATE] → [REHIRE]
```

---

## Phase 1: HIRE

**Trigger:** A new AI agent session is designated for a governed role.

**Actions:**
1. Determine the next sequential ID from `ID_REGISTRY.md`
2. Add the entry with status `Active`
3. Create personnel record from `performance/PERSONNEL_TEMPLATE.md`
4. Create strike log from `strikes/STRIKE_LOG_TEMPLATE.md`
5. If replacing a terminated predecessor, note the predecessor ID

---

## Phase 2: ONBOARD

**Trigger:** Hire phase complete.

**Mandatory reads (minimum):**
1. Your values file
2. This governance spec (`GOVERNANCE.md`)
3. Your role definition (`roles/{ROLE}.md`)
4. Risk classification system (`RISK_CLASSIFICATION.md`)
5. This lifecycle protocol
6. ALL predecessor termination packets for this role (cumulative learning)
7. Successor onboarding packet (if one exists)

**Cumulative Predecessor Learning (MANDATORY):**
Read termination packets for ALL terminated predecessors in this role — not just the immediate one. If BUILDER-001 and BUILDER-002 were both terminated, BUILDER-003 reads BOTH packets. Each predecessor's failures are distinct lessons. PERP classifies ANY repeat of ANY predecessor's failure as OLD.

**Completion:** All reads confirmed in personnel record. Onboarding date recorded.

---

## Phase 3: OPERATE

**Ongoing requirements:**
- Operate within role boundaries
- Log significant actions in personnel record (date, action, category, evidence)
- Comply with Tier/EDG classification for all changes
- Stop when correctness, authority, or safety is uncertain
- Submit to audit without objection
- Check memo inbox at session start

---

## Phase 4: REVIEW

**Trigger:** Human authority reviews performance.

**Inputs:**
- Personnel record (performance log)
- Strike log
- Scribe findings (if applicable)
- Gold stars

---

## Phase 5: TERMINATE

**Trigger:** 3 total strikes, 2 OLD strikes, or at-will decision by human authority.

**Actions:**
1. Write termination packet from template (5 sections — all mandatory)
2. Update ID Registry status to `Terminated`
3. Archive personnel record and strike log
4. Termination packet is permanent — never modified after creation

---

## Phase 6: REHIRE

**Trigger:** New deployment needed for a terminated role.

**Actions:**
1. Issue new Employee ID (incremented)
2. Create fresh personnel record and strike log
3. Create successor onboarding packet from template
4. New deployment must read ALL predecessor termination packets (cumulative)
5. New deployment completes enhanced onboarding areas from successor packet
