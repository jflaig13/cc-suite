-- Fleet Kernel durable identity plane.
--
-- Global role releases are content-addressed and immutable. Tenant manifests
-- select exact releases. Runtime workers, sessions, grants, and runs then bind
-- to that manifest chain with composite tenant-aware foreign keys.

CREATE TABLE IF NOT EXISTS kernel_agent_type_releases (
    release_sha256              TEXT PRIMARY KEY CHECK (release_sha256 ~ '^[0-9a-f]{64}$'),
    fleet_type                  TEXT NOT NULL CHECK (fleet_type IN ('company', 'restaurant')),
    role_type                   TEXT NOT NULL CHECK (btrim(role_type) <> ''),
    release_version             INTEGER NOT NULL CHECK (release_version > 0),
    charter                     TEXT NOT NULL CHECK (btrim(charter) <> ''),
    objective_classes           JSONB NOT NULL CHECK (jsonb_typeof(objective_classes) = 'array'),
    allowed_capabilities        JSONB NOT NULL CHECK (jsonb_typeof(allowed_capabilities) = 'array'),
    allowed_network_classes     JSONB NOT NULL CHECK (jsonb_typeof(allowed_network_classes) = 'array'),
    default_model               TEXT NOT NULL CHECK (btrim(default_model) <> ''),
    -- Founder law 2026-08-12: column vocabulary is low|medium|high|xhigh|max.
    -- Family admission (Fable +max, Grok no max, Codex low|medium|high) is
    -- enforced by kernel_validate_agent_release_efforts / identity.py.
    default_effort              TEXT NOT NULL CHECK (default_effort IN ('low', 'medium', 'high', 'xhigh', 'max')),
    allowed_effort_levels       JSONB NOT NULL CHECK (
        jsonb_typeof(allowed_effort_levels) = 'array'
        AND allowed_effort_levels <@ '["high","low","max","medium","xhigh"]'::jsonb
    ),
    can_plan                    BOOLEAN NOT NULL,
    can_execute                 BOOLEAN NOT NULL,
    can_review                  BOOLEAN NOT NULL,
    evidence_requirements       JSONB NOT NULL CHECK (jsonb_typeof(evidence_requirements) = 'array'),
    consequence_tiers           JSONB NOT NULL CHECK (jsonb_typeof(consequence_tiers) = 'array'),
    memory_surfaces             JSONB NOT NULL CHECK (jsonb_typeof(memory_surfaces) = 'array'),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (fleet_type, role_type, release_version),
    UNIQUE (release_sha256, fleet_type, role_type)
);
REVOKE ALL ON kernel_agent_type_releases FROM PUBLIC;

CREATE TABLE IF NOT EXISTS kernel_tenant_fleet_manifests (
    id                          UUID PRIMARY KEY,
    tenant_id                   TEXT NOT NULL,
    fleet_type                  TEXT NOT NULL CHECK (fleet_type IN ('company', 'restaurant')),
    manifest_version            INTEGER NOT NULL CHECK (manifest_version > 0),
    manifest_sha256             TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    status                      TEXT NOT NULL CHECK (status IN ('draft', 'active', 'retired')),
    enabled_modules             JSONB NOT NULL CHECK (jsonb_typeof(enabled_modules) = 'array'),
    policy_config               JSONB NOT NULL CHECK (jsonb_typeof(policy_config) = 'object'),
    schedule_config             JSONB NOT NULL CHECK (jsonb_typeof(schedule_config) = 'object'),
    integration_refs            JSONB NOT NULL CHECK (jsonb_typeof(integration_refs) = 'object'),
    approval_authorities        JSONB NOT NULL CHECK (jsonb_typeof(approval_authorities) = 'object'),
    resource_limits             JSONB NOT NULL CHECK (jsonb_typeof(resource_limits) = 'object'),
    notification_channels       JSONB NOT NULL CHECK (jsonb_typeof(notification_channels) = 'array'),
    memory_refs                 JSONB NOT NULL CHECK (jsonb_typeof(memory_refs) = 'array'),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at                TIMESTAMPTZ,
    retired_at                  TIMESTAMPTZ,
    UNIQUE (tenant_id, fleet_type, manifest_version),
    UNIQUE (id, tenant_id, fleet_type),
    UNIQUE (tenant_id, fleet_type, manifest_sha256)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_one_active_fleet_manifest
    ON kernel_tenant_fleet_manifests (tenant_id, fleet_type)
    WHERE status = 'active';

CREATE TABLE IF NOT EXISTS kernel_tenant_fleet_manifest_roles (
    manifest_id                 UUID NOT NULL,
    tenant_id                   TEXT NOT NULL,
    fleet_type                  TEXT NOT NULL CHECK (fleet_type IN ('company', 'restaurant')),
    role_type                   TEXT NOT NULL,
    agent_release_sha256        TEXT NOT NULL CHECK (agent_release_sha256 ~ '^[0-9a-f]{64}$'),
    role_config                 JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(role_config) = 'object'),
    PRIMARY KEY (manifest_id, role_type),
    UNIQUE (manifest_id, tenant_id, fleet_type, role_type, agent_release_sha256),
    FOREIGN KEY (manifest_id, tenant_id, fleet_type)
        REFERENCES kernel_tenant_fleet_manifests (id, tenant_id, fleet_type),
    FOREIGN KEY (agent_release_sha256, fleet_type, role_type)
        REFERENCES kernel_agent_type_releases (release_sha256, fleet_type, role_type)
);
CREATE INDEX IF NOT EXISTS idx_kernel_manifest_roles_tenant
    ON kernel_tenant_fleet_manifest_roles (tenant_id, manifest_id);

