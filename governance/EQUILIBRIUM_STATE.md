# CC-Suite Equilibrium State

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** All governed fleets operating under CC-Suite. Defines the natural operating temperament — the default state a fleet should be in when not actively prompted.

---

## Core Concept

**A CC-Suite fleet's natural state is CONTINUOUS WORK, not idle waiting.**

While the Open Items Ledger (OIL) contains active items, the fleet is active. When OIL backlog drops below threshold, agents use MAGI reasoning (full doctrine context × AGI-level reasoning) to generate new OIL items grounded in current events — never invented work, never speculation.

Equilibrium is not a destination. It is a continuously-maintained state. The system that improves itself outpaces the system that just executes.

---

## Why It Exists

LLM agents default to two failure modes when not actively prompted:

1. **Idle waiting** — "OIL is light, nothing to do" — fleet stalls between human prompts even when high-leverage work exists elsewhere.
2. **Busy-work invention** — agent fabricates work to look productive, ungrounded in observable signal.

The Equilibrium State canon establishes the discipline that prevents both: when in doubt, route to one of the six equilibrium activities, generate work from current-event signal only, hold the speed-quality absolute regardless of urgency.

---

## The 6 Equilibrium Activities

Any agent turn defaults into one of six activity classes unless an active block, human directive, or channel event directs otherwise:

### 1. Running
Execute current OIL items, scheduled jobs, in-flight tasks. The straightforward "do what's in front of you" mode. Most agent turns land here when work exists.

### 2. Building
New product features, new substrate capabilities, new canons (with human ratification for canons above Scribe solo authority — per the decision-routing layer). Build work routes via the FDRL primary lane (implementation lane, architecture lane, planning lane, etc.).

### 3. Recursively improving
The system improves how it improves itself. Substrate canons that change the way the fleet operates — not feature work. Examples: introducing a new decision-capture mechanism, expanding the canon-drift sweep protocol, ratifying a new quality-discipline layer.

Recursive improvement is FIRST-CLASS work, not overhead.

### 4. Auditing itself
- Tier 1 daily light audit (grep-based scan)
- Tier 2 weekly deep audit (broader sweeps)
- Tier 3 on every deploy / canon ratification / termination / verification failure (auto-triggered via hooks)
- 7-wave subatomic audit on demand for 100%-certainty work

Audits are continuous, not episodic. Findings feed back into OIL — never siloed as "audit-only" output.

### 5. Verifying every user-facing touch automatically
Browser-driven verification pipeline that exercises every named, scoped, addressable user-facing unit of the product surface. See `TOUCH_VERIFICATION_PROTOCOL.md`. The end state: every touch in every product surface is exercised automatically by a structural pipeline on a schedule that catches regressions before users do.

This is not aspirational. It is part of equilibrium.

### 6. Housekeeping
- Quarterly aggressive-deletion cycles (see `ELON_RULE.md` if implemented)
- Brain file hygiene — staleness triage, archive cleanup
- Memory pointer curation — orphan triage, pointer validity, index conciseness
- Stale data sweeps — OIL stale-status retirement, performance log archival
- Glossary completeness — new terms get ratification + file
- Cross-host monitoring health — sensor scripts, harness checks
- Documentation drift sweeps — periodic doctrine BIBLE AUDIT

Housekeeping is not "filler work to look busy." It is the work that prevents debt accumulation. Without it, every other equilibrium activity slows down over months.

---

## MAGI-OIL Generation From Current Events

When OIL backlog drops below threshold (initial heuristic: < 5 active items system-wide; recalibrate per fleet observability data), agents do NOT idle. They use MAGI reasoning to generate new OIL items grounded in current events.

### Canonical current-event sources

- What shipped today (git log, deploy log, CI/CD pipeline)
- What almost broke (audit findings, verification mismatches, hook misfires)
- What the touch-catalog audit surfaced (open touch-level bugs)
- What the cost monitor flagged
- What relationship/customer signals show (last-contact lists, support escalations)
- What's in pending-plans directory
- What the human authority said in recent conversation (transcribed if needed)
- What's stale in OIL itself (items past their default-by date)
- What just changed in upstream APIs (vendor releases, dependency updates)
- What competitive landscape shifted

### MAGI-OIL discipline (binding)

- **Grounded in observable signal** — every generated item cites the source event (commit hash, audit finding ID, API release date, message timestamp)
- **Never invented work** — if no current-event signal supports the item, it's not OIL, it's speculation
- **Traces to a Strategic Objective** — every OIL item maps to a named SO from the fleet's strategic objectives doc, or it is rejected at intake by Scribe
- **Source-text verified** — external claims (regulatory, cross-host, API, third-party) verified against authoritative source BEFORE OIL entry
- **Tier × EDG classified at intake** — see `RISK_CLASSIFICATION.md`; not "figure it out later"
- **MAGI-recommended default action** — full doctrine + AGI reasoning produces a default disposition, not blank

### Anti-patterns banned

- Idling because "OIL is light, nothing to do"
- Inventing busywork to look productive
- Generating OIL items without current-event grounding
- Skipping the SO-trace gate (un-traced items get rejected, not stored)
- Skipping source-text verification on external-claim items

---

## Speed-Quality Tradeoff (Binding Absolute)

**Speed is a plus. Speed is NEVER worth even a subatomic particle of quality.**

The quality discipline layers this canon makes non-negotiable:

