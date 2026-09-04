-- Fleet Kernel — M1 Phase-1 schema DDL (OIL-227).
--
-- Mirrors fleet_kernel/schemas.py's 10 dataclasses exactly — keep the two in
-- lockstep; tests/test_tier1_kernel_schemas.py's round-trip suite is the
-- drift guard (a field added to one and not the other fails the test).
--
-- Every table carries tenant_id NOT NULL (Universal Tenant Isolation
-- Invariant — no shared-default tenant, no cross-tenant row ever). Phase 4
-- (Structural Isolation) is what adds per-tenant DB principals + RLS on top
-- of this schema; M1 is schema-only, zero runtime contact, so RLS/grants are
-- deliberately NOT part of this file (that is Phase 4's acceptance bar, not
-- M1's — see cc_execs/memos/ccto/fleet_kernel_phase0_packet/08_acceptance_criteria.md).

CREATE TABLE IF NOT EXISTS objectives (
    id                       UUID PRIMARY KEY,
    tenant_id                TEXT NOT NULL,
    schema_version           INTEGER NOT NULL DEFAULT 1,
    title                    TEXT NOT NULL,
    state                    TEXT NOT NULL,
    owner_role               TEXT NOT NULL,
    strategic_objective_ref  TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL,
    updated_at               TIMESTAMPTZ NOT NULL,
    deadline                 TIMESTAMPTZ,
    parent_objective_id      UUID
);
CREATE INDEX IF NOT EXISTS idx_objectives_tenant ON objectives (tenant_id);

CREATE TABLE IF NOT EXISTS runs (
    id                UUID PRIMARY KEY,
    tenant_id         TEXT NOT NULL,
    schema_version    INTEGER NOT NULL DEFAULT 1,
    objective_id      UUID NOT NULL,
    state             TEXT NOT NULL,
    worker_identity   TEXT NOT NULL,
    attempt_number    INTEGER NOT NULL DEFAULT 1,
    started_at        TIMESTAMPTZ,
    ended_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_runs_tenant ON runs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_runs_objective ON runs (objective_id);

CREATE TABLE IF NOT EXISTS events (
    id              UUID PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    stream_seq      BIGINT NOT NULL,
    event_type      TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ NOT NULL,
    objective_id    UUID,
    run_id          UUID
);
CREATE INDEX IF NOT EXISTS idx_events_tenant ON events (tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_tenant_seq ON events (tenant_id, stream_seq);

CREATE TABLE IF NOT EXISTS ownership_claims (
    id              UUID PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    objective_id    UUID NOT NULL,
    owner_role      TEXT NOT NULL,
    lease_token     TEXT NOT NULL,
    acquired_at     TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    released_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ownership_claims_tenant ON ownership_claims (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ownership_claims_objective ON ownership_claims (objective_id);

CREATE TABLE IF NOT EXISTS approvals (
    id              UUID PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    objective_id    UUID NOT NULL,
    requested_by    TEXT NOT NULL,
    decision        TEXT NOT NULL,
    decided_by      TEXT,
    requested_at    TIMESTAMPTZ NOT NULL,
    decided_at      TIMESTAMPTZ,
    rationale       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_approvals_tenant ON approvals (tenant_id);

CREATE TABLE IF NOT EXISTS tool_invocations (
    id               UUID PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    schema_version   INTEGER NOT NULL DEFAULT 1,
    run_id           UUID NOT NULL,
    tool_name        TEXT NOT NULL,
    args_hash        TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    invoked_at       TIMESTAMPTZ NOT NULL,
    completed_at     TIMESTAMPTZ,
    result_code      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tool_invocations_tenant ON tool_invocations (tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_invocations_idem ON tool_invocations (tenant_id, idempotency_key);

CREATE TABLE IF NOT EXISTS verification_results (
    id                  UUID PRIMARY KEY,
    tenant_id           TEXT NOT NULL,
    schema_version      INTEGER NOT NULL DEFAULT 1,
    objective_id        UUID NOT NULL,
    verifier_identity   TEXT NOT NULL,
    verdict             TEXT NOT NULL,
    evidence_ref        TEXT NOT NULL DEFAULT '',
    verified_at         TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verification_results_tenant ON verification_results (tenant_id);

CREATE TABLE IF NOT EXISTS receipts (
    id              UUID PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    objective_id    UUID NOT NULL,
    artifact_ref    TEXT NOT NULL,
    kind            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_receipts_tenant ON receipts (tenant_id);

CREATE TABLE IF NOT EXISTS cost_records (
    id              UUID PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    objective_id    UUID NOT NULL,
    run_id          UUID,
    usd_amount      NUMERIC(12,6) NOT NULL DEFAULT 0,
    model           TEXT NOT NULL DEFAULT '',
    tokens_in       BIGINT NOT NULL DEFAULT 0,
    tokens_out      BIGINT NOT NULL DEFAULT 0,
    recorded_at     TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_records_tenant ON cost_records (tenant_id);
CREATE INDEX IF NOT EXISTS idx_cost_records_objective ON cost_records (objective_id);

CREATE TABLE IF NOT EXISTS incidents (
    id               UUID PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    schema_version   INTEGER NOT NULL DEFAULT 1,
    objective_id     UUID,
    severity         TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    detected_at      TIMESTAMPTZ NOT NULL,
    resolved_at      TIMESTAMPTZ,
    root_cause_ref   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_incidents_tenant ON incidents (tenant_id);
