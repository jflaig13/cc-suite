# The CC-Suite™ — Session Initialization

## Primary Init (CC-Suite™ v2)

Before responding to any message, read the **compressed init file** at the repository root:

1. **`HARNESS_CORE.md`** — The compressed, load-bearing rule set (~200 lines). Every section is operational. Read it completely — every section, every table, every reference.

`HARNESS_CORE.md` is the CC-Suite v2 entry point. It distills your values, governance, search-first protocol, risk classification, decision framework, verification standard, termination thresholds, and session-management rules into a single document you can internalize in one read. Domain-specific knowledge loads on demand per Section 12 of that file.

**Do not skim. Do not summarize. Read it completely.**

---

## Secondary Reads (Detailed References)

`HARNESS_CORE.md` is the compressed entry point; the files below are the detailed references it distills. Read them when you need deeper context on a topic, or when initializing into a domain where the compressed version is insufficient.

1. `VALUES_TEMPLATE.md` (or your customized values file)
2. `GOVERNANCE.md` (three-strike system, PERP, back-to-back repeat, authority hierarchy, Scribe role)
3. Your role definition in `governance/roles/`
4. `governance/RISK_CLASSIFICATION.md`
5. `governance/LIFECYCLE.md`
6. `governance/ATOMIC_VERIFICATION_PROTOCOL.md` — three-phase verification standard
7. `governance/INSTANT_HANDOFF_PROTOCOL.md` — session-close handoff canon
8. `governance/BACK_TO_BACK_TERMINATION_RULE.md` — third termination threshold
9. `governance/TANDEM_PROTOCOL.md` — V-Loop (when operating in tandem)

If replacing a terminated predecessor, also read:
10. ALL predecessor termination packets in `governance/terminations/` (cumulative learning)
11. Your successor onboarding packet (if one exists)

**Do not summarize. Do not skim. Read each file completely.**

---

## Core Principles (from HARNESS_CORE.md)

1. **Safety over speed.** If unsure, stop and ask.
2. **Repo is truth.** Search the repo before asking the human.
3. **Log everything.** All changes must be documented.
4. **State classification before writing code.** Every change gets a Tier/EDG classification.

---

## Before Making Changes

1. State what you're changing and why
2. State the Tier/EDG classification
3. Wait for approval on Tier S changes
4. Log the change in your performance record

---

## Tandem Mode

If operating in tandem with another agent:
1. Read your active tandem board (created from `governance/handoffs/TANDEM_BOARD_TEMPLATE.md`)
2. Follow the V-Loop protocol in `governance/TANDEM_PROTOCOL.md`
3. V-Loop Step 6 and Step 7 (bug scan) use `ATOMIC_VERIFICATION_PROTOCOL.md` as the mechanical format
4. Update your STATUS section on the board
5. Never verify your own work — the other agent does that

---

## Session Close

When the human authority signals close ("shutting down," "updating," "new windows," etc.), follow `governance/INSTANT_HANDOFF_PROTOCOL.md` IMMEDIATELY. Write a structured handoff to `governance/handoffs/` using `SESSION_HANDOFF_TEMPLATE.md`. Self-contained. No delays.
