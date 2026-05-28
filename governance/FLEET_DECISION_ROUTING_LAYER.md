# Fleet Decision-Routing Layer (FDRL)

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** All governed fleets operating under CC-Suite. Defines the canonical layer connecting decision authority-classification (Tier × EDG) to delivery — which agent answers, with what confidence threshold, via what escalation path.

---

## Core Concept

**Human authority attention is the scarcest resource. FDRL routes decisions agents can resolve from the codebase to the right internal authority, escalating to the human only as a last resort.**

The end state (in the human's own words): *"I want our agents to be able to bounce stuff like that off of each other to arrive at a path forward that makes sense, based on all the data in [the repo], and only if the fleet really can not come up with a confident path forward, do I want to be asked my opinion."*

---

## Why It Exists

A CC-Suite fleet has a comms layer (push channels, durable cursors, queue hydration) and a governance layer (authority hierarchy, Tier × EDG decision classification, OIL, FODL, truth classification rule, Scribe independence).

What it lacks without FDRL is the **decision-routing layer** — the canonical rules and primitives connecting authority-classification to delivery. Without it, agents fall back to either:
1. Asking the human for every Tier B+ decision (overloads human attention)
2. Deciding solo without proper escalation (silent drift)

FDRL provides the structure: agents self-assess confidence, escalate at named thresholds, route via canonical primitives to peers → arbiter → human.

---

## The 5 Components

### Component 1 — Decision-Confidence Protocol

Agents self-report confidence on Tier S/A or EDG-2+ work using a four-level scale:

| Level | Meaning | Default routing |
|-------|---------|----------------|
| **VF — Verified Fact** | File:line evidence, code grep, deterministic answer in repo | Decide solo |
| **MN — Multi-Node Confident** | Multiple sources align, no contradiction found | Decide solo with attestation |
| **FDP — Found Disagreement / Plausible** | Sources disagree OR plausible reasoning, no definitive evidence | Peer-ask (T1) |
| **U — Unknown** | No clear answer in repo | Peer-ask → arbiter-ask → FODL emission |

#### Three thresholds

- **T1 (peer-ask trigger)** — Agent confidence < 80% on conclusion at Tier B+
- **T2 (arbiter-ask trigger)** — Peer agent also < 80% confidence OR cross-lane question
- **T3 (FODL emission trigger)** — Operational arbiter (CoS-equivalent) cannot resolve after (a) at least 3 targeted file searches across the repo's `docs/`, `legal/`, registry directories for precedent, (b) routing-table lookup, AND (c) decision-trail registry query — OR arbiter confidence on ruling is < 70% — OR decision is in an always-human routing-table row

#### Output format

Every agent response on Tier S/A or EDG-2+ work includes a confidence footer:

```
**Confidence:** MN 85% — [evidence pointer]
**Routing:** Solo (above T1).
```

### Component 2 — Inter-Agent Routing Table

Canonical map of decision types → primary lane → escalation path. The routing table has rows like:

| # | Decision type | Primary lane | Escalation path |
|---|---------------|--------------|----------------|
| 1 | Code implementation | Implementation lane | → architecture lane → CoS → human |
| 2 | Architecture decision | Architecture lane | → CoS → human |
| 3 | Verification/V-Loop | Planning lane | → architecture → CoS → human |
| ... | ... | ... | ... |
| **13** | **Canon ratification** | **N/A** | **→ human directly** |
| **14** | **Strike issuance** | **N/A** | **→ human directly** |
| **15** | **Termination initiation** | **N/A** | **→ human directly** |
| **16** | **Glossary addition** | **N/A** | **→ human directly** |
| **19** | **Founder-confirmed data correction** | **N/A** | **→ human directly** |

#### Always-human rows

Rows 13/14/15/16/19 bypass all agent routing — human authority is the only valid resolver. No CoS arbitration, no peer-ask, no agent solo-decide.

### Component 3 — CoS-as-Arbiter Protocol

The operational arbiter role (CoS or equivalent non-exec) exposes an `arbiter_ask` primitive (e.g., `mcp__cos-channel__arbiter_ask` in Claude Code MCP implementations). When agents hit T2, they invoke:

```
arbiter_ask(decision_type, options, evidence, lane_context)
```

CoS responds within 60 minutes during active hours with one of:
- **Ruling** — direct resolution; agent proceeds
- **Redirect** — different lane is primary; CoS routes
- **Needs-data** — request specific evidence before ruling
- **FODL-escalate** — beyond arbiter authority; emit_fodl

### Component 4 — FODL Auto-Emission Primitive

When CoS rules FODL-escalate, the operational arbiter has a primitive (e.g., `mcp__cos-channel__emit_fodl`) that writes the item to FODL with:
- Plain-English question text
- Options enumeration
- Time-sensitivity attribute (emergency / normal / low)
- Routing-table row reference
- Confidence-trail (peer-ask + arbiter-ask history)

The emission also follows the FODL Scope Definition Canon's orchestrator-blocking test.

### Component 5 — OIL Auto-Update from Task-Complete Announcements

A PostToolUse hook on task-complete announcement files (e.g., `cc_execs/announcements/*__task-complete-*.md`) parses the announcement envelope and:
- Updates per-role sub-state in OIL
- Computes composite OIL state (in-progress, blocked, completed)
- Surfaces disputed status (e.g., two agents claiming same OIL item) to CoS arbitration

---

## Authority Order at Runtime

When multiple canons could apply to a decision:

1. **Tier × EDG wins on what-is-this-decision** — the risk-classification determines the discipline level required
2. **FDRL wins on who-answers + how-it-routes** — given the classification, FDRL determines the routing
3. **Role-boundary disputes** — resolved by the master role spec (the document defining each role's scope)
4. **Tier × EDG overrides FDRL routing on approval requirements** — an EDG-3 decision still requires the higher tier's approval gate even if FDRL would route to a lower tier

---

## Confidence-Path vs Scope-Path Parallelism

There are TWO parallel paths to FODL emission:

### Path A — Confidence-based (via T3)
Agent confidence drops, peer can't resolve, CoS can't resolve → T3 → FODL.

### Path B — Scope-based (via orchestrator-blocking test)
The FODL Scope Definition Canon's blocking test fires independently. Even when agent confidence is HIGH (above T1/T2), if the answer is human-only by row (e.g., canon ratification per row 13), the item routes to FODL regardless of confidence.

**Both paths combine into the full emission rule.** Either firing is sufficient.

Path A catches "agent isn't sure." Path B catches "agent IS sure but the answer is human-only."

---

## Routing-Table Evolution

If row 22 (novel / uncategorized question) fires 3+ times on the same question type within a 14-day window, CoS auto-proposes a new routing row to human for canon ratification. Single-instance novel questions stay as FODL emissions.

---

## Decision Trail Registry

Every significant decision logs to `cc_execs/decision_trail/{YYYY-MM}/` (or fleet-equivalent path) with:
- Decision type + routing-table row
- Agent confidence at decision time
- Escalation path traversed (solo / peer / arbiter / human)
- Resolution + reasoning
- Implementing artifact reference

This becomes the corpus that future agents query via "decision-trail registry query" as part of T3 trigger evaluation — past resolutions inform present ones.

---

## Sequential Ratification Enforcement

FDRL canon ratification is BLOCKED until role-split canon is ratified AND Phase 0 checklist steps execute (Scribe verifies). The two canons are co-dependent:
- Role split (Scribe → Scribe + CoS; CCTO → CCTO + CCDE in Mise reference) is Phase 0 prerequisite
- FDRL routing assumes the split exists (CoS lane, separate implementation lane)

---

## Anti-Patterns Rejected by This Canon

1. **Asking the human directly without peer-ask first** — bypasses confidence ladder; wastes human attention
2. **Solo-deciding above T1 threshold** — silent drift; no audit trail
3. **CoS arbiter rulings without classification (ruling / redirect / needs-data / FODL-escalate)** — ambiguous outcomes; agent doesn't know how to proceed
4. **Freeform agent-to-agent decision via Slack DM** — bypasses arbiter-ask primitive; no audit trail
5. **Skipping decision-trail registry log** — kills future agent's ability to look up precedent

---

## Composition

- **FODL Scope Definition** (`FODL_SCOPE_DEFINITION.md`) — Scope-based path to FODL (Path B above)
- **FODL Trigger Protocol** (`FODL_TRIGGER_PROTOCOL.md`) — how FODL is rendered + decisions captured
- **Risk Classification** (`RISK_CLASSIFICATION.md`) — Tier × EDG inputs to FDRL routing
- **Role Split Canon** — Scribe + CoS and CCTO + CCDE split is FDRL Phase 0 prerequisite
- **Mechanism Verification Attestation** — FDRL primitives are substrate; mechanism claims verified at ratification

---

## Reference Implementation

This canon was authored in Mise's `mise-core` repo as `docs/brain/052626__fleet-decision-routing-layer-fdrl-canon.md` (2026-05-26), co-ratified with the role-split canon `docs/brain/052626__role-splits-scribe-cos-and-ccto-ccde-canon.md`. Mise is CC-Suite's reference implementation; the canon was ratified there in active production use. The framework version sanitizes Mise-specific identifiers while preserving the operational substance.
