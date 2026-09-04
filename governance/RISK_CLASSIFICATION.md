# Engineering risk classification

Classify the affected behavior before implementation. File type alone does not determine risk: a prompt, test, configuration or operating rule can control a consequential system.

| Tier | Consequence of a defect |
|---|---|
| S | Financial harm, data corruption, unauthorized effects or trust destruction |
| A | Significant degradation, inconsistent durable state or broad workflow failure |
| B | Bounded inconvenience or cosmetic behavior |
| C | Internal ergonomics with no higher-risk downstream effect |

| Grade | Change complexity |
|---|---|
| EDG-0 | No behavioral change |
| EDG-1 | Clear, contained change |
| EDG-2 | Multiple files or meaningful workflow interactions |
| EDG-3 | Architectural or cross-cutting change |
| EDG-4 | Tier S work with architectural complexity |

Tier S requires explicit scope authority. Design first for Tier S at EDG-2 or above and for architectural work in every tier. EDG-4 requires a formal directive and full audit trail. Existing user authorization is applied to the whole named sequence; classification must not manufacture repeated approval ceremonies.

Inspect the complete impact before assigning the tier. Include shared state, data boundaries, permissions, upstream inputs and downstream consumers. Record uncertain risk and resolve it using current evidence. Only the human may override a governing classification.

Avoid silent refactors and unrelated scope expansion. Necessary adjacent changes that complete the authorized outcome belong in the declared impact inventory. Choose verification through `QUALITY_VERIFICATION_LADDER.md`; a low-risk label cannot waive an applicable required check.
