# Asymptote Protocol — Quality Convergence Standard

**Status:** Canonical. Part of CC-Suite™ v2.

**Scope:** All Tier A+ deliverables in all governed fleets. Specs, canons, brain files, outreach, code, verification output, audit reports.

---

## Core Concept

**Every significant deliverable has a quality ceiling. Each additional pass under a fresh lens moves the output closer to that ceiling. In practice, improvements become negligible around pass 5 — the asymptote. Without automation, agents stop after one pass.**

The Asymptote Protocol makes them run until convergence by default. The framing is from Jon Flaig at Mise: "reach the asymptote." The mechanism is **lens-cycle convergence** — running named lenses over the output until one full cycle produces zero changes.

---

## Why It Exists

LLM agents have a structural bias toward declaring their own work done. Quality improvements stop not when the work is good but when the agent's attention rotates to the next task. Single-pass output is recognizably AI: structurally complete, missing the second-pass tightening that separates a draft from a deliverable.

Three patterns this protocol addresses:

1. **Stop-after-one-pass.** The agent reviews its draft once, finds nothing obvious, and ships.
2. **Self-certified convergence.** The agent declares "this is good now" — the same blind spot Atomic Verification closes for verification work.
3. **Drift across lenses.** The agent reviews only the lens it cares about (correctness for code, voice for outreach) and never the others (security, edge cases, simplicity).

Asymptote forces named-lens cycles until convergence is reached on every lens.

---

## Definitions

- **A "pass"** = one complete scan of the output under one named lens, with a logged delta describing what was checked and what changed. No lens name, no delta = not a pass. Skipping a lens invalidates convergence.
- **Convergence** = one full lens cycle where every lens returns zero changes. That is the asymptote declaration.

---

## Writing Tasks — Mandatory (Canon Refinement 2026-05-18)

**Any time writing is involved, Asymptote Protocol is REQUIRED — not optional — and MUST be paired with the writing-specific `/goal` condition:**

```
/goal achieve the final, perfect, A+, non-AI-looking version
```

"Writing tasks" includes: outreach, memos, brain files, specs, copy (investor/marketing/website), Slack posts, legal drafts, IMDs, and any other text deliverable where voice, tone, and quality are properties of the output.

This condition is qualitative, not mechanical: the evaluator is checking whether the writing has reached the standard of a finished, non-AI-sounding human document, not just whether a lens returned zero diffs.

The producing agent cannot declare convergence on writing. Only the evaluator can.

---

## Standard Lens Sets

Completeness of the lens set is mandatory for the deliverable type. Lens sets may be extended; they may not be removed.

| Task type | Lens set |
|-----------|----------|
| Spec / canon / brain file | Logic → Completeness → Composability → Falsifiability → Edge cases |
| Code implementation | Correctness → Coverage → Edge cases → Security → Simplicity |
| Outreach / memo | Voice canon → Accuracy → Clarity → Concision → Tone |
| Verification | Coverage → Independence → Numerical accuracy → Edge case robustness |
| 7-wave subatomic audit | Wave 6 meta-audit IS the convergence check; no separate cycle needed |

---

## Mechanics

1. **Step 1 — Identify task type and lens set.** At the start of any significant deliverable, the producing agent declares which lens set applies. Custom lens additions allowed; deletions forbidden.
2. **Step 2 — Run Cycle 1.** Apply each lens in sequence. After each lens, log the delta. If a lens finds improvements, apply them before moving to the next lens.
3. **Step 3 — Check for convergence.** After completing one full cycle, review the delta log. If any lens produced changes, run Cycle 2. Repeat until one full cycle completes with zero changes on every lens.
4. **Step 4 — Declare convergence.** "Asymptote reached. N cycles, M passes."

---

## Optional External-Evaluator Integration

If your runtime provides an external evaluator command (a model separate from the producing agent that decides when work is "done"), use it instead of self-declared convergence. The canonical example as of 2026-05-11 is Claude Code's `/goal` command (introduced in v2.1.139), which routes the "is the condition met?" judgment to a separate Haiku evaluator after every turn.

