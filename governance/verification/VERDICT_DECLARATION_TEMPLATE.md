# Verification verdict

**Objective:** {original acceptance criteria}
**Exact subject:** {commit/tree or artifact hashes}
**Environment:** {observed version and location class}
**Input basis:** {authoritative source revision}
**Verifier and independence:** {actor, method and separate basis}
**Extraction / comparison / manifest:** {references}

**Verdict:** {CLEAN / FAILURES FOUND / INCOMPLETE}

- `CLEAN`: every required comparison is present, the independent basis is established, no mismatches remain, and all counts reconcile.
- `FAILURES FOUND`: at least one mismatch exists. List every mismatch and its consequence.
- `INCOMPLETE`: required observations, authoritative expected values, comparisons or evidence are missing. List the full remainder. Known failures remain listed even when evidence is also incomplete.

**Required / completed / mismatched / missing:** {reconciled counts}
**Persistence, downstream and failure-path evidence:** {references}
**Open requirements and findings:** {complete list or verified none}
**Overall objective status:** {COMPLETE / INCOMPLETE / BLOCKED}

The whole objective closes only when its original acceptance surface and required independent reviews pass. A clean limited comparison must not be reported as complete deployment or product verification.
