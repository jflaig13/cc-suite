-- PHASE-4 COMPANY DATABASE MIGRATION.
--
-- WALL-CLOCK ADMISSION-LEASE REPAIR -- SCRIBE PLANE.
--
-- HELD. Same defect class and same repair idiom as DDL 040; see that file's
-- header for the full defect statement. This half is separated because it
-- replaces the Scribe-plane authorization functions and the validator
-- trigger that every renewal of the LIVE Company Scribe (epoch 19) rides.
-- Applying it is a live-runtime change, not a schema addition: the readers'
-- commit-authority checks would fence the live Scribe's provenance-less
-- admission row immediately (fail-closed and therefore safe, but it takes
-- the running Scribe down). Deploy only after an observation window
-- establishes the actual renewal lock-wait distribution on the live
-- database, and only under its own authorization.
--
-- ENFORCEMENT LOCUS (Codex CMCV 2026-08-08, finding HIGH-1 -- OPEN, owned
-- by the un-hold arc). The live supervisor renews admissions by a direct
-- table update (fleet_kernel/company_scribe_admission.py::
-- renew_live_admission), NOT by calling
-- kernel_heartbeat_company_scribe_admission -- the same call-path shape the
-- role plane has under DDL 040. The SQL heartbeat's 5-second
-- transaction-age rule therefore binds only callers of that function; on
-- the live renewal path the operative guards are the validator trigger
-- (lapse guard, extension-lineage proof, trigger-owned provenance) and the
-- post-commit authority readers, which fence any renewal that commits after
-- the expiry it superseded. Before any un-hold, this gap must be
-- dispositioned: either the supervisor's renewal is routed through the SQL
-- heartbeat function (bringing the transaction-age bound onto the live
-- path), or the leg-6 observation window and its 5-second premise are
-- re-founded on the validator-path bounds and that choice founder-ratified.
--
-- DERIVATION (post-C1-closure, re-derived 2026-08-08). The heartbeat, bind,
-- and write-guard bodies derive from the live DDL-025 definitions by the
-- clock substitutions, the lapse-facing horizon rules, and DDL 040's
-- commit-authority reader check (every reader refuses an admission row
-- version whose pg_xact_commit_timestamp(xmin) exceeds the
-- prior_lease_expires_at it superseded, or whose provenance or commit
-- timestamp is unavailable -- fail-closed); bind carries NO transaction-age
-- rule (the earlier draft's undisclosed bind restriction was removed and its
-- removal ratified in the design record -- only the heartbeat keeps the
-- 5-second transaction-age bound). The validator body is the UNION of DDL
-- 039's lease-binding validator (authority-lease immutability, exact insert
-- binding, per-role epoch rules) and DDL 040's repaired shape (renewal-
-- provenance maintenance: trigger-assigned prior_lease_expires_at on insert
-- and extension, writer-supplied values discarded; the extension-lineage
-- check proving the OLD version's own commit-authority before any
-- provenance re-anchor, closing the laundering interleaving). Every
-- derivation is mechanically byte-pinned by enumerated transformation lists
-- in tests/test_tier1_kernel_admission_lease_wallclock.py and exercised
-- empirically in tests/test_tier1_kernel_scribe_wallclock_hold.py on a
-- disposable PostgreSQL 16 cluster with track_commit_timestamp=on. The
-- provenance column itself was added by DDL 040 (shared table); this file
-- adds no table, index, policy, grant, principal, admission, or signature
-- change.
--
-- UN-HOLD PRECONDITIONS (leg 7 of the Company-DB deploy runbook,
-- docs/design/20260807__company-db-deploy-window-runbook.md): (1) the leg-6
-- live Scribe heartbeat lock-wait observation window with p100 comfortably
-- under the 5-second bound; (2) its own founder authorization; (3)
-- track_commit_timestamp=on live before the Scribe's next renewal cycle;
-- (4) the deployer's provenance postcondition widened to the Scribe plane
-- in the same change -- the live Scribe admission row predates the
-- provenance column, so these readers would fence it fail-closed: the live
-- Scribe (epoch 19 today) must be revoked and re-admitted through the
-- normal ceremony inside the un-hold window, exactly as the deployer
-- already refuses to certify provenance-less live NON-scribe admissions
-- (the H3 disposition).
--
-- NOT in DDL_ORDER and NOT in the Company migration allowlist. The file is
-- STATE-inert, not statement-free: its one executable statement is the
-- notice below, kept deliberately so a stray application announces the hold
-- instead of exiting silently -- a zero exit from this file never means
-- "applied" (an all-comment file would exit zero too; the receipt-bearing
-- deployer chain is the only application authority).

