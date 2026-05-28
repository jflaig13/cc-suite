# Touch Terminology

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** All governed fleets producing user-facing software products. Defines the class noun "touch" for the unit of product-surface verification + documentation.

---

## Core Concept

**"Touch" is the class noun for any named, scoped, addressable, user-facing unit of software functionality below the module/page level.**

A touch is to the product UX what a node is to a graph — discrete addressable unit. The difference: nodes are topological while touches are intentional (every touch has a verb in its essence).

---

## Why It Exists

A growing product surface needs a class noun for the small-feature unit, otherwise every conversation defaults to inconsistent fallbacks:

- "the X feature"
- "the Y button"
- "the Z widget"
- "the W thing"
- "the touchpoint" (marketing jargon — wrong layer)
- "the component" (implementation-flavored)
- "the microservice" (wrong layer — backend, not user-facing)
- "the tile" (dashboard-flavored)

Without a class noun, audits drift, bug reports lose specificity, design conversations stall on disambiguation, onboarding becomes harder, outreach copy becomes vague.

With "touch" canonized: audits sharper, bug reports clearer, design conversations faster, onboarding teachable, outreach copy concrete.

---

## Five-Point Definition

A touch is:

1. **Named** — has a phrase users read (e.g., "Search catalog", "Anything to add?", "Tweak just a little").
2. **Scoped** — does ONE thing.
3. **Addressable** — can be referred to by name in docs, memos, support tickets.
4. **User-facing** — lives on a surface a real user can see and interact with.
5. **Below module/page level** — modules host pages, pages host touches.

---

## Test of Fit

> *"Can a user point at this and say 'I tapped that' or 'I use that'?"*

If yes, it's a touch.

---

## Examples (Generic Product Patterns)

Touches:
- Search field
- "Submit" button
- Date-step arrows (◀ ▶)
- Bottom-sheet modal (specific one, e.g., "Add to catalog" modal)
- Empty-state CTA ("Record your first thing")
- Tap-to-record button
- Confirmation dialog ("Yes / Cancel")
- Inline action buttons (Rename, Delete, Edit)
- Chat-home pill (e.g., "Shifties / Shelfies / Something else")

NOT touches:
- Modules (Inventory, Payroll, Admin — bigger)
- Pages (host of touches, not a touch itself)
- Flows (sequences of touches across pages)
- Routes/endpoints (implementation, not user-facing affordance)
- Backend helpers (internal)
- Data records (content, not affordance)
- Component libraries (UI primitive, not specific product touch)

---

## Class Noun vs. Proper Noun

"Touch" is the class.

Each touch may have a proper-noun name. Example: the touch named "the Anything to add? touch" might also have a domain-canonical proper-noun name like "the adjustment layer" (in Mise's case — see `docs/brain/042826__adjustment-layer-canon.md` in the Mise reference repo).

Both compose cleanly: a touch's proper-noun name is its identifier; the class noun "touch" puts it in the right ontological bucket.

---

## Anti-Terms (Banned)

- **"Touchpoint"** — marketing jargon; conflates the UI unit with the user-experience event
- **"Microservice"** — wrong layer; backend service decomposition has nothing to do with UI affordances
- **"Component"** — implementation-flavored; a React component is not the same as a product touch
- **"Widget"** — UI-library-flavored; same conflation as "component"
- **"Tile"** — dashboard-flavored; too narrow
- **"Feature" used at sub-module scope** — overloads the term; "feature" is reserved for module-level capabilities

---

## Composition

- **Touch Verification Protocol** (`verification/TOUCH_VERIFICATION_PROTOCOL.md`) — uses "touch" as the unit of verification
- **No Non-Blocking Classification** — touch-level bugs go on active TODO (same as any other bug)
- **Atomic Verification Protocol** — touches verified per same EXTRACT → COMPARE → VERDICT structure as other verification work

---

## Applicability Domains

This canon is universally applicable to any fleet producing user-facing software. Adopt as-is for any product:
- Restaurant tech (Mise reference implementation)
- Healthcare apps
- B2B SaaS dashboards
- Consumer mobile apps
- Internal admin tools

The class noun is product-domain-agnostic.

---

## Reference Implementation

This canon was authored in Mise's `mise-core` repo as `docs/brain/052726__touch-terminology-canon.md` (2026-05-27). Mise is CC-Suite's reference implementation; the canon was ratified there via the glossary-addition mechanism (`mga:` trigger). The framework version sanitizes Mise-specific identifiers while preserving the operational substance.
