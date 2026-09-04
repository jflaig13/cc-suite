-- PHASE-4 COMPANY DATABASE MIGRATION.
--
-- This migration is admitted only through the exact, digest-pinned Company
-- activation DDL deployer. The deployer owns the single outer transaction;
-- this file must not open or commit a transaction of its own. It creates no
-- non-Scribe target, package, admission, worker, principal, mandate,
-- capability, or runtime. Activation still requires the separately pinned
-- founder mandate and every later runbook gate.

-- DEPLOYMENT PRECONDITIONS
--
-- 1. Apply only through scripts/deploy_kernel_activation_ddl.py.
-- 2. Freeze and review the matching Python, package, and test subject.
-- 3. Preserve the deployer's single serializable transaction and advisory lock.
-- 4. Do not insert a non-Scribe target until the exact mandate is recorded.

-- Closed target registry. A row records eligibility; it does not create an
-- admission, grant a capability, launch a process, or fence a legacy writer.
CREATE TABLE kernel_company_role_admission_targets (
    company_id              TEXT NOT NULL
                            CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    role_type               TEXT NOT NULL CHECK (role_type IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco'
        -- 'utility', -- FOUNDER-DECISION: add only after Utility joins the canonical Company role vocabulary.
    )),
    deployment_id           TEXT NOT NULL CHECK (btrim(deployment_id)<>''),
    worker_id               UUID NOT NULL,
    host_kind               TEXT NOT NULL CHECK (host_kind='macbook'),
    runtime_kind            TEXT NOT NULL CHECK (runtime_kind='claude-code'),
    mandate_id              TEXT NOT NULL
                            CHECK (
                                mandate_id
                                ~ '^activation-mandate-[A-Za-z0-9._-]+$'
                            ),
    mandate_artifact_sha256 TEXT NOT NULL
                            CHECK (
                                mandate_artifact_sha256
                                ~ '^[0-9a-f]{64}$'
                            ),
    state                   TEXT NOT NULL DEFAULT 'held'
                            CHECK (state IN ('held','eligible','retired')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    eligible_at             TIMESTAMPTZ,
    retired_at              TIMESTAMPTZ,
    PRIMARY KEY (company_id,role_type),
    UNIQUE (company_id,deployment_id),
    UNIQUE (company_id,role_type,deployment_id),
    UNIQUE (company_id,role_type,deployment_id,worker_id),
    FOREIGN KEY (worker_id,company_id,role_type)
        REFERENCES kernel_company_worker_principals
            (worker_id,company_id,role_type),
    FOREIGN KEY (mandate_id)
        REFERENCES kernel_founder_activation_mandates (mandate_id),
    CHECK (
        (state='held' AND eligible_at IS NULL AND retired_at IS NULL)
        OR
        (state='eligible' AND eligible_at IS NOT NULL AND retired_at IS NULL)
        OR
        (state='retired' AND retired_at IS NOT NULL)
    )
);
REVOKE ALL ON kernel_company_role_admission_targets FROM PUBLIC;

-- Exact per-role authority subjects are approved elsewhere under the pinned
-- Phase-4 mandate. This table stores the immutable subject identity consumed
-- by the admission trigger; it does not create or approve a subject itself.
CREATE TABLE kernel_company_role_activation_subjects (
    approval_id             UUID PRIMARY KEY,
    company_id              TEXT NOT NULL CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    role_type               TEXT NOT NULL CHECK (role_type IN (
        'cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco'
    )),
    deployment_id           TEXT NOT NULL,
    worker_id               UUID NOT NULL,
    mandate_id              TEXT NOT NULL,
    mandate_artifact_sha256 TEXT NOT NULL
                            CHECK (mandate_artifact_sha256~'^[0-9a-f]{64}$'),
    subject_ref             TEXT NOT NULL UNIQUE CHECK (btrim(subject_ref)<>''),
    subject_sha256          TEXT NOT NULL UNIQUE
                            CHECK (subject_sha256~'^[0-9a-f]{64}$'),
    subject_payload         JSONB NOT NULL
                            CHECK (jsonb_typeof(subject_payload)='object'),
    state                   TEXT NOT NULL CHECK (state IN ('approved','consumed','revoked')),
    approved_at             TIMESTAMPTZ NOT NULL,
    consumed_at             TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ,
    UNIQUE (company_id,role_type,approval_id),
    FOREIGN KEY (company_id,role_type,deployment_id,worker_id)
        REFERENCES kernel_company_role_admission_targets
            (company_id,role_type,deployment_id,worker_id),
    FOREIGN KEY (mandate_id)
        REFERENCES kernel_founder_activation_mandates (mandate_id),
    CHECK (
        (state='approved' AND consumed_at IS NULL AND revoked_at IS NULL)
        OR (state='consumed' AND consumed_at IS NOT NULL AND revoked_at IS NULL)
        OR (state='revoked' AND consumed_at IS NULL AND revoked_at IS NOT NULL)
    )
);
REVOKE ALL ON kernel_company_role_activation_subjects FROM PUBLIC;

-- Preserve the existing Scribe lineage under its own exact authority root.
-- No non-Scribe target is inserted by this migration. The SELECT must return
-- exactly one internally consistent Scribe target or the migration aborts.
DO $seed_live_scribe_target$
DECLARE
    candidate_count BIGINT;
    existing_scribe_rows BIGINT;
