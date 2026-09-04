-- Ratified Utility Company-role vocabulary, with no admission authority.
--
-- This forward-only migration expands the physical Company-role vocabulary
-- from eleven to twelve.  It creates no worker, principal, package, target,
-- mandate, subject, admission, grant, session, or runtime effect.  The pinned
-- Phase-4 mandate validator remains unchanged and continues to refuse Utility.

ALTER TABLE kernel_company_worker_principals
    DROP CONSTRAINT kernel_company_worker_principals_role_type_check;
ALTER TABLE kernel_company_worker_principals
    ADD CONSTRAINT kernel_company_worker_principals_role_type_check CHECK (
        role_type IN (
            'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
            'cclo','ccgo','ccco','utility'
        )
    ) NOT VALID;
ALTER TABLE kernel_company_worker_principals
    VALIDATE CONSTRAINT kernel_company_worker_principals_role_type_check;

ALTER TABLE kernel_company_objectives
    DROP CONSTRAINT kernel_company_objectives_owner_role_check;
ALTER TABLE kernel_company_objectives
    ADD CONSTRAINT kernel_company_objectives_owner_role_check CHECK (
        owner_role IN (
            'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
            'cclo','ccgo','ccco','utility'
        )
    ) NOT VALID;
ALTER TABLE kernel_company_objectives
    VALIDATE CONSTRAINT kernel_company_objectives_owner_role_check;

ALTER TABLE kernel_company_objective_events
    DROP CONSTRAINT kernel_company_objective_events_actor_role_check;
ALTER TABLE kernel_company_objective_events
    ADD CONSTRAINT kernel_company_objective_events_actor_role_check CHECK (
        actor_role IN (
            'founder','scribe','cos','ccto','ccpo','ccde','ccro','ccfo',
            'ccmo','cclo','ccgo','ccco','utility'
        )
    ) NOT VALID;
ALTER TABLE kernel_company_objective_events
    VALIDATE CONSTRAINT kernel_company_objective_events_actor_role_check;

ALTER TABLE kernel_company_role_admission_targets
    DROP CONSTRAINT kernel_company_role_admission_targets_role_type_check;
ALTER TABLE kernel_company_role_admission_targets
    ADD CONSTRAINT kernel_company_role_admission_targets_role_type_check CHECK (
        role_type IN (
            'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
            'cclo','ccgo','ccco','utility'
        )
    ) NOT VALID;
ALTER TABLE kernel_company_role_admission_targets
    VALIDATE CONSTRAINT kernel_company_role_admission_targets_role_type_check;

ALTER TABLE kernel_company_role_activation_subjects
    DROP CONSTRAINT kernel_company_role_activation_subjects_role_type_check;
ALTER TABLE kernel_company_role_activation_subjects
    ADD CONSTRAINT kernel_company_role_activation_subjects_role_type_check CHECK (
        role_type IN (
            'cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo',
            'ccgo','ccco','utility'
        )
    ) NOT VALID;
ALTER TABLE kernel_company_role_activation_subjects
    VALIDATE CONSTRAINT kernel_company_role_activation_subjects_role_type_check;

ALTER TABLE kernel_company_scribe_packages
    DROP CONSTRAINT kernel_company_package_role_type_check;
ALTER TABLE kernel_company_scribe_packages
    ADD CONSTRAINT kernel_company_package_role_type_check CHECK (
        role_type IN (
            'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
            'cclo','ccgo','ccco','utility'
        )
    ) NOT VALID;
ALTER TABLE kernel_company_scribe_packages
    VALIDATE CONSTRAINT kernel_company_package_role_type_check;

ALTER TABLE kernel_company_scribe_admissions
    DROP CONSTRAINT kernel_company_admission_role_type_check;
ALTER TABLE kernel_company_scribe_admissions
    ADD CONSTRAINT kernel_company_admission_role_type_check CHECK (
        role_type IN (
            'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
            'cclo','ccgo','ccco','utility'
        )
    ) NOT VALID;
ALTER TABLE kernel_company_scribe_admissions
    VALIDATE CONSTRAINT kernel_company_admission_role_type_check;

ALTER TABLE kernel_company_scribe_recovery_intents
    DROP CONSTRAINT kernel_company_recovery_role_type_check;
ALTER TABLE kernel_company_scribe_recovery_intents
    ADD CONSTRAINT kernel_company_recovery_role_type_check CHECK (
        role_type IN (
            'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
            'cclo','ccgo','ccco','utility'
        )
    ) NOT VALID;
ALTER TABLE kernel_company_scribe_recovery_intents
    VALIDATE CONSTRAINT kernel_company_recovery_role_type_check;

ALTER TABLE kernel_company_role_provisioner_principals
    DROP CONSTRAINT kernel_company_role_provisioner_principals_role_type_check;
ALTER TABLE kernel_company_role_provisioner_principals
    ADD CONSTRAINT kernel_company_role_provisioner_principals_role_type_check CHECK (
        role_type IN (
            'cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo',
            'ccgo','ccco','utility'
        )
    ) NOT VALID;
ALTER TABLE kernel_company_role_provisioner_principals
    VALIDATE CONSTRAINT kernel_company_role_provisioner_principals_role_type_check;

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
        'ccco','ccde','ccfo','ccgo','cclo','ccmo','ccpo','ccro','ccto','cos',
        'scribe','utility'
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
            RAISE EXCEPTION 'company manifest must bind exactly the twelve canonical roles';
        END IF;
        NEW.activated_at := now();
        NEW.retired_at := NULL;
    ELSIF TG_OP = 'UPDATE' AND NEW.status = 'retired' AND OLD.status <> 'retired' THEN
        NEW.retired_at := now();
    END IF;
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION kernel_company_role_context_proof(
    p_role_type TEXT,p_admission_id UUID,p_admission_epoch BIGINT,
    p_db_role NAME,p_txid BIGINT
)
RETURNS TEXT LANGUAGE plpgsql STABLE STRICT SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE admission_key BYTEA;
BEGIN
    IF p_role_type NOT IN (
        'cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco',
        'utility'
    ) THEN RAISE EXCEPTION 'Company role context names an ineligible role'; END IF;
    SELECT secret_key INTO admission_key
      FROM kernel_company_scribe_admission_keys
     WHERE company_id='__CC_SUITE_COMPANY_ID__';
    IF admission_key IS NULL THEN
        RAISE EXCEPTION 'Company role admission context key is unavailable';
    END IF;
    RETURN encode(kernel_hmac_sha256(convert_to(concat_ws(
        '|','company-role-admission-v1',p_role_type,p_admission_id::TEXT,
        p_admission_epoch::TEXT,p_db_role::TEXT,p_txid::TEXT
    ),'UTF8'),admission_key),'hex');
END
$function$;

CREATE FUNCTION kernel_utility_role_vocabulary_contract()
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT 'mise.company-role-vocabulary.v12:utility:admission-authority-separate'::TEXT
$$;
REVOKE ALL ON FUNCTION kernel_utility_role_vocabulary_contract() FROM PUBLIC;
