-- M2 proving spike DDL (OIL-227) — the kernel lease module + synthetic
-- payroll-shaped state the D4 spike chaos-tests against. Zero contact with
-- any real tenant data (D4/07's premise): the ledger below is fake money,
-- never touching mise_app/ storage or a real payroll path.

-- Monotonic, DB-issued fencing tokens — the D4 Codex-round addition. A
-- sequence (not a per-row counter) is what makes the token GLOBALLY
-- monotonic across every resource, so "worker A's token=7 is older than
-- worker B's token=9" is always comparable regardless of which resource
-- either was issued for.
CREATE SEQUENCE IF NOT EXISTS kernel_fencing_seq;

-- Heartbeat leases: overlap group #6 done for real (Ownership claim +
-- heartbeat lease, per 02_state_surface_map.md) — liveness is an
-- unexpired lease, contended for via a single atomic UPSERT (Postgres's
-- own row-level locking on the unique constraint makes the race safe with
-- no application-level mutex).
CREATE TABLE IF NOT EXISTS kernel_leases (
    id              UUID PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    resource_key    TEXT NOT NULL,
    owner_token     TEXT NOT NULL,
    fencing_token   BIGINT NOT NULL,
    acquired_at     TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    UNIQUE (tenant_id, resource_key)
);

-- Synthetic payroll ledger — fake money, fake accounts, exists ONLY for
-- the payroll-hard chaos tests (compensation-of-a-partial-workflow,
-- reconciles-to-zero). last_fencing_token is the write-side half of the
-- fencing protocol: a write is accepted only if its token is STRICTLY
-- greater than what's already recorded (the stale-owner-resumes-and-
-- writes refusal the D4 Codex round demanded).
CREATE TABLE IF NOT EXISTS synthetic_payroll_ledger (
    id                   UUID PRIMARY KEY,
    tenant_id            TEXT NOT NULL,
    account              TEXT NOT NULL,
    balance_cents        BIGINT NOT NULL DEFAULT 0,
    last_fencing_token   BIGINT NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, account)
);

-- Transactional outbox — exactly-once EFFECT defined honestly as
-- at-least-once delivery + an idempotency key the external adapter
-- honors (D4's own framing: "never asserted as magic").
CREATE TABLE IF NOT EXISTS kernel_outbox (
    id                UUID PRIMARY KEY,
    tenant_id         TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL,
    adapter           TEXT NOT NULL,
    payload           JSONB NOT NULL,
    delivered         BOOLEAN NOT NULL DEFAULT FALSE,
    attempts          INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);

-- Dead-letter queue — visible + redrivable, not just "dropped."
CREATE TABLE IF NOT EXISTS kernel_dead_letters (
    id             UUID PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    resource_key   TEXT NOT NULL,
    reason         TEXT NOT NULL,
    payload        JSONB NOT NULL,
    attempts       INTEGER NOT NULL,
    redriven       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
