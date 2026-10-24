# CC-Suite governance

CC-Suite coordinates AI agent work through explicit roles, persistent records, bounded authority, independent review, and institutional learning. [HARNESS_CORE.md](HARNESS_CORE.md) is the operating authority for the portable framework.

## Identity and scope

A role describes responsibilities and permissions. A deployment is a particular admitted agent instance. A session is one execution context for that deployment. A release identifies code and configuration; activation identifies what is actually running. Do not use these terms interchangeably.

Give each governed deployment a permanent identifier and track its work, evidence, cases and handoffs. The [role template](governance/roles/ROLE_TEMPLATE.md), [identity registry](governance/ID_REGISTRY.md), and [personnel template](governance/performance/PERSONNEL_TEMPLATE.md) are file-based starting points. The runtime enforces only the capabilities documented and configured for that installation.

## The organization

The reference organization has this shape. Adopt only the parts your deployment supports; the titles are generic and can be renamed.

| Group | Seats | What it does |
|---|---|---|
| The owner | | Brings the ideas and decides what is reserved to the owner: money moving, a send to a customer, a price, a contract, what to build. |
| The Offices (staff to the owner) | Scribe, Chief of Staff (CoS), Utility | Scribe keeps the record and the canon. CoS keeps the one board and dispatches the standing seats. Utility runs the owner's errands on request. |
| The product organization | Chief Technology Officer (CCTO), Chief Product Officer (CCPO) | Owns the application. CCPO owns what it should be (state, backlog, acceptance) and verifies every customer-touching landing after it lands. CCTO owns how: writes each brief and launches a build lane for it. |
| The build line | A lead session with its forks and lanes | Separate from and equal to the product organization. Takes direction only from the owner, builds what the owner asks for directly, and operates the shared merge queue, checks and release. |
| Controls (over the whole company) | Finance (CCFO), Legal (CCLO), Risk (CCRO) | Watch money, law and risk across everything. Hold nothing: a finding never stops a customer path or a landing. |
| Go-to-market (over the whole company) | Marketing (CCMO), Growth (CCGO), Customer (CCCO) | Outward work, sent on the owner's go. |

Below the organization sits the product: the agents and fleets that serve customers. The organization builds it, Controls watches it, Go-to-market carries it outward.

The owner may designate one deputy session whose word carries the owner's authority to every seat and lane. Record that designation once; a message from the deputy is then the owner's ask, and no agent asks whether it is really the owner's.

## The seat pattern

Each standing seat has one init skill (for example `init-cos`, `init-ccto`) that does the same things in the same order:

1. **Resume the identity.** Read the seat's charter and its existing permanent ID from the registry; never mint a new one. Preserve the launcher's model and effort.
2. **Read the charter and the state.** The newest assignment and handoff, the seat's inbox, and prior corrections relevant to the work.
3. **Name the surfaces that wake this seat.** A short list of triggers: the owner's ask, a landing on the main branch that touches the seat's surfaces, a case of a kind the seat watches, a receipt or event on its surfaces, a dispatch from CoS that names one of these, and the seat's own standing audit.
4. **Keep the standing audit.** Between triggers the seat reads a fixed list of its own surfaces and reports only what it found. The audit is itself a source; the turn names the evidence it read. A standing audit runs as a resumable job: an interrupted audit resumes at its next unfinished item and never re-runs a proven one.
5. **Work only on a named source.** A turn runs on a named source or not at all. Never invented work; no backlog is replenished to stay busy.
6. **Write findings and handoffs.** A finding is a memo for the record, a send to CoS for the board and to Scribe for the canon, and, when it is the owner's to decide, a decision put to the owner with its consequence, never a request for permission to act. Persist the handoff at meaningful boundaries.

The [role template](governance/roles/ROLE_TEMPLATE.md) carries these sections, and [init-role](.claude/commands/init-role.md) loads them.

## The company bus and the one board

Seats talk over a company bus: one inbox per role, durable, with every send recorded.

