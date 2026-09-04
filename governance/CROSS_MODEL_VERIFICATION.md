# Cross-model verification

An independent reviewer from another model family reviews the complete frozen subject. Record both model families, the artifact hashes or commit/tree identity, the original acceptance criteria, the review scope and the evidence available to the reviewer.

The reviewer receives the artifact and governing criteria without the producer's preferred verdict. It identifies substantive defects, authority or data-boundary violations, incomplete behaviors, unsupported claims and coverage gaps. Findings name a location, consequence and reproducing evidence when available. A favorable summary cannot replace a whole-subject review.

The producer repairs the union of accepted findings in one coherent revision. The reviewer then reads the complete revised subject. Two consecutive passes with zero critical findings on the same frozen subject satisfy the Q2 criterion. When Q2b parallel discovery is used, the closing serial whole-subject passes must be CLEAN, with coverage debt resolved. Record remaining noncritical findings and their authorized dispositions; do not hide them inside the verdict.

Parallel finders may examine distinct lenses independently. Keep them blind to one another's findings until synthesis. Assign bounded scopes, avoid overlapping writers, and use a verifier canary when the selected review contract requires one. A canary check does not replace actual review.

If reviewers disagree, inspect the disputed evidence and resolve the cause. The original completion predicate remains fixed. Unresolved substantive findings keep the review open; human taste choices are surfaced without inventing a technical failure.

A model switch alone does not establish independence when both reviewers reuse the same faulty parser, unexamined data basis, or earlier verdict. See `VERIFICATION_INDEPENDENCE.md`.