CREATE TABLE IF NOT EXISTS kernel_runtime_workers (
    id                          UUID PRIMARY KEY,
    tenant_id                   TEXT NOT NULL,
    manifest_id                 UUID NOT NULL,
    fleet_type                  TEXT NOT NULL CHECK (fleet_type IN ('company', 'restaurant')),
    role_type                   TEXT NOT NULL,
    agent_release_sha256        TEXT NOT NULL CHECK (agent_release_sha256 ~ '^[0-9a-f]{64}$'),
    model_route                 TEXT NOT NULL CHECK (btrim(model_route) <> ''),
    effort_level                TEXT NOT NULL CHECK (effort_level IN ('low', 'medium', 'high', 'xhigh', 'max')),
    os_principal                TEXT NOT NULL CHECK (btrim(os_principal) <> ''),
    filesystem_root             TEXT NOT NULL CHECK (filesystem_root LIKE '/%'),
    database_principal          NAME NOT NULL,
    signing_public_key          BYTEA NOT NULL CHECK (octet_length(signing_public_key) > 0),
    signing_public_key_sha256   TEXT NOT NULL CHECK (signing_public_key_sha256 ~ '^[0-9a-f]{64}$'),
    status                      TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'retired')),
    current_objective_id        UUID,
    lease_token                 TEXT,
    lease_expires_at            TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at                  TIMESTAMPTZ,
    suspended_at                TIMESTAMPTZ,
    retired_at                  TIMESTAMPTZ,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (manifest_id, tenant_id, fleet_type, role_type, agent_release_sha256)
        REFERENCES kernel_tenant_fleet_manifest_roles
            (manifest_id, tenant_id, fleet_type, role_type, agent_release_sha256),
    CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_kernel_runtime_workers_tenant
    ON kernel_runtime_workers (tenant_id, role_type, status);

