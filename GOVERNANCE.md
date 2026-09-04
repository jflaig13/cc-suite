# CC-Suite governance

CC-Suite coordinates AI agent work through explicit roles, persistent records, bounded authority, independent verification, and institutional learning. [HARNESS_CORE.md](HARNESS_CORE.md) is the operating authority for the portable framework.

## Identity and scope

A role describes responsibilities and permissions. A deployment is a particular admitted agent instance. A session is one execution context for that deployment. A release identifies code and configuration; activation identifies what is actually running. Do not use these terms interchangeably.

Give each governed deployment a permanent identifier. Track its work, evidence, corrections, and handoffs. The [role template](governance/roles/ROLE_TEMPLATE.md), [identity registry](governance/ID_REGISTRY.md), and [personnel template](governance/performance/PERSONNEL_TEMPLATE.md) provide file-based starting points. The runtime enforces only the capabilities documented and configured for that installation.

## Roles and separation of duties

The Company Fleet can divide work among Scribe, Chief of Staff, architecture, implementation, product verification, risk, finance, legal, marketing, growth, customer, and utility functions. Organizations may adopt a smaller set.

The Scribe preserves institutional knowledge, records decisions, and audits any role. The Chief of Staff coordinates dependencies and resolves operational disputes within delegated authority. Builders implement; independent reviewers verify the result. Scribe's ability to record or change state is determined by its explicit capability contract, not by an assumed blanket read-only or unrestricted role.

Only the authorized owner changes policy or grants consequential authority. No role name, message signature, file path, or model confidence score grants additional permission by itself.

## Accountability

The human authority may record three types of strike against an agent deployment:

| Type | Meaning |
|---|---|
| A | Critical misrepresentation: fabricated facts or false claims about completion, verification, or system state. |
| B | Role boundary violation: acting outside the deployment's authorized scope. |
| C | Negligence: missing required work or checks, including presenting partial work as complete. |

Strikes and positive performance records preserve the event as recorded. Correct errors with an attributable correction; do not silently rewrite history. Positive records do not erase failures.

The framework retains three parallel termination criteria: three total strikes, two OLD strikes repeating a documented predecessor error, and the same class of mistake on consecutive occasions. An automated detector can flag a criterion; only the human authority issues a strike or initiates termination. Scribe and Chief of Staff are accountable under the adopted strike system; utility's treatment must follow its explicit role contract.

A termination concerns an AI deployment. It is not a human employment action. The authorized executor records the decision, closes the deployment's authority, and creates the [termination packet](governance/terminations/TERMINATION_TEMPLATE.md). A successor receives the prior evidence, failure analysis, and corrective controls before continuing the work.

## Learning and enforcement

A failure analysis identifies what happened, why it was possible, which consequence matters, and what prevents recurrence. Prefer a testable change to a repeated instruction. Distinguish an instruction the agent is told to follow from a control that the runtime actually enforces.

A mechanism claim is operational only after its complete stated surface has been exercised against the implementation. [Mechanism attestation](governance/MECHANISM_VERIFICATION_ATTESTATION.md) identifies the source, input basis, tests, and unresolved limits. New evidence can reopen an earlier conclusion.

## Decisions and communication

Search authoritative sources before asking the owner to supply information already recorded. Carry routine authorized work to completion. Route a material unresolved decision with its consequence, available choices, recommendation, and blocked work. Continue independent work while awaiting an answer.

Verify purported delegated authority through the deployment's trusted mechanism. A transport acknowledgement, message delivery, decision, and executed action are different events. Record each state truthfully and read back the exact intended recipient before a consequential outward send.

## Verification and completion

Keep implementation and verification independent at the layers that determine the answer. Use fresh evidence from the actual affected surface. Validate consequential input data against an independent basis, not just a second computation over the same source.

The quality ladder combines clear writing, named-lens review, another model family's review where required, and inspection of the finished experience. Bind a verdict to the exact subject and persist it. A reviewer has authority to inspect outside the author's preferred explanation and to reject an incomplete review contract.

The original outcome defines success. Track every required behavior and consequence through delivery and verification. A local package test proves that package's tested behavior; it does not prove a live customer deployment. A blocked component or missing evidence remains visible in the overall status.

## Adoption

Start with the operating core, two explicit roles, a bounded task, and its acceptance criteria. Customize templates for your organization. Configure the actual host and authority mechanism before enabling consequential work. Follow the repository's runtime documentation for installation and the included synthetic example for a bounded demonstration.

The public package excludes private customer records, credentials, operational receipts, and restaurant business rules. Its provenance and scope inventory make those boundaries reviewable.