### Canonical condition

```
/goal all named lens passes for [task type] return zero changes, or stop after [N] turns
```

### Why this matters

The single gap in self-guided cycle execution is that convergence is declared by the same agent that produced the deliverable. An external evaluator closes this the same way Atomic Verification's mechanical pipeline closed self-verification: the "done" judgment routes to a separate model that reads only the reported output, not the work in progress. The agent cannot declare convergence — only the evaluator can.

### When to use

Any interactive session with a Tier A+ deliverable. Set the condition at Step 1 alongside the task type and lens set declaration.

### When NOT to use

Audits using the 7-wave subatomic pattern, because Wave 6 meta-audit is already an independent convergence check. No additional cycle is needed for those.

### When the evaluator is not available

If your runtime does not yet have an external evaluator command, run more cycles rather than fewer. The absence of a mechanical evaluator raises the obligation; it does not lower it. Self-declared convergence in the absence of an evaluator should require at least 3 full cycles with all-zero deltas before declaring asymptote.

---

## Expo Protocol Integration

Asymptote runs AFTER the Expo-equivalent error-fixing loop completes (output is error-free). It does not replace Expo — it extends it.

- Expo Steps 5-6 (or your fleet's equivalent): catch errors and fix them; stop when zero errors.
- Asymptote: improve quality until convergence; stop when zero changes.
- If Asymptote finds an improvement, return to Expo Step 5 to catch any errors the improvement introduced.

Sequence: Expo clean → Asymptote converged → ship.

---

## Delta Log Placement

The convergence declaration ALWAYS appears in the session output to the human authority — always visible. It never lives inside the deliverable itself.

| Deliverable type | Where the declaration goes |
|------------------|----------------------------|
| Spec / brain file | One line in the changelog section: "Asymptote reached, N cycles, M passes" |
| Code | Commit message or PR description |
| Outreach / memo | Appendix in session output, separated from the deliverable |
| Audit | The audit ledger already captures this via Wave 6 |

The deliverable stays clean. The proof is always findable in the session output.

---

## Reference Shorthand

"2 cycles, 8 passes" is the canonical lens-cycle shorthand from the Mise spec development session that ratified this protocol (2026-04-27). In practice, a 5-lens set typically converges in 2 cycles (~10 passes), sometimes fewer. "2 cycles, 8 passes" describes the case where the second cycle finds two minor changes (in different lenses) before convergence on cycle 3.

---

## Sibling Canons

- **Atomic Verification Protocol** (`governance/ATOMIC_VERIFICATION_PROTOCOL.md`) — same mandatory-step structure, applied to verification rather than quality improvement.
- **Expo Protocol** (fleet-specific; see your fleet's equivalent) — Asymptote runs after the Expo error-fixing loop completes.
- **7-wave subatomic audit** (Scribe / auditor pattern) — Wave 6 meta-audit satisfies Asymptote convergence for audit tasks.

---

## Related Files

- `HARNESS_CORE.md` §15 — the compressed entry point for this protocol
- `governance/ATOMIC_VERIFICATION_PROTOCOL.md` — sibling canon for verification work
- `governance/WRITING_CANON.md` — the voice canon lens (in the outreach / memo lens set) enforces fleet writing rules

---

## Reference Implementation

Mise Inc. ratified the Asymptote Protocol on 2026-04-27 after Jon Flaig observed that Claude Code produces meaningfully better output on each additional pass, plateauing around pass 5. The brain file is `docs/brain/042726__asymptote-protocol.md`. The v1.1 `/goal` integration was added 2026-05-12 after Claude Code v2.1.139 introduced an external evaluator command. Mise applies the protocol to every Tier A+ deliverable: brain files (Scribe), product specs (CCPO), code implementations (CCTO), outreach (CCMO / CCGO / CCCO), financial analysis (CCFO), legal output (CCLO), risk assessments (CCRO), customer assessments (CCCO). Per-role lens sets and canonical `/goal` conditions are declared in each role's `init-{role}.md` file at `.claude/commands/init-{role}.md`.
