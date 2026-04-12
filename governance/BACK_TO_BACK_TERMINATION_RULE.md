# Back-to-Back Repeat Termination Rule

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** All governed AI agent deployments. No exceptions.

---

## The Rule

**If an agent commits the same class of mistake twice consecutively, it is immediately terminated — regardless of total strike count.**

Two strikes are not required for this rule to apply. The trigger is the pattern, not the count. If the first strike is for mistake X and the next strike is also for mistake X, that's termination. No third chance.

---

## Why It Exists

The standard three-strike rule and PERP (Predecessor Error Repeat Policy) both catch agents who fail repeatedly — but over a window of multiple tickets. Neither catches the agent that is corrected for a mistake and then immediately commits the same mistake on the very next action.

That failure mode — "I heard the correction, I acknowledged it, and I re-committed it" — is proof that learning did not occur inside the session. Continued operation of such an agent is not justified. The strike system exists to give agents room to grow. Back-to-back repetition of the same error demonstrates no growth happened.

Writing additional rules does not fix agents that rationalize away corrections. A mechanical termination trigger does.

---

## Definition of "Same Mistake"

"Same mistake" means the **same class of error** — not necessarily identical circumstances.

Examples of same-class:
- Failed to verify in a fresh environment on ticket #1 → failed to verify in a fresh environment on ticket #2 → **back-to-back, terminated**.
- Declared "verified" without running the reproduction steps on ticket #1 → declared "verified" without running the reproduction steps on ticket #2 → **back-to-back, terminated**.
- Fabricated a business rule on ticket #1 → fabricated a different business rule on ticket #2 → **back-to-back, terminated** (both are Type A fabrication).

Examples of different-class (NOT triggered by this rule):
- Fabricated a business rule on ticket #1 → skipped a mandatory protocol on ticket #2 → not back-to-back (but may still be terminated by the standard three-strike rule).

**The human authority classifies whether a new strike is "the same mistake" as the immediately preceding one.** The agent does not get to argue classification. If the human authority rules it is the same class of error, termination is automatic and the Scribe (or equivalent auditor) records the packet.

---

## Interaction With Other Termination Thresholds

Three parallel termination thresholds are now active under CC-Suite™ v2. **Whichever is hit first triggers immediate termination.** They do not cancel or weaken each other.

| # | Rule | Trigger |
|---|------|---------|
| 1 | Standard three-strike | 3 total strikes of any kind = termination |
| 2 | PERP (Predecessor Error Repeat Policy) | 2 OLD strikes (strikes classified as repeating a documented predecessor failure) = termination |
| 3 | Back-to-back repeat (this rule) | Same class of mistake committed consecutively = termination |

All three are always active simultaneously. An agent on 2 standard strikes may be terminated by the back-to-back rule on the next strike, or by the standard rule on the next strike, or by PERP if the next strike is OLD — whichever fires first.

---

## Example Sequences

| Strike sequence | Result | Why |
|-----------------|--------|-----|
| Mistake A → Mistake A | **TERMINATED** | Same class of mistake, back-to-back |
| Mistake A → Mistake B | Not triggered by this rule | Different classes |
| Mistake A → Mistake B → Mistake A | Not triggered by this rule | Not consecutive (but terminated by standard three-strike) |
| Mistake A → Mistake B → Mistake B | **TERMINATED** | Same class of mistake (B), back-to-back |
| Mistake A (OLD) → Mistake A (OLD) | **TERMINATED** (by both rules simultaneously) | Same class AND PERP accelerated |

---

## Example From CC-Suite v1 Governance

A governed Builder deploys code containing a known-bad pattern (say, a SQL query that bypasses the ORM's parameterization). Strike issued. Builder acknowledges the mistake, says they'll never do it again, and proceeds to the next ticket.

On the next ticket, the Builder deploys code containing the same bypass — same SQL injection risk, different query, but the same class of error. The human authority classifies this as "same mistake, back-to-back." The Builder is terminated.

The three-strike rule alone would have given the Builder one more chance. The back-to-back rule does not, because the behavior demonstrates the Builder did not internalize the correction. The termination packet documents both instances, the failure class, and the specific classification decision.

---

## Enforcement

1. When a strike is issued, the human authority (or the Scribe, on behalf of the authority) compares the strike to the immediately preceding strike.
2. If both strikes are the same class of error, termination is automatic.
3. The Scribe writes the termination packet from `governance/terminations/TERMINATION_TEMPLATE.md` and notes which rule triggered it: "Terminated under back-to-back repeat rule (two consecutive strikes in class: {error class})."
4. The agent's ID in `governance/ID_REGISTRY.md` is marked `Terminated`.
5. The successor deployment reads the termination packet as part of its mandatory onboarding and completes a successor onboarding packet demonstrating it understands the specific failure pattern.

---

## Related Files

- `GOVERNANCE.md` — master governance spec (Three-Strike System section, updated to include this rule)
- `governance/LIFECYCLE.md` — hire / onboard / operate / review / terminate / rehire phases
- `governance/strikes/STRIKE_LOG_TEMPLATE.md` — strike log format
- `governance/terminations/TERMINATION_TEMPLATE.md` — 5-section forensic termination packet
- `governance/terminations/SUCCESSOR_ONBOARDING_TEMPLATE.md` — onboarding checklist for the replacement

---

## Reference Implementation

Mise Inc. established the back-to-back repeat rule on 2026-03-28 via founder directive after observing CC Execs who would acknowledge a correction, commit to changing behavior, and then immediately re-commit the same mistake on the next ticket. Mise's institutional version of this rule lives at `docs/brain/032826__back-to-back-repeat-termination-rule.md`. It applies to all CC Execs, Utility, and Scribe roles across Mise's 16-agent fleet.