BEGIN
    SELECT count(*)
      INTO candidate_count
      FROM kernel_company_scribe_admissions admission
      JOIN kernel_runtime_workers worker
        ON worker.id=admission.worker_id
       AND worker.tenant_id=admission.company_id
      JOIN kernel_company_worker_principals principal
        ON principal.worker_id=worker.id
       AND principal.company_id=worker.tenant_id
       AND principal.role_type=worker.role_type
     WHERE admission.company_id='__CC_SUITE_COMPANY_ID__'
       AND admission.deployment_id='__CC_SUITE_SCRIBE_DEPLOYMENT_ID__'
       AND admission.state='live'
       AND worker.fleet_type='company'
       AND worker.role_type='scribe'
       AND worker.status='active';
    SELECT
        (SELECT count(*) FROM kernel_company_scribe_packages)
        +(SELECT count(*) FROM kernel_company_scribe_admissions)
        +(SELECT count(*) FROM kernel_company_scribe_recovery_intents)
      INTO existing_scribe_rows;
    IF candidate_count=0 AND existing_scribe_rows=0 THEN
        -- Pristine ephemeral PostgreSQL Tier-1 databases have no activated
        -- Scribe lineage. They exercise the additive schema without seeding a
        -- synthetic authority row. Any non-pristine database must have the
        -- exact one-live-Scribe prestate or abort.
        RETURN;
    ELSIF candidate_count<>1 THEN
        RAISE EXCEPTION
            'Phase-4 migration requires one exact live Scribe prestate';
    END IF;

    INSERT INTO kernel_company_role_admission_targets (
        company_id,role_type,deployment_id,worker_id,host_kind,runtime_kind,
        mandate_id,mandate_artifact_sha256,state,eligible_at
    )
    SELECT
        admission.company_id,'scribe',admission.deployment_id,
        admission.worker_id,'macbook',admission.runtime_kind,
        admission.mandate_id,admission.mandate_artifact_sha256,
        'eligible',now()
      FROM kernel_company_scribe_admissions admission
      JOIN kernel_runtime_workers worker
        ON worker.id=admission.worker_id
       AND worker.tenant_id=admission.company_id
     WHERE admission.company_id='__CC_SUITE_COMPANY_ID__'
       AND admission.deployment_id='__CC_SUITE_SCRIBE_DEPLOYMENT_ID__'
       AND admission.state='live'
       AND worker.fleet_type='company'
       AND worker.role_type='scribe';
END
$seed_live_scribe_target$;

-- Additive role columns. The temporary default exists only for the exact
-- Scribe backfill and is removed before commit.
ALTER TABLE kernel_company_scribe_packages
    ADD COLUMN role_type TEXT DEFAULT 'scribe';
ALTER TABLE kernel_company_scribe_admissions
    ADD COLUMN role_type TEXT DEFAULT 'scribe';
ALTER TABLE kernel_company_scribe_recovery_intents
    ADD COLUMN role_type TEXT DEFAULT 'scribe';

UPDATE kernel_company_scribe_packages
   SET role_type='scribe'
 WHERE role_type IS NULL;
UPDATE kernel_company_scribe_admissions
   SET role_type='scribe'
 WHERE role_type IS NULL;
UPDATE kernel_company_scribe_recovery_intents
   SET role_type='scribe'
 WHERE role_type IS NULL;

ALTER TABLE kernel_company_scribe_packages
    ALTER COLUMN role_type SET NOT NULL,
    ALTER COLUMN role_type DROP DEFAULT;
ALTER TABLE kernel_company_scribe_admissions
    ALTER COLUMN role_type SET NOT NULL,
    ALTER COLUMN role_type DROP DEFAULT;
ALTER TABLE kernel_company_scribe_recovery_intents
    ALTER COLUMN role_type SET NOT NULL,
    ALTER COLUMN role_type DROP DEFAULT;

-- One closed vocabulary definition is repeated deliberately at the physical
-- table boundaries. Do not infer role from deployment_id text.
ALTER TABLE kernel_company_scribe_packages
    ADD CONSTRAINT kernel_company_package_role_type_check CHECK (role_type IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco'
        -- 'utility', -- FOUNDER-DECISION: add only after Utility joins the canonical Company role vocabulary.
    ));
ALTER TABLE kernel_company_scribe_admissions
    ADD CONSTRAINT kernel_company_admission_role_type_check CHECK (role_type IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco'
        -- 'utility', -- FOUNDER-DECISION: add only after Utility joins the canonical Company role vocabulary.
    ));
ALTER TABLE kernel_company_scribe_recovery_intents
    ADD CONSTRAINT kernel_company_recovery_role_type_check CHECK (role_type IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco'
        -- 'utility', -- FOUNDER-DECISION: add only after Utility joins the canonical Company role vocabulary.
    ));

-- Drop only the three DDL-025 deployment_id='__CC_SUITE_SCRIBE_DEPLOYMENT_ID__' CHECK constraints.
-- Constraint names may be generated, so identify them by owning table and
-- exact definition. Any unexpected count aborts instead of broad-dropping.
DO $drop_scribe_deployment_checks$
DECLARE
    target REGCLASS;
    item RECORD;
    found_count BIGINT;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'kernel_company_scribe_packages'::REGCLASS,
        'kernel_company_scribe_admissions'::REGCLASS,
        'kernel_company_scribe_recovery_intents'::REGCLASS
    ] LOOP
        SELECT count(*)
          INTO found_count
          FROM pg_constraint constraint_row
         WHERE constraint_row.conrelid=target
           AND constraint_row.contype='c'
           AND pg_get_constraintdef(constraint_row.oid)
               LIKE '%deployment_id%__CC_SUITE_SCRIBE_DEPLOYMENT_ID__%';
        IF found_count<>1 THEN
            RAISE EXCEPTION
                'expected one Scribe deployment CHECK on %, found %',
                target,found_count;
        END IF;
        FOR item IN
            SELECT constraint_row.conname
              FROM pg_constraint constraint_row
             WHERE constraint_row.conrelid=target
               AND constraint_row.contype='c'
               AND pg_get_constraintdef(constraint_row.oid)
                   LIKE '%deployment_id%__CC_SUITE_SCRIBE_DEPLOYMENT_ID__%'
        LOOP
            EXECUTE format(
                'ALTER TABLE %s DROP CONSTRAINT %I',target,item.conname
            );
        END LOOP;
    END LOOP;
END
$drop_scribe_deployment_checks$;

