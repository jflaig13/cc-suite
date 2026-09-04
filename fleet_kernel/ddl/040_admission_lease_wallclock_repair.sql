-- PHASE-4 COMPANY DATABASE MIGRATION.
--
-- WALL-CLOCK ADMISSION-LEASE REPAIR -- ROLE PLANE.
--
-- Closes Codex CMCV revision-4 finding C2 (kernel-comms design review r4,
-- cc_execs/verification/codex_reviews/20260807__kernel-comms-design-review-r4.md)
-- and the three same-class sites that review did not name.
--
-- DEFECT. PostgreSQL now() is transaction_timestamp(), fixed when the
-- transaction's first statement begins. Every admission-plane authorization
-- predicate evaluated lease liveness against it AFTER acquiring a row lock
-- that can block for an unbounded time. A transaction that starts inside the
-- lease and acquires the lock after the lease has really expired therefore
-- decides against a stale clock. On the heartbeat path that retroactively
-- extends an expired lease, which destroys renewal monotonicity: an admission
-- that was fenced at wall-clock T is live again at T+n. On the bind and write
-- guard paths the same stale clock admits writes under a lapsed lease.
-- statement_timestamp() does NOT fix this -- the whole function is one
-- statement, so it is also pinned before the lock wait. Only clock_timestamp()
-- is evaluated after the lock is granted.
--
-- REPAIR. Three cooperating legs, decided at three different loci.
--
-- Leg 1 (observation): each function samples observed:=clock_timestamp()
-- immediately after its row lock is granted and decides every liveness and
-- horizon predicate against `observed`. The heartbeat additionally refuses
-- when its own transaction age exceeds the file's existing 5-second skew
-- tolerance, and refuses a proposed expiry that is not in the future of
-- `observed`.
--
-- Leg 2 (write structure): the validator trigger refuses any update that
-- raises lease_expires_at on a row whose recorded lease had already lapsed
-- at the trigger's own clock_timestamp(), and maintains the renewal
-- provenance column itself: prior_lease_expires_at is trigger-assigned to
-- the expiry the new row version supersedes (the initial horizon on INSERT,
-- OLD.lease_expires_at on an extension), so ordinary DML cannot forge it.
-- Before ANY extension may re-anchor provenance, the trigger first proves
-- the OLD row version was itself commit-authoritative (its commit timestamp
-- within OLD.prior_lease_expires_at, unavailable = refuse) -- otherwise a
-- direct enabled-trigger extension inside a late-committed renewal's
-- recorded horizon would re-anchor provenance to the poisoned expiry and
-- restore its authority (the laundering interleaving the 2026-08-07 CMCV
-- re-review found as C2).
--
-- Leg 3 (post-commit authority -- the C1 closure). Legs 1 and 2 run before
-- commit and therefore cannot bound the observation-to-commit residual: a
-- validator lock-wait or a client-held transaction can delay the commit
-- arbitrarily past the recorded expiry. So the AUTHORITY of a renewal is
-- decided after the fact, by the readers: every authorization reader
-- (heartbeat, bind, write guard) refuses an admission row version whose
-- server-assigned commit timestamp (pg_xact_commit_timestamp(xmin), which
-- exists only once the writing transaction has truly committed and cannot
-- be forced early or forged by any protocol writer) is later than the
-- prior_lease_expires_at it superseded, or whose commit timestamp or
-- provenance is unavailable (fail-closed). A renewal that commits after the
-- previously recorded expiry can therefore never become authoritative, and
-- the heartbeat's own check prevents laundering the poisoned version through
-- a follow-on renewal. Requires track_commit_timestamp=on (deployer
-- postcondition; readers fail closed where it is off).
--
-- SCOPE. Role plane only. Function bodies are derived from their live
-- definitions (031 for bind/heartbeat/require, 034 for the validator) by the
-- exact per-function transformation sets pinned mechanically in
-- tests/test_tier1_kernel_admission_lease_wallclock.py -- clock
-- substitutions, the lapse guard, the heartbeat transaction-age and
-- forward-horizon rules, the provenance column maintenance, and the
-- commit-authority reader checks. The single schema change is the nullable
-- provenance column added below; bind carries NO transaction-age rule (an
-- earlier draft's undisclosed bind restriction was removed and its removal
-- ratified in the design record). This migration creates no table, index,
-- policy, grant, principal, target, admission, or process, and changes no
-- function signature. The Scribe plane is DDL 041 and is held separately: it
-- touches the live epoch-19 Scribe heartbeat path, and its held body predates
-- this file's commit-authority closure -- 041 MUST be re-derived to this
-- file's repaired shape before any un-hold.
--
-- DEPLOYMENT PRECONDITIONS
--
-- 1. Apply only through scripts/deploy_kernel_activation_ddl.py.
-- 2. The deployer owns the single outer transaction; open none here.
-- 3. This file IS pinned in DDL_ORDER (chain 037 -> 039 -> 040 -> 998;
--    deployment authorization: founder GO 2026-08-07). The Scribe plane
--    (041) remains outside every chain until its own authorization.
-- 4. The target cluster must run with track_commit_timestamp=on before the
--    first admission commits; the deployer refuses the deploy otherwise.

