-- Fleet Kernel M4 handoff-ledger shadow migration.
--
-- The legacy JSONL remains authoritative during shadow/parity. Each source row
-- maps to one Objective and one OwnershipClaim; the exact legacy line is kept
-- so rollback/read compatibility can be proven byte-for-byte before cutover.

CREATE TABLE IF NOT EXISTS m4_handoff_bindings (
    tenant_id          TEXT NOT NULL,
    source_key         TEXT NOT NULL,
    handoff_id         TEXT NOT NULL,
    objective_id       UUID NOT NULL,
    ownership_claim_id UUID NOT NULL,
    legacy_ordinal     BIGINT NOT NULL CHECK (legacy_ordinal >= 0),
    legacy_line        TEXT NOT NULL,
    legacy_sha256      TEXT NOT NULL CHECK (legacy_sha256 ~ '^[0-9a-f]{64}$'),
    legacy_status      TEXT NOT NULL CHECK (legacy_status IN ('open', 'closed', 'escalated')),
    present            BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, source_key)
);
CREATE INDEX IF NOT EXISTS idx_m4_handoff_id
    ON m4_handoff_bindings (tenant_id, handoff_id);
CREATE INDEX IF NOT EXISTS idx_m4_handoff_present_order
    ON m4_handoff_bindings (tenant_id, present, legacy_ordinal);

CREATE TABLE IF NOT EXISTS m4_handoff_snapshots (
    id                  UUID PRIMARY KEY,
    tenant_id           TEXT NOT NULL,
    cycle_key           TEXT NOT NULL,
    legacy_sha256       TEXT NOT NULL CHECK (legacy_sha256 ~ '^[0-9a-f]{64}$'),
    kernel_render_sha256 TEXT NOT NULL CHECK (kernel_render_sha256 ~ '^[0-9a-f]{64}$'),
    legacy_rows         INTEGER NOT NULL CHECK (legacy_rows >= 0),
    kernel_rows         INTEGER NOT NULL CHECK (kernel_rows >= 0),
    changed_rows        INTEGER NOT NULL CHECK (changed_rows >= 0),
    verdict             TEXT NOT NULL CHECK (verdict IN ('clean', 'diverged')),
    differences         JSONB NOT NULL DEFAULT '[]'::jsonb,
    compared_at         TIMESTAMPTZ NOT NULL,
    UNIQUE (tenant_id, cycle_key)
);
CREATE INDEX IF NOT EXISTS idx_m4_handoff_snapshots_recent
    ON m4_handoff_snapshots (tenant_id, compared_at DESC);