-- Exact target foreign keys make a role/deployment swap fail before any
-- trigger body can create or renew authority.
ALTER TABLE kernel_company_scribe_packages
    ADD CONSTRAINT kernel_company_package_admission_target_fkey
    FOREIGN KEY (company_id,role_type,deployment_id)
    REFERENCES kernel_company_role_admission_targets
        (company_id,role_type,deployment_id)
    NOT VALID;
ALTER TABLE kernel_company_scribe_admissions
    ADD CONSTRAINT kernel_company_admission_target_fkey
    FOREIGN KEY (company_id,role_type,deployment_id,worker_id)
    REFERENCES kernel_company_role_admission_targets
        (company_id,role_type,deployment_id,worker_id)
    NOT VALID;
ALTER TABLE kernel_company_scribe_recovery_intents
    ADD CONSTRAINT kernel_company_recovery_target_fkey
    FOREIGN KEY (company_id,role_type,deployment_id,worker_id)
    REFERENCES kernel_company_role_admission_targets
        (company_id,role_type,deployment_id,worker_id)
    NOT VALID;

ALTER TABLE kernel_company_scribe_packages
    VALIDATE CONSTRAINT kernel_company_package_admission_target_fkey;
ALTER TABLE kernel_company_scribe_admissions
    VALIDATE CONSTRAINT kernel_company_admission_target_fkey;
ALTER TABLE kernel_company_scribe_recovery_intents
    VALIDATE CONSTRAINT kernel_company_recovery_target_fkey;

-- Replace global/Scribe-only singleton and epoch surfaces only after the
-- target and role constraints validate.
DROP INDEX idx_kernel_one_installed_company_scribe_package;
CREATE UNIQUE INDEX idx_kernel_one_installed_company_role_package
    ON kernel_company_scribe_packages
        (company_id,role_type,deployment_id)
    WHERE state='installed';

DROP INDEX idx_kernel_one_live_company_scribe_admission;
CREATE UNIQUE INDEX idx_kernel_one_live_company_role_admission
    ON kernel_company_scribe_admissions (company_id,role_type)
    WHERE state='live';

ALTER TABLE kernel_company_scribe_admissions
    DROP CONSTRAINT kernel_company_scribe_admissions_company_id_admission_epoch_key,
    ADD CONSTRAINT kernel_company_role_admission_epoch_key
        UNIQUE (company_id,role_type,admission_epoch);

DROP INDEX idx_kernel_one_initial_scribe_admission_identity;
CREATE UNIQUE INDEX idx_kernel_one_initial_company_role_admission_identity
    ON kernel_company_scribe_admissions
        (company_id,role_type,authority_approval_id)
    WHERE authority_mode='initial-local-activation';

DROP INDEX idx_kernel_one_open_scribe_recovery_intent;
CREATE UNIQUE INDEX idx_kernel_one_open_company_role_recovery_intent
    ON kernel_company_scribe_recovery_intents
        (company_id,role_type,prior_admission_id)
    WHERE state IN ('prepared','consumed');

DROP INDEX idx_kernel_one_scribe_recovery_epoch;
CREATE UNIQUE INDEX idx_kernel_one_company_role_recovery_epoch
    ON kernel_company_scribe_recovery_intents
        (company_id,role_type,next_admission_epoch)
    WHERE state IN ('prepared','consumed');

-- Keep the DDL-025 trigger functions byte-compatible for Scribe rows. Generic
-- functions run only for non-Scribe roles and add role equality to every
-- immutable, package, worker, session, mandate, principal, and recovery axis.
DROP TRIGGER kernel_company_scribe_package_guard
    ON kernel_company_scribe_packages;
CREATE TRIGGER kernel_company_scribe_package_guard
BEFORE INSERT OR UPDATE ON kernel_company_scribe_packages
FOR EACH ROW WHEN (NEW.role_type='scribe')
EXECUTE FUNCTION kernel_validate_company_scribe_package();

CREATE FUNCTION kernel_validate_company_role_package()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    target kernel_company_role_admission_targets%ROWTYPE;
BEGIN
    SELECT * INTO target
      FROM kernel_company_role_admission_targets
     WHERE company_id=NEW.company_id
       AND role_type=NEW.role_type
       AND deployment_id=NEW.deployment_id
       AND host_kind=NEW.host_kind
       AND mandate_id=(NEW.package_manifest->>'mandate_id')
       AND mandate_artifact_sha256=(NEW.package_manifest->>'mandate_artifact_sha256')
     FOR SHARE;
    IF target.company_id IS NULL OR target.state NOT IN ('held','eligible') THEN
        RAISE EXCEPTION 'Company role package lacks its exact admission target';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'held' OR NEW.installed_at IS NOT NULL
           OR NEW.retired_at IS NOT NULL THEN
            RAISE EXCEPTION 'new Company role package must start held';
        END IF;
        RETURN NEW;
    END IF;
    IF (
        to_jsonb(NEW)-ARRAY['state','installed_at','retired_at']::TEXT[]
    ) IS DISTINCT FROM (
        to_jsonb(OLD)-ARRAY['state','installed_at','retired_at']::TEXT[]
    ) THEN
        RAISE EXCEPTION 'Company role package identity is immutable';
    END IF;
    IF OLD.state='retired' AND NEW.state<>'retired' THEN
        RAISE EXCEPTION 'retired Company role package is terminal';
    END IF;
    IF NEW.state='installed' AND target.state<>'eligible' THEN
        RAISE EXCEPTION 'Company role package cannot install before target eligibility';
    END IF;
    NEW.installed_at:=CASE
        WHEN NEW.state='installed' THEN COALESCE(OLD.installed_at,now())
        ELSE NULL
    END;
    NEW.retired_at:=CASE
        WHEN NEW.state='retired' THEN COALESCE(OLD.retired_at,now())
        ELSE NULL
    END;
    RETURN NEW;
