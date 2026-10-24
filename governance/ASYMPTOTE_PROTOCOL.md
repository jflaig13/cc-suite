# The convergence loop (Asymptote)

The organization's one convergence loop, run where the owner asks for it. It drives a subject toward a score of 100 on a named rubric with nobody typing each pass. It replaces the earlier named-lens cycles, the quality ladder, the V-Loop, Line Check and two-pass cross-model review; those documents are kept as records (see [GOVERNANCE.md](../GOVERNANCE.md)).

## The rubric

A run grades against one rubric held in a single rubric store, pinned exactly as written and never retyped or kept anywhere else. Each rubric lists traits, each trait lists checks, and each check is a short numbered list of requirements a grader can find or not find at an exact spot in the evidence. Requirements the check cannot do without are marked core: full points when every requirement holds, half when every core one holds, none otherwise. Each requirement says when it does not apply, so a subject that meets the source in full can reach 100. A rubric also lists its hard fails.

A new rubric is written only when someone asks for one. The run drafts it in round 0 and stores it only after all three graders accept it. Each grader accepts a draft when every requirement can be judged at an exact spot, every requirement of the source is in some check, every requirement says when it does not apply, nothing makes 100 unreachable, every hard fail can be judged at an exact spot, and no check states what the subject is today. A rejected draft never goes back unchanged.

## The run

Each round, the evidence is frozen and every grade binds to its hash. Three fresh graders from three model families grade it, blind to each other. A grade that does not show its model counts for nothing, and an unavailable grader means the round is not done. Each grader lists its tasks in the same answer; one session merges the lists into a single plan. Each task runs in an isolated copy and is checked apart from the session that did it: by a command where a command decides, by another session where judgment is needed. Internal changes land through the merge queue; anything outward waits for the owner's yes.

## The rules

- **The floor.** Every grader at 98 or higher, unrounded, with no hard fail (the rubric's own, a made-up claim, something done without the owner's yes, or something broken). The average is reported beside each grade and is never the floor.
- **The finish.** At the floor, with nothing unsettled, the run ends when a real change wins no more points, every grader gives 100, nothing left can change the subject, or every grader has given two grades above 98. A grader that has given two grades above 98 stops grading.
- **The cap.** A run still unfinished after 7 graded rounds, or 7 rejected rubric drafts, brings the owner its open points and nothing else, and waits.
- **Lean.** A new round runs only on a real change to the subject or its evidence. Each step runs on the cheapest model that does it safely. A paid step that failed is bought again only once something it depends on has changed.
- **The owner's yes.** Anything outward a run would release (a page made public, a send, a payment, legal terms put in force) waits for the owner's yes on that exact artifact, its destination and its action. A changed artifact is a new question.

A run is always running, finished, waiting on each named thing, or not converged with its reason. It never stops silently. A deployment records where its runs live and how one is requested; this protocol does not prescribe a vendor, model or price.
