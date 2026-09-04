# Interactive worker lifecycle

An interactive worker receives human or peer input during an ongoing session. It may run in a terminal, desktop host or admitted runtime. Its role, identity and capability boundary remain explicit; a UI window does not by itself prove admission or authority.

Onboard through `../LIFECYCLE.md`. Read the shared operating rules, the installed host adapter, role definition, current task and applicable domain sources. Record which communication, browser, supervision and recovery mechanisms have actually been verified.

Configure the supported transport for the deployment. A local queue, file bus, hosted broker or in-process channel may be appropriate. Ports, shell aliases, polling processes and launch flags are adapter choices, not universal prerequisites. Verify the actual path from sender through durable receipt to the intended worker.

Acknowledge receipt distinctly from processing and completion. Typed verdicts carry exact subject and evidence references. A required phrase alone does not prove an audit. Follow the authorized external-acknowledgment policy without creating acknowledgment loops or unauthorized messages.

Maintain one writer for a shared mutable subject. Multiple workers may share a role vocabulary when identity, ownership and transport state are distinct. Do not reuse an active deployment ID for another worker.

Recover interrupted work from durable state. Verify ownership before restarting or cleaning up another process. Preserve pending messages and effect idempotency; report actual transport failure instead of assuming silence means healthy work.

Routine compaction and session close follow `../SESSION_MANAGEMENT.md` and `../INSTANT_HANDOFF_PROTOCOL.md`. Termination requires the human decision and runtime revocation evidence described in `../LIFECYCLE.md`.
