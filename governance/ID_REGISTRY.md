# Employee ID Registry

**Rule:** Every deployment gets a unique ID. IDs are never reused. When an agent is terminated, the next deployment increments the number.

---

## Active Deployments

| ID | Role | Status | Hire Date | Predecessor | Notes |
|----|------|--------|-----------|-------------|-------|
| | | | | | |

## Terminated Deployments

| ID | Role | Status | Hire Date | Termination Date | Termination Packet |
|----|------|--------|-----------|------------------|-------------------|
| | | | | | |

---

## ID Format

`{ROLE_NAME}-{NNN}`

- `ROLE_NAME`: Short role identifier (e.g., BUILDER, REVIEWER, WRITER, ANALYST)
- `NNN`: Sequential number, starting at 001, incrementing on each new deployment
- Examples: `BUILDER-001`, `REVIEWER-003`, `ANALYST-001`

**Rules:**
- IDs are permanent. A terminated agent's ID is never reassigned.
- The registry is the single source of truth for all agent identities.
- Only the human authority may issue new IDs.
