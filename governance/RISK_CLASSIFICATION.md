# Engineering Risk Classification

Every file in your codebase belongs to exactly one **Tier**. Every proposed change has exactly one **Difficulty Grade**. The combination determines required caution.

---

## Tiers (Subsystem Risk)

| Tier | Name | Description | Example |
|------|------|-------------|---------|
| **S** | Safety-Critical | A bug directly causes financial harm, data corruption, or trust destruction | Payment calculations, approval flows, auth systems |
| **A** | High-Impact | A bug degrades experience significantly or causes data inconsistency | API integrations, session management, data storage |
| **B** | Moderate-Impact | A bug causes inconvenience or cosmetic issues | UI routes, templates, display logic |
| **C** | Low-Impact | A bug affects dev ergonomics or internal tooling | Scripts, tests, docs, config |

## Difficulty Grades (Change Complexity)

| Grade | Name | Description | Example |
|-------|------|-------------|---------|
| **EDG-0** | Trivial | Zero behavioral impact | Typo in comment, whitespace |
| **EDG-1** | Simple | Single-file, clear scope | Fix obvious bug, update config value |
| **EDG-2** | Moderate | Multi-file, requires context | New function, route change, prompt edit |
| **EDG-3** | Complex | Architectural, cross-cutting | New subsystem, schema change, new integration |
| **EDG-4** | Critical | Tier S + EDG-3 complexity | Financial calculation changes, approval flow restructuring |

---

## Decision Matrix

| | EDG-0 | EDG-1 | EDG-2 | EDG-3 | EDG-4 |
|---|---|---|---|---|---|
| **Tier S** | State classification. Careful. | Explicit approval required. | Design-first MANDATORY. | Formal plan required. | Formal directive REQUIRED. Full audit trail. |
| **Tier A** | Proceed. State classification. | Normal caution. Approval. | Design-first recommended. | Design-first MANDATORY. | N/A |
| **Tier B** | Proceed freely. | State classification. | Normal caution. | Design-first MANDATORY. | N/A |
| **Tier C** | Proceed freely. | Proceed freely. | State classification. | Design-first recommended. | N/A |

---

## Non-Negotiable Rules

1. **Silent refactors in Tier S are absolutely prohibited.** No renaming, reorganizing, or "cleaning up" Tier S files unless that specific change was requested.
2. **Scope creep in Tier S/A is absolutely prohibited.** If asked to fix X, fix X. Do not also fix Y.
3. **When in doubt, classify UP.** More caution never hurts.
4. **State classification before writing code.** "This is Tier A x EDG-2. Proceeding."

---

## Your Tier Assignments

Fill this in for your codebase:

### Tier S — Safety-Critical
| Subsystem | Key Files | Why Tier S |
|-----------|-----------|------------|
| | | |

### Tier A — High-Impact
| Subsystem | Key Files | Why Tier A |
|-----------|-----------|------------|
| | | |

### Tier B — Moderate-Impact
| Subsystem | Key Files | Why Tier B |
|-----------|-----------|------------|
| | | |

### Tier C — Low-Impact
| Subsystem | Key Files | Why Tier C |
|-----------|-----------|------------|
| | | |
