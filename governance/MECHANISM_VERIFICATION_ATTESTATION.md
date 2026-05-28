# Mechanism Verification Attestation

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** All Tier S substrate canons (those whose correctness depends on a named mechanism behaving as described). Defines the discipline required before ratification + the claim-surface enumeration refinement.

---

## Core Concept

**Before any Tier S substrate canon is ratified, the authoring agent MUST verify each load-bearing mechanism claim against the implementation that provides it — actual code or infrastructure, never another document's prose — AND enumerate the full claim surface before verifying any subset.**

A "substrate canon" is one whose correctness depends on a named mechanism behaving as described — a file exists/is durable, a queue persists, a hook fires, a process runs, a schema holds, an API returns X.

A canon is an action that asserts facts; its mechanism facts must be code-verified with the same rigor as a code change.

---

## Why It Exists

LLMs propagate plausible-sounding claims from one document to another. Without the attestation discipline, a canon authored from another canon's prose can encode false mechanism premises that look verified but never were.

The cost of this drift: substrate canons declared `RESOLVED OPERATIONAL` when only a subset of the claim surface was actually verified. A user attempting to rely on the canon hits the unverified surface and the canon falsifies. Recovery: re-classify, sweep, re-attest, propagate corrections across all composing canons.

The Refinement (post-overclaim incident, 2026-05-24) makes claim-surface enumeration mandatory because verifying one surface is not verifying the canon — the canon implicitly claims a broader surface, and only enumerated surfaces are verified surfaces.

---

## The Scope Test

Before ratifying any canon, ask:

> *"Does this canon's correctness depend on a named file, queue, hook, process, schema, or API behaving as described?"*

- **YES** → attestation required. Record:
  ```
  **Mechanism verified:** "<claim>" — confirmed against `<file>:<symbol>` (<date>)
  ```
- **NO** → note "No mechanism — attestation N/A."

External APIs verify against the integration brain file equivalent (the API expertise documentation) — same logic.

---

## The Claim-Surface Enumeration Refinement (Binding)

Before verifying ANY subset of a canon's mechanism claims, the authoring agent MUST enumerate the FULL claim surface that the canon's correctness depends on.

### Why this matters

A canon that says *"the durable inbox preserves events across restarts"* has multiple implicit surfaces:
- Cold-start hydration
- Warm-fork reception
- Restart recovery
- Multi-fork race handling
- ACK round-trip
- Failure modes (queue corruption, host loss, network partition)

Verifying one surface (e.g., cold-start hydration) and declaring the canon `RESOLVED OPERATIONAL` overclaims. The canon's text implies all six surfaces; only one was tested. A user encountering any of the other five hits the unverified surface and the canon falsifies.

### The 5 binding refinement rules

1. **Enumerate the full claim surface BEFORE verifying any subset.** Write down every surface the canon's correctness depends on. Treat the enumeration as part of the canon, not as preparation for it.

2. **Verify each enumerated surface INDEPENDENTLY.** Each surface gets its own evidence trail. One surface verifying does not transfer to others.

3. **`RESOLVED OPERATIONAL` is reserved for the FULL enumerated surface.** Partial verification → `PARTIALLY RESOLVED` with explicit per-surface unresolved notes.

4. **Empirical falsification by a working agent is SUFFICIENT EVIDENCE to re-open any prior `RESOLVED OPERATIONAL` attestation that covers the falsified surface.** The re-open is automatic — no canon-amendment vote needed.

5. **Scribe MUST refuse to ratify any substrate canon whose mechanism claims lack enumerated surfaces.** Ratifying a canon with implicit / unenumerated surfaces is itself a Type A violation.

---

## Attestation Format (Required Header)

For substrate canons, the brain-file header MUST include:

```markdown
**Mechanism verified:**
- <Surface 1 claim> — confirmed via <file>:<symbol> (<date>)
- <Surface 2 claim> — confirmed via <file>:<symbol> OR PENDING <reason> (<date>)
- <Surface 3 claim> — confirmed via <file>:<symbol> (<date>)
- ...
```

