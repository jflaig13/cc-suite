# Verification manifest

**Original objective:** {instruction and acceptance criteria}
**Subject and source revision:** {identity}
**Affected surfaces and consumers:** {complete inventory}
**Roles, tenants and environments:** {applicable set}
**Exclusions:** {explicit rationale and authority where required}

| Requirement ID | Field/behavior | Authoritative expected source | Verification method | Evidence destination |
|---|---|---|---|---|
| {ID} | {requirement} | {source/revision} | {independent method} | {path} |

Cover applicable user controls, canonical writes, projections, permission boundaries, persistence, downstream exports, failure states, retry, interruption and recovery. A required field cannot disappear because extraction is inconvenient.

| Domain tolerance | Exact rule | Rationale | Human authorization reference |
|---|---|---|---|
| {only if approved} | {explicit rule} | {reason} | {record} |

Do not invent example financial or time tolerances. A deployment supplies its actual governing rules before verification. Where no tolerance is authorized, compare exactly.

Record how the verifier's input basis differs from the implementation's derived data and shared code. Count reconciliation, known-defect rejection and complete field coverage are necessary parts of the verification design.