ALTER TABLE kernel_company_scribe_admissions
    ADD COLUMN IF NOT EXISTS prior_lease_expires_at TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION kernel_heartbeat_company_role_admission(
    p_role_type TEXT,p_admission_id UUID,p_lease_token TEXT,
    p_lease_expires_at TIMESTAMPTZ
)
RETURNS VOID LANGUAGE plpgsql VOLATILE STRICT SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    admission kernel_company_scribe_admissions%ROWTYPE;
    observed TIMESTAMPTZ;
    version_committed_at TIMESTAMPTZ;
BEGIN
    SELECT * INTO admission FROM kernel_company_scribe_admissions
     WHERE id=p_admission_id AND company_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=p_role_type FOR UPDATE;
    observed:=clock_timestamp();
    IF admission.id IS NOT NULL THEN
        BEGIN
            SELECT pg_xact_commit_timestamp(current_version.xmin)
              INTO version_committed_at
              FROM kernel_company_scribe_admissions current_version
             WHERE current_version.id=admission.id;
        EXCEPTION WHEN OTHERS THEN
            version_committed_at:=NULL;
        END;
        IF admission.prior_lease_expires_at IS NULL
           OR version_committed_at IS NULL
           OR version_committed_at>admission.prior_lease_expires_at THEN
            RAISE EXCEPTION
                'Company role admission lease lineage is not commit-authoritative';
        END IF;
    END IF;
    IF admission.id IS NULL OR admission.state<>'live'
       OR admission.lease_expires_at<=observed
       OR observed>transaction_timestamp()+interval '5 seconds'
       OR encode(digest(convert_to(p_lease_token,'UTF8'),'sha256'),'hex')
          <>admission.lease_token_sha256
       OR p_lease_expires_at<=admission.lease_expires_at
       OR p_lease_expires_at<=observed
       OR p_lease_expires_at>observed+interval '5 minutes'
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
       SET heartbeat_at=observed,lease_expires_at=p_lease_expires_at,
           prior_lease_expires_at=admission.lease_expires_at
     WHERE id=admission.id;
END
$function$;

CREATE OR REPLACE FUNCTION kernel_bind_company_role_admission(
    p_role_type TEXT,p_admission_id UUID,p_lease_token TEXT,p_binding JSONB
)
RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    admission kernel_company_scribe_admissions%ROWTYPE;
    expected_binding JSONB;
    observed TIMESTAMPTZ;
    version_committed_at TIMESTAMPTZ;
    proof TEXT;
BEGIN
    SELECT * INTO admission FROM kernel_company_scribe_admissions
     WHERE id=p_admission_id AND company_id='__CC_SUITE_COMPANY_ID__'
       AND role_type=p_role_type FOR SHARE;
    observed:=clock_timestamp();
    IF admission.id IS NOT NULL THEN
        BEGIN
            SELECT pg_xact_commit_timestamp(current_version.xmin)
              INTO version_committed_at
              FROM kernel_company_scribe_admissions current_version
             WHERE current_version.id=admission.id;
        EXCEPTION WHEN OTHERS THEN
            version_committed_at:=NULL;
        END;
        IF admission.prior_lease_expires_at IS NULL
           OR version_committed_at IS NULL
           OR version_committed_at>admission.prior_lease_expires_at THEN
            RAISE EXCEPTION
                'Company role admission lease lineage is not commit-authoritative';
        END IF;
    END IF;
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
       OR admission.lease_expires_at<=observed
       OR p_binding<>expected_binding
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
               AND session.state='active'
               AND session.lease_expires_at>observed
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

