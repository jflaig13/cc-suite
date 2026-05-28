# Touch Verification Protocol

**Status:** Canonical. Part of CC-Suite™ v2. Third pillar of CC-Suite competitive positioning (alongside strike-and-terminate accountability + mechanism verification attestation).

**Scope:** All governed fleets producing user-facing software products. Defines the structural pipeline for browser-driven verification of every named, scoped, addressable user-facing unit (a "touch") of the product surface.

---

## Core Concept

**The pipeline drives a real browser through every touch in the product's catalog and asserts each renders + behaves per the catalog claim. Synthesized voice/audio input is uploaded through the actual browser file picker — exercising the frontend MIME chain + UX as if a real user.**

This is not unit testing. It is not API integration testing. It is **substrate-level UI behavior verification** that catches the gap between "the API returns correct data" and "the user sees the correct outcome."

---

## Why It Exists

Three classes of bugs that survive unit tests + API integration tests + manual smoke tests:

1. **MIME-chain bugs** — backend works, but the frontend file-upload flow on a specific mobile browser version fails to attach the file (the canonical incident: an iPad Safari MIME bug that lived for weeks before browser-driven verification surfaced it)
2. **State-machine UX gaps** — touches that render but produce wrong outcomes (e.g., a button visually present but with a missing event handler, or a route that 404s only when accessed from a specific authentication state)
3. **Catalog drift** — touches that were spec'd but never shipped; or touches that shipped but were never cataloged; or touches that exist but reference dead routes

Manual touch-by-touch inspection cannot catch these reliably at scale. As product surface area grows (hundreds of touches across multiple module/page combinations), human verification breaks down. The pipeline scales linearly with touch count; humans don't.

---

## The Touch Catalog

The catalog is the canon. The pipeline asserts the catalog's claims.

Every named, scoped, addressable user-facing unit gets a row:

```
| Touch ID | Touch name | Location | Behavior | Cardinality | Expected when | Notes |
|----------|------------|----------|----------|-------------|---------------|-------|
| T-XXX    | "..."      | page/element | what happens | 1 per ... | condition | ... |
```

**Test of fit:** *"Can a user point at this and say 'I tapped that' or 'I use that'?"* If yes, it's a touch.

**NOT touches:** modules (bigger), pages (hosts of touches), flows (sequences), routes/endpoints (implementation), backend helpers (internal), data records (content not affordance).

The catalog is authoritative; the pipeline drives the browser through every catalog entry and asserts each.

---

## Pipeline Architecture

### Browser-driven
Use a real browser (e.g., Playwright async with isolated tenant contexts). Each fire runs in an isolated browser context with test-tenant credentials — never against production data.

### Audio synthesis (when relevant)
When the product accepts voice/audio input, the pipeline synthesizes voice via a text-to-speech service (e.g., ElevenLabs) and uploads the audio through the actual browser file picker. This exercises:
- Frontend MIME-type detection
- File-input event handling
- Backend audio receipt + storage
- ASR pipeline
- Resolver / parser logic

### Test-tenant isolation
The pipeline operates against a TEST_-prefixed tenant or test mode. Every artifact produced (uploaded audio, generated records) gets a TEST_ prefix in its identifier so it is mechanically distinguishable from production data and can be cleanly purged.

### Expectations sidecar
Each touch in the catalog has an `expectations.json` (or equivalent) entry describing:
- Selector (CSS / data-attribute / role-based)
- Expected text / value / attribute
- Expected behavior (click action, navigation, modal open, etc.)
- Optional preview (mockup, copy diff)

The pipeline asserts each expectation. MATCH / MISMATCH per assertion.

---

## Same Mechanical Obligation As Other Verification Pipelines

Per other CC-Suite verification canons (`/verify-shift`, `/verify-inventory` if implemented), the rule is:

**Manual touch-by-touch inspection is prohibited. The agent ENTERS the pipeline.**

If a human says "check this touch" / "verify the X button works" — the agent's first action is to invoke the touch verification pipeline.

There is no judgment latitude on whether to use it. Manual inspection without the pipeline is a protocol violation.

---

## Verification Independence Principle (Composes)

The touch verification pipeline MUST NOT share code with the thing it verifies.

Bugs in shared code are invisible to the MATCH test by construction (same code → same output → always MATCH → bug never fires).

Concrete rule: the touch verification pipeline does NOT import the product's resolver, parser, or business-logic modules. It calls the user-facing endpoints through the browser and asserts the rendered output — never the internal model.

---

## Phase Rollout (Recommended)

### Phase 1 — Proof of concept
- Subset of the catalog (e.g., 20-30 representative touches)
- Single tenant / test mode
- Confirms pipeline mechanics work end-to-end with a structural V-Loop pass

### Phase 2 — Expansion to full catalog
- Every cataloged touch has an `expectations.json` entry
- Pipeline drives all touches per fire
- Cleanup endpoint invocation at end-of-fire to purge TEST_ artifacts

### Phase 3 — Automated cadence
- Daily fire (or per-deploy fire) catches regressions before users do
- Failures route to FODL Class 1 active block if Tier S surface affected

### Phase 4 — Multi-product
- Same pipeline pattern across product modules (e.g., inventory + payroll + admin in a multi-product fleet)
- Per-module touch catalogs unified under fleet-level touch verification

---

## The Three-Pillar Positioning

This canon is the third flagship novelty claim of CC-Suite, alongside:

1. **Strike-and-terminate performance system with real consequences** — RLFA paper (Jan 2025) is closest academic analog; not a productized system before CC-Suite
2. **Mechanism verification attestation, proof of execution vs. fabricated success** — NabaOS (arXiv 2603.10060) is closest analog; not a productized library
3. **Touch-level autonomous verification of every product surface** — this canon. Browser-driven structural pipeline at every user-facing unit, on schedule, no human in the verification loop.

Together: governed multi-agent dev system that catches its own work, holds its own accountability, and verifies its own output.

---

## Anti-Patterns Rejected by This Canon

1. **Manual touch-by-touch inspection** — does not scale; not repeatable; not auditable
2. **Pipeline that imports product logic** — violates verification independence
3. **Pipeline that writes to production data** — TEST_ prefix isolation is mandatory
4. **Catalog drift (touches without expectations entries, or expectations without catalog entries)** — pipeline cannot assert what isn't cataloged
5. **Failures routed to lane lead instead of FODL Class 1 when Tier S surface affected**

---

## Composition

- **Touch Terminology** (`TOUCH_TERMINOLOGY.md` if implemented) — defines "touch" as the class noun
- **Atomic Verification Protocol** (`ATOMIC_VERIFICATION_PROTOCOL.md`) — same EXTRACT → COMPARE → VERDICT structure applied to per-touch verification
- **Verification Independence Principle** — verifier cannot share code with verified
- **Mechanism Verification Attestation** (`MECHANISM_VERIFICATION_ATTESTATION.md`) — pipeline mechanisms attested per substrate canon discipline
- **FODL Scope Definition** — pipeline failures route to FODL Class 1 active block when Tier S surface affected
- **Equilibrium State** (`EQUILIBRIUM_STATE.md`) — touch verification IS Activity #5 (Verifying every touch automatically)

---

## Reference Implementation

This canon was authored in Mise's `mise-core` repo as `docs/brain/052726__verify-touches-architecture.md` (2026-05-27). Mise is CC-Suite's reference implementation; the canon was ratified there in active production use, Phase 1 RESOLVED OPERATIONAL via 20/20 V-Loop CLEAN against a real tenant's inventory module on 2026-05-27. The framework version sanitizes Mise-specific identifiers while preserving the operational substance + the three-pillar positioning.
