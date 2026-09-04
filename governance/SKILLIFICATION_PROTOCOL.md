# Skillification and the reliability loop

Use this process after a defect, near miss, accepted procedure, or repeated correction. The result is a tested improvement to the system that allowed the problem.

1. Capture the exact failing subject, input, observed behavior and source evidence. Preserve sensitive records inside the deployment's private boundary.
2. Question the requirement. Identify its owner, user outcome and evidence. Delete unnecessary behavior before optimizing it.
3. State a falsifiable invariant. Enumerate the affected paths and consumers, including retries, interruption, permission failures and tenant boundaries.
4. Choose the lowest suitable enforcement layer: data model, domain operation, capability boundary, effect ledger, test or deployment check. Reuse an existing mechanism where possible.
5. Implement the complete path, including observable failure and recovery. Never replace a necessary mechanism with a prose promise.
6. Add a regression that fails for the original defect. A mutation restoring the defect should make that test fail; run the affected integration and human workflow checks.
7. Update the source-of-truth behavior map, host wiring, configuration and downstream representations that depend on the change.
8. Add or revise a skill only for reusable procedure that remains after mechanical enforcement. State inputs, authority, effects, recovery and exit evidence.
9. Apply the required independent review to the exact final subject and record what is implemented, tested and deployed.

`TOLD_AND_BOUND.md` defines evidence for enforcement claims. `NO_SILENT_SCOPE_REDUCTION.md` prevents a local repair from hiding unfinished downstream work.