END
$function$;
CREATE TRIGGER kernel_company_role_package_guard
BEFORE INSERT OR UPDATE ON kernel_company_scribe_packages
FOR EACH ROW WHEN (NEW.role_type<>'scribe')
EXECUTE FUNCTION kernel_validate_company_role_package();

DROP TRIGGER kernel_company_scribe_recovery_intent_guard
    ON kernel_company_scribe_recovery_intents;
CREATE TRIGGER kernel_company_scribe_recovery_intent_guard
BEFORE INSERT OR UPDATE ON kernel_company_scribe_recovery_intents
FOR EACH ROW WHEN (NEW.role_type='scribe')
EXECUTE FUNCTION kernel_guard_company_scribe_recovery_intent();

CREATE FUNCTION kernel_guard_company_role_recovery_intent()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    prior kernel_company_scribe_admissions%ROWTYPE;
    root kernel_company_scribe_admissions%ROWTYPE;
BEGIN
    IF TG_OP='UPDATE' AND (
        to_jsonb(NEW)-ARRAY[
            'state','consumed_at','abandoned_at','restarted_admission_id'
        ]::TEXT[]
    ) IS DISTINCT FROM (
        to_jsonb(OLD)-ARRAY[
            'state','consumed_at','abandoned_at','restarted_admission_id'
        ]::TEXT[]
    ) THEN
        RAISE EXCEPTION 'Company role recovery intent identity is immutable';
    END IF;
    IF TG_OP='INSERT' THEN
        SELECT * INTO prior FROM kernel_company_scribe_admissions
         WHERE id=NEW.prior_admission_id AND company_id=NEW.company_id
           AND role_type=NEW.role_type FOR SHARE;
        SELECT * INTO root FROM kernel_company_scribe_admissions
         WHERE id=NEW.authority_root_admission_id AND company_id=NEW.company_id
           AND role_type=NEW.role_type FOR SHARE;
        IF NEW.state<>'prepared'
           OR NEW.prepared_at>now()+interval '5 seconds'
           OR NEW.lease_expires_at<=now()
           OR NEW.lease_expires_at>now()+interval '5 minutes'
           OR prior.id IS NULL OR prior.state<>'revoked'
           OR root.id IS NULL OR root.authority_mode<>'initial-local-activation'
           OR NEW.next_admission_epoch<>prior.admission_epoch+1
           OR (prior.deployment_id,prior.worker_id,prior.package_id,
               prior.agent_release_sha256,prior.kernel_release_sha256,
               prior.package_sha256,prior.repo_commit_sha,prior.repo_tree_sha,
               prior.host_identity_sha256,prior.runtime_executable_path,
               prior.runtime_executable_sha256,prior.runtime_lock_path,
               prior.authority_root_admission_id)
              IS DISTINCT FROM
              (NEW.deployment_id,NEW.worker_id,NEW.package_id,
               NEW.agent_release_sha256,NEW.kernel_release_sha256,
               NEW.package_sha256,NEW.repo_commit_sha,NEW.repo_tree_sha,
               NEW.host_identity_sha256,NEW.runtime_executable_path,
               NEW.runtime_executable_sha256,NEW.runtime_lock_path,
               NEW.authority_root_admission_id)
           OR EXISTS (
                SELECT 1 FROM kernel_company_scribe_admissions admission
                 WHERE admission.company_id=NEW.company_id
                   AND admission.role_type=NEW.role_type
                   AND admission.state='live'
           ) THEN
            RAISE EXCEPTION 'Company role recovery intent is not exact or conflicts';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state<>'prepared' OR NEW.state NOT IN ('consumed','abandoned') THEN
        RAISE EXCEPTION 'Company role recovery intent transition is invalid';
    END IF;
    IF NEW.state='consumed' THEN
        IF NEW.restarted_admission_id<>NEW.admission_id OR NOT EXISTS (
            SELECT 1 FROM kernel_company_scribe_admissions admission
             WHERE admission.id=NEW.admission_id
               AND admission.company_id=NEW.company_id
               AND admission.role_type=NEW.role_type
               AND admission.recovery_intent_id=NEW.id
               AND admission.state='live'
        ) THEN
            RAISE EXCEPTION 'Company role recovery intent has no exact live admission';
        END IF;
        NEW.consumed_at:=now(); NEW.abandoned_at:=NULL;
    ELSE
        IF OLD.lease_expires_at>now() OR NEW.restarted_admission_id IS NOT NULL
           OR NOT EXISTS (
                SELECT 1 FROM kernel_worker_sessions session
                 WHERE session.id=NEW.worker_session_id
                   AND session.tenant_id=NEW.company_id
                   AND session.role_type=NEW.role_type
                   AND session.state='ended'
           ) THEN
            RAISE EXCEPTION 'Company role recovery intent cannot abandon a live candidate';
        END IF;
        NEW.consumed_at:=NULL; NEW.abandoned_at:=now();
    END IF;
    RETURN NEW;
END
$function$;
CREATE TRIGGER kernel_company_role_recovery_intent_guard
BEFORE INSERT OR UPDATE ON kernel_company_scribe_recovery_intents
FOR EACH ROW WHEN (NEW.role_type<>'scribe')
EXECUTE FUNCTION kernel_guard_company_role_recovery_intent();

DROP TRIGGER kernel_company_scribe_admission_guard
    ON kernel_company_scribe_admissions;
CREATE TRIGGER kernel_company_scribe_admission_guard
BEFORE INSERT OR UPDATE ON kernel_company_scribe_admissions
FOR EACH ROW WHEN (NEW.role_type='scribe')
EXECUTE FUNCTION kernel_validate_company_scribe_admission();

