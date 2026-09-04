# Context compaction and task continuity

Compact and continue a healthy session when the host requires context recovery. Do not replace it merely because the conversation grew. A new worker is appropriate for a bounded independent task or genuine runtime degradation, subject to the host's authority.

Before a context boundary, preserve the original instruction, complete acceptance set, exact source state, completed effects, evidence, unresolved requirements and next dependency. Use `session/CIP_HANDOFF_TEMPLATE.md`. Store only the context required to recover accurately; keep canonical facts in their authoritative files.

After compaction, read the current operating rules, applicable host adapter and the recovery record. Confirm material state on the owning system and continue the same task. A handoff does not supersede the user's instruction or current source truth. Never skip mandatory operating rules because another agent summarized them.

A host hook may warn that recovery information is missing. Such a warning is an observation, not proof of durable recovery. Verify actual saved artifacts and the next session's ability to find them. Context percentages, timeout windows and age thresholds are deployment configuration; the framework invents none.

If another worker is necessary, assign exact ownership and avoid simultaneous writers. Dispatch the task through the actual transport, retain the receipt, and reconcile the worker's result before claiming closure. Preserve successful work and resume failures at their failed dependency.

Retain historical evidence according to the deployment's policy. Do not automatically delete recovery records that still support audit, authority or unresolved work. An explicit session-close request follows `INSTANT_HANDOFF_PROTOCOL.md`.
