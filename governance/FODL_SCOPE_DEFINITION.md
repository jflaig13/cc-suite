# FODL Scope Definition

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** All governed fleets operating under CC-Suite. Defines what belongs on the Founder Open-Decision List (FODL) and the orchestrator's continuous-sweep duty to prevent silent blocks.

---

## Core Concept

**FODL consists of every human-authority decision that IS or WILL be a block on the orchestrator's ability to advance the fleet.**

If human input gates downstream work, it lands on FODL. No exceptions.

The complement: anything NOT blocking goes on other surfaces (OIL execution lanes, performance logs, lane sweep memos, status updates) — see "What Does Not Belong" below.

---

## Why It Exists

The highest-cost orchestration failure is the **silent block**: an agent asks a peer, can't resolve, drafts a memo to the human, memo dies in a directory, nothing on FODL, human never sees it, fleet halts in some lane, human catches days late.

Cost grows super-linearly with time-to-detection because downstream lanes accumulate their own blocked sub-states. Recovery isn't "make the decision today" — it's "make the decision today + cascade through N downstream un-blockings + restore lane sequencing."

This canon makes invisibility structurally impossible — every named source is scanned every sweep cycle. Items can be on FODL prematurely (human dismisses them); they CANNOT be off FODL when they should be on.

---

## The Orchestrator-Blocking Test

One question, asked at routing time when ANY potentially-decision-bearing surface emerges (peer-ask, memo, channel event, conversation, sweep-detected stuck):

> **"Is this something the orchestrator (Scribe today, CoS for operational arbitration) cannot proceed past without human authority input?"**

- **YES** → FODL. Add an item with plain-English title + `what you're deciding` + `why it matters` + options + agent's recommendation.
- **NO** → other surface.
- **AMBIGUOUS** → default to FODL. Over-inclusion is recoverable; under-inclusion creates silent block.

---

## Three Classes of Blocks (All Belong on FODL)

### Class 1 — Active block
Fleet stuck right now in some lane awaiting human input. Highest urgency. Decay-class = days or hours.

*Example:* "Implementation lane cannot ship API schema change without ratifying whether to break v1 or add v2/ namespace."

### Class 2 — Conditional block
Will become active when condition X fires. Tracked NOW so it doesn't surface as a fresh emergency LATER. Decay-class = weeks; trigger-conditioned activation.

*Example:* "Human approval needed when regulator acknowledgment PDF arrives — disposition of confirmation number + cap-table update."

### Class 3 — Pre-emptive block
Human-only authority required BEFORE a Tier S action can execute. Always pre-flight, never post-hoc. Includes:
- Terminations (only human initiates per termination registry rule)
- Canon ratifications above governance role authority
- Financial commitments above CFO-equivalent authority threshold
- Strike issuance
- Any irreversible Tier S × EDG-3+ deploy with multi-tenant blast radius

*Example:* "Wire transfer request to vendor exceeds CFO-equivalent solo-authorize ceiling, requires human approval before send."

---

## What Does NOT Belong on FODL (Hard Exclusions)

These go on other surfaces, NEVER FODL:

1. **Decisions Scribe can make within canon authority** — Scribe-decided items appear in a separate "decided last 7 days" bucket for human override visibility only, NOT as open decisions. They are NOT asked.
2. **Decisions inside an agent's lane authority** — handled per the inter-agent routing table; if the agent can decide solo within their lane × Tier × EDG cell, they decide. No FODL emission.
3. **Status updates / situational awareness items** — go in daily roll-ups, channel posts, memos. NOT FODL.
4. **Mid-flight execution checkpoints** — not decisions. Track in task list / OIL execution status.
5. **Items the human has already decided but Scribe hasn't applied yet** — these are EXECUTION work. Track in task list with already-decided status; remove from FODL once the original decision is logged + closed.
6. **Things merely "worth human awareness"** — if it doesn't gate any lane, it goes in the roll-up's situational-awareness section or a lane surfacing memo — NOT FODL.
7. **"It would be nice if the human weighed in"** — if no lane is blocked, this is not a FODL item.
8. **OIL items with human-decision sub-state** — cross-reference, never duplicate. The FODL entry is what gets shown to human; the OIL entry is the execution shell.

---

## Scribe's Continuous Sweep Duty (Load-Bearing)

This is the mechanism that prevents the silent-block failure mode. Scribe has a STANDING OBLIGATION to detect + route every human-blocking decision to FODL.

### 9 canonical sweep sources

1. **OIL ledger** — items with `awaiting-human` flag, `human-blocker` annotation, or status field naming the human authority
2. **Channel queues** — any peer-ask that escalated past T1 (peer threshold) AND T2 (CoS arbiter threshold) without resolution
3. **Memo directories** — files with `human-approval-needed` / `awaiting-human` / `FODL-candidate` markers OR addressed `TO: <Human Authority>` without a `task-complete-*` resolution
4. **Performance logs** — entries flagged for human review (strike adjudication, calibration sign-off, lane re-scope)
5. **Termination-initiation events** — per termination registry rule, only human initiates; Scribe surfaces any pending termination consideration as FODL
6. **Canon-ratification requests** — any "this is the way" / canon-shift / canon-correction needing human ratification beyond Scribe's solo canon authority
7. **Ad-hoc human-decision-bearing intent** — direct messages, in-person conversation notes, screen-recording transcripts, text where human asked an agent to "think about X and bring it back" — if it gates a lane, it goes on FODL
8. **CoS-as-Arbiter rulings** — when CoS resolves arbiter-ask with `FODL-escalate` verdict, the item lands on FODL automatically via `emit_fodl` primitive
9. **FDRL T3 emissions** — when an agent's decision-confidence drops below T3 threshold, FDRL ratchets to FODL emission

