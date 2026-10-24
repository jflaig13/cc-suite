# Fleet decision routing

Resolve a decision from current sources before asking the human. Record the action, evidence, role boundary, affected resources and consequence of error.

1. The responsible role searches the authoritative source and applies existing rules.
2. A peer supplies missing domain evidence or independently checks a material conclusion.
3. The operational arbiter resolves cross-lane ownership or interpretation within existing authority.
4. A decision reserved to the human, or essential input that cannot be discovered, enters the human decision queue with concrete options and a recommendation.

Human-reserved decisions include money moving, a send to a customer, a price, a contract, what to build, new consequential authority, a change that loosens a customer protection, retiring a deployment and correction of human-confirmed facts. A previously granted instruction is execution work, not another approval request; work the crew can do itself is never put to the human.

Confidence is useful context, never authority. Any number the routing uses (a threshold, a deadline, a cadence) is set from evidence and defined once where the owner can see and change it (`HARNESS_CORE.md` section 0, class 3).

Put each decision to the human as a one-tap: one short question naming the decision; each option opening with the whole story in plain words, as if the human had not thought about it in months, then what the choice does and what happens next; the recommended option first and marked; and an option that hands the decision back to the agent, which the agent then closes with its recommendation and says so. A decision request records what must be decided, the supporting source, available alternatives, recommendation, exact blocked requirement and work continuing independently. Close the loop the same turn: record the actual answer and the time, carry it out, mark it decided in every record that lists it, relay it verbatim to Scribe, and verify the downstream result. Do not substitute silence, elapsed time or a message acknowledgment for a decision.

`FODL_SCOPE_DEFINITION.md` defines the queue boundary; `FODL_TRIGGER_PROTOCOL.md` defines its handling. `AUTHORITY_AND_EVIDENCE.md` governs delegated instructions and evidence.
