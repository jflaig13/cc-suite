# User-facing touch verification

A touch is a named, scoped user-facing action below page or module level. The catalog binds each touch to its user outcome, preconditions, allowed roles, expected state changes, failure behavior and evidence method.

Enumerate the affected catalog before verification. Include primary actions, secondary controls, dialogs, menus, cancellation, retry and recovery. Cover applicable empty, loading, success, failure, unauthorized and stale states. Record what is excluded and why.

Exercise the real interface in the environment required and authorized for the task. Use browser or native actions where the user does. For audio or uploads, pass the representative input through the actual picker and processing chain. A direct API call alone cannot prove the UI workflow.

Verify persistence after refresh, navigation and reopening. Trace the canonical write to related displays, aggregates, exports and integrations. Compare all required fields and behaviors against independent expected truth through `../ATOMIC_VERIFICATION_PROTOCOL.md`.

Use isolated synthetic data and explicit cleanup when writes are authorized. A test must not affect a real customer's account, recipient or payment. Missing identity or permissions is not a reason to borrow another tenant's data.

Record exact build and input revisions, steps executed, observed results and evidence. A screenshot of one state or a successful page load does not establish completion. Failures remain active work and reopen affected claims. Automated runs require verified trigger, routing and observable failure mechanisms; scheduling is owner-approved configuration.