### Sweep cadence (binding)

- **Continuous (real-time)** — when a block surfaces via channel event, memo, or active session work, Scribe routes within the response cycle (no batching to next morning)
- **Mandatory at session start** — before any other work, Scribe reads the FODL file + scans the 9 sweep sources for items missing from FODL. Detected misses get added immediately.
- **Mandatory in every daily roll-up** — explicit step "FODL completeness check: did anything block today that should be on FODL but isn't?" Documented disposition per detected miss.
- **At every `fodl` trigger fire** — Scribe runs a quick (≤60-second) freshness pass on the 9 sources before rendering, catching any block that emerged since last sweep.
- **At every Tier 1+ audit-subatomic invocation** — FODL completeness becomes an audit dimension.

### What "routing to FODL" means concretely

1. Append a new item to the FODL file with full internal schema (Source / Classification: Tier × EDG / Status / Reversibility / Decay / Blast radius / Ask / Options / Reasoning / Ruled out / Default / Related). Schema stays in the file per Plain-English Render Canon.
2. Map to the parent canon source (e.g., OIL cross-reference, memo path, channel event_id, conversation timestamp)
3. Stage agent's MAGI recommendation in the `Options` + `Default` fields
4. Surface to human authority either (a) immediately if active block + Tier S, (b) at next `fodl` trigger fire if non-emergency, (c) at next daily roll-up if conditional/pre-emptive
5. If a `task-complete-*` for the originating work depends on the FODL resolution, the originating agent's announcement carries `Blockers: FODL-<ID>` until human decides

---

## The Silent-Block Failure Mode This Canon Prevents

The canonical anti-pattern:

```
Agent asks a peer → can't resolve → memo dies in directory →
nothing on FODL → human never sees → fleet halts in some lane →
human catches days late → cost compounds super-linearly.
```

This canon's binding effect: Scribe's continuous-sweep duty makes invisibility structurally impossible. If a block exists in any of the 9 sources, it surfaces. Scribe accountability.

---

## Enforcement

### Scribe (non-exec governance role)
- A silent block discovered post-hoc (human finds a stuck item NOT on FODL) triggers an immediate Scribe self-audit
- Pattern (≥2 silent-block discoveries within a 14-day window) → human ratifies whether stand-down is appropriate
- Scribe's daily roll-up explicitly cites the FODL completeness check pass (positive evidence, not absence)

### Subject-to-strike roles (executive lanes)
- An agent who lets a human-blocking decision die in a memo without escalating via channel / CoS arbiter / direct FODL emission → Tier B negligence (Type C strike)
- Pattern → Back-to-Back Repeat Termination class
- The agent's standing obligation: when peer-ask or CoS arbitration cannot resolve a human-decision dependency, the agent MUST trigger FODL emission

### Operational arbiter (CoS-equivalent)
- Arbiter-ask resolution must classify FODL-escalate explicitly when applicable; failure to emit when warranted → human-ratified review

---

## Anti-Patterns Rejected by This Canon

1. **"Not urgent, I'll mention it next time"** — if it blocks the orchestrator, it goes on FODL NOW.
2. **"The human already knows about this"** — knowing isn't deciding. Track in FODL until disposition is logged.
3. **"This is too small for FODL"** — orchestrator-blocking test, not effort-to-decide.
4. **"This will resolve itself"** — if it actively blocks a lane, it does NOT resolve itself.
5. **"FODL is for big strategic stuff"** — FODL is for blocking decisions of every Tier × EDG. The classification goes in the audit schema; the render is plain English regardless.
6. **"Human said hold on this; doesn't need to be on FODL anymore"** — `hold` is a status, not a removal. Item stays on FODL with status `held-by-human` until human closes it.
7. **"I'll add it after I check with [other agent]"** — checking and adding can happen in parallel. Surface to FODL first; let parallel verification refine the entry.
8. **"It's in OIL, that's enough"** — OIL is execution state. If the item has a human-decision sub-state that is actively blocking, it ALSO goes on FODL with cross-reference.

---

## Composition

- **FODL Trigger Protocol** (`FODL_TRIGGER_PROTOCOL.md`) — `fodl` trigger mechanics (HOW the FODL is rendered + decisions captured). This canon defines WHAT belongs.
- **Fleet Decision-Routing Layer** (`FLEET_DECISION_ROUTING_LAYER.md`) — FDRL T3 confidence threshold → FODL emission. FDRL says WHEN to emit via confidence-based path; this canon defines the SCOPE criterion via orchestrator-blocking test. Both paths combine into full emission rule.
- **Equilibrium State** (`EQUILIBRIUM_STATE.md`) — this canon handles BLOCKERS; equilibrium canon handles everything else. Together they cover the full fleet operating posture.
- **No Non-Blocking Classification** — sister anti-pattern: real bugs stay on active TODO; this canon: real human-blocking decisions stay on FODL.
- **Source-Text Verification** — verify the block IS a block before routing; don't over-escalate on unverified claims.
- **OIL Backfill** — OIL is execution-state sibling; FODL is decision-pending sibling.

---

## Reference Implementation

This canon was authored in Mise's `mise-core` repo as `docs/brain/052726__fodl-scope-definition-canon.md` (2026-05-27). Mise is CC-Suite's reference implementation; the canon was ratified there in active production use. The framework version sanitizes Mise-specific identifiers while preserving the operational substance.