Each enumerated surface must either be:
- **CONFIRMED** with `file:symbol` reference + verification date
- **PENDING** with explicit gap description + escalation path

A canon with **PENDING** surfaces ratifies as `PARTIALLY RESOLVED`, not `RESOLVED OPERATIONAL`.

---

## Scribe's Enforcement Duty

The attestation check is a mandatory precondition of the Canon Drift Protocol ratification sweep. Scribe re-reads the cited code — attestation is itself verified, not taken on faith.

### At ratification time

- Scribe reads the canon's claim-surface enumeration
- For each CONFIRMED surface, Scribe opens the cited `file:symbol` and confirms the behavior described is actually implemented
- For each PENDING surface, Scribe confirms the gap description is accurate and the escalation path is real
- If any CONFIRMED surface does not actually match its code, Scribe REFUSES ratification + flags the claim as Tier A misrepresentation

### Post-ratification

- If empirical falsification surfaces (a working agent reports the canon does not hold), Scribe re-opens the canon, downgrades to `PARTIALLY RESOLVED`, surfaces to human authority + originating agent for fix
- The originating agent (the one who attested falsely) gets a Type A misrepresentation entry in their performance log if the falsity is traceable to skipped surface enumeration

---

## Examples (from production use)

### Good attestation
```markdown
**Mechanism verified:**
- OIL ledger `docs/ledger/OPEN_ITEMS_LEDGER.md` exists + persists across sessions — confirmed via filesystem read 2026-05-27 23:08 EDT
- MAGI reasoning protocol — confirmed via `docs/brain/041726__magi-canon.md`
- Touch verification Phase 1 substrate RESOLVED OPERATIONAL — confirmed via test ledger `cc_execs/handoffs/audit_sam-touch-catalog_20260527T065000Z/` showing 20/20 V-Loop CLEAN
- Dream Cycle nightly maintenance — PENDING (Mini host offline since 2026-05-18 per human ack); activates on Mini return
```

### Bad attestation (rejected)
```markdown
**Mechanism verified:** the durable inbox works.
```

(Single sentence, no enumeration, no surfaces, no `file:symbol` references. Scribe refuses.)

---

## Anti-Patterns Rejected by This Canon

1. **Lifting mechanism claims from another canon's prose** — every claim verifies against its actual code, not against another document's claim about its code.
2. **"It worked when I tested it"** — without `file:symbol` reference + date, the attestation is not verifiable later.
3. **Declaring `RESOLVED OPERATIONAL` after testing one surface** — see the refinement rules. Verify enumerated surface in full.
4. **Treating empirical falsification as a one-off** — falsification re-opens the canon automatically; no negotiation.
5. **Skipping the enumeration step "because the surface is obvious"** — the enumeration is the canon, not preparation for it.

---

## Composition

- **Canon Drift Protocol** — mechanism attestation check is mandatory precondition of every Canon Drift Protocol ratification sweep.
- **Atomic Verification Protocol** — same discipline (claim → evidence) applied to verification work; this canon applies it to canon authoring.
- **Search-First Protocol** — Scribe verifies attestations against code, not against memory or assumption.
- **Risk Classification** — Tier S substrate canons get this discipline; lower-tier canons don't need full attestation.
- **FODL Scope Definition** — failed attestation → FODL emission for human ratification of canon downgrade.

---

## Reference Implementation

This canon was authored in Mise's `mise-core` repo as `docs/brain/051726__mechanism-verification-attestation-canon.md` (2026-05-17) + `docs/brain/052426__mechanism-attestation-refinement-after-may18-overclaim.md` (2026-05-24 refinement). Mise is CC-Suite's reference implementation; the canon was ratified there in active production use, refined after a real overclaim incident (a canon was declared `RESOLVED OPERATIONAL` on 2026-05-18 with three signatures and falsified by three independent working agents within 6 days). The framework version sanitizes Mise-specific identifiers while preserving the operational substance + the refinement's binding force.