CREATE OR REPLACE FUNCTION kernel_require_company_role_admission()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    principal_role TEXT;
    admission kernel_company_scribe_admissions%ROWTYPE;
    bound_id UUID; bound_epoch BIGINT; supplied_proof TEXT; expected_proof TEXT;
    observed TIMESTAMPTZ;
    version_committed_at TIMESTAMPTZ;
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
    observed:=clock_timestamp();
    IF admission.id IS NOT NULL THEN
        BEGIN
            SELECT pg_xact_commit_timestamp(current_version.xmin)
              INTO version_committed_at
              FROM kernel_company_scribe_admissions current_version
             WHERE current_version.id=admission.id;
        EXCEPTION WHEN OTHERS THEN
            version_committed_at:=NULL;
        END;
        IF admission.prior_lease_expires_at IS NULL
           OR version_committed_at IS NULL
           OR version_committed_at>admission.prior_lease_expires_at THEN
            RAISE EXCEPTION
                'Company role admission lease lineage is not commit-authoritative';
        END IF;
    END IF;
    expected_proof:=kernel_company_role_context_proof(
        principal_role,bound_id,bound_epoch,session_user::NAME,txid_current()
    );
    IF admission.id IS NULL OR admission.state<>'live'
       OR admission.lease_expires_at<=observed
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
               AND session.state='active'
               AND session.lease_expires_at>observed
       ) THEN
        RAISE EXCEPTION 'Company role write admission is missing, stale, or revoked';
    END IF;
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION kernel_validate_company_role_admission()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    previous_epoch BIGINT;
    old_version_committed_at TIMESTAMPTZ;
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
        IF NEW.lease_expires_at>OLD.lease_expires_at THEN
            BEGIN
                SELECT pg_xact_commit_timestamp(current_version.xmin)
                  INTO old_version_committed_at
                  FROM kernel_company_scribe_admissions current_version
                 WHERE current_version.id=OLD.id;
            EXCEPTION WHEN OTHERS THEN
                old_version_committed_at:=NULL;
            END;
            IF OLD.prior_lease_expires_at IS NULL
               OR old_version_committed_at IS NULL
               OR old_version_committed_at>OLD.prior_lease_expires_at THEN
                RAISE EXCEPTION
                    'Company role admission extension lineage is not commit-authoritative';
            END IF;
            NEW.prior_lease_expires_at:=OLD.lease_expires_at;
        ELSE
            NEW.prior_lease_expires_at:=OLD.prior_lease_expires_at;
        END IF;
        IF (
            to_jsonb(NEW)-ARRAY[
                'state','heartbeat_at','lease_expires_at','closed_at',
                'prior_lease_expires_at'
            ]::TEXT[]
        ) IS DISTINCT FROM (
            to_jsonb(OLD)-ARRAY[
                'state','heartbeat_at','lease_expires_at','closed_at',
                'prior_lease_expires_at'
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
        IF OLD.state='live' AND NEW.state='live'
           AND NEW.lease_expires_at>OLD.lease_expires_at
           AND OLD.lease_expires_at<=clock_timestamp() THEN
            RAISE EXCEPTION
                'Company role admission lease cannot be extended after it lapsed';
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
    IF NEW.heartbeat_at>clock_timestamp()+interval '5 seconds'
       OR NEW.lease_expires_at<=NEW.heartbeat_at
       OR NEW.lease_expires_at>NEW.heartbeat_at+interval '5 minutes' THEN
        RAISE EXCEPTION 'Company role admission lease exceeds its bounded horizon';
    END IF;
    IF TG_OP='INSERT' THEN
        NEW.prior_lease_expires_at:=NEW.lease_expires_at;
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
       AND state=CASE WHEN TG_OP='INSERT' THEN 'approved' ELSE 'consumed' END
       FOR UPDATE;
    IF target.company_id IS NULL
       OR worker.id IS NULL OR worker.fleet_type<>'company'
       OR worker.role_type<>NEW.role_type OR worker.status<>'active'
       OR worker.manifest_status<>'active'
       OR worker.agent_release_sha256<>NEW.agent_release_sha256
       OR session.id IS NULL OR session.role_type<>NEW.role_type
       OR session.agent_release_sha256<>NEW.agent_release_sha256
       OR session.state<>'active'
       OR session.lease_expires_at<=clock_timestamp()
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