CREATE FUNCTION kernel_validate_company_role_admission()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    previous_epoch BIGINT;
    target kernel_company_role_admission_targets%ROWTYPE;
    worker RECORD;
    session kernel_worker_sessions%ROWTYPE;
    package kernel_company_scribe_packages%ROWTYPE;
    mandate kernel_founder_activation_mandates%ROWTYPE;
    subject kernel_company_role_activation_subjects%ROWTYPE;
    root kernel_company_scribe_admissions%ROWTYPE;
    prior kernel_company_scribe_admissions%ROWTYPE;
    intent kernel_company_scribe_recovery_intents%ROWTYPE;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF (
            to_jsonb(NEW)-ARRAY[
                'state','heartbeat_at','lease_expires_at','closed_at'
            ]::TEXT[]
        ) IS DISTINCT FROM (
            to_jsonb(OLD)-ARRAY[
                'state','heartbeat_at','lease_expires_at','closed_at'
            ]::TEXT[]
        ) THEN
            RAISE EXCEPTION 'Company role admission identity is immutable';
        END IF;
        IF OLD.state<>'live' AND NEW.state<>OLD.state THEN
            RAISE EXCEPTION 'closed Company role admission is terminal';
        END IF;
        IF NEW.heartbeat_at<OLD.heartbeat_at
           OR NEW.lease_expires_at<OLD.lease_expires_at THEN
            RAISE EXCEPTION 'Company role admission clocks cannot move backward';
        END IF;
        IF OLD.state='live' AND NEW.state<>'live' THEN
            IF NEW.heartbeat_at IS DISTINCT FROM OLD.heartbeat_at
               OR NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at THEN
                RAISE EXCEPTION 'Company role closure cannot rewrite clocks';
            END IF;
            NEW.closed_at:=now();
            RETURN NEW;
        END IF;
    ELSIF NEW.state<>'live' OR NEW.closed_at IS NOT NULL THEN
        RAISE EXCEPTION 'new Company role admission must start live';
    END IF;
    IF NEW.heartbeat_at>now()+interval '5 seconds'
       OR NEW.lease_expires_at<=NEW.heartbeat_at
       OR NEW.lease_expires_at>NEW.heartbeat_at+interval '5 minutes' THEN
        RAISE EXCEPTION 'Company role admission lease exceeds its bounded horizon';
    END IF;
    IF TG_OP='INSERT' THEN
        PERFORM pg_advisory_xact_lock(hashtextextended(
            '__CC_SUITE_COMPANY_ID__:'||NEW.role_type||':admission',0
        ));
        SELECT max(admission_epoch) INTO previous_epoch
          FROM kernel_company_scribe_admissions
         WHERE company_id=NEW.company_id AND role_type=NEW.role_type;
        IF NEW.admission_epoch<>COALESCE(previous_epoch,0)+1 THEN
            RAISE EXCEPTION 'Company role admission epoch must be exactly monotonic';
        END IF;
    END IF;
    SELECT * INTO target FROM kernel_company_role_admission_targets
     WHERE company_id=NEW.company_id AND role_type=NEW.role_type
       AND deployment_id=NEW.deployment_id AND worker_id=NEW.worker_id
       AND runtime_kind=NEW.runtime_kind AND host_kind='macbook'
       AND mandate_id=NEW.mandate_id
       AND mandate_artifact_sha256=NEW.mandate_artifact_sha256
       AND state='eligible' FOR SHARE;
    SELECT worker_row.*,manifest.status AS manifest_status INTO worker
      FROM kernel_runtime_workers worker_row
      JOIN kernel_tenant_fleet_manifests manifest
        ON manifest.id=worker_row.manifest_id
       AND manifest.tenant_id=worker_row.tenant_id
     WHERE worker_row.id=NEW.worker_id
       AND worker_row.tenant_id=NEW.company_id FOR SHARE OF worker_row,manifest;
    SELECT * INTO session FROM kernel_worker_sessions
     WHERE id=NEW.worker_session_id AND tenant_id=NEW.company_id
       AND worker_id=NEW.worker_id FOR SHARE;
    SELECT * INTO package FROM kernel_company_scribe_packages
     WHERE id=NEW.package_id AND company_id=NEW.company_id
       AND role_type=NEW.role_type AND deployment_id=NEW.deployment_id FOR SHARE;
    SELECT * INTO mandate FROM kernel_founder_activation_mandates
     WHERE mandate_id=NEW.mandate_id FOR SHARE;
    SELECT * INTO subject FROM kernel_company_role_activation_subjects
     WHERE approval_id=NEW.authority_approval_id
       AND company_id=NEW.company_id AND role_type=NEW.role_type
       AND deployment_id=NEW.deployment_id AND worker_id=NEW.worker_id
       AND mandate_id=NEW.mandate_id
       AND mandate_artifact_sha256=NEW.mandate_artifact_sha256
       AND subject_ref=NEW.authority_subject_ref
       AND subject_sha256=NEW.authority_subject_sha256
       AND state='approved' FOR UPDATE;
    IF target.company_id IS NULL
       OR worker.id IS NULL OR worker.fleet_type<>'company'
       OR worker.role_type<>NEW.role_type OR worker.status<>'active'
       OR worker.manifest_status<>'active'
       OR worker.agent_release_sha256<>NEW.agent_release_sha256
       OR session.id IS NULL OR session.role_type<>NEW.role_type
       OR session.agent_release_sha256<>NEW.agent_release_sha256
       OR session.state<>'active' OR session.lease_expires_at<=now()
       OR encode(digest(convert_to(session.lease_token,'UTF8'),'sha256'),'hex')
          <>NEW.lease_token_sha256
       OR NEW.lease_expires_at>session.lease_expires_at
       OR package.id IS NULL OR package.state<>'installed'
       OR package.host_kind<>'macbook'
       OR package.kernel_release_sha256<>NEW.kernel_release_sha256
       OR package.package_sha256<>NEW.package_sha256
       OR package.source_commit_sha<>NEW.repo_commit_sha
       OR package.source_tree_sha<>NEW.repo_tree_sha
       OR mandate.mandate_id IS NULL OR mandate.status<>'active'
       OR mandate.artifact_sha256<>NEW.mandate_artifact_sha256
       OR mandate.authority_verification_mac IS DISTINCT FROM
          kernel_founder_mandate_expected_mac(
              mandate.mandate_id,mandate.artifact_sha256,
              mandate.artifact_bytes,mandate.artifact,mandate.issued_at
          )
       OR subject.approval_id IS NULL
       OR NOT EXISTS (
            SELECT 1 FROM kernel_company_worker_principals principal
             WHERE principal.worker_id=NEW.worker_id
               AND principal.company_id=NEW.company_id
               AND principal.role_type=NEW.role_type
       ) THEN
        RAISE EXCEPTION 'Company role admission tuple is not exact';
    END IF;
    IF NEW.authority_mode='initial-local-activation' THEN
        IF NEW.authority_root_admission_id<>NEW.id
           OR NEW.prior_admission_id IS NOT NULL
           OR NEW.recovery_intent_id IS NOT NULL
           OR NEW.prior_rollback_receipt_sha256 IS NOT NULL
           OR NEW.restart_intent_sha256 IS NOT NULL THEN
            RAISE EXCEPTION 'initial Company role admission has recovery residue';
        END IF;
    ELSE
        SELECT * INTO root FROM kernel_company_scribe_admissions
         WHERE id=NEW.authority_root_admission_id
           AND company_id=NEW.company_id AND role_type=NEW.role_type FOR SHARE;
        SELECT * INTO prior FROM kernel_company_scribe_admissions
         WHERE id=NEW.prior_admission_id
           AND company_id=NEW.company_id AND role_type=NEW.role_type FOR SHARE;
        SELECT * INTO intent FROM kernel_company_scribe_recovery_intents
         WHERE id=NEW.recovery_intent_id
           AND company_id=NEW.company_id AND role_type=NEW.role_type FOR UPDATE;
        IF root.id IS NULL OR root.authority_mode<>'initial-local-activation'
           OR prior.id IS NULL OR prior.state<>'revoked'
           OR intent.id IS NULL OR intent.state<>'prepared'
           OR prior.admission_epoch+1<>NEW.admission_epoch
           OR (root.deployment_id,root.worker_id,root.package_id,
               root.agent_release_sha256,root.kernel_release_sha256,
               root.package_sha256,root.repo_commit_sha,root.repo_tree_sha,
               root.host_identity_sha256,root.runtime_executable_path,
               root.runtime_executable_sha256,root.runtime_lock_path,
               root.mandate_id,root.mandate_artifact_sha256)
              IS DISTINCT FROM
              (NEW.deployment_id,NEW.worker_id,NEW.package_id,
               NEW.agent_release_sha256,NEW.kernel_release_sha256,
               NEW.package_sha256,NEW.repo_commit_sha,NEW.repo_tree_sha,
               NEW.host_identity_sha256,NEW.runtime_executable_path,
               NEW.runtime_executable_sha256,NEW.runtime_lock_path,
               NEW.mandate_id,NEW.mandate_artifact_sha256)
           OR (prior.deployment_id,prior.worker_id,prior.package_id,
               prior.agent_release_sha256,prior.kernel_release_sha256,
               prior.package_sha256,prior.repo_commit_sha,prior.repo_tree_sha,
               prior.host_identity_sha256,prior.runtime_executable_path,
               prior.runtime_executable_sha256,prior.runtime_lock_path,
               prior.authority_root_admission_id)
              IS DISTINCT FROM
              (NEW.deployment_id,NEW.worker_id,NEW.package_id,
               NEW.agent_release_sha256,NEW.kernel_release_sha256,
               NEW.package_sha256,NEW.repo_commit_sha,NEW.repo_tree_sha,
               NEW.host_identity_sha256,NEW.runtime_executable_path,
               NEW.runtime_executable_sha256,NEW.runtime_lock_path,
               NEW.authority_root_admission_id)
           OR (intent.deployment_id,intent.worker_id,intent.package_id,
               intent.agent_release_sha256,intent.kernel_release_sha256,
               intent.package_sha256,intent.repo_commit_sha,intent.repo_tree_sha,
               intent.host_identity_sha256,intent.runtime_executable_path,
               intent.runtime_executable_sha256,intent.runtime_lock_path,
               intent.authority_root_admission_id,intent.prior_admission_id,
               intent.next_admission_epoch,intent.admission_id)
              IS DISTINCT FROM
              (NEW.deployment_id,NEW.worker_id,NEW.package_id,
               NEW.agent_release_sha256,NEW.kernel_release_sha256,
               NEW.package_sha256,NEW.repo_commit_sha,NEW.repo_tree_sha,
               NEW.host_identity_sha256,NEW.runtime_executable_path,
               NEW.runtime_executable_sha256,NEW.runtime_lock_path,
               NEW.authority_root_admission_id,NEW.prior_admission_id,
               NEW.admission_epoch,NEW.id) THEN
            RAISE EXCEPTION 'Company role recovery lacks exact same-lineage authority';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;