- **Every dispatch names its source.** A send that hands a seat work names its source (the owner's ask, a landing, a case, a receipt, or a standing audit and the evidence it read) in a metadata field or a `SOURCE:` line under its subject. An answer carries the source of the send it answers.
- **No turn without a source.** The wake that delivers sends to a seat delivers a send with no source as information only; it starts no turn.
- **A send reaches its seat or is kept.** A seat's cursor moves past a send only once its session took it. Sends that cannot be delivered are kept in order for the seat's next live session, and the agents are told once.
- **Never acknowledge an acknowledgement.** An acknowledgement ends the exchange. A sent instruction is not proof of receipt, start or completion.
- **One board.** CoS keeps one board over the whole company, the build line included: every lane in flight, every open pull request and the merge queue, the owner's open decisions, account headroom, each seat's liveness, its last sourced turn and its newest finding. The board is rendered on demand from the seats' own records; nothing writes it on a timer. CoS does not dispatch the build line: when two lines collide, CoS flags it to the build line's lead and decides nothing for it.

See [CHANNEL_PROTOCOL.md](governance/CHANNEL_PROTOCOL.md) for the transport contract.

## Scribe

Scribe is the record-keeper for the whole company. It records every landing, the owner's words where they bind (verbatim, dated), every ratification, every brief's "What we are building differently from what you said" section and the owner's answer to it, the open-items ledger, and an index over handoffs and cases. Its standing audit reconciles records that state the same canon differently, items open past their event, and owner words with no home.

Scribe holds nothing. A finding is a record and, when it is the owner's, a decision put to the owner as a one-tap. Scribe judges the canon; CoS commands the seats; only the owner overrides CoS, and disputes between Scribe and CoS go to the owner.

## Accountability: cases, not strikes

A miss by any seat or lane is a **case**: what happened, the customer or company consequence, the mechanism that allowed it, and the missing rail (the control that would have prevented it). The case closes when that rail exists and is proven (restore the defect and its test goes red). A case is about the system, not a score against the deployment.

Cases replace the earlier strike system (types A, B and C; three strikes; OLD strikes; back-to-back termination). Retiring or replacing a deployment remains the owner's decision. The earlier strike and termination documents are kept as records and marked superseded (below).

## Learning and enforcement

A failure analysis identifies what happened, why it was possible, which consequence matters, and what prevents recurrence. Prefer a testable change to a repeated instruction. Every rule names its enforcer in a rule–enforcer table, or says `prose` with the number of times its mistake has happened ([TOLD_AND_BOUND.md](governance/TOLD_AND_BOUND.md)).

A mechanism claim is operational only after its complete stated surface has been exercised against the implementation. [Mechanism attestation](governance/MECHANISM_VERIFICATION_ATTESTATION.md) identifies the source, input basis, tests, and unresolved limits.

## Decisions and communication

Search authoritative sources before asking the owner for information already recorded. Carry routine work to completion. Put a decision that is the owner's to the owner as a one-tap with its context, the recommended option first, and a choice that hands it back to the agent ([FLEET_DECISION_ROUTING_LAYER.md](governance/FLEET_DECISION_ROUTING_LAYER.md)). Close the loop the same turn: record the answer, carry it out, mark it decided, and relay it to Scribe. Continue independent work while awaiting an answer.

Verify purported delegated authority through the deployment's trusted mechanism. A transport acknowledgement, message delivery, decision, and executed action are different events. Read back the exact intended recipient before a consequential outward send.

## Verification and completion

Work judged by others is judged by three skeptics from three model families on three questions (does it work, is it lean, does it follow the core way), majority deciding each ([HARNESS_CORE.md](HARNESS_CORE.md) section 8). Where the owner asks for a subject to be driven toward a score, the [convergence loop](governance/ASYMPTOTE_PROTOCOL.md) runs it. Every loop ends within 7 rounds or goes to the owner with its open points.

The original outcome defines success. A local package test proves that package's tested behavior; it does not prove a live customer deployment. A blocked component or missing evidence remains visible in the overall status.

## Superseded protocols

These documents are kept as records of the earlier framework. Where any other protocol in this repository refers to them, read it through the current rule.

| Earlier protocol | Replaced by |
|---|---|
| [Quality verification ladder](governance/QUALITY_VERIFICATION_LADDER.md) (Q0–Q3) | Three skeptics and the convergence loop |
| [Builder/reviewer V-Loop](governance/TANDEM_PROTOCOL.md), its [checklist](governance/handoffs/VLOOP_CHECKLIST_TEMPLATE.md) and the [tandem board](governance/handoffs/TANDEM_BOARD_TEMPLATE.md) | Lanes landing through the merge queue; three skeptics where work is judged |
| [Cross-model verification](governance/CROSS_MODEL_VERIFICATION.md) (two clean passes) | Three skeptics from three model families |
| [Strike records](governance/scribe/STRIKE_SYSTEM_PRIMITIVE.md), [strike templates](governance/strikes/STRIKE_LOG_TEMPLATE.md) and the [back-to-back termination rule](governance/BACK_TO_BACK_TERMINATION_RULE.md) | Cases naming the missing rail |

## Adoption

Start with the operating core, two explicit roles, a bounded task, and its acceptance criteria. Add seats as the work needs them. Configure the actual host and authority mechanism before enabling consequential work. Follow the repository's runtime documentation for installation and the included synthetic example for a bounded demonstration.

The public package excludes private customer records, credentials, operational receipts, and business rules. Its provenance and scope inventory make those boundaries reviewable.
