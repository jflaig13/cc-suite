-- DDL 105: Company review seat on Fable 5.1 (founder 9/1: "I want all of
-- those on Fable 5.1 high. I want Codex reserved as the reviewer, spawned at
-- the will of the CC execs, as necessary for cross model verifications.")
-- The identity plane (fleet_kernel/identity.py) moves every Company seat,
-- CCPO included, to claude-fable-5-1; DDL 012's runtime-worker validator
-- pinned the formal review route to the literal 'claude-fable-5'. This
-- amendment reproduces DDL 012's kernel_validate_runtime_worker() verbatim
-- and changes one clause and adds one: a COMPANY review worker may route to
-- claude-fable-5 or claude-fable-5-1; the reviewer roles are pinned even when
-- a release drops can_review; a RESTAURANT review worker (the
-- fable_verifier, the SDK evidence lane behind the D13 gate and role
-- signing) stays claude-fable-5 until that era changes on its own terms.
-- Grants untouched (CREATE OR REPLACE preserves the ACL). Never edit 012.
--
-- DDL monotonicity (DDL 103 convention): the function carries a
-- kernel-ddl-version stamp; an OLDER amendment refuses to apply over it.
DO $company_review_seat_monotonicity$
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
    IF installed IS NOT NULL AND installed > 105 THEN
        RAISE EXCEPTION USING ERRCODE='55000', MESSAGE=pg_catalog.format(
            'runtime-worker validator amendment 105 refuses to apply over newer kernel-ddl-version %s',
            installed);
    END IF;
END
$company_review_seat_monotonicity$;

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
        -- DDL 105: the Company review seat (CCPO) runs claude-fable-5-1
        -- (founder 9/1); the Restaurant fable_verifier and the SDK review
        -- lane stay claude-fable-5 until their own era change.
        (NEW.fleet_type='company' AND NEW.model_route NOT IN ('claude-fable-5','claude-fable-5-1'))
        OR (NEW.fleet_type='restaurant' AND NEW.model_route <> 'claude-fable-5')
        OR (NEW.fleet_type='restaurant' AND NEW.role_type<>'fable_verifier')
        OR (NEW.fleet_type='company' AND NEW.role_type<>'ccpo')
    ) THEN
        RAISE EXCEPTION 'Fable is the sole formal review model for both fleet archetypes';
    END IF;
    -- DDL 105 (Codex r3): the reviewer ROLES are pinned regardless of the
    -- release's can_review flag. A fable_verifier or ccpo release that does
    -- not review, or routes elsewhere, is not a reviewer release.
    IF (NEW.fleet_type='restaurant' AND NEW.role_type='fable_verifier'
        AND (NOT release_can_review OR NEW.model_route <> 'claude-fable-5'))
       OR (NEW.fleet_type='company' AND NEW.role_type='ccpo'
        AND (NOT release_can_review
             OR NEW.model_route NOT IN ('claude-fable-5','claude-fable-5-1'))) THEN
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

COMMENT ON FUNCTION public.kernel_validate_runtime_worker() IS
    'Runtime worker validator: DDL 012 text with the formal-review route '
    'accepting claude-fable-5-1 for the Company review seat (CCPO) while the '
    'Restaurant fable_verifier stays claude-fable-5. kernel-ddl-version: 105';
