# Told and Bound

**Told** means the agent was instructed through a prompt, operating rule, skill, memory note or checklist. Another session can miss or disregard it.

**Bound** means an implemented layer refuses or records the prohibited transition. Examples include a database constraint, tenant-scoped access check, capability broker, idempotent effect record, or regression test that blocks a defective merge.

Identify which applies to every operational claim. A Markdown protocol, signed document, test file, or example in this repository is not proof that a running deployment enforces it. Name the actual enforcing function or schema, the path that invokes it, the refusal or recorded outcome, and evidence from an adversarial test.

Enforcement has a scope. A CI test can bind publication of a change while leaving an unreviewed runtime mutation possible. A queue receipt can prove persistence without proving recipient processing. State both the guarantee and the limit.

Keep a rule–enforcer table: one row per rule an agent is bound by, giving the rule in a few words, where it is stated, and what enforces it today (a test, a guard, a hook, a check or a code path). Where nothing does, the row says `prose` and how many times the rule's mistake is recorded as having happened. A rule written later gets its row when it is written; give each new rule its own file in a rows directory when several authors would otherwise edit one table at once. The table is a record; nothing refuses a change over its bookkeeping.

A rule that protects a customer, whose mistake happens again while it is `prose`, gets an enforcer at the highest level that holds it (the architecture, then a type or a check whose refusal names the fix, then a test, documentation last), and its row changes in the same commit. A rule about the crew's own work is enforced by its plain words or a tool that already exists, never by new crew code: rules turned into new scripts, checks and tests are a common source of code that keeps coming back after cleanups.

Move repeated lessons to the lowest suitable layer through `SKILLIFICATION_PROTOCOL.md`. Add prompts only for the remaining reasoning, navigation or judgment. Every new guard must protect against a real net negative impact, and must satisfy the customer-path and recovery rules in `CONDUCT_FLOOR.md`.
