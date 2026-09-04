# Mechanism attestation

A mechanism attestation ties an operational claim to an exact implementation and observed behavior. It records what the system enforces, how it enforces it, where the boundary ends, and what evidence supports that statement.

Enumerate the complete claim surface first. For each claim, identify the function or schema, its callers and relevant consumers, the subject revision, environment, input basis and independent test. Include persistence, permission failures, restart/retry behavior and downstream effects where applicable.

Distinguish Told from Bound. A document requiring verification does not prove the verifier runs. A required verdict phrase does not prove the underlying observations exist. A hash proves content identity, not a successful effect.

Every enumerated claim needs its own evidence. A partial result is PARTIALLY RESOLVED with an explicit open set. RESOLVED OPERATIONAL requires the complete applicable surface to have been observed in the required environment.

An observed contradiction reopens the affected attestation. A material implementation change makes the prior evidence stale for the changed behavior. Preserve the old record, repair the mechanism, rerun affected checks and write a new attestation tied to the new subject.

See `../MECHANISM_VERIFICATION_ATTESTATION.md`, `../TOLD_AND_BOUND.md` and `../VERIFICATION_INDEPENDENCE.md`.
