# A synthetic CC-Suite work example

This example uses invented identifiers and a local document-index task. It illustrates the records a deployment should produce; it is not evidence of a running service or a production outcome.

The owner asks a builder to repair a search filter that loses its selected category after refresh. The authorization covers a local source change and verification. Publication is not included.

## Objective record

| Field | Value |
|---|---|
| Objective | Preserve selected category through search, refresh and reopening |
| Builder | BUILDER-001 |
| Independent reviewer | REVIEWER-001 |
| Source | Recorded commit/tree identity |
| Required behaviors | Select category; search; clear; refresh; reopen; invalid category recovery |
| Authority boundary | Local source and test artifacts; no outward deployment |
| Initial status | INCOMPLETE |

The builder reproduces the lost selection, reads the state owner and its consumers, and implements the correction. Tests cover the original defect and invalid saved state. The builder's tests are evidence about implementation, while the reviewer independently exercises the requested user behavior.

## Review and evidence

The reviewer reads the frozen change and the original requirements. It records the exact artifact and test environment, walks the relevant controls, refreshes and reopens the view, and verifies that clearing the filter also clears the stored selection. A finding about stale stored state returns to the builder for repair and creates a new review subject.

The final record links the complete behavior inventory, independent observations, required review and corrected source identity. It says `COMPLETE` only when all required rows have evidence. It still says the deployment status is unchanged, because publication was outside the authorized objective.

## Interrupted work

If the session stops after implementation, the handoff records the source identity, completed tests, outstanding review and next dependency. The next worker resumes review without rebuilding the feature from scratch or describing it as shipped.

## A governance finding

If the builder claims the independent review passed when no reviewer ran, the auditor records a candidate misrepresentation with evidence. The human decides whether to issue a strike. A threshold detector cannot terminate a deployment on its own.

A durable corrective lesson belongs at its lowest effective layer. For example, the completion validator can reject a record missing a required review reference. Its test proves only that validation boundary; it does not prove the referenced review was truthful.
