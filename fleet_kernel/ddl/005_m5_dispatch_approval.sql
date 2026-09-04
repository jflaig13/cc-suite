-- Fleet Kernel M5 dispatch-intent + FODL approval shadow migration.
--
-- Legacy JSONL/markdown remain authoritative during shadow. Exact source bytes
-- are retained for parity and replay; only structurally provable dispatch and
-- emit state is projected into Run + Approval.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS dispatch_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_tenant_dispatch_key
    ON runs (tenant_id, dispatch_key) WHERE dispatch_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS m5_dispatch_bindings (
    tenant_id        TEXT NOT NULL,
    source_key       TEXT NOT NULL,
    dispatch_identity TEXT NOT NULL,
    objective_id     UUID NOT NULL,
    run_id           UUID,
    approval_id      UUID,
    legacy_ordinal   BIGINT NOT NULL CHECK (legacy_ordinal >= 0),
    legacy_line      TEXT NOT NULL,
    legacy_sha256    TEXT NOT NULL CHECK (legacy_sha256 ~ '^[0-9a-f]{64}$'),
    legacy_type      TEXT NOT NULL CHECK (legacy_type IN ('oil_dispatch', 'task_handoff', 'fodl')),
    legacy_status    TEXT NOT NULL CHECK (
        legacy_status IN ('pending', 'delivered', 'skipped', 'blocked', 'resolved', 'closed')
    ),
    present          BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, source_key),
    CHECK ((legacy_type = 'fodl') = (approval_id IS NOT NULL)),
    CHECK ((legacy_type <> 'fodl') = (run_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_m5_dispatch_present_order
    ON m5_dispatch_bindings (tenant_id, present, legacy_ordinal);
CREATE INDEX IF NOT EXISTS idx_m5_dispatch_identity
    ON m5_dispatch_bindings (tenant_id, dispatch_identity);

CREATE TABLE IF NOT EXISTS m5_fodl_audit_bindings (
    tenant_id       TEXT NOT NULL,
    source_key      TEXT NOT NULL,
    fodl_id         TEXT,
    objective_id    UUID,
    approval_id     UUID,
    legacy_ordinal  BIGINT NOT NULL CHECK (legacy_ordinal >= 0),
    legacy_line     TEXT NOT NULL,
    legacy_sha256   TEXT NOT NULL CHECK (legacy_sha256 ~ '^[0-9a-f]{64}$'),
    is_emit         BOOLEAN NOT NULL,
    present         BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, source_key),
    CHECK (is_emit = (fodl_id IS NOT NULL AND objective_id IS NOT NULL AND approval_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_m5_fodl_audit_present_order
    ON m5_fodl_audit_bindings (tenant_id, present, legacy_ordinal);

CREATE TABLE IF NOT EXISTS m5_fodl_documents (
    tenant_id        TEXT PRIMARY KEY,
    legacy_bytes     BYTEA NOT NULL,
    legacy_sha256    TEXT NOT NULL CHECK (legacy_sha256 ~ '^[0-9a-f]{64}$'),
    synced_at        TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS m5_surface_snapshots (
    id                    UUID PRIMARY KEY,
    tenant_id             TEXT NOT NULL,
    cycle_key             TEXT NOT NULL,
    dispatch_sha256       TEXT NOT NULL CHECK (dispatch_sha256 ~ '^[0-9a-f]{64}$'),
    dispatch_render_sha256 TEXT NOT NULL CHECK (dispatch_render_sha256 ~ '^[0-9a-f]{64}$'),
    fodl_audit_sha256      TEXT NOT NULL CHECK (fodl_audit_sha256 ~ '^[0-9a-f]{64}$'),
    fodl_audit_render_sha256 TEXT NOT NULL CHECK (fodl_audit_render_sha256 ~ '^[0-9a-f]{64}$'),
    fodl_current_sha256    TEXT NOT NULL CHECK (fodl_current_sha256 ~ '^[0-9a-f]{64}$'),
    dispatch_rows         INTEGER NOT NULL CHECK (dispatch_rows >= 0),
    fodl_audit_rows       INTEGER NOT NULL CHECK (fodl_audit_rows >= 0),
    changed_rows          INTEGER NOT NULL CHECK (changed_rows >= 0),
    verdict               TEXT NOT NULL CHECK (verdict IN ('clean', 'diverged')),
    differences           JSONB NOT NULL DEFAULT '[]'::jsonb,
    compared_at           TIMESTAMPTZ NOT NULL,
    UNIQUE (tenant_id, cycle_key)
);
CREATE INDEX IF NOT EXISTS idx_m5_surface_snapshots_recent
    ON m5_surface_snapshots (tenant_id, compared_at DESC);

-- A decision is immutable and idempotent. The generic Approval row carries
-- current typed state; this table preserves the authority proof and request
-- key that caused the one pending -> approved/denied transition.
CREATE TABLE IF NOT EXISTS m5_approval_decisions (
    tenant_id         TEXT NOT NULL,
    approval_id       UUID NOT NULL,
    decision_key      TEXT NOT NULL,
    decision          TEXT NOT NULL CHECK (decision IN ('approved', 'denied')),
    decided_by        TEXT NOT NULL,
    authority_kind    TEXT NOT NULL CHECK (
        authority_kind IN (
            'founder_direct', 'signed_conveyance',
            'authenticated_operator'
        )
    ),
    authority_ref     TEXT NOT NULL,
    rationale         TEXT NOT NULL,
    decided_at        TIMESTAMPTZ NOT NULL,
    event_id          UUID NOT NULL,
    outbox_id         UUID NOT NULL,
    PRIMARY KEY (tenant_id, approval_id),
    UNIQUE (tenant_id, decision_key),
    UNIQUE (event_id),
    UNIQUE (outbox_id)
);
ALTER TABLE m5_approval_decisions
    DROP CONSTRAINT IF EXISTS m5_approval_decisions_authority_kind_check;
ALTER TABLE m5_approval_decisions
    ADD CONSTRAINT m5_approval_decisions_authority_kind_check CHECK (
        authority_kind IN (
            'founder_direct', 'signed_conveyance',
            'authenticated_operator'
        )
    );
CREATE UNIQUE INDEX IF NOT EXISTS idx_m5_signed_conveyance_consume_once
    ON m5_approval_decisions (tenant_id, authority_ref)
    WHERE authority_kind = 'signed_conveyance';

-- Consumption does not claim an outside effect completed. It atomically
-- converts one approved decision into exactly one idempotent outbox request;
-- the adapter remains responsible for at-least-once delivery and receipt.
CREATE TABLE IF NOT EXISTS m5_approval_consumptions (
    tenant_id         TEXT NOT NULL,
    approval_id       UUID NOT NULL,
    consume_key       TEXT NOT NULL,
    consumer          TEXT NOT NULL,
    effect_adapter    TEXT NOT NULL,
    effect_payload    JSONB NOT NULL,
    consumed_at       TIMESTAMPTZ NOT NULL,
    event_id          UUID NOT NULL,
    outbox_id         UUID NOT NULL,
    PRIMARY KEY (tenant_id, approval_id),
    UNIQUE (tenant_id, consume_key),
    UNIQUE (event_id),
    UNIQUE (outbox_id)
);