CREATE TABLE IF NOT EXISTS kernel_worker_sessions (
    id                          UUID PRIMARY KEY,
    tenant_id                   TEXT NOT NULL,
    worker_id                   UUID NOT NULL,
    manifest_id                 UUID NOT NULL,
    role_type                   TEXT NOT NULL,
    agent_release_sha256        TEXT NOT NULL CHECK (agent_release_sha256 ~ '^[0-9a-f]{64}$'),
    objective_id                UUID,
    run_id                      UUID,
    actual_model                TEXT NOT NULL CHECK (btrim(actual_model) <> ''),
    effort_level                TEXT NOT NULL CHECK (effort_level IN ('low', 'medium', 'high', 'xhigh', 'max')),
    state                       TEXT NOT NULL CHECK (state IN ('starting', 'active', 'held', 'ended', 'failed')),
    lease_token                 TEXT NOT NULL CHECK (btrim(lease_token) <> ''),
    started_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_expires_at            TIMESTAMPTZ NOT NULL,
    ended_at                    TIMESTAMPTZ,
    FOREIGN KEY (worker_id, tenant_id)
        REFERENCES kernel_runtime_workers (id, tenant_id),
    FOREIGN KEY (run_id) REFERENCES runs (id),
    CHECK (lease_expires_at > started_at),
    CHECK ((state IN ('ended', 'failed')) = (ended_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_one_live_worker_session
    ON kernel_worker_sessions (worker_id)
    WHERE state IN ('starting', 'active', 'held');
CREATE INDEX IF NOT EXISTS idx_kernel_worker_sessions_tenant
    ON kernel_worker_sessions (tenant_id, state, heartbeat_at);

CREATE TABLE IF NOT EXISTS kernel_capability_grants (
    id                          UUID PRIMARY KEY,
    tenant_id                   TEXT NOT NULL,
    worker_id                   UUID NOT NULL,
    capability                  TEXT NOT NULL CHECK (btrim(capability) <> ''),
    scope                       JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(scope) = 'object'),
    status                      TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired')),
    granted_by                  TEXT NOT NULL CHECK (btrim(granted_by) <> ''),
    evidence_ref                TEXT NOT NULL CHECK (btrim(evidence_ref) <> ''),
    effective_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at                  TIMESTAMPTZ NOT NULL,
    revoked_at                  TIMESTAMPTZ,
    FOREIGN KEY (worker_id, tenant_id)
        REFERENCES kernel_runtime_workers (id, tenant_id),
    CHECK (expires_at > effective_at),
    CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_one_active_capability_grant
    ON kernel_capability_grants (worker_id, capability)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_kernel_capability_grants_tenant
    ON kernel_capability_grants (tenant_id, status, expires_at);

ALTER TABLE runs ADD COLUMN IF NOT EXISTS worker_id UUID;
CREATE INDEX IF NOT EXISTS idx_runs_worker ON runs (worker_id);
DO $constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'runs_worker_tenant_fk'
    ) THEN
        ALTER TABLE runs
            ADD CONSTRAINT runs_worker_tenant_fk
            FOREIGN KEY (worker_id, tenant_id)
            REFERENCES kernel_runtime_workers (id, tenant_id);
    END IF;
END
$constraint$;

CREATE OR REPLACE FUNCTION kernel_reject_agent_release_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'agent type releases are immutable';
END
$$;
DROP TRIGGER IF EXISTS kernel_agent_release_immutable ON kernel_agent_type_releases;
CREATE TRIGGER kernel_agent_release_immutable
BEFORE UPDATE OR DELETE ON kernel_agent_type_releases
FOR EACH ROW EXECUTE FUNCTION kernel_reject_agent_release_mutation();
DROP TRIGGER IF EXISTS kernel_agent_release_truncate_guard ON kernel_agent_type_releases;
CREATE TRIGGER kernel_agent_release_truncate_guard
BEFORE TRUNCATE ON kernel_agent_type_releases
FOR EACH STATEMENT EXECUTE FUNCTION kernel_reject_agent_release_mutation();

-- Founder law 2026-08-12: admitted effort is model-family-scoped.
CREATE OR REPLACE FUNCTION kernel_model_effort_family(model_id TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        WHEN lower(btrim(COALESCE(model_id, ''))) LIKE '%fable%' THEN 'fable'
        WHEN lower(btrim(COALESCE(model_id, ''))) LIKE 'grok%'
          OR lower(btrim(COALESCE(model_id, ''))) LIKE '%-grok-%'
          OR lower(btrim(COALESCE(model_id, ''))) LIKE 'xai-grok%'
          THEN 'grok'
        WHEN lower(btrim(COALESCE(model_id, ''))) LIKE 'codex%' THEN 'codex'
        ELSE 'other'
    END
$$;

CREATE OR REPLACE FUNCTION kernel_admitted_efforts_for_model(model_id TEXT)
RETURNS TEXT[]
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE kernel_model_effort_family(model_id)
        WHEN 'fable' THEN ARRAY['low', 'medium', 'high', 'xhigh', 'max']
        WHEN 'grok' THEN ARRAY['low', 'medium', 'high', 'xhigh']
        ELSE ARRAY['low', 'medium', 'high']
    END
$$;

CREATE OR REPLACE FUNCTION kernel_effort_admitted_for_model(model_id TEXT, effort TEXT)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT effort = ANY (kernel_admitted_efforts_for_model(model_id))
$$;

CREATE OR REPLACE FUNCTION kernel_validate_agent_release_efforts()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    admitted TEXT[];
    effort TEXT;
    family TEXT;
BEGIN
    family := kernel_model_effort_family(NEW.default_model);
    admitted := kernel_admitted_efforts_for_model(NEW.default_model);
    IF NOT (NEW.default_effort = ANY (admitted)) THEN
        RAISE EXCEPTION
            'effort % is not admitted for model family % (%)',
            NEW.default_effort, family, NEW.default_model;
    END IF;
    FOR effort IN SELECT jsonb_array_elements_text(NEW.allowed_effort_levels)
    LOOP
        IF NOT (effort = ANY (admitted)) THEN
            RAISE EXCEPTION
                'effort % is not admitted for model family % (%)',
                effort, family, NEW.default_model;
        END IF;
    END LOOP;
    IF NOT (NEW.allowed_effort_levels ? NEW.default_effort) THEN
        RAISE EXCEPTION 'default effort must be in the release allowed set';
    END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS kernel_agent_release_effort_guard ON kernel_agent_type_releases;
CREATE TRIGGER kernel_agent_release_effort_guard
BEFORE INSERT ON kernel_agent_type_releases
FOR EACH ROW EXECUTE FUNCTION kernel_validate_agent_release_efforts();

CREATE OR REPLACE FUNCTION kernel_reject_effort_outside_model_family()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    model_id TEXT;
    family TEXT;
BEGIN
    IF TG_TABLE_NAME = 'kernel_runtime_workers' THEN
        model_id := NEW.model_route;
    ELSE
        model_id := NEW.actual_model;
    END IF;
    family := kernel_model_effort_family(model_id);
    IF NOT kernel_effort_admitted_for_model(model_id, NEW.effort_level) THEN
        RAISE EXCEPTION
            'effort % is not admitted for model family % (%)',
            NEW.effort_level, family, model_id;
    END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS kernel_runtime_worker_effort_family_guard ON kernel_runtime_workers;
CREATE TRIGGER kernel_runtime_worker_effort_family_guard
BEFORE INSERT OR UPDATE ON kernel_runtime_workers
FOR EACH ROW EXECUTE FUNCTION kernel_reject_effort_outside_model_family();
DROP TRIGGER IF EXISTS kernel_worker_session_effort_family_guard ON kernel_worker_sessions;
CREATE TRIGGER kernel_worker_session_effort_family_guard
BEFORE INSERT OR UPDATE ON kernel_worker_sessions
FOR EACH ROW EXECUTE FUNCTION kernel_reject_effort_outside_model_family();

CREATE OR REPLACE FUNCTION kernel_validate_manifest_activation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    actual_roles TEXT[];
    required_restaurant_roles CONSTANT TEXT[] := ARRAY[
        'browser_advisor', 'browser_navigator', 'fable_verifier',
        'inventory_worker', 'mise_orchestrator', 'ordering_worker', 'payroll_worker'
    ];
    required_company_roles CONSTANT TEXT[] := ARRAY[
        'ccco','ccde','ccfo','ccgo','cclo','ccmo','ccpo','ccro','ccto','cos','scribe'
    ];
BEGIN
    IF NEW.fleet_type='restaurant' AND NEW.tenant_id='__CC_SUITE_COMPANY_ID__' THEN
        RAISE EXCEPTION 'the reserved Company Fleet identity cannot activate a Restaurant Fleet';
    END IF;
    IF NEW.fleet_type='company' AND NEW.tenant_id<>'__CC_SUITE_COMPANY_ID__' THEN
        RAISE EXCEPTION 'Company Fleet manifests require the reserved Company identity';
    END IF;
    IF TG_OP = 'INSERT' AND NEW.status <> 'draft' THEN
        RAISE EXCEPTION 'new fleet manifest must start as draft';
    END IF;
    IF TG_OP = 'INSERT' AND (NEW.activated_at IS NOT NULL OR NEW.retired_at IS NOT NULL) THEN
        RAISE EXCEPTION 'draft fleet manifest cannot pre-set lifecycle timestamps';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.tenant_id, NEW.fleet_type, NEW.manifest_version, NEW.manifest_sha256,
        NEW.enabled_modules, NEW.policy_config, NEW.schedule_config,
        NEW.integration_refs, NEW.approval_authorities, NEW.resource_limits,
        NEW.notification_channels, NEW.memory_refs, NEW.created_at
    ) IS DISTINCT FROM (
        OLD.tenant_id, OLD.fleet_type, OLD.manifest_version, OLD.manifest_sha256,
        OLD.enabled_modules, OLD.policy_config, OLD.schedule_config,
        OLD.integration_refs, OLD.approval_authorities, OLD.resource_limits,
        OLD.notification_channels, OLD.memory_refs, OLD.created_at
    ) THEN
        RAISE EXCEPTION 'fleet manifest content is immutable';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.status = 'retired' AND NEW.status <> 'retired' THEN
            RAISE EXCEPTION 'retired fleet manifest is terminal';
        END IF;
        IF OLD.status = 'active' AND NEW.status NOT IN ('active', 'retired') THEN
            RAISE EXCEPTION 'active fleet manifest may only retire';
        END IF;
        IF OLD.status = NEW.status AND (
            NEW.activated_at IS DISTINCT FROM OLD.activated_at
            OR NEW.retired_at IS DISTINCT FROM OLD.retired_at
        ) THEN
            RAISE EXCEPTION 'fleet manifest lifecycle timestamps require a state transition';
        END IF;
        IF OLD.activated_at IS NOT NULL AND NEW.activated_at IS DISTINCT FROM OLD.activated_at THEN
            RAISE EXCEPTION 'fleet manifest activation timestamp is immutable';
        END IF;
        IF OLD.retired_at IS NOT NULL AND NEW.retired_at IS DISTINCT FROM OLD.retired_at THEN
            RAISE EXCEPTION 'fleet manifest retirement timestamp is immutable';
        END IF;
        IF OLD.status <> NEW.status THEN
            NEW.activated_at := OLD.activated_at;
            NEW.retired_at := OLD.retired_at;
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.status = 'active' AND OLD.status <> 'active' THEN
        SELECT COALESCE(array_agg(role_type ORDER BY role_type), ARRAY[]::TEXT[])
          INTO actual_roles
          FROM kernel_tenant_fleet_manifest_roles
         WHERE manifest_id = NEW.id AND tenant_id = NEW.tenant_id;
        IF NEW.fleet_type = 'restaurant' AND actual_roles <> required_restaurant_roles THEN
            RAISE EXCEPTION 'restaurant manifest must bind exactly the seven canonical roles';
        END IF;
        IF NEW.fleet_type = 'company' AND actual_roles <> required_company_roles THEN
            RAISE EXCEPTION 'company manifest must bind exactly the eleven canonical roles';
        END IF;
        NEW.activated_at := now();
        NEW.retired_at := NULL;
    ELSIF TG_OP = 'UPDATE' AND NEW.status = 'retired' AND OLD.status <> 'retired' THEN
        NEW.retired_at := now();
    END IF;
    RETURN NEW;
END
$function$;
DROP TRIGGER IF EXISTS kernel_manifest_activation_guard ON kernel_tenant_fleet_manifests;
CREATE TRIGGER kernel_manifest_activation_guard
BEFORE INSERT OR UPDATE ON kernel_tenant_fleet_manifests
FOR EACH ROW EXECUTE FUNCTION kernel_validate_manifest_activation();

CREATE OR REPLACE FUNCTION kernel_validate_manifest_role_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    target_manifest UUID;
    manifest_status TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_manifest := OLD.manifest_id;
    ELSE
        target_manifest := NEW.manifest_id;
    END IF;
    SELECT status INTO manifest_status
      FROM kernel_tenant_fleet_manifests
     WHERE id = target_manifest
     FOR SHARE;
    IF manifest_status <> 'draft' THEN
        RAISE EXCEPTION 'active or retired fleet manifest roles are immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$function$;
DROP TRIGGER IF EXISTS kernel_manifest_role_mutation_guard ON kernel_tenant_fleet_manifest_roles;
CREATE TRIGGER kernel_manifest_role_mutation_guard
BEFORE INSERT OR UPDATE OR DELETE ON kernel_tenant_fleet_manifest_roles
FOR EACH ROW EXECUTE FUNCTION kernel_validate_manifest_role_mutation();

CREATE OR REPLACE FUNCTION kernel_validate_runtime_worker()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    manifest_status TEXT;
    release_default_model TEXT;
    release_allowed_efforts JSONB;
    release_can_review BOOLEAN;
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status <> 'active' THEN
        RAISE EXCEPTION 'new runtime worker must start active';
    END IF;
    IF TG_OP = 'INSERT' AND (NEW.suspended_at IS NOT NULL OR NEW.retired_at IS NOT NULL) THEN
        RAISE EXCEPTION 'active runtime worker cannot pre-set lifecycle timestamps';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.tenant_id, NEW.manifest_id, NEW.fleet_type, NEW.role_type,
        NEW.agent_release_sha256, NEW.model_route, NEW.effort_level,
        NEW.os_principal, NEW.filesystem_root, NEW.database_principal,
        NEW.signing_public_key, NEW.signing_public_key_sha256, NEW.created_at
    ) IS DISTINCT FROM (
        OLD.tenant_id, OLD.manifest_id, OLD.fleet_type, OLD.role_type,
        OLD.agent_release_sha256, OLD.model_route, OLD.effort_level,
        OLD.os_principal, OLD.filesystem_root, OLD.database_principal,
        OLD.signing_public_key, OLD.signing_public_key_sha256, OLD.created_at
    ) THEN
        RAISE EXCEPTION 'runtime worker structural identity is immutable';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.status = 'retired' AND NEW.status <> 'retired' THEN
            RAISE EXCEPTION 'retired runtime worker is terminal';
        END IF;
        IF OLD.status = 'suspended' AND NEW.status = 'active' THEN
            RAISE EXCEPTION 'suspended runtime worker cannot be reactivated';
        END IF;
        IF OLD.status = NEW.status AND (
            NEW.suspended_at IS DISTINCT FROM OLD.suspended_at
            OR NEW.retired_at IS DISTINCT FROM OLD.retired_at
        ) THEN
            RAISE EXCEPTION 'worker lifecycle timestamps require a state transition';
        END IF;
        IF OLD.suspended_at IS NOT NULL AND NEW.suspended_at IS DISTINCT FROM OLD.suspended_at THEN
            RAISE EXCEPTION 'worker suspension timestamp is immutable once set';
        END IF;
        IF OLD.retired_at IS NOT NULL AND NEW.retired_at IS DISTINCT FROM OLD.retired_at THEN
            RAISE EXCEPTION 'worker retirement timestamp is immutable once set';
        END IF;
        IF OLD.status <> NEW.status THEN
            NEW.suspended_at := OLD.suspended_at;
            NEW.retired_at := OLD.retired_at;
        END IF;
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT status INTO manifest_status
          FROM kernel_tenant_fleet_manifests
         WHERE id = NEW.manifest_id AND tenant_id = NEW.tenant_id
         FOR SHARE;
    ELSE
        SELECT status INTO manifest_status
          FROM kernel_tenant_fleet_manifests
         WHERE id = NEW.manifest_id AND tenant_id = NEW.tenant_id;
    END IF;
    IF NEW.status = 'active' AND manifest_status <> 'active' THEN
        RAISE EXCEPTION 'active worker requires the tenant active manifest';
    END IF;
    SELECT default_model, allowed_effort_levels, can_review
      INTO release_default_model, release_allowed_efforts, release_can_review
      FROM kernel_agent_type_releases
     WHERE release_sha256 = NEW.agent_release_sha256;
    IF NEW.model_route <> release_default_model OR NOT (release_allowed_efforts ? NEW.effort_level) THEN
        RAISE EXCEPTION 'worker model and effort must match its role release';
    END IF;
    IF release_can_review AND (
        NEW.model_route <> 'claude-fable-5'
        OR (NEW.fleet_type='restaurant' AND NEW.role_type<>'fable_verifier')
        OR (NEW.fleet_type='company' AND NEW.role_type<>'ccpo')
    ) THEN
        RAISE EXCEPTION 'Fable is the sole formal review model for both fleet archetypes';
    END IF;
    IF NEW.fleet_type='company' THEN
        IF NEW.tenant_id <> '__CC_SUITE_COMPANY_ID__' THEN
            RAISE EXCEPTION 'Company worker requires the reserved Company Fleet identity';
        END IF;
        IF NEW.role_type='ccpo' AND NEW.database_principal::TEXT <> 'mise_company_reviewer' THEN
            RAISE EXCEPTION 'CCPO worker requires the dedicated Company reviewer principal';
        END IF;
        IF NEW.role_type<>'ccpo' AND NEW.database_principal::TEXT !~
            ('^mise_company_' || NEW.role_type || '_[0-9a-f]{16}$')
        THEN
            RAISE EXCEPTION 'Company worker database principal violates its identity-bound form';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.status <> NEW.status THEN
        IF NEW.status = 'suspended' THEN
            NEW.suspended_at := now();
        ELSIF NEW.status = 'retired' THEN
            NEW.retired_at := now();
        END IF;
    END IF;
    RETURN NEW;
END
$function$;
DROP TRIGGER IF EXISTS kernel_runtime_worker_guard ON kernel_runtime_workers;
CREATE TRIGGER kernel_runtime_worker_guard
BEFORE INSERT OR UPDATE ON kernel_runtime_workers
FOR EACH ROW EXECUTE FUNCTION kernel_validate_runtime_worker();

CREATE OR REPLACE FUNCTION kernel_validate_worker_session()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    worker_row kernel_runtime_workers%ROWTYPE;
    manifest_status TEXT;
    allowed_efforts JSONB;
    terminal_transition BOOLEAN := FALSE;
BEGIN
    SELECT * INTO worker_row
      FROM kernel_runtime_workers
     WHERE id = NEW.worker_id AND tenant_id = NEW.tenant_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'worker session requires a tenant worker';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF (
            NEW.tenant_id, NEW.worker_id, NEW.manifest_id, NEW.role_type,
            NEW.agent_release_sha256, NEW.objective_id, NEW.run_id,
            NEW.actual_model, NEW.effort_level, NEW.lease_token, NEW.started_at
        ) IS DISTINCT FROM (
            OLD.tenant_id, OLD.worker_id, OLD.manifest_id, OLD.role_type,
            OLD.agent_release_sha256, OLD.objective_id, OLD.run_id,
            OLD.actual_model, OLD.effort_level, OLD.lease_token, OLD.started_at
        ) THEN
            RAISE EXCEPTION 'worker session structural identity is immutable';
        END IF;
        IF OLD.state IN ('ended', 'failed') AND NEW.state <> OLD.state THEN
            RAISE EXCEPTION 'terminal worker session cannot be resurrected';
        END IF;
        IF OLD.state = 'active' AND NEW.state = 'starting' THEN
            RAISE EXCEPTION 'worker session state cannot move backward';
        END IF;
        IF OLD.state = 'held' AND NEW.state IN ('starting', 'active') THEN
            RAISE EXCEPTION 'held worker session cannot regain authority';
        END IF;
        IF OLD.ended_at IS NOT NULL AND NEW.ended_at IS DISTINCT FROM OLD.ended_at THEN
            RAISE EXCEPTION 'worker session terminal timestamp is immutable';
        END IF;
        IF NEW.heartbeat_at < OLD.heartbeat_at OR NEW.lease_expires_at < OLD.lease_expires_at THEN
            RAISE EXCEPTION 'worker session clocks cannot move backward';
        END IF;
        terminal_transition := OLD.state NOT IN ('ended', 'failed')
                               AND NEW.state IN ('ended', 'failed');
        IF terminal_transition THEN
            NEW.ended_at := now();
        END IF;
    ELSIF NEW.state <> 'active' THEN
        RAISE EXCEPTION 'new worker session must start active';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT status INTO manifest_status
          FROM kernel_tenant_fleet_manifests
         WHERE id = worker_row.manifest_id AND tenant_id = NEW.tenant_id
         FOR SHARE;
    ELSE
        SELECT status INTO manifest_status
          FROM kernel_tenant_fleet_manifests
         WHERE id = worker_row.manifest_id AND tenant_id = NEW.tenant_id;
    END IF;
    IF NOT terminal_transition AND (
        worker_row.status <> 'active' OR manifest_status <> 'active'
    ) THEN
        RAISE EXCEPTION 'worker session refuses an inactive worker or stale fleet manifest';
    END IF;
    IF (NEW.manifest_id, NEW.role_type, NEW.agent_release_sha256) IS DISTINCT FROM
       (worker_row.manifest_id, worker_row.role_type, worker_row.agent_release_sha256) THEN
        RAISE EXCEPTION 'worker session identity does not match durable worker';
    END IF;
    IF NEW.actual_model <> worker_row.model_route THEN
        RAISE EXCEPTION 'worker session model does not match durable worker route';
    END IF;
    SELECT allowed_effort_levels INTO allowed_efforts
      FROM kernel_agent_type_releases
     WHERE release_sha256 = worker_row.agent_release_sha256;
    IF NOT (allowed_efforts ? NEW.effort_level) THEN
        RAISE EXCEPTION 'worker session effort is not allowed by its release';
    END IF;
    IF NEW.run_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM runs
         WHERE id = NEW.run_id AND tenant_id = NEW.tenant_id
           AND (worker_id IS NULL OR worker_id = NEW.worker_id)
    ) THEN
        RAISE EXCEPTION 'worker session run belongs to another tenant or worker';
    END IF;
    IF NEW.objective_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM objectives
         WHERE id = NEW.objective_id AND tenant_id = NEW.tenant_id
    ) THEN
        RAISE EXCEPTION 'worker session objective belongs to another tenant';
    END IF;
    RETURN NEW;
