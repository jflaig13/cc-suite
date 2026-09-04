-- M6 spend shadow: legacy envelope rows are budget reservations, never actual cost.
-- The protected legacy envelope remains authoritative through read cutover.

CREATE TABLE IF NOT EXISTS m6_spend_bindings (
    tenant_id          TEXT NOT NULL,
    source_key         TEXT NOT NULL,
    legacy_ordinal     BIGINT NOT NULL CHECK (legacy_ordinal >= 0),
    legacy_line        TEXT NOT NULL,
    legacy_sha256      TEXT NOT NULL CHECK (length(legacy_sha256) = 64),
    recorded_at        TIMESTAMPTZ NOT NULL,
    run_type           TEXT NOT NULL,
    return_code        INTEGER NOT NULL,
    budget_usd         NUMERIC(12,6),
    ledger_tenant      TEXT,
    idempotency_key    TEXT,
    objective_id       UUID,
    run_id             UUID,
    attribution_status TEXT NOT NULL CHECK (
        attribution_status IN ('attributed', 'unattributed_legacy')
    ),
    present            BOOLEAN NOT NULL DEFAULT TRUE,
    synced_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source_key),
    UNIQUE (tenant_id, legacy_ordinal),
    CHECK (budget_usd IS NOT NULL OR idempotency_key IS NOT NULL),
    CHECK (
        (attribution_status = 'attributed' AND objective_id IS NOT NULL AND run_id IS NOT NULL)
        OR (attribution_status = 'unattributed_legacy' AND objective_id IS NULL AND run_id IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_m6_spend_attribution
    ON m6_spend_bindings (tenant_id, attribution_status, recorded_at);

CREATE TABLE IF NOT EXISTS m6_spend_snapshots (
    id                    UUID PRIMARY KEY,
    tenant_id             TEXT NOT NULL,
    cycle_key             TEXT NOT NULL,
    legacy_sha256         TEXT NOT NULL CHECK (length(legacy_sha256) = 64),
    kernel_render_sha256  TEXT NOT NULL CHECK (length(kernel_render_sha256) = 64),
    row_count             BIGINT NOT NULL CHECK (row_count >= 0),
    attributed_rows       BIGINT NOT NULL CHECK (attributed_rows >= 0),
    unattributed_rows     BIGINT NOT NULL CHECK (unattributed_rows >= 0),
    changed_rows          BIGINT NOT NULL CHECK (changed_rows >= 0),
    verdict               TEXT NOT NULL CHECK (verdict = 'clean'),
    compared_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, cycle_key),
    CHECK (attributed_rows + unattributed_rows = row_count)
);

CREATE OR REPLACE VIEW m6_actual_cost_attribution AS
SELECT tenant_id, objective_id, run_id, usd_amount, model, tokens_in, tokens_out, recorded_at
FROM cost_records;
