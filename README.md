# CC-Suite

CC-Suite is the governed multi-agent system I use to build and operate [Mise](https://getmise.io). It combines defined roles, durable work records, permissions, independent review, and the operating rules developed while building the business.

The public package's operating rules and governance are synchronized with the October 24, 2026 reference; its runtime source remains as released on September 4, 2026. It includes the portable framework, host entry points, and reusable Company runtime. The source inventory records what was carried over, what was adapted for a standalone installation, and which private product components are excluded.

## How the parts fit together

| Part | Responsibility |
|---|---|
| **CC-Suite / Company Fleet** | The seats and workflows that build, review, coordinate, and maintain the company’s work: the Offices (Scribe, Chief of Staff, Utility), the product organization, a separate build line of a lead with its forks and lanes, Controls, and Go-to-market. |
| **Fleet Kernel** | Shared infrastructure for identity, authority, durable work, evidence, and lifecycle management. The public runtime exposes its documented reusable Company surface. |
| **Restaurant fleets** | Separately configured fleets serving individual restaurants. Their accounts, permissions, data, business rules, and live activations belong to those deployments. |
| **Mise / Ask Mise** | The customer application and its conversational interface. Operators ask questions, assign work, and review results here. |

Shared infrastructure does not imply shared permissions or customer data. A downloaded source package is not a live fleet activation.

## Start with the framework

1. Read [the operating core](HARNESS_CORE.md). It states the rules shared by every role and host.
2. Define a builder and an independent reviewer using [the role templates](governance/roles/). Record their responsibilities, authority, triggers and standing audit. Add standing seats as the work needs them ([GOVERNANCE.md](GOVERNANCE.md)).
3. Use [the Codex entry point](AGENTS.md) or [the Claude Code adapter](HOST_CLAUDE_CODE.md), according to your installed tools.
4. Give the pair a bounded task with explicit acceptance criteria. Preserve the work, evidence, decisions, and handoffs in the provided templates.
5. Follow the runtime installation and example instructions below when adopting the executable components.

The files are useful on their own as a governance framework. Runtime enforcement depends on the specific capabilities you install and configure. Each mechanism’s documentation distinguishes an instruction from a control that code actually enforces.

## Run the public example

Use Python 3.11 or newer and local PostgreSQL server tools. The example creates its own disposable database, synthetic workers, and signing keys. It exercises an objective through signed review and completion, restarts PostgreSQL, and checks the recovered state. It makes no model calls and performs no external business actions.

```sh
git clone https://github.com/jflaig13/cc-suite.git
cd cc-suite
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
export CC_SUITE_TEST_PG_BIN="$(pg_config --bindir)"
export CC_SUITE_COMPANY_ID=example-company
export CC_SUITE_SCRIBE_DEPLOYMENT_ID=Scribe-Example
python -m fleet_kernel.example
```

`CC_SUITE_TEST_PG_BIN` must contain `initdb` and `pg_ctl`. See [the runtime guide](docs/runtime.md) for configuration, database setup, and component boundaries.

With Bash, zsh, and Bun 1.3.12 also installed for the host/channel checks, run:

```sh
python tools/check_distribution.py
python -m unittest discover -s tools -p 'test_*.py' -v
python -m pytest tests -q
```

Database checks require PostgreSQL; an unavailable database is a failed prerequisite. The [host guide](docs/host-runtime.md) gives the full channel build and verification commands. CI runs both language suites and rebuilds both channel bundles.

**Live fleet activation requires deployment-specific authority and configuration.** The public package includes the current source and verified synthetic workflows. Installing it does not establish a founder mandate, admitted workers, operational credentials, or a running fleet. The runtime guide identifies those prerequisites and the code paths that refuse to proceed without them.

## What changed in the October 24, 2026 refresh

- **Thirteen prohibited behaviors.** Section 0 of the operating core now lists every class: self-inflicted gates, fail-closed customer paths, hidden operating numbers, invented schedules, cross-tenant state, re-buying proven work, claims without a look, speaking for the owner, ceremonies, fallbacks as the path, one-off patches, urgency judgments, and handing the owner the crew's work.
- **Rules serve the core.** Every rule is a best guess at how the organization lives its core way of treating customers. An agent that finds a rule causing a snag changes it and says why; changes that loosen a customer protection go to the owner first.
- **Gates must protect.** A check, hook, test or hold stays only while it protects against a net negative impact greater than its own cost.
- **Three skeptics and one convergence loop.** Work judged by others is judged by three reviewers from three model families on three questions (does it work, is it lean, does it follow the core way), majority deciding. The [convergence loop](governance/ASYMPTOTE_PROTOCOL.md) replaces the quality ladder, the V-Loop and two-pass cross-model review. Every loop takes the fewest rounds and tokens and goes to the owner after 7 rounds.
- **The organization and its seats.** The org chart, the seat pattern (resume identity, triggers, standing audit, sourced work, handoff), the company bus (every dispatch names its source, no turn without one) and the one board are in [GOVERNANCE.md](GOVERNANCE.md).
- **Cases replace strikes.** A miss is a case naming the missing rail; the fix is that control.
- **Pit crew and racer.** The sessions that build the system never do what the system's own agents are built to do, read outcomes from the record instead of polling, and end when their hand-ins are in.
- **Every rule names its enforcer**, in a rule–enforcer table ([TOLD_AND_BOUND.md](governance/TOLD_AND_BOUND.md)).
- **Skills and lanes.** Reusable procedures are skills with trigger-bearing descriptions, indexed by a shorthand table; [build-lane](.claude/agents/build-lane.md) and [read-lane](.claude/agents/read-lane.md) agent definitions are included ([HOST_CLAUDE_CODE.md](HOST_CLAUDE_CODE.md)).
- **Decisions as one-taps.** A decision that is the owner's is put as one question with its full context, the recommended option first, and closed the same turn ([FLEET_DECISION_ROUTING_LAYER.md](governance/FLEET_DECISION_ROUTING_LAYER.md)).

Superseded protocols are kept as records and listed in [GOVERNANCE.md](GOVERNANCE.md).

## What changed in the September 4, 2026 release

- **Operations first.** The operating core is the common authority across hosts. Culture and values are documented separately from execution rules.
- **Complete outcomes.** The original objective remains the completion criterion through implementation, review, publication, and verification. A finished subtask does not close an unfinished objective.
- **Durable work and evidence.** The current Company runtime and its portable configuration accompany the framework.
- **Independent review.** The quality ladder included another model family, exact-subject evidence, and clear handling of unresolved findings (superseded on October 24 by three skeptics and the convergence loop).
- **Explicit authority.** Role names, messages, and confidence scores do not grant permissions. Consequential actions preserve their designated approval boundary.
- **Corrections that last.** Material lessons become tests, schemas, or executable controls where possible, with an honest distinction between policy and enforcement.
- **Host separation.** Shared operating rules and tool-specific entry points have separate homes. Capabilities are verified on the installed host.

## Find a protocol

| Topic | Start here |
|---|---|
| Organization, seats, bus and accountability | [GOVERNANCE.md](GOVERNANCE.md), [role template](governance/roles/ROLE_TEMPLATE.md) |
| Whole-objective completion | [NO_SILENT_SCOPE_REDUCTION.md](governance/NO_SILENT_SCOPE_REDUCTION.md) |
| Authority and evidence | [AUTHORITY_AND_EVIDENCE.md](governance/AUTHORITY_AND_EVIDENCE.md) |
| Instructions and executable controls | [TOLD_AND_BOUND.md](governance/TOLD_AND_BOUND.md) |
| Verification | [ATOMIC_VERIFICATION_PROTOCOL.md](governance/ATOMIC_VERIFICATION_PROTOCOL.md) |
| Review and convergence | [HARNESS_CORE.md](HARNESS_CORE.md) section 8, [ASYMPTOTE_PROTOCOL.md](governance/ASYMPTOTE_PROTOCOL.md) |
| Decisions put to the owner | [FLEET_DECISION_ROUTING_LAYER.md](governance/FLEET_DECISION_ROUTING_LAYER.md) |
| Skills, lanes and host entry | [HOST_CLAUDE_CODE.md](HOST_CLAUDE_CODE.md), [AGENTS.md](AGENTS.md) |
| Communication | [CHANNEL_PROTOCOL.md](governance/CHANNEL_PROTOCOL.md) |
| Session continuity | [SESSION_MANAGEMENT.md](governance/SESSION_MANAGEMENT.md), [handoff templates](governance/handoffs/) |
| Persistent learning | [SKILLIFICATION_PROTOCOL.md](governance/SKILLIFICATION_PROTOCOL.md) |
| Writing | [WRITING_CANON.md](governance/WRITING_CANON.md) |

## Public scope

This repository is the canonical portable distribution of CC-Suite. Mise’s private repository remains the reference implementation for its company and restaurant operations. Public updates are deliberate releases, not automatic copies of the private working tree.

The distribution excludes credentials, customer records, private operational history, actual deployment mandates, live account configuration, and restaurant payroll, ordering, or inventory business logic. Configuration and examples use synthetic identities. Installing this package grants no access to Mise’s infrastructure.

The [runtime source inventory](provenance/runtime-source-inventory.json) and [governance inventory](provenance/governance-source-inventory.json) identify the reference revision and portability changes. Tests and example results describe this package; they do not certify an installation on a different host or a live customer workflow.

## Author and license

Built by [Jonathan Flaig](https://github.com/jflaig13), founder of [Mise](https://getmise.io).

Copyright © 2026 Mise, Inc., for its own contributions. Software is licensed
under [MPL-2.0](LICENSE); documentation and governance prose are licensed under
[CC BY-SA 4.0](LICENSES/CC-BY-SA-4.0.txt). See [COPYING.md](COPYING.md) for the
scope, embedded code examples, third-party exceptions and earlier releases.
Private modifications do not have to be published merely because you make them.

Bundled dependencies retain their [upstream notices](THIRD_PARTY_NOTICES.md).
[Trademark claims](TRADEMARKS.md) are separate from copyright permissions.
“CC” stands for Chief Code. Agent accountability and termination records
concern AI deployments, not human employment.