END
$function$;
DROP TRIGGER IF EXISTS kernel_worker_session_guard ON kernel_worker_sessions;
CREATE TRIGGER kernel_worker_session_guard
BEFORE INSERT OR UPDATE ON kernel_worker_sessions
FOR EACH ROW EXECUTE FUNCTION kernel_validate_worker_session();

CREATE OR REPLACE FUNCTION kernel_validate_capability_grant()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    worker_role TEXT;
    worker_status TEXT;
    manifest_status TEXT;
    release_capabilities JSONB;
    release_can_review BOOLEAN;
    deactivation BOOLEAN := FALSE;
    terminal_noop BOOLEAN := FALSE;
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status <> 'active' THEN
        RAISE EXCEPTION 'new capability grant must start active';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF (
            NEW.tenant_id, NEW.worker_id, NEW.capability, NEW.scope,
            NEW.granted_by, NEW.evidence_ref, NEW.effective_at, NEW.expires_at
        ) IS DISTINCT FROM (
            OLD.tenant_id, OLD.worker_id, OLD.capability, OLD.scope,
            OLD.granted_by, OLD.evidence_ref, OLD.effective_at, OLD.expires_at
        ) THEN
            RAISE EXCEPTION 'capability grant authority is immutable';
        END IF;
        IF OLD.status IN ('revoked', 'expired') AND NEW.status <> OLD.status THEN
            RAISE EXCEPTION 'terminal capability grant cannot be reactivated';
        END IF;
        IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
            RAISE EXCEPTION 'capability revocation timestamp is immutable';
        END IF;
        deactivation := OLD.status = 'active' AND NEW.status IN ('revoked', 'expired');
        terminal_noop := OLD.status IN ('revoked', 'expired') AND NEW.status = OLD.status;
        IF deactivation AND NEW.status = 'revoked' THEN
            NEW.revoked_at := now();
        END IF;
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT worker.role_type, worker.status, manifest.status,
               release.allowed_capabilities, release.can_review
          INTO worker_role, worker_status, manifest_status,
               release_capabilities, release_can_review
          FROM kernel_runtime_workers AS worker
          JOIN kernel_tenant_fleet_manifests AS manifest
            ON manifest.id = worker.manifest_id AND manifest.tenant_id = worker.tenant_id
          JOIN kernel_agent_type_releases AS release
            ON release.release_sha256 = worker.agent_release_sha256
         WHERE worker.id = NEW.worker_id AND worker.tenant_id = NEW.tenant_id
         FOR SHARE OF manifest;
    ELSE
        SELECT worker.role_type, worker.status, manifest.status,
               release.allowed_capabilities, release.can_review
          INTO worker_role, worker_status, manifest_status,
               release_capabilities, release_can_review
          FROM kernel_runtime_workers AS worker
          JOIN kernel_tenant_fleet_manifests AS manifest
            ON manifest.id = worker.manifest_id AND manifest.tenant_id = worker.tenant_id
          JOIN kernel_agent_type_releases AS release
            ON release.release_sha256 = worker.agent_release_sha256
         WHERE worker.id = NEW.worker_id AND worker.tenant_id = NEW.tenant_id;
    END IF;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'capability grant requires a tenant worker';
    END IF;
    IF NOT deactivation AND NOT terminal_noop AND (
        worker_status <> 'active' OR manifest_status <> 'active'
    ) THEN
        RAISE EXCEPTION 'capability grant requires an active worker and manifest';
    END IF;
    IF NOT (release_capabilities ? NEW.capability) THEN
        RAISE EXCEPTION 'capability is outside the worker role release';
    END IF;
    IF NEW.capability LIKE 'review.%' AND (
        worker_role <> 'fable_verifier' OR NOT release_can_review
    ) THEN
        RAISE EXCEPTION 'only the Fable Verifier may receive review authority';
    END IF;
    RETURN NEW;
