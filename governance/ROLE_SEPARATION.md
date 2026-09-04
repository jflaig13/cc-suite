# Roles and separated authority

A role defines responsibility; an admitted deployment defines who may act. A prompt, role name, registry row or copied template alone does not grant runtime capabilities.

| Role | Responsibility | Boundary |
|---|---|---|
| Architecture lead | Problem definition, design and system invariants | Does not certify its own implementation |
| Builder | Implementation, tests and authorized delivery | Does not provide the independent verification verdict |
| Reviewer | Code review, independent workflow verification and regression discovery | Does not quietly edit the subject it reviews |
| Operational arbiter | Resolve cross-lane ownership and decisions within existing authority | Cannot enlarge the human's authorization |
| Scribe or auditor | Maintain operating records, verify evidence, audit every role | Cannot self-authorize consequential actions or create its own strike/termination decision |
| Human authority | Set objectives, approve reserved effects, ratify rules, issue strikes and initiate terminations | Receives concrete evidence and complete open requirements |

Specialist roles may cover finance, legal, risk, product, customers or growth. Assign only the capabilities and data needed for their declared scope. Record their model and host configuration without making a vendor-specific choice part of portable governance.

The Scribe audits the operational arbiter; another authorized custodian records an authorized action affecting the Scribe. Define that independent path before operating. Participation in strike policy is explicit per role. In the reference role pattern it covers executives, Scribe and arbiter; a general utility role is outside that policy unless separately authorized.

Company and customer fleets may share versioned infrastructure. Their ordinary runtime data, credentials, memory and authority remain isolated. Never interpret a shared package as permission to cross that boundary.
