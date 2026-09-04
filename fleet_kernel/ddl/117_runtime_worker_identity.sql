-- Shared runtime-worker validator extracted from canonical DDL 117.
-- Restaurant orchestrator tick and grants are outside this export.

DO $restaurant_seat_models_monotonicity$
DECLARE
    installed INTEGER;
BEGIN
    IF pg_catalog.to_regprocedure('public.kernel_validate_runtime_worker()') IS NULL THEN
        RETURN;
    END IF;
    installed := NULLIF((pg_catalog.regexp_match(
        COALESCE(pg_catalog.obj_description(
            pg_catalog.to_regprocedure('public.kernel_validate_runtime_worker()'),'pg_proc'
        ),''),
        'kernel-ddl-version: ([0-9]+)'
    ))[1],'')::INTEGER;
    IF installed IS NOT NULL AND installed > 117 THEN
        RAISE EXCEPTION USING ERRCODE='55000', MESSAGE=pg_catalog.format(
            'runtime-worker validator amendment 117 refuses to apply over newer kernel-ddl-version %s',
            installed);
    END IF;
END
$restaurant_seat_models_monotonicity$;

CREATE OR REPLACE FUNCTION public.kernel_validate_runtime_worker()
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
        -- DDL 117: the founder's 9/3 correction moved the seats that were on
        -- Fable 5 to Fable 5.1, so claude-fable-5-1 is the one formal review
        -- route in both fleet archetypes. Each archetype keeps its own arm so
        -- a bite can neuter one without the other.
        (NEW.fleet_type='company' AND NEW.model_route <> 'claude-fable-5-1')
        OR (NEW.fleet_type='restaurant' AND NEW.model_route <> 'claude-fable-5-1')
        OR (NEW.fleet_type='restaurant' AND NEW.role_type<>'fable_verifier')
        OR (NEW.fleet_type='company' AND NEW.role_type<>'ccpo')
    ) THEN
        RAISE EXCEPTION 'Fable is the sole formal review model for both fleet archetypes';
    END IF;
    -- DDL 105 (Codex r3): the reviewer ROLES are pinned regardless of the
    -- release's can_review flag. A fable_verifier or ccpo release that does
    -- not review, or routes elsewhere, is not a reviewer release.
    IF (NEW.fleet_type='restaurant' AND NEW.role_type='fable_verifier'
        AND (NOT release_can_review OR NEW.model_route <> 'claude-fable-5-1'))
       OR (NEW.fleet_type='company' AND NEW.role_type='ccpo'
        AND (NOT release_can_review
             OR NEW.model_route <> 'claude-fable-5-1')) THEN
        RAISE EXCEPTION 'Fable is the sole formal review model for both fleet archetypes';
    END IF;
    -- DDL 117 seat table (founder 9/3). A canonical Restaurant seat runs the
    -- model its role declares and nothing else: the workers stay on Grok, the
    -- navigator stays on Codex, the three Fable seats are 5.1. The CASE yields
    -- NULL for a non-canonical role_type, and `<> NULL` is NULL, so probe
    -- roles are untouched. tests/test_tier1_restaurant_seat_model_identity.py
    -- reads this clause back out of the file and asserts it against
    -- fleet_kernel/identity.py RESTAURANT_ROLE_SPECS, so the two cannot drift.
    -- The CASE is parenthesized: plpgsql scans an IF condition for the first
    -- THEN at paren depth zero, and a bare CASE would hand it the CASE's own.
    IF NEW.fleet_type='restaurant' AND NEW.model_route <> (CASE NEW.role_type
        WHEN 'browser_advisor'   THEN 'claude-fable-5-1'
        WHEN 'browser_navigator' THEN 'codex-current'
        WHEN 'fable_verifier'    THEN 'claude-fable-5-1'
        WHEN 'inventory_worker'  THEN 'grok-4.6'
        WHEN 'mise_orchestrator' THEN 'claude-fable-5-1'
        WHEN 'ordering_worker'   THEN 'grok-4.6'
        WHEN 'payroll_worker'    THEN 'grok-4.6'
    END) THEN
        RAISE EXCEPTION
            'Restaurant seat % runs its declared model, not %',
            NEW.role_type, NEW.model_route;
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

COMMENT ON FUNCTION public.kernel_validate_runtime_worker() IS
    'Runtime worker validator: DDL 012 text with claude-fable-5-1 as the one '
    'formal-review route in both fleet archetypes, plus the Restaurant seat '
    'table (workers grok-4.6, navigator codex-current, orchestrator, verifier '
    'and advisor claude-fable-5-1). kernel-ddl-version: 117';