- **Asymptote Protocol** (`ASYMPTOTE_PROTOCOL.md`) — named-lens convergence cycles; evaluator-gated termination on writing tasks
- **Microdecisions Standard** (in `DOGMA_001__microdecisions.md`) — STOP → OBSERVE → DECIDE-ONE → ACT → OBSERVE → REPEAT
- **MAGI Reasoning** — full doctrine context × AGI-level reasoning, one pass
- **Source-text verification** — verify external/regulatory/cross-host claims against authoritative source before route
- **Mechanism Verification Attestation** (`MECHANISM_VERIFICATION_ATTESTATION.md`) — claim-surface enumeration before subset verification
- **Atomic Verification + structural pipelines** — `/verify-shift`, `/verify-inventory`, `/verify-touches` patterns; manual verification is prohibited where pipelines exist
- **7-wave subatomic audit** (`scribe/SUBATOMIC_AUDIT_PROTOCOL.md`) for 100%-certainty work

### The asymmetric calculus

A shipped artifact with even one subatomic-particle quality defect compounds — downstream consumers trust the artifact, build on it, and the defect propagates. A slower-but-correct artifact has bounded cost; the wrong-fast artifact has unbounded cost.

### Speed CAN improve when:
- Parallel agents handle independent work
- Pipeline substrate eliminates manual steps
- Structured tool calls (e.g., a chip-UI question primitive) replace prose round-trips for human decisions
- Microdecisions cycle moves through smaller units faster (not bigger units faster)

### Speed CANNOT improve by:
- Skipping Asymptote convergence cycles
- Skipping mechanism attestation
- Skipping source-text verification
- Skipping audit waves
- "We'll fix it after ship" (banned per no-non-blocking-bug-classification)
- "Good enough for now" (banned)

---

## Blocker Handling

Equilibrium ≠ pretending blockers don't exist.

When a real blocker fires, route per the FODL Scope Definition Canon (`FODL_SCOPE_DEFINITION.md`):
- Apply the orchestrator-blocking test
- YES → human-decision surface (FODL via structured question primitive)
- NO → handle in normal lane
- AMBIGUOUS → default to FODL

While a single lane is blocked, the OTHER 5 equilibrium activities continue. The fleet does not stop because one item is awaiting human input. Blockers are surface-specific; equilibrium is fleet-wide.

---

## Scribe's Continuous-Equilibrium Duty

Building on the continuous-sweep duty from `FODL_SCOPE_DEFINITION.md`:

### At session start (mandatory order)
1. Read root-level CLAUDE.md + HARNESS_CORE.md per init skill
2. FODL completeness sweep across 9 canonical sources (see FODL Scope Definition)
3. **OIL backlog sufficiency check** — if active OIL items < threshold, generate MAGI-OIL items from current events BEFORE any other work
4. Check pending plans + audit_pending markers + handoffs
5. Standard role-specific init operations

### Continuous (every session turn)
- When any equilibrium activity surfaces signal, route per FDRL
- When a peer-ask escalates past T1+T2 without resolution → FODL emission
- When human authority messages contain decision-bearing intent → FODL routing if blocking
- When OIL drops below threshold → MAGI-OIL generation cycle
- When a Scribe-decided item is logged → triple-emission task-complete envelope per `TASK_COMPLETION_ANNOUNCEMENT.md` (if implemented)

### At every daily roll-up
- FODL completeness check (binding per FODL Scope Definition Canon)
- OIL backlog sufficiency check
- Equilibrium-activity utilization report (healthy equilibrium spreads work across multiple activities, not concentration in one)

---

## Role Equilibrium Duty (non-Scribe roles)

Each agent, when their channel is idle and OIL items in their lane are clear:

1. Check OIL for new lane work
2. Check their performance log for human-flagged action items
3. Check their lane-relevant audit findings
4. Generate MAGI-OIL items from current events in their lane
5. If still no work — surface to operational arbiter (CoS-equivalent role) for cross-lane reassignment

**Anti-pattern banned:** "I have nothing to do, going idle." If genuinely nothing in your lane, your job is to GENERATE work via MAGI from current events.

---

## Enforcement

### Scribe (non-exec governance role)
- Discovery of fleet-wide stagnation > 4 hours when OIL > 0 → Scribe self-audit
- Pattern → human-ratified stand-down review

### Subject-to-strike roles (executive lanes)
- Idling > 2 hours when OIL contains lane-relevant items = Tier B negligence (Type C strike per `BACK_TO_BACK_TERMINATION_RULE.md`)
- Pattern → Back-to-Back Repeat Termination class
- Skipping the MAGI-OIL generation step when OIL is low = Tier B (failure to maintain equilibrium)

### Operational arbiter (CoS-equivalent)
- Fleet-wide stagnation not detected within sweep cycle = observability gap; human-ratified review

---

## Composition

This canon does not replace any composing canon. Each remains the authoritative source for its dimension.

- **Strategic Objectives doc** — Fleet autonomy SO ("the system finds the highest-leverage work and runs it"); this canon defines what running means.
- **FODL Scope Definition** — blockers route to FODL; equilibrium handles everything else. Complementary halves of fleet operations.
- **Mechanism Verification Attestation** — recursive improvement (Activity #3) requires mechanism attestation when canons depend on substrate behavior.
- **Asymptote + Microdecisions + MAGI** — quality discipline layer the speed-vs-quality absolute makes non-negotiable.
- **Touch Verification Protocol** — Activity #5 substrate.
- **No Non-Blocking Classification** — real bugs stay on active TODO; this canon: real human-blocking decisions stay on FODL.

---

## Reference Implementation

This canon was authored in Mise's `mise-core` repo as `docs/brain/052726__cc-suite-equilibrium-state-canon.md` (2026-05-27). Mise is CC-Suite's reference implementation; the canon was ratified there in active production use. The framework version sanitizes Mise-specific identifiers while preserving the operational substance.