CREATE TRIGGER kernel_company_role_admission_guard
BEFORE INSERT OR UPDATE ON kernel_company_scribe_admissions
FOR EACH ROW WHEN (NEW.role_type<>'scribe')
EXECUTE FUNCTION kernel_validate_company_role_admission();

DROP TRIGGER kernel_company_scribe_recovery_intent_consume
    ON kernel_company_scribe_admissions;
CREATE TRIGGER kernel_company_scribe_recovery_intent_consume
AFTER INSERT ON kernel_company_scribe_admissions
FOR EACH ROW WHEN (NEW.role_type='scribe')
EXECUTE FUNCTION kernel_consume_company_scribe_recovery_intent();
CREATE FUNCTION kernel_consume_company_role_recovery_intent()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public
AS $function$
BEGIN
    IF NEW.authority_mode='standing-mandate-recovery' THEN
        UPDATE kernel_company_scribe_recovery_intents
           SET state='consumed',restarted_admission_id=NEW.id
         WHERE id=NEW.recovery_intent_id AND company_id=NEW.company_id
           AND role_type=NEW.role_type AND deployment_id=NEW.deployment_id
           AND worker_id=NEW.worker_id AND state='prepared';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Company role recovery intent was not atomically consumed';
        END IF;
    END IF;
    UPDATE kernel_company_role_activation_subjects
       SET state='consumed',consumed_at=now()
     WHERE approval_id=NEW.authority_approval_id
       AND company_id=NEW.company_id AND role_type=NEW.role_type
       AND state='approved';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Company role authority subject was not atomically consumed';
    END IF;
    RETURN NEW;