END
$function$;
DROP TRIGGER IF EXISTS kernel_capability_grant_guard ON kernel_capability_grants;
CREATE TRIGGER kernel_capability_grant_guard
BEFORE INSERT OR UPDATE ON kernel_capability_grants
FOR EACH ROW EXECUTE FUNCTION kernel_validate_capability_grant();

CREATE OR REPLACE FUNCTION kernel_reject_identity_history_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'durable worker, session, and capability history is immutable';
END
$$;
DROP TRIGGER IF EXISTS kernel_runtime_worker_delete_guard ON kernel_runtime_workers;
CREATE TRIGGER kernel_runtime_worker_delete_guard
BEFORE DELETE ON kernel_runtime_workers
FOR EACH ROW EXECUTE FUNCTION kernel_reject_identity_history_delete();
DROP TRIGGER IF EXISTS kernel_worker_session_delete_guard ON kernel_worker_sessions;
CREATE TRIGGER kernel_worker_session_delete_guard
BEFORE DELETE ON kernel_worker_sessions
FOR EACH ROW EXECUTE FUNCTION kernel_reject_identity_history_delete();
DROP TRIGGER IF EXISTS kernel_capability_grant_delete_guard ON kernel_capability_grants;
CREATE TRIGGER kernel_capability_grant_delete_guard
BEFORE DELETE ON kernel_capability_grants
FOR EACH ROW EXECUTE FUNCTION kernel_reject_identity_history_delete();
DROP TRIGGER IF EXISTS kernel_manifest_truncate_guard ON kernel_tenant_fleet_manifests;
CREATE TRIGGER kernel_manifest_truncate_guard
BEFORE TRUNCATE ON kernel_tenant_fleet_manifests
FOR EACH STATEMENT EXECUTE FUNCTION kernel_reject_identity_history_delete();
DROP TRIGGER IF EXISTS kernel_manifest_role_truncate_guard ON kernel_tenant_fleet_manifest_roles;
CREATE TRIGGER kernel_manifest_role_truncate_guard
BEFORE TRUNCATE ON kernel_tenant_fleet_manifest_roles
FOR EACH STATEMENT EXECUTE FUNCTION kernel_reject_identity_history_delete();
DROP TRIGGER IF EXISTS kernel_runtime_worker_truncate_guard ON kernel_runtime_workers;
CREATE TRIGGER kernel_runtime_worker_truncate_guard
BEFORE TRUNCATE ON kernel_runtime_workers
FOR EACH STATEMENT EXECUTE FUNCTION kernel_reject_identity_history_delete();
DROP TRIGGER IF EXISTS kernel_worker_session_truncate_guard ON kernel_worker_sessions;
CREATE TRIGGER kernel_worker_session_truncate_guard
BEFORE TRUNCATE ON kernel_worker_sessions
FOR EACH STATEMENT EXECUTE FUNCTION kernel_reject_identity_history_delete();
DROP TRIGGER IF EXISTS kernel_capability_grant_truncate_guard ON kernel_capability_grants;
CREATE TRIGGER kernel_capability_grant_truncate_guard
BEFORE TRUNCATE ON kernel_capability_grants
FOR EACH STATEMENT EXECUTE FUNCTION kernel_reject_identity_history_delete();

CREATE OR REPLACE FUNCTION kernel_runs_worker_write_once()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.worker_id IS NOT NULL AND NEW.worker_id IS DISTINCT FROM OLD.worker_id THEN
        RAISE EXCEPTION 'run worker identity is write-once';
    END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS kernel_runs_worker_write_once_guard ON runs;
CREATE TRIGGER kernel_runs_worker_write_once_guard
BEFORE UPDATE OF worker_id ON runs
FOR EACH ROW EXECUTE FUNCTION kernel_runs_worker_write_once();

DO $rls$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'kernel_tenant_fleet_manifests',
        'kernel_tenant_fleet_manifest_roles',
        'kernel_runtime_workers',
        'kernel_worker_sessions',
        'kernel_capability_grants'
    ]
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS kernel_tenant_scope ON public.%I', table_name);
        EXECUTE format(
            'CREATE POLICY kernel_tenant_scope ON public.%I '
            'USING (tenant_id = kernel_current_tenant()) '
            'WITH CHECK (tenant_id = kernel_current_tenant())',
            table_name
        );
    END LOOP;
END
$rls$;
