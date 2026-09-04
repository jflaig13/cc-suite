# Bounded worker lifecycle

A bounded worker starts for a declared task or event, persists its result and exits. Its invocation may be scheduled, event-driven or directly authorized. The base identity, authority and onboarding rules in `../LIFECYCLE.md` still apply.

Before launch, record the objective, scope, role/deployment identity, input source, output destination, permitted effects, recovery behavior and completion predicate. Load `HARNESS_CORE.md` and required domain rules. A short-lived worker cannot skip authority or data boundaries.

Use only owner-approved scheduling and operational limits. Separate each invocation's output and state. Prevent overlapping writers or duplicate effects with the actual lease, idempotency or scheduler mechanism required by the task. A new invocation does not imply that prior effects should run again.

A worker may consume a durable event queue if the adapter supports it. Record claim, processing, acknowledgment and retry semantics explicitly; do not assume that every short-lived process is incapable of receiving events. A persistent interactive session is not required for durable message delivery.

On success, write the result and evidence before acknowledging the completed task. On failure or interruption, preserve the failed dependency and completed effects. A digest bridge may deliver the result to another authorized surface, but transport success is separate from task completion.

Verify trigger installation, observed execution, output persistence and failure handling before describing an automation as operational. A registered schedule or process exit alone is insufficient.

Only an authorized human termination decision permits revoking a governed deployment; disabling an unrelated schedule is not implied. Preserve records and update actual admission/runtime state as applicable.
