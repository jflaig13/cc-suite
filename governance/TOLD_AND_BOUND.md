# Told and Bound

**Told** means the agent was instructed through a prompt, operating rule, skill, memory note or checklist. Another session can miss or disregard it.

**Bound** means an implemented layer refuses or records the prohibited transition. Examples include a database constraint, tenant-scoped access check, capability broker, idempotent effect record, or regression test that blocks a defective merge.

Identify which applies to every operational claim. A Markdown protocol, signed document, test file, or example in this repository is not proof that a running deployment enforces it. Name the actual enforcing function or schema, the path that invokes it, the refusal or recorded outcome, and evidence from an adversarial test.

Enforcement has a scope. A CI test can bind publication of a change while leaving an unreviewed runtime mutation possible. A queue receipt can prove persistence without proving recipient processing. State both the guarantee and the limit.

Move repeated lessons to the lowest suitable layer through `SKILLIFICATION_PROTOCOL.md`. Add prompts only for the remaining reasoning, navigation or judgment. Every new guard must prevent a real unauthorized or harmful effect, and must satisfy the customer-path and recovery rules in `CONDUCT_FLOOR.md`.
