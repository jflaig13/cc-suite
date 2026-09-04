# Builder/reviewer verification loop

Use the full V-Loop for Tier S/A changes at EDG-2 or above, or when the applicable task contract requires it. Other work follows its governing risk and verification requirements. Neither role can replace independent verification with its own implementation tests.

A coordination record names the original objective, exact subject, affected behavior inventory, current step, next owner, evidence and open requirements. Each role writes its own section. A silent peer is an unknown state: observe the actual worker or transport before claiming it is working, idle or failed.

| Step | Owner | Required work |
|---|---|---|
| 1 | Builder | State risk, authorized outcome, scope and complete acceptance surface |
| 2 | Builder | Read current implementation and authoritative input basis; reproduce the defect or establish intended behavior |
| 3 | Builder | Implement the complete change, run required tests, diagnose and repair failures |
| 4 | Reviewer | Read the exact diff and governing contracts; independently check claims and dependencies |
| 5 | Authorized operator | Build or deploy the exact reviewed version in the environment authorized for this task |
| 6 | Reviewer | Exercise the original human workflow and persistence; perform complete Atomic verification |
| 7 | Reviewer | Verify affected neighboring behavior, failure paths and regressions |
| 8 | Both | Record separate evidence-backed declarations for the exact subject and reconcile the original objective |
| 9 | Auditor | Perform the required evidence, coverage and independence audit before final closure |

A failed check returns to the failed dependency. Preserve proven work and valid effects while diagnosing the cause. A repair changes the subject and requires affected review and verification again. Do not impose a one-edit limit or repeat an entire successful paid workflow because one downstream step failed.

All real findings stay visible. A mismatch affecting the original outcome or its regressions keeps that outcome incomplete. Unrelated findings are recorded separately; they cannot disappear as supposedly harmless defects.

Deployment is an effect with its own authority. This protocol does not grant a public push, production deployment or external communication. A source-only release is verified as such and must not be reported production verified.

The closing audit examines evidence, not merely the presence of verdict words. Missing reproduction, stale inputs, incomplete coverage or shared verification logic prevents closure. See `ATOMIC_VERIFICATION_PROTOCOL.md`, `VERIFICATION_INDEPENDENCE.md`, `AUTHORITY_AND_EVIDENCE.md` and `NO_SILENT_SCOPE_REDUCTION.md`.