DO $held$ BEGIN RAISE NOTICE
    'DDL 041 is HELD: the Scribe-plane lease repair is not applied.';
END $held$;

/* HELD BODY -- REVIEW SUBJECT ONLY. Applying this file must be a
   deliberate act with its own authorization, never a directory glob.
   Un-hold by removing this comment opener and the closer on the final
   line, in the same change that adds 041 to DDL_ORDER.

CREATE OR REPLACE FUNCTION kernel_heartbeat_company_scribe_admission(
    p_admission_id UUID,p_lease_token TEXT,p_lease_expires_at TIMESTAMPTZ
)
RETURNS VOID
LANGUAGE plpgsql
VOLATILE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    admission kernel_company_scribe_admissions%ROWTYPE;
    observed TIMESTAMPTZ;
    version_committed_at TIMESTAMPTZ;
BEGIN
    SELECT *
      INTO admission
      FROM kernel_company_scribe_admissions
     WHERE id=p_admission_id
       AND company_id='__CC_SUITE_COMPANY_ID__'
     FOR UPDATE;
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
                'Company Scribe admission lease lineage is not commit-authoritative';
        END IF;
    END IF;
    IF admission IS NULL
       OR admission.state <> 'live'
       OR admission.lease_expires_at <= observed
       OR observed > transaction_timestamp() + interval '5 seconds'
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_founder_activation_mandates mandate
             WHERE mandate.mandate_id=admission.mandate_id
               AND mandate.artifact_sha256=admission.mandate_artifact_sha256
               AND mandate.status IN ('active','completed')
       )
       OR encode(
            digest(convert_to(p_lease_token,'UTF8'),'sha256'),
            'hex'
          ) <> admission.lease_token_sha256
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_company_worker_principals principal
             WHERE principal.worker_id=admission.worker_id
               AND principal.company_id=admission.company_id
               AND principal.role_type='scribe'
               AND principal.db_role=session_user::NAME
       )
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_worker_sessions worker_session
             WHERE worker_session.id=admission.worker_session_id
               AND worker_session.worker_id=admission.worker_id
               AND worker_session.tenant_id=admission.company_id
               AND worker_session.state='active'
               AND worker_session.lease_expires_at >= p_lease_expires_at
       )
       OR p_lease_expires_at <= admission.lease_expires_at
       OR p_lease_expires_at <= observed
       OR p_lease_expires_at > observed + interval '5 minutes' THEN
        RAISE EXCEPTION 'Company Scribe admission heartbeat is invalid or fenced';
    END IF;
    UPDATE kernel_company_scribe_admissions
       SET heartbeat_at=observed,lease_expires_at=p_lease_expires_at,
           prior_lease_expires_at=admission.lease_expires_at
     WHERE id=admission.id;
END
$function$;

CREATE OR REPLACE FUNCTION kernel_bind_company_scribe_admission(
    p_admission_id UUID,
    p_lease_token TEXT,
    p_deployment_id TEXT,
    p_agent_release_sha256 TEXT,
    p_kernel_release_sha256 TEXT,
    p_package_sha256 TEXT,
    p_task_id TEXT,
    p_claude_session_id TEXT,
    p_process_id BIGINT,
    p_process_start_fingerprint_sha256 TEXT,
    p_repo_commit_sha TEXT,
    p_repo_tree_sha TEXT,
    p_admission_epoch BIGINT,
    p_mandate_id TEXT,
    p_mandate_artifact_sha256 TEXT,
    p_authority_approval_id UUID,
    p_authority_subject_ref TEXT,
    p_authority_subject_sha256 TEXT,
    p_mini_activation_receipt_sha256 TEXT,
    p_host_identity_sha256 TEXT,
    p_host_boot_id_sha256 TEXT,
    p_runtime_executable_path TEXT,
    p_runtime_executable_sha256 TEXT,
    p_runtime_lock_path TEXT,
    p_authority_mode TEXT,
    p_authority_root_admission_id UUID,
    p_prior_admission_id UUID,
    p_recovery_intent_id UUID,
    p_prior_rollback_receipt_sha256 TEXT,
    p_restart_intent_sha256 TEXT
)
RETURNS VOID
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    admission kernel_company_scribe_admissions%ROWTYPE;
    txid BIGINT;
    observed TIMESTAMPTZ;
    version_committed_at TIMESTAMPTZ;
    proof TEXT;
BEGIN
    SELECT *
      INTO admission
      FROM kernel_company_scribe_admissions
     WHERE id=p_admission_id
       AND company_id='__CC_SUITE_COMPANY_ID__'
     FOR SHARE;
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
                'Company Scribe admission lease lineage is not commit-authoritative';
        END IF;
    END IF;
    IF admission IS NULL
       OR admission.state <> 'live'
       OR admission.lease_expires_at <= observed
       OR admission.heartbeat_at > observed + interval '5 seconds'
       OR admission.deployment_id <> p_deployment_id
       OR admission.agent_release_sha256 <> p_agent_release_sha256
       OR admission.kernel_release_sha256 <> p_kernel_release_sha256
       OR admission.package_sha256 <> p_package_sha256
       OR admission.task_id <> p_task_id
       OR admission.claude_session_id <> p_claude_session_id
       OR admission.process_id <> p_process_id
       OR admission.process_start_fingerprint_sha256
          <> p_process_start_fingerprint_sha256
       OR admission.repo_commit_sha <> p_repo_commit_sha
       OR admission.repo_tree_sha <> p_repo_tree_sha
       OR admission.admission_epoch <> p_admission_epoch
       OR admission.mandate_id <> p_mandate_id
       OR admission.mandate_artifact_sha256 <> p_mandate_artifact_sha256
       OR admission.authority_approval_id <> p_authority_approval_id
       OR admission.authority_subject_ref <> p_authority_subject_ref
       OR admission.authority_subject_sha256 <> p_authority_subject_sha256
       OR admission.mini_activation_receipt_sha256
          <> p_mini_activation_receipt_sha256
       OR admission.host_identity_sha256 <> p_host_identity_sha256
       OR admission.host_boot_id_sha256 <> p_host_boot_id_sha256
       OR admission.runtime_executable_path <> p_runtime_executable_path
       OR admission.runtime_executable_sha256 <> p_runtime_executable_sha256
       OR admission.runtime_lock_path <> p_runtime_lock_path
       OR admission.authority_mode <> p_authority_mode
       OR admission.authority_root_admission_id
          <> p_authority_root_admission_id
       OR admission.prior_admission_id
          IS DISTINCT FROM p_prior_admission_id
       OR admission.recovery_intent_id
          IS DISTINCT FROM p_recovery_intent_id
       OR admission.prior_rollback_receipt_sha256
          IS DISTINCT FROM p_prior_rollback_receipt_sha256
       OR admission.restart_intent_sha256
          IS DISTINCT FROM p_restart_intent_sha256
       OR encode(
            digest(convert_to(p_lease_token,'UTF8'),'sha256'),
            'hex'
          ) <> admission.lease_token_sha256
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_company_worker_principals principal
             WHERE principal.worker_id=admission.worker_id
               AND principal.company_id=admission.company_id
               AND principal.role_type='scribe'
               AND principal.db_role=session_user::NAME
       )
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_worker_sessions worker_session
             WHERE worker_session.id=admission.worker_session_id
               AND worker_session.worker_id=admission.worker_id
               AND worker_session.tenant_id=admission.company_id
               AND worker_session.state='active'
               AND worker_session.lease_expires_at > observed
               AND encode(
                    digest(
                        convert_to(worker_session.lease_token,'UTF8'),
                        'sha256'
                    ),
                    'hex'
                   )=admission.lease_token_sha256
       )
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_founder_activation_mandates mandate
             WHERE mandate.mandate_id=admission.mandate_id
               AND mandate.artifact_sha256=admission.mandate_artifact_sha256
               AND mandate.status IN ('active','completed')
       ) THEN
        RAISE EXCEPTION 'Company Scribe admission binding is invalid or fenced';
    END IF;
    txid := txid_current();
    proof := kernel_company_scribe_context_proof(
        admission.id,admission.admission_epoch,session_user::NAME,txid
    );
    PERFORM set_config('mise.scribe_admission_id',admission.id::TEXT,true);
    PERFORM set_config(
        'mise.scribe_admission_epoch',admission.admission_epoch::TEXT,true
    );
    PERFORM set_config('mise.scribe_admission_proof',proof,true);
END
$function$;

CREATE OR REPLACE FUNCTION kernel_require_company_scribe_admission()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    admission kernel_company_scribe_admissions%ROWTYPE;
    bound_id UUID;
    bound_epoch BIGINT;
    supplied_proof TEXT;
    observed TIMESTAMPTZ;
    version_committed_at TIMESTAMPTZ;
    expected_proof TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM kernel_company_worker_principals principal
         WHERE principal.company_id='__CC_SUITE_COMPANY_ID__'
           AND principal.role_type='scribe'
           AND principal.db_role=session_user::NAME
    ) THEN
        RETURN NEW;
    END IF;
    BEGIN
        bound_id := current_setting('mise.scribe_admission_id',true)::UUID;
        bound_epoch := current_setting(
            'mise.scribe_admission_epoch',true
        )::BIGINT;
        supplied_proof := current_setting(
            'mise.scribe_admission_proof',true
        );
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'Company Scribe write requires an exact transaction admission';
    END;
    SELECT *
      INTO admission
      FROM kernel_company_scribe_admissions
     WHERE id=bound_id
       AND company_id='__CC_SUITE_COMPANY_ID__'
     FOR SHARE;
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
                'Company Scribe admission lease lineage is not commit-authoritative';
        END IF;
    END IF;
    expected_proof := kernel_company_scribe_context_proof(
        bound_id,bound_epoch,session_user::NAME,txid_current()
    );
    IF admission IS NULL
       OR admission.state <> 'live'
       OR admission.lease_expires_at <= observed
       OR admission.admission_epoch <> bound_epoch
       OR admission.process_id <= 1
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_founder_activation_mandates mandate
             WHERE mandate.mandate_id=admission.mandate_id
               AND mandate.artifact_sha256=admission.mandate_artifact_sha256
               AND mandate.status IN ('active','completed')
       )
       OR supplied_proof IS NULL
       OR NOT (
            supplied_proof::BYTEA
            = expected_proof::BYTEA
       )
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_worker_sessions worker_session
             WHERE worker_session.id=admission.worker_session_id
               AND worker_session.worker_id=admission.worker_id
               AND worker_session.tenant_id=admission.company_id
               AND worker_session.state='active'
               AND worker_session.lease_expires_at > observed
       ) THEN
        RAISE EXCEPTION 'Company Scribe write admission is missing, stale, or revoked';
    END IF;
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION kernel_validate_company_scribe_admission()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    worker RECORD;
    worker_session RECORD;
    package RECORD;
    mandate kernel_founder_activation_mandates%ROWTYPE;
    final_receipt kernel_founder_activation_step_receipts%ROWTYPE;
    root_admission kernel_company_scribe_admissions%ROWTYPE;
    prior_admission kernel_company_scribe_admissions%ROWTYPE;
    prior_session kernel_worker_sessions%ROWTYPE;
    recovery_intent kernel_company_scribe_recovery_intents%ROWTYPE;
    previous_epoch BIGINT;
    root_exact BOOLEAN;
    prior_exact BOOLEAN;
    recovery_intent_exact BOOLEAN;
    terminal_recovery_exact BOOLEAN;
    old_version_committed_at TIMESTAMPTZ;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.lease_expires_at > OLD.lease_expires_at THEN
            BEGIN
                SELECT pg_xact_commit_timestamp(current_version.xmin)
                  INTO old_version_committed_at
                  FROM kernel_company_scribe_admissions current_version
                 WHERE current_version.id=OLD.id;
            EXCEPTION WHEN OTHERS THEN
                old_version_committed_at := NULL;
            END;
            IF OLD.prior_lease_expires_at IS NULL
               OR old_version_committed_at IS NULL
               OR old_version_committed_at > OLD.prior_lease_expires_at THEN
                RAISE EXCEPTION
                    'Company Scribe admission extension lineage is not commit-authoritative';
            END IF;
            NEW.prior_lease_expires_at := OLD.lease_expires_at;
        ELSE
            NEW.prior_lease_expires_at := OLD.prior_lease_expires_at;
        END IF;
        IF (
            NEW.id,NEW.company_id,NEW.deployment_id,NEW.worker_id,
            NEW.worker_session_id,NEW.package_id,NEW.agent_release_sha256,
            NEW.kernel_release_sha256,NEW.package_sha256,NEW.runtime_kind,
            NEW.task_id,NEW.claude_session_id,NEW.process_id,
            NEW.process_start_fingerprint_sha256,NEW.repo_commit_sha,
            NEW.repo_tree_sha,NEW.lease_token_sha256,NEW.admission_epoch,
            NEW.mandate_id,NEW.mandate_artifact_sha256,
            NEW.authority_approval_id,NEW.authority_subject_ref,
            NEW.authority_subject_sha256,NEW.mini_activation_receipt_sha256,
            NEW.host_identity_sha256,NEW.host_boot_id_sha256,
            NEW.runtime_executable_path,NEW.runtime_executable_sha256,
            NEW.runtime_lock_path,NEW.authority_mode,
            NEW.authority_root_admission_id,NEW.prior_admission_id,
            NEW.recovery_intent_id,NEW.prior_rollback_receipt_sha256,
            NEW.restart_intent_sha256,
            NEW.authority_lease_expires_at,
            NEW.admitted_at
        ) IS DISTINCT FROM (
            OLD.id,OLD.company_id,OLD.deployment_id,OLD.worker_id,
            OLD.worker_session_id,OLD.package_id,OLD.agent_release_sha256,
            OLD.kernel_release_sha256,OLD.package_sha256,OLD.runtime_kind,
            OLD.task_id,OLD.claude_session_id,OLD.process_id,
            OLD.process_start_fingerprint_sha256,OLD.repo_commit_sha,
            OLD.repo_tree_sha,OLD.lease_token_sha256,OLD.admission_epoch,
            OLD.mandate_id,OLD.mandate_artifact_sha256,
            OLD.authority_approval_id,OLD.authority_subject_ref,
            OLD.authority_subject_sha256,OLD.mini_activation_receipt_sha256,
            OLD.host_identity_sha256,OLD.host_boot_id_sha256,
            OLD.runtime_executable_path,OLD.runtime_executable_sha256,
            OLD.runtime_lock_path,OLD.authority_mode,
            OLD.authority_root_admission_id,OLD.prior_admission_id,
            OLD.recovery_intent_id,OLD.prior_rollback_receipt_sha256,
            OLD.restart_intent_sha256,
            OLD.authority_lease_expires_at,
            OLD.admitted_at
        ) THEN
            RAISE EXCEPTION 'Company Scribe admission identity is immutable';
        END IF;
        IF OLD.state <> 'live' AND NEW.state <> OLD.state THEN
            RAISE EXCEPTION 'closed Company Scribe admission is terminal';
        END IF;
        IF NEW.heartbeat_at < OLD.heartbeat_at
           OR NEW.lease_expires_at < OLD.lease_expires_at THEN
            RAISE EXCEPTION 'Company Scribe admission clocks cannot move backward';
        END IF;
        IF OLD.state = 'live' AND NEW.state = 'live'
           AND NEW.lease_expires_at > OLD.lease_expires_at
           AND OLD.lease_expires_at <= clock_timestamp() THEN
            RAISE EXCEPTION
                'Company Scribe admission lease cannot be extended after it lapsed';
        END IF;
        IF OLD.state = 'live' AND NEW.state <> 'live' THEN
            IF NEW.heartbeat_at IS DISTINCT FROM OLD.heartbeat_at
               OR NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at THEN
                RAISE EXCEPTION
                    'Company Scribe admission closure cannot rewrite its clocks';
            END IF;
            IF NEW.state='expired'
               AND OLD.lease_expires_at > clock_timestamp() THEN
                RAISE EXCEPTION
                    'live Company Scribe admission cannot be marked expired early';
            END IF;
            NEW.closed_at := now();
            -- Closing is the recovery path when a process/session/package or
            -- mandate dependency is stale. Requiring those stale dependencies
            -- to remain live here would strand the partial-unique singleton
            -- row forever and make a safe epoch restart impossible.
            RETURN NEW;
        ELSIF NEW.state = 'live' THEN
            NEW.closed_at := NULL;
        END IF;
    ELSIF NEW.state <> 'live' OR NEW.closed_at IS NOT NULL THEN
        RAISE EXCEPTION 'new Company Scribe admission must start live';
    END IF;
    IF NEW.state='live' AND (
        NEW.heartbeat_at > clock_timestamp() + interval '5 seconds'
        OR NEW.lease_expires_at <= NEW.heartbeat_at
        OR NEW.lease_expires_at > NEW.heartbeat_at + interval '5 minutes'
    ) THEN
        RAISE EXCEPTION 'Company Scribe admission lease exceeds the bounded horizon';
    END IF;

    IF TG_OP = 'INSERT' THEN
        NEW.prior_lease_expires_at := NEW.lease_expires_at;
        PERFORM pg_advisory_xact_lock(
            hashtextextended('__CC_SUITE_COMPANY_ID__:scribe-admission',0)
        );
        -- DDL 031 generalized this table to every Company role with
        -- per-role epochs (the generic validator already scopes by
        -- role_type); the Scribe epoch check is scoped the same way so a
        -- non-Scribe role's epoch lead can never refuse a legitimate
        -- Scribe restart.
        SELECT max(admission_epoch)
          INTO previous_epoch
          FROM kernel_company_scribe_admissions
         WHERE company_id=NEW.company_id
           AND role_type=NEW.role_type;
        IF NEW.admission_epoch <> COALESCE(previous_epoch,0) + 1 THEN
            RAISE EXCEPTION 'Company Scribe admission epoch must be exactly monotonic';
        END IF;
        IF NEW.authority_lease_expires_at IS DISTINCT FROM NEW.lease_expires_at THEN
            RAISE EXCEPTION
                'Company Scribe admission must persist its exact minted authority lease';
        END IF;
    END IF;

    SELECT worker_row.*,manifest.status AS manifest_status
      INTO worker
      FROM kernel_runtime_workers worker_row
      JOIN kernel_tenant_fleet_manifests manifest
        ON manifest.id=worker_row.manifest_id
       AND manifest.tenant_id=worker_row.tenant_id
     WHERE worker_row.id=NEW.worker_id
       AND worker_row.tenant_id=NEW.company_id
     FOR SHARE OF worker_row,manifest;
    IF worker.id IS NULL
       OR worker.fleet_type <> 'company'
       OR worker.role_type <> 'scribe'
       OR worker.status <> 'active'
       OR worker.manifest_status <> 'active'
       OR worker.agent_release_sha256 <> NEW.agent_release_sha256 THEN
        RAISE EXCEPTION 'Company Scribe admission requires the active Scribe worker';
    END IF;

    SELECT *
      INTO worker_session
      FROM kernel_worker_sessions
     WHERE id=NEW.worker_session_id
       AND tenant_id=NEW.company_id
       AND worker_id=NEW.worker_id
     FOR SHARE;
    IF worker_session.id IS NULL
       OR worker_session.role_type <> 'scribe'
       OR worker_session.agent_release_sha256 <> NEW.agent_release_sha256
       OR worker_session.state <> 'active'
       OR worker_session.lease_expires_at <= clock_timestamp()
       OR encode(
            digest(convert_to(worker_session.lease_token,'UTF8'),'sha256'),
            'hex'
          ) <> NEW.lease_token_sha256
       OR NEW.lease_expires_at > worker_session.lease_expires_at THEN
        RAISE EXCEPTION 'Company Scribe admission requires the exact live worker session lease';
    END IF;

    SELECT *
      INTO package
      FROM kernel_company_scribe_packages
     WHERE id=NEW.package_id
       AND company_id=NEW.company_id
     FOR SHARE;
    IF package.id IS NULL
       OR package.state <> 'installed'
       OR package.deployment_id <> NEW.deployment_id
       OR package.kernel_release_sha256 <> NEW.kernel_release_sha256
       OR package.package_sha256 <> NEW.package_sha256
       OR package.source_commit_sha <> NEW.repo_commit_sha
       OR package.source_tree_sha <> NEW.repo_tree_sha THEN
        RAISE EXCEPTION 'Company Scribe admission requires the exact installed package and tree';
    END IF;

    SELECT *
      INTO mandate
      FROM kernel_founder_activation_mandates
     WHERE mandate_id=NEW.mandate_id
     FOR SHARE;
    IF mandate.mandate_id IS NULL
       OR mandate.status NOT IN ('active','completed')
       OR mandate.artifact_sha256<>NEW.mandate_artifact_sha256
       OR mandate.authority_verification_mac IS DISTINCT FROM
          kernel_founder_mandate_expected_mac(
              mandate.mandate_id,mandate.artifact_sha256,
              mandate.artifact_bytes,mandate.artifact,mandate.issued_at
          ) THEN
        RAISE EXCEPTION
            'Company Scribe admission lacks the exact active mandate';
    END IF;

    IF NEW.authority_mode='initial-local-activation' THEN
        IF TG_OP='INSERT' AND mandate.status<>'active' THEN
            RAISE EXCEPTION
                'initial Company Scribe admission requires the active mandate';
        END IF;
        IF NEW.authority_approval_id<>NEW.id
           OR NEW.mini_activation_receipt_sha256<>repeat('0',64) THEN
            RAISE EXCEPTION
                'initial Company Scribe admission identity is not local-only';
        END IF;
    ELSE
        SELECT *
          INTO root_admission
          FROM kernel_company_scribe_admissions
         WHERE id=NEW.authority_root_admission_id
           AND company_id=NEW.company_id
         FOR SHARE;
        SELECT *
          INTO prior_admission
          FROM kernel_company_scribe_admissions
         WHERE id=NEW.prior_admission_id
           AND company_id=NEW.company_id
         FOR SHARE;
        IF prior_admission.id IS NOT NULL THEN
            SELECT *
              INTO prior_session
              FROM kernel_worker_sessions
             WHERE id=prior_admission.worker_session_id
               AND tenant_id=NEW.company_id
             FOR SHARE;
        END IF;
        SELECT *
          INTO recovery_intent
          FROM kernel_company_scribe_recovery_intents
         WHERE id=NEW.recovery_intent_id
           AND company_id=NEW.company_id
         FOR UPDATE;
        IF mandate.status='completed' THEN
            SELECT *
              INTO final_receipt
              FROM kernel_founder_activation_step_receipts
             WHERE mandate_id=NEW.mandate_id
               AND activation_step='scribe-verification'
             FOR SHARE;
        END IF;
        root_exact := root_admission.id IS NOT NULL
            AND root_admission.authority_mode='initial-local-activation'
            AND root_admission.authority_root_admission_id=root_admission.id
            AND root_admission.mandate_id=NEW.mandate_id
            AND root_admission.mandate_artifact_sha256
                =NEW.mandate_artifact_sha256
            AND root_admission.worker_id=NEW.worker_id
            AND root_admission.package_id=NEW.package_id
            AND root_admission.agent_release_sha256=NEW.agent_release_sha256
            AND root_admission.kernel_release_sha256
                =NEW.kernel_release_sha256
            AND root_admission.package_sha256=NEW.package_sha256
            AND root_admission.repo_commit_sha=NEW.repo_commit_sha
            AND root_admission.repo_tree_sha=NEW.repo_tree_sha
            AND root_admission.host_identity_sha256=NEW.host_identity_sha256
            AND root_admission.mini_activation_receipt_sha256
                =NEW.mini_activation_receipt_sha256
            AND root_admission.runtime_executable_path
                =NEW.runtime_executable_path
            AND root_admission.runtime_executable_sha256
                =NEW.runtime_executable_sha256
            AND root_admission.runtime_lock_path=NEW.runtime_lock_path
            AND root_admission.authority_approval_id
                =NEW.authority_approval_id;
        prior_exact := prior_admission.id IS NOT NULL
            AND prior_admission.state='revoked'
            AND prior_admission.authority_root_admission_id
                =NEW.authority_root_admission_id
            AND prior_admission.worker_id=NEW.worker_id
            AND prior_admission.package_id=NEW.package_id
            AND prior_admission.agent_release_sha256=NEW.agent_release_sha256
            AND prior_admission.kernel_release_sha256
                =NEW.kernel_release_sha256
            AND prior_admission.package_sha256=NEW.package_sha256
            AND prior_admission.repo_commit_sha=NEW.repo_commit_sha
            AND prior_admission.repo_tree_sha=NEW.repo_tree_sha
            AND prior_admission.host_identity_sha256=NEW.host_identity_sha256
            AND prior_admission.mini_activation_receipt_sha256
                =NEW.mini_activation_receipt_sha256
            AND prior_admission.runtime_executable_path
                =NEW.runtime_executable_path
            AND prior_admission.runtime_executable_sha256
                =NEW.runtime_executable_sha256
            AND prior_admission.runtime_lock_path=NEW.runtime_lock_path
            AND prior_admission.admission_epoch+1=NEW.admission_epoch
            AND prior_session.id IS NOT NULL
            AND prior_session.state='ended';
        recovery_intent_exact := recovery_intent.id IS NOT NULL
            AND (
                (TG_OP='INSERT' AND recovery_intent.state='prepared')
                OR (TG_OP='UPDATE' AND recovery_intent.state='consumed')
            )
            AND (
                TG_OP='UPDATE' OR (
                recovery_intent.mandate_id,
                recovery_intent.mandate_artifact_sha256,
                recovery_intent.authority_root_admission_id,
                recovery_intent.prior_admission_id,
                recovery_intent.next_admission_epoch,
                recovery_intent.worker_id,recovery_intent.worker_session_id,
                recovery_intent.package_id,
                recovery_intent.agent_release_sha256,
                recovery_intent.kernel_release_sha256,
                recovery_intent.package_sha256,
                recovery_intent.repo_commit_sha,
                recovery_intent.repo_tree_sha,
                recovery_intent.host_identity_sha256,
                recovery_intent.mini_activation_receipt_sha256,
                recovery_intent.prior_rollback_receipt_sha256,
                recovery_intent.restart_intent_sha256,
                recovery_intent.admission_id,
                recovery_intent.authority_approval_id,
                recovery_intent.authority_subject_ref,
                recovery_intent.authority_subject_sha256,
                recovery_intent.task_id,
                recovery_intent.claude_session_id,
                recovery_intent.process_id,
                recovery_intent.process_start_fingerprint_sha256,
                recovery_intent.host_boot_id_sha256,
                recovery_intent.runtime_executable_path,
                recovery_intent.runtime_executable_sha256,
                recovery_intent.runtime_lock_path,
                recovery_intent.lease_token_sha256,
                recovery_intent.lease_expires_at
              ) IS NOT DISTINCT FROM (
                NEW.mandate_id,NEW.mandate_artifact_sha256,
                NEW.authority_root_admission_id,NEW.prior_admission_id,
                NEW.admission_epoch,NEW.worker_id,NEW.worker_session_id,
                NEW.package_id,NEW.agent_release_sha256,
                NEW.kernel_release_sha256,NEW.package_sha256,
                NEW.repo_commit_sha,NEW.repo_tree_sha,
                NEW.host_identity_sha256,
                NEW.mini_activation_receipt_sha256,
                NEW.prior_rollback_receipt_sha256,
                NEW.restart_intent_sha256,NEW.id,
                NEW.authority_approval_id,NEW.authority_subject_ref,
                NEW.authority_subject_sha256,NEW.task_id,
                NEW.claude_session_id,NEW.process_id,
                NEW.process_start_fingerprint_sha256,
                NEW.host_boot_id_sha256,NEW.runtime_executable_path,
                NEW.runtime_executable_sha256,NEW.runtime_lock_path,
                NEW.lease_token_sha256,NEW.lease_expires_at
              )
            );
        terminal_recovery_exact := mandate.status='active' OR (
            mandate.status='completed'
            AND final_receipt.mandate_id IS NOT NULL
            AND final_receipt.receipt_payload->>'status'='completed'
            AND final_receipt.receipt_payload->>'singleton'='verified'
            AND final_receipt.receipt_payload->>'persistence'='verified'
            AND final_receipt.receipt_payload->>'restart'='passed'
            AND final_receipt.receipt_payload->>'receipts'='verified'
        );
        IF root_exact IS NOT TRUE
           OR prior_exact IS NOT TRUE
           OR recovery_intent_exact IS NOT TRUE
           OR terminal_recovery_exact IS NOT TRUE THEN
            RAISE EXCEPTION
                'Company Scribe recovery lacks its exact standing authority chain'
                USING DETAIL=format(
                    'root=%s prior=%s intent=%s terminal=%s',
                    root_exact,prior_exact,
                    recovery_intent_exact,terminal_recovery_exact
                );
        END IF;
    END IF;
    RETURN NEW;
END
$function$;
*/
