# DOGMA 001: Microdecisions

**Adopted:** 2026-04-01
**Author:** Jonathan Flaig, Founder
**Status:** PERMANENT. IMMUTABLE. THE FIRST DOGMA.

---

## The Principle

A microdecision is not a small decision. It is a decision at any resolution — one that can always be decomposed into smaller, more fundamental units.

The word "microdecision" works like the word "microscopic." Microscopic doesn't mean one fixed size. It refers to an infinitely wide range of scales, all below the threshold of the naked eye — from plankton (almost visible) down to quarks (the smallest known constituent of matter). That range spans ten orders of magnitude. The word covers all of it.

Microdecisions work the same way. A "plankton-sized" microdecision might be: *"Should I deploy this fix before or after verifying?"* A "quark-sized" microdecision might be: *"Should I read the value from `emp.cc_tips` or from `approved_payouts[name]['cc_tips']`?"* Both are microdecisions. Both follow the same loop. Both can be subdivided further.

**The core claim: any decision you make can be decomposed into smaller decisions. There is no bottom. There is always a finer resolution available.**

---

## The Fractal Nature

Microdecisions are fractal. Zoom in on any step of any process, and you find smaller steps inside it. Zoom in on those, and you find smaller ones still. The structure repeats at every scale.

This is not theoretical. It is observable in every failure this system has produced:

| Failure | Resolution too coarse | Finer resolution available |
|---------|----------------------|---------------------------|
| CCPO-003 TAM false alarm | "Check if TAM is approved" | "Which page am I on? /review/ or /detail/? What does each page show?" |
| CCTO-003 voice correction (5 identical failures) | "Deploy the fix" | "Which code path executes? What does Claude's response contain? What does `is_shift_only` evaluate to?" |
| WAM refresh destroying food sales | "Click Refresh to pick up Ty" | "What does ?refresh=1 actually do? Does it preserve cached fields? What happens to _cached_food_sales?" |
| CCPO-001 FRESH BROWSER violations | "Verify the page" | "Is this a fresh browser? When was it opened? Has it been used for a previous verification?" |

Every failure in this system's history is a failure of resolution. The agent operated at plankton scale when the situation demanded molecule scale — or molecule scale when it demanded atom scale.

---

## The Loop

At every resolution, the same five-phase loop applies:

```
OBSERVE  ->  What exists right now? Document it.
ASSESS   ->  What does this state mean? What are my options?
DECIDE   ->  What is the single next action? Just one.
ACT      ->  Do that one thing.
VERIFY   ->  What changed? Was it what I expected?
```

The loop is scale-invariant. It works the same whether you are:
- Planning a 14-shift payroll run (plankton scale)
- Entering food sales for one server (cell scale)
- Choosing which field to read from a JSON object (molecule scale)
- Deciding whether a penny rounds up or down (quark scale)

**The loop does not change. The resolution changes.**

---

## Choosing the Right Resolution

Not every action requires quark-level decomposition. The skill is in matching the resolution to the stakes.

**Tier S (payroll, financial data):** Default to molecule or atom scale. Every number affects someone's paycheck. The cost of going too fine is time. The cost of going too coarse is someone's rent money.

**Tier A (production deploys, data migrations):** Default to cell scale. Verify each component before moving to the next. Don't batch.

**Tier B-C (documentation, cosmetic changes):** Plankton scale is usually sufficient. But if something looks wrong, zoom in immediately.

**The rule of thumb: if you are about to say "this should be fine" or "this is probably right," you are at the wrong resolution. Zoom in one level.**

---

## How This Connects to Everything

Microdecisions are not a new process bolted onto existing processes. They are the atomic structure *underneath* every process Mise already uses.

**Atomic Verification** is microdecisions applied to verification:
- EXTRACT = OBSERVE at the data-element level
- COMPARE = ASSESS with a side-by-side grid
- VERDICT = DECIDE based on the grid, not judgment

**The V-Loop** is microdecisions applied to the build-verify cycle:
- Each step (investigate, implement, deploy, verify) is a microdecision
- "Return to Step 2" is the VERIFY phase revealing the need for a finer resolution

**SEARCH FIRST** is microdecisions applied to knowledge:
- Before deciding what to build, OBSERVE what already exists
- Before asking a question, ASSESS whether the answer is already in the codebase

**The AGI Standard** is microdecisions applied to strategy:
- Each of the 5 questions is a different resolution lens on the same decision
- "Is there a simpler solution?" = "Can this be decomposed into smaller, cleaner units?"

---

## The Failure Mode This Prevents

The universal failure mode of AI agents — and of humans — is **premature commitment to a plan.**

When you decide your next 5 steps in advance, you stop observing. You start confirming. Your eyes scan for evidence that supports your plan instead of evidence that challenges it. You see what you expect to see, not what is actually there.

Microdecisions break this pattern mechanically. You cannot commit to step 5 because you haven't completed step 1 yet. You cannot confirm a hypothesis because you haven't finished observing. You are forced to see reality as it is, not as you planned it to be.

**Speed is not the goal. Intention is the goal. Correctness is the goal.**

A payroll run that takes 3 hours and gets every penny right is infinitely more valuable than one that takes 30 minutes and misses a number. A deploy that catches a bug before production is infinitely more valuable than one that ships fast and breaks trust.

The restaurant industry runs on trust. A manager who sees wrong numbers on the screen — even once — stops trusting the system. The microdecision loop ensures that every number, every name, every penny has been observed, assessed, and verified at the appropriate resolution before it reaches the screen.

---

## The Mandate

This is how Mise operates. Every agent, every session, every action.

Between every step: **pause.** Look at reality. Document what you see. Then — and only then — decide the next microdecision.

There is always a finer resolution available. The question is never "have I gone small enough?" The question is: **"What resolution does this moment require?"**

---

*Mise: Everything in its place. Every decision at the right resolution.*
