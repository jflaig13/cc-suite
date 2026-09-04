# Asymptote: review until a complete lens cycle is dry

State the subject, original acceptance criteria and applicable lenses before review. A pass reads the complete subject through one named lens and records what changed. A cycle applies every selected lens. Q1 convergence requires a complete cycle with zero changes.

| Subject | Lenses |
|---|---|
| Specification or operating rule | Logic, completeness, composition, falsifiability, edge cases |
| Implementation | Correctness, coverage, edge cases, security, simplicity |
| Prose | Voice, accuracy, clarity, concision, tone |
| Verification artifact | Coverage, independence, input basis, comparisons, failure detection |

Apply improvements as they are found, then repeat the complete cycle. A later change can invalidate an earlier pass. Keep the subject identity and delta log separate from the finished deliverable.

For a requested deep re-pass, use fresh angles and an angles-used ledger. Repeating the same review without new evidence does not establish additional coverage. Do not claim convergence when substantive findings remain, when the subject moved unnoticed, or when a required lens was skipped.

Use an independent evaluator when available and authorized by the host. Configure the actual task predicate, not an unbounded promise of perfection. A host command or model named in an old example is not a portable requirement. If the host lacks that mechanism, record the limit and use the applicable independent review path; do not invent a substitute count of extra self-reviews.

Q1 addresses deliverable quality. Product verification and Q2 cross-model requirements still apply as selected by `QUALITY_VERIFICATION_LADDER.md`. Complete evidence is required before reporting the corresponding rung passed.
