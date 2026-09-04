# Atomic verification

Verification uses three explicit phases: extract the observed state, compare it with independent authoritative expected truth, and derive the verdict. The complete field and behavior manifest is established before verification begins.

## Extract

Read the exact artifact or running behavior required by the task. Record literal observations for every required field, including explicit missing or unavailable values. Bind the extraction to subject revision, environment, input source, capture time and method. Do not mix observations with judgments or inferred expected values.

## Compare

Independently establish the expected basis, including identity, position, units and effective time. Compare every manifest row. Record `MATCH`, `WITHIN TOLERANCE` with its previously authorized rule, or `MISMATCH`. Missing observation or expected truth is incomplete evidence and cannot be recorded as a match.

Tolerances are domain-specific human decisions. Record the exact rule and source before the pass. This framework supplies no financial or time tolerance. Never change a tolerance or source record to make a failing result pass.

## Derive the verdict

A complete comparison with zero mismatches is `CLEAN`; a mismatch produces `FAILURES FOUND`. Missing required evidence produces `INCOMPLETE`, with every known finding and missing item listed. Reconcile required, completed, mismatched and missing counts. Never turn a mismatch into a clean result through an explanatory paragraph.

Use the templates in `verification/` for the manifest, extraction, comparison and verdict. An implemented validator must reject missing fields and inconsistent totals. The actual mechanism, invocation and result are evidence; describing a validator in a document does not prove it ran.

## Verify the whole behavior

The verifier must be independent of the implementation's parsing, calculations and derived inputs. For consequential data, validate the input basis against an authoritative source outside the pipeline. Multiple identical calculations over wrong input do not prove correctness.

Exercise persistence, roles, permissions, relevant tenant boundaries, retries, interruption and affected downstream views. A clean limited table cannot establish an untested UI workflow or deployed integration. Report the exact verified scope and the original objective's unresolved requirements.

A finding stays visible until repaired or explicitly dispositioned by the authorized owner. The agent reports the evidence; only the human may decide that a required outcome changes. Any strike consideration follows human-only issuance in `AUTHORITY_AND_EVIDENCE.md`.
