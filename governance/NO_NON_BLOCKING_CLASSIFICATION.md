# No "Non-Blocking" Bug Classification

**Status:** Canonical. Part of CC-Suite™ v2. Fleet-wide permanent — stronger than the standard "this is the way" canonization (the originating ratification used "remembered by every agent forever" phrasing).

**Scope:** All governed fleets operating under CC-Suite. Defines what counts as legitimate priority-ordering vs. anti-pattern bug demotion.

---

## Core Concept

**A bug is a bug. If it's a real bug (not user error, not intended behavior, not a design preference), it goes on the active TODO list. Period.**

Stop categorizing real bugs as "non-blocking" — the framing demotes work off the active list, leads to "fix a bunch of other shit, deploy, and the bugs still exist."

---

## Why It Exists

LLM agents (and humans under pressure) instinctively reach for framings that allow them to ship without addressing every known defect:

- "non-blocking"
- "not a [stakeholder]-blocker"
- "nice-to-have"
- "P2/P3" (used as deferral mechanism)
- "follow-up for later" / "open follow-up"
- "out of scope"
- "separate concern"
- "shipping gap" (used to soft-pedal a scoped-but-undelivered feature)
- "we can address that later"

Every one of these is a route to leaving a real bug behind. The originating incident (the canon's provenance): an agent reported a day-step arrow bug as "not a stakeholder-blocker" after shipping three browser-driven fixes; same session demoted 4 transcribe failures + 1 upload failure as "pre-existing, not regressions." The human caught the pattern within minutes. All 5 items added back to the active TODO at ratification.

The cost of these demotions compounds:
- Demoted bug accumulates in shadow lists nobody reviews
- Downstream agents build on top of the buggy surface
- Eventually a user hits the bug and the surface area to recover is larger than if it had been fixed at first detection

---

## The Rule

**Banned framings (DO NOT use to demote a real bug off the active list):**
- "non-blocking"
- "not a [stakeholder]-blocker"
- "nice-to-have"
- "P2/P3" (used as deferral)
- "follow-up for later" / "open follow-up"
- "out of scope"
- "separate concern"
- "shipping gap"
- "we can address that later"

**What IS allowed:**
- **Priority ordering on the active TODO** — all items stay on the list, worked highest-priority first
- **Explicit human-deferred items** tracked in FODL/OIL with stated trigger condition (e.g., "human deferred until X happens; re-surfaces when trigger fires")
- **Closing bugs as "won't fix"** with documented reasoning (e.g., "rendered obsolete by Y change", "design preference confirmed by human", "false positive from misclassification")

The distinction is structural: priority ordering keeps items ON the list; the banned framings remove them.

---

## Self-Audit at Session End

Before emitting a session-close task-complete envelope, every agent re-reads their own messages from the session, compares against the active TODO, and adds back any item they demoted with banned framing.

This is mandatory. Skipping it is itself a Tier B violation.

---

## Enforcement

### Scribe audit duty
- Flag any agent message using banned framings to demote a real bug
- Tier B strike for repeat use
- Tier A × EDG-1 if the demoted bug had Tier S impact (financial / payroll / inventory / customer-facing)

### Pattern detection
- Two same-session demotions by the same agent = formal warning
- Two consecutive sessions with demotions by the same agent = Back-to-Back Repeat Termination class

---

## Composition

- **Engineering Risk Classification** (`RISK_CLASSIFICATION.md`) — Tier × EDG is for SCHEDULING priority, not deletion from the active list
- **Equilibrium State** (`EQUILIBRIUM_STATE.md`) — speed-quality absolute reinforces this canon: speed is never worth a quality defect, including known bugs left behind
- **Task Completion Announcement Canon** — task-complete envelopes must list every open bug in the affected surface, not "blockers-only"
- **Microdecisions Standard** — "later never comes" parent insight from microdecisions
- **Asymptote Protocol** — convergence = zero deltas, including zero "we'll fix it later" deltas
- **FODL Scope Definition** — sister canon: real human-blocking decisions stay on FODL; this canon: real bugs stay on active TODO. Same anti-pattern protection at different layers.

---

## Reference Implementation

This canon was authored in Mise's `mise-core` repo as `docs/brain/052726__no-non-blocking-bug-classification-canon.md` (2026-05-27). Mise is CC-Suite's reference implementation; the canon was ratified there as fleet-wide PERMANENT (stronger than "this is the way" — human verbatim: *"remembered by every agent forever"*). The framework version sanitizes Mise-specific identifiers while preserving the operational substance + the permanence force.
