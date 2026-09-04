-- Fleet Kernel M3 runtime substrate (OIL-227).
--
-- Adds only kernel-owned state. Legacy JSONL remains authoritative until the
-- M3 parity and pointer-flip gates execute green.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS cycle_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_tenant_cycle
    ON runs (tenant_id, cycle_key) WHERE cycle_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS event_stream_heads (
    tenant_id   TEXT PRIMARY KEY,
    next_seq    BIGINT NOT NULL CHECK (next_seq >= 1)
);
INSERT INTO event_stream_heads (tenant_id, next_seq)
SELECT tenant_id, MAX(stream_seq) + 1
FROM events
GROUP BY tenant_id
ON CONFLICT (tenant_id) DO UPDATE
SET next_seq = GREATEST(event_stream_heads.next_seq, EXCLUDED.next_seq);

-- M2 already owns ``kernel_outbox``. Extend that table rather than shadowing
-- it with an incompatible second definition. M2 rows keep their adapter and
-- delivery columns; M3 rows additionally bind the authoritative kernel event
-- and its tenant sequence.
ALTER TABLE kernel_outbox ADD COLUMN IF NOT EXISTS event_id UUID;
ALTER TABLE kernel_outbox ADD COLUMN IF NOT EXISTS stream_seq BIGINT;
ALTER TABLE kernel_outbox ADD COLUMN IF NOT EXISTS destination TEXT;
ALTER TABLE kernel_outbox ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE kernel_outbox ADD COLUMN IF NOT EXISTS rendered_at TIMESTAMPTZ;
ALTER TABLE kernel_outbox ADD COLUMN IF NOT EXISTS last_error TEXT NOT NULL DEFAULT '';
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'kernel_outbox_status_check'
          AND conrelid = 'kernel_outbox'::regclass
    ) THEN
        ALTER TABLE kernel_outbox ADD CONSTRAINT kernel_outbox_status_check
            CHECK (status IN ('pending', 'rendered', 'failed'));
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_outbox_event
    ON kernel_outbox (event_id) WHERE event_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_outbox_event_destination
    ON kernel_outbox (tenant_id, stream_seq, destination)
    WHERE stream_seq IS NOT NULL AND destination IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kernel_outbox_pending
    ON kernel_outbox (tenant_id, status, stream_seq);

-- The first sequence governed by the M3 event+outbox invariant. This makes
-- leading, middle, and tail loss detectable without treating pre-M3 events as
-- missing projections.
ALTER TABLE event_stream_heads ADD COLUMN IF NOT EXISTS outbox_start_seq BIGINT;
UPDATE event_stream_heads h
SET outbox_start_seq = seeded.first_seq
FROM (
    SELECT tenant_id, MIN(stream_seq) AS first_seq
    FROM kernel_outbox
    WHERE event_id IS NOT NULL AND stream_seq IS NOT NULL
    GROUP BY tenant_id
) AS seeded
WHERE h.tenant_id = seeded.tenant_id AND h.outbox_start_seq IS NULL;

CREATE TABLE IF NOT EXISTS m3_provider_cost_dedup (
    tenant_id           TEXT NOT NULL,
    provider_request_id TEXT NOT NULL,
    cost_record_id      UUID NOT NULL UNIQUE,
    PRIMARY KEY (tenant_id, provider_request_id)
);

CREATE TABLE IF NOT EXISTS m3_quota_parks (
    tenant_id     TEXT NOT NULL,
    run_id        UUID NOT NULL,
    reason        TEXT NOT NULL,
    resume_at     TIMESTAMPTZ NOT NULL,
    state         TEXT NOT NULL DEFAULT 'parked'
                  CHECK (state IN ('parked', 'resumed', 'cancelled')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resumed_at    TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, run_id)
);

CREATE TABLE IF NOT EXISTS m3_parity_results (
    id                    UUID PRIMARY KEY,
    tenant_id             TEXT NOT NULL,
    run_id                UUID NOT NULL,
    cycle_key             TEXT NOT NULL,
    input_manifest_sha256 TEXT NOT NULL,
    verdict               TEXT NOT NULL CHECK (verdict IN ('clean', 'diverged')),
    differences           JSONB NOT NULL DEFAULT '[]'::jsonb,
    compared_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, cycle_key)
);
ALTER TABLE m3_parity_results
    ADD COLUMN IF NOT EXISTS control_tags JSONB NOT NULL DEFAULT '[]'::jsonb;