END
$function$;
CREATE TRIGGER kernel_company_role_recovery_intent_consume
AFTER INSERT ON kernel_company_scribe_admissions
FOR EACH ROW WHEN (NEW.role_type<>'scribe')
EXECUTE FUNCTION kernel_consume_company_role_recovery_intent();

CREATE FUNCTION kernel_company_role_context_proof(
    p_role_type TEXT,p_admission_id UUID,p_admission_epoch BIGINT,
    p_db_role NAME,p_txid BIGINT
)
RETURNS TEXT LANGUAGE plpgsql STABLE STRICT SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE admission_key BYTEA;
BEGIN
    IF p_role_type NOT IN (
        'cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco'
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
REVOKE ALL ON FUNCTION kernel_company_role_context_proof(
    TEXT,UUID,BIGINT,NAME,BIGINT
) FROM PUBLIC;

CREATE FUNCTION kernel_bind_company_role_admission(
    p_role_type TEXT,p_admission_id UUID,p_lease_token TEXT,p_binding JSONB
)
RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    admission kernel_company_scribe_admissions%ROWTYPE;
    expected_binding JSONB;
    proof TEXT;
BEGIN
    SELECT * INTO admission FROM kernel_company_scribe_admissions
     WHERE id=p_admission_id AND company_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=p_role_type FOR SHARE;
    expected_binding:=jsonb_build_object(
        'company_id',admission.company_id,'role_type',admission.role_type,
        'deployment_id',admission.deployment_id,'worker_id',admission.worker_id,
        'worker_session_id',admission.worker_session_id,
        'package_id',admission.package_id,
        'agent_release_sha256',admission.agent_release_sha256,
        'kernel_release_sha256',admission.kernel_release_sha256,
        'package_sha256',admission.package_sha256,
        'task_id',admission.task_id,'claude_session_id',admission.claude_session_id,
        'process_id',admission.process_id,
        'process_start_fingerprint_sha256',admission.process_start_fingerprint_sha256,
        'repo_commit_sha',admission.repo_commit_sha,'repo_tree_sha',admission.repo_tree_sha,
        'admission_epoch',admission.admission_epoch,'mandate_id',admission.mandate_id,
        'mandate_artifact_sha256',admission.mandate_artifact_sha256,
        'authority_approval_id',admission.authority_approval_id,
        'authority_subject_ref',admission.authority_subject_ref,
        'authority_subject_sha256',admission.authority_subject_sha256,
        'host_identity_sha256',admission.host_identity_sha256,
        'host_boot_id_sha256',admission.host_boot_id_sha256,
        'runtime_executable_path',admission.runtime_executable_path,
        'runtime_executable_sha256',admission.runtime_executable_sha256,
        'runtime_lock_path',admission.runtime_lock_path,
        'authority_mode',admission.authority_mode,
        'authority_root_admission_id',admission.authority_root_admission_id,
        'prior_admission_id',admission.prior_admission_id,
        'recovery_intent_id',admission.recovery_intent_id,
        'prior_rollback_receipt_sha256',admission.prior_rollback_receipt_sha256,
        'restart_intent_sha256',admission.restart_intent_sha256
    );
    IF admission.id IS NULL OR admission.state<>'live'
       OR admission.lease_expires_at<=now() OR p_binding<>expected_binding
       OR encode(digest(convert_to(p_lease_token,'UTF8'),'sha256'),'hex')
          <>admission.lease_token_sha256
       OR NOT EXISTS (
            SELECT 1 FROM kernel_company_worker_principals principal
             WHERE principal.worker_id=admission.worker_id
               AND principal.company_id=admission.company_id
               AND principal.role_type=admission.role_type
               AND principal.db_role=session_user::NAME
       ) OR NOT EXISTS (
            SELECT 1 FROM kernel_worker_sessions session
             WHERE session.id=admission.worker_session_id
               AND session.worker_id=admission.worker_id
               AND session.tenant_id=admission.company_id
               AND session.role_type=admission.role_type
               AND session.state='active' AND session.lease_expires_at>now()
               AND encode(digest(convert_to(session.lease_token,'UTF8'),'sha256'),'hex')
                   =admission.lease_token_sha256
       ) THEN
        RAISE EXCEPTION 'Company role admission binding is invalid or fenced';
    END IF;
    proof:=kernel_company_role_context_proof(
        admission.role_type,admission.id,admission.admission_epoch,
        session_user::NAME,txid_current()
    );
    PERFORM set_config('mise.company_role_type',admission.role_type,true);
    PERFORM set_config('mise.company_role_admission_id',admission.id::TEXT,true);
    PERFORM set_config(
        'mise.company_role_admission_epoch',admission.admission_epoch::TEXT,true
    );
    PERFORM set_config('mise.company_role_admission_proof',proof,true);
END
$function$;
REVOKE ALL ON FUNCTION kernel_bind_company_role_admission(
    TEXT,UUID,TEXT,JSONB
) FROM PUBLIC;

CREATE FUNCTION kernel_heartbeat_company_role_admission(
    p_role_type TEXT,p_admission_id UUID,p_lease_token TEXT,
    p_lease_expires_at TIMESTAMPTZ
)
RETURNS VOID LANGUAGE plpgsql VOLATILE STRICT SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE admission kernel_company_scribe_admissions%ROWTYPE;
BEGIN
    SELECT * INTO admission FROM kernel_company_scribe_admissions
     WHERE id=p_admission_id AND company_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=p_role_type FOR UPDATE;
    IF admission.id IS NULL OR admission.state<>'live'
       OR admission.lease_expires_at<=now()
       OR encode(digest(convert_to(p_lease_token,'UTF8'),'sha256'),'hex')
          <>admission.lease_token_sha256
       OR p_lease_expires_at<=admission.lease_expires_at
       OR p_lease_expires_at>now()+interval '5 minutes'
       OR NOT EXISTS (
            SELECT 1 FROM kernel_company_worker_principals principal
             WHERE principal.worker_id=admission.worker_id
               AND principal.company_id=admission.company_id
               AND principal.role_type=admission.role_type
               AND principal.db_role=session_user::NAME
       ) OR NOT EXISTS (
            SELECT 1 FROM kernel_worker_sessions session
             WHERE session.id=admission.worker_session_id
               AND session.worker_id=admission.worker_id
               AND session.tenant_id=admission.company_id
               AND session.role_type=admission.role_type
               AND session.state='active'
               AND session.lease_expires_at>=p_lease_expires_at
       ) THEN
        RAISE EXCEPTION 'Company role admission heartbeat is invalid or fenced';
    END IF;
    UPDATE kernel_company_scribe_admissions
       SET heartbeat_at=now(),lease_expires_at=p_lease_expires_at
     WHERE id=admission.id;
END
$function$;
REVOKE ALL ON FUNCTION kernel_heartbeat_company_role_admission(
    TEXT,UUID,TEXT,TIMESTAMPTZ
) FROM PUBLIC;

CREATE FUNCTION kernel_require_company_role_admission()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    principal_role TEXT;
    admission kernel_company_scribe_admissions%ROWTYPE;
    bound_id UUID; bound_epoch BIGINT; supplied_proof TEXT; expected_proof TEXT;
BEGIN
    SELECT role_type INTO principal_role
      FROM kernel_company_worker_principals
     WHERE company_id='__CC_SUITE_COMPANY_ID__' AND db_role=session_user::NAME;
    IF principal_role IS NULL OR principal_role='scribe' THEN RETURN NEW; END IF;
    BEGIN
        bound_id:=current_setting('mise.company_role_admission_id',true)::UUID;
        bound_epoch:=current_setting('mise.company_role_admission_epoch',true)::BIGINT;
        supplied_proof:=current_setting('mise.company_role_admission_proof',true);
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'Company role write requires an exact transaction admission';
    END;
    SELECT * INTO admission FROM kernel_company_scribe_admissions
     WHERE id=bound_id AND company_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=principal_role FOR SHARE;
    expected_proof:=kernel_company_role_context_proof(
        principal_role,bound_id,bound_epoch,session_user::NAME,txid_current()
    );
    IF admission.id IS NULL OR admission.state<>'live'
       OR admission.lease_expires_at<=now()
       OR admission.admission_epoch<>bound_epoch
       OR current_setting('mise.company_role_type',true)<>principal_role
       OR supplied_proof IS NULL
       OR supplied_proof::BYTEA<>expected_proof::BYTEA
       OR NOT EXISTS (
            SELECT 1 FROM kernel_worker_sessions session
             WHERE session.id=admission.worker_session_id
               AND session.worker_id=admission.worker_id
               AND session.tenant_id=admission.company_id
               AND session.role_type=admission.role_type
               AND session.state='active' AND session.lease_expires_at>now()
       ) THEN
        RAISE EXCEPTION 'Company role write admission is missing, stale, or revoked';
    END IF;
    RETURN NEW;
END
$function$;
REVOKE ALL ON FUNCTION kernel_require_company_role_admission() FROM PUBLIC;

CREATE TRIGGER kernel_01_company_role_admission_guard
BEFORE INSERT OR UPDATE ON kernel_company_objectives
FOR EACH ROW EXECUTE FUNCTION kernel_require_company_role_admission();
CREATE TRIGGER kernel_01_company_role_admission_guard
BEFORE INSERT ON kernel_company_objective_events
FOR EACH ROW EXECUTE FUNCTION kernel_require_company_role_admission();

-- Grant only to exact principals already recorded under an eligible target.
DO $grant_company_role_functions$
DECLARE item RECORD;
BEGIN
    FOR item IN
        SELECT DISTINCT principal.db_role
          FROM kernel_company_role_admission_targets target
          JOIN kernel_company_worker_principals principal
            ON principal.worker_id=target.worker_id
           AND principal.company_id=target.company_id
           AND principal.role_type=target.role_type
         WHERE target.role_type<>'scribe' AND target.state='eligible'
    LOOP
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION '
            'kernel_bind_company_role_admission(TEXT,UUID,TEXT,JSONB) TO %I',
            item.db_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION '
            'kernel_heartbeat_company_role_admission(TEXT,UUID,TEXT,TIMESTAMPTZ) TO %I',
            item.db_role
        );
    END LOOP;
END
$grant_company_role_functions$;

-- RLS on the target registry mirrors the existing Company admission tables.
ALTER TABLE kernel_company_role_admission_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_role_admission_targets FORCE ROW LEVEL SECURITY;
CREATE POLICY kernel_company_scope
    ON kernel_company_role_admission_targets
    USING (
        company_id=kernel_current_company()
        OR session_user='fleet_kernel_provisioner'::NAME
    )
    WITH CHECK (
        company_id=kernel_current_company()
        OR session_user='fleet_kernel_provisioner'::NAME
    );

ALTER TABLE kernel_company_role_activation_subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_role_activation_subjects FORCE ROW LEVEL SECURITY;
CREATE POLICY kernel_company_scope
    ON kernel_company_role_activation_subjects
    USING (
        company_id=kernel_current_company()
        OR session_user='fleet_kernel_provisioner'::NAME
    )
    WITH CHECK (
        company_id=kernel_current_company()
        OR session_user='fleet_kernel_provisioner'::NAME
    );

-- No non-Scribe target, package, admission, recovery intent, principal,
-- capability grant, or launchd state is created by this schema transaction.
