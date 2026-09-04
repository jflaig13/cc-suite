# Mechanism verification attestation

Before claiming that an operating rule is enforced by a mechanism, enumerate its entire claim surface and verify each applicable claim against the actual implementation and behavior. A document's description of a file, queue, hook, schema or API is not independent evidence that it works.

## Required record

**Original objective and claim scope:** {reference}
**Exact implementation subject:** {commit/tree or artifact hashes}
**Environment and data/source revision:** {identity}
**Observer and independent method:** {identity and method}

| Claim/surface | Implementation location | Test or observation | Evidence | State |
|---|---|---|---|---|
| {claim} | {file:symbol or schema} | {actual method} | {reference} | {CONFIRMED / PENDING / FALSIFIED} |

For a durable inbox, the surface can include initial receipt, restart recovery, concurrent claims, acknowledgment persistence, corruption handling and failed-delivery recovery. Enumerate the actual relevant set before testing a subset.

An attestation is `RESOLVED OPERATIONAL` only when the whole required surface is confirmed in the required environment. Otherwise it is `PARTIALLY RESOLVED`, with every pending or falsified claim explicit. Distinguish a public protocol, implemented code, passing isolated tests and a running integration.

The reviewer reads the cited implementation, checks that the invoking path exists, validates the evidence and tests the relevant failure boundary. An allowed verdict word, checksum, registry row or screenshot cannot by itself prove a consequential effect.

An empirical contradiction reopens the affected claim. A material source or configuration change makes affected prior evidence stale. Preserve the old receipt, repair the mechanism, rerun the affected checks and issue a new exact-subject attestation.

Only the human ratifies operating-rule changes. The auditor can report missing attestation evidence and withhold a verification verdict; it does not invent authority to ratify or issue a strike. See `AUTHORITY_AND_EVIDENCE.md`, `TOLD_AND_BOUND.md` and `VERIFICATION_INDEPENDENCE.md`.
