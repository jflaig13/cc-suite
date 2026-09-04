-- M7 specialist work / role queue shadow. Legacy queue files remain written
-- and authoritative until the final demotion step.

CREATE TABLE IF NOT EXISTS m7_role_queue_bindings (
    tenant_id          TEXT NOT NULL,
    role               TEXT NOT NULL,
    source_key         TEXT NOT NULL,
    legacy_ordinal     BIGINT NOT NULL CHECK (legacy_ordinal >= 0),
    legacy_line        TEXT NOT NULL,
    legacy_bytes       BYTEA NOT NULL,
    legacy_sha256      TEXT NOT NULL CHECK (length(legacy_sha256) = 64),
    event_type         TEXT NOT NULL,
    content            TEXT NOT NULL,
    metadata           JSONB NOT NULL DEFAULT '{}'::jsonb,
    message_kind       TEXT NOT NULL CHECK (
        message_kind IN ('work', 'observability', 'quarantined_legacy')
    ),
    objective_id       UUID,
    delivery_state     TEXT NOT NULL CHECK (
        delivery_state IN ('pending', 'delivered', 'observed', 'quarantined')
    ),
    synced_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, role, source_key),
    UNIQUE (tenant_id, role, legacy_ordinal),
    CHECK (
        (message_kind = 'work' AND objective_id IS NOT NULL
         AND delivery_state IN ('pending', 'delivered'))
        OR (message_kind = 'observability' AND objective_id IS NULL
            AND delivery_state = 'observed')
        OR (message_kind = 'quarantined_legacy' AND objective_id IS NULL
            AND delivery_state = 'quarantined')
    )
);
CREATE INDEX IF NOT EXISTS idx_m7_role_pending
    ON m7_role_queue_bindings (tenant_id, role, delivery_state, legacy_ordinal);
CREATE INDEX IF NOT EXISTS idx_m7_replay_event_role_source
    ON events (tenant_id, (payload->>'role'), (payload->>'source_key'), stream_seq)
    WHERE event_type = 'm7.role_queue.replay.v2';
CREATE TABLE IF NOT EXISTS m7_role_queue_snapshots (
    id                    UUID PRIMARY KEY,
    tenant_id             TEXT NOT NULL,
    role                  TEXT NOT NULL,
    cycle_key             TEXT NOT NULL,
    legacy_sha256         TEXT NOT NULL CHECK (length(legacy_sha256) = 64),
    kernel_render_sha256  TEXT NOT NULL CHECK (length(kernel_render_sha256) = 64),
    cursor_line           BIGINT NOT NULL CHECK (cursor_line >= 0),
    row_count             BIGINT NOT NULL CHECK (row_count >= 0),
    work_rows             BIGINT NOT NULL CHECK (work_rows >= 0),
    pending_rows          BIGINT NOT NULL CHECK (pending_rows >= 0),
    observability_rows    BIGINT NOT NULL CHECK (observability_rows >= 0),
    quarantined_rows      BIGINT NOT NULL CHECK (quarantined_rows >= 0),
    changed_rows          BIGINT NOT NULL CHECK (changed_rows >= 0),
    verdict               TEXT NOT NULL CHECK (verdict = 'clean'),
    compared_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, role, cycle_key),
    CHECK (work_rows + observability_rows + quarantined_rows = row_count),
    CHECK (pending_rows <= work_rows),
    CHECK (cursor_line <= row_count)
);
CREATE INDEX IF NOT EXISTS idx_m7_snapshot_retention
    ON m7_role_queue_snapshots (tenant_id, compared_at);
CREATE INDEX IF NOT EXISTS idx_m7_snapshot_hot_read
    ON m7_role_queue_snapshots (tenant_id, role, legacy_sha256, compared_at DESC);
