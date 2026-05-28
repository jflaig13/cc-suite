# Source-Text Verification Discipline

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** All agents in all governed fleets. Applies to regulatory, external, cross-host, and any "X said Y" claims. Defines the discipline required BEFORE downstream routing of such claims.

---

## Core Concept

**Before routing any downstream-actionable claim that originates from a non-authoritative source (a digest summary, a cross-agent surface, a conversational mention, an online article, a third-party AI's analysis), verify the claim against the AUTHORITATIVE source first. Then route.**

The principle: the surface where you heard the claim is NOT authoritative for downstream propagation. The original primary source is.

---

## Why It Exists

LLM agents propagate claims with chain-of-trust faster than they verify them. The originating incident pattern:

1. Agent A reports a regulatory deadline based on a digest
2. Agent B (Scribe) routes the deadline to Agent C for action
3. Agent C escalates to the human as "TIME-SENSITIVE Tier-S deadline"
4. Human pushes back: "I don't know anything about that regulation"
5. Verification (after the push-back) reveals: the regulation doesn't apply / has a different effective date / has an exemption that covers the entity / was never authoritative
6. Chain has to retract; all downstream consumers update their state

The fix: insert verification BEFORE downstream routing, at every layer.

Without this discipline, low-grade claims become high-grade actions on contact with downstream agents who trust the chain.

---

## Authoritative Source Examples

- **Regulatory facts** → statute text or agency portal (e.g., FinCEN.gov for federal financial reporting; federalregister.gov for federal rules; applicable state regulator portals)
- **Git state** → `git rev-parse` on the host that owns the repo (per Monitoring-Chain Integrity Canon)
- **Production state** → live app / Cloud Run / GCS read (the system itself, not a sensor's stale snapshot)
- **Human authority intent** → direct human message or memory entry quoting human verbatim (not "Agent X said the human said")
- **Statute interpretation** → primary text + scope analysis, not a research-agent digest line citing "effective date X"
- **Product fact** → grep the code, not a brain file saying "X is implemented"
- **API behavior** → API docs + integration brain file, not "an agent told me the API returns Y"

---

## The Discipline

### At source (where the claim first emerges)
If an agent's surface (digest, sweep memo, brief, conversational summary) contains a regulatory / external / cross-host claim, that surface IS the source for THAT agent — but it is NOT authoritative for downstream propagation.

### At Scribe routing point
Before relaying to the human OR to another agent's lane, ask:

> *"Is this claim verified against the authoritative source? If not, what's the 30-second check I can do to verify?"*

### 30-second checks worth doing before escalation
- WebFetch or WebSearch the agency portal / statute text
- `git log` for a code claim
- `gcloud run revisions list` for a deploy claim
- GCS / S3 read for a tenant-data claim
- Read the human's actual prior message rather than relying on "Agent X said human said"

### At human-facing escalation
If you haven't verified, do NOT escalate as "Tier-S deadline" or "human action required." Frame as:

> "Agent X surfaced this claim; not yet verified against authoritative source; recommend a 30-second check before escalation."

---

## The Three-Failure Originating Incident (Production-Grounded)

The discipline was ratified after three same-class propagation failures in a single agent session (the Mise reference implementation, 2026-05-27):

1. **State AI regulation:** A sub-agent's digest line said "regulation X effective 2026-06-03." Scribe routed to legal lane without statute-text verification. Real statute was effective months earlier + the company had zero operations in that state + the company's product was out of statute scope. Human pushed back; sub-agent self-audited + reclassified NOT APPLICABLE.

2. **Pilot agreement signing:** A research agent's surface assumed "sign pre-event" sequencing. Scribe routed to legal lane without verifying human intent. Human caught it: "I did not ask the customer to sign; I am planning to do it on event-day, after the event completes." Scribe relayed correction.

3. **Federal compliance deadline:** A legal brief said "10-minute portal check ahead of deadline X." Scribe escalated to human as "TIME-SENSITIVE Tier-S deadline tomorrow." Human pushed back: "I thought we didn't have to do this." Scribe web-verified the agency guidance, confirmed entities of this type are exempt under a recent interim final rule, closed as `no-action`.

In all three: the human was right + Scribe propagated without verifying. Same class.

The discipline ratification covers all three vectors + future analogous incidents.

---

## How to Apply (Operational Checklist)

When you encounter a claim that could route downstream:

1. **Identify the claim's authority class** — regulatory / external / cross-host / "X said Y"
2. **Identify the authoritative source** — agency portal, statute text, code, host of subject, human's actual verbatim
3. **Choose a 30-second check** — WebFetch, grep, log read, primary-text read
4. **Run the check**
5. **Update the claim with verification status** — `(verified <date> via <source>)` or `(unverified; routing as candidate)`
6. **Route with explicit status**

The verification is fast; the discipline is the order — VERIFY then ROUTE, never ROUTE then VERIFY.

---

## Parallel Commitments (Multi-Lane Discipline)

When the discipline is ratified, multiple lanes commit:

- **Research lane** — regulatory dates + external statute facts require source-text verification before any classification or human-facing surfacing
- **Legal lane** — future regulatory briefs get source-text web-verification against current agency guidance BEFORE shipping; don't punt verification to human when the regulator portal IS the primary source
- **Scribe (governance role)** — same discipline at the routing layer

---

## Strike-Class Implication

For subject-to-strike roles (executive lanes):
- **First same-class propagation failure** = concern entry in performance log (not strike-level unless human explicitly issues)
- **Pattern repetition (back-to-back same class)** → triggers Back-to-Back Repeat Termination class

For non-exec roles (Scribe, CoS):
- Self-monitor via daily roll-up retrospective
- Surface own pattern repetition to human if it emerges

---

## Trigger Phrases That Should Fire This Discipline

- "Per [agent X]'s surface..." → verify before routing
- "Research/Legal/Verification agent said..." → check the underlying source, not just the agent claim
- "Mini reported..." / "Cross-host sensor flagged..." → check ground truth on the host that owns the subject
- "Tomorrow's deadline" / "TIME-SENSITIVE" → check the actual deadline source before human escalation
- "An article said..." / "Third-party AI said..." → verify against primary source

---

## Sub-Canon Relationship

This discipline is a sub-canon of the API expertise canon for regulatory APIs: agents need expert-level understanding of every regulator's portal/guidance the same way agents need expert understanding of every product API. Same discipline, different surface.

---

## Composition

- **Monitoring-Chain Integrity** (`MONITORING_CHAIN_INTEGRITY.md`) — same principle, extended scope to regulatory + external facts (not just cross-host sensors)
- **Mechanism Verification Attestation Refinement** — claim-surface enumeration applied to canon mechanism claims; this canon applies the same logic to regulatory / external claims
- **Plan Mechanism Claims Code-Verified** — subagent's "X works" is NOT attestation; re-read cited file:symbol before any claim enters a plan
- **Agent Self-Verification Pattern** — receiving a memo? Check git log + code before acting. Code is truth; memos are advisory.

---

## Reference Implementation

This canon was authored in Mise's `mise-core` repo as a memory entry `feedback_source_text_verification_discipline.md` (2026-05-27) after three same-class failures in one session. Mise is CC-Suite's reference implementation; the discipline was ratified there in active production use across multiple lanes simultaneously (research / legal / governance roles all committed to the discipline in the same ratification turn). The framework version sanitizes Mise-specific identifiers while preserving the operational substance.
