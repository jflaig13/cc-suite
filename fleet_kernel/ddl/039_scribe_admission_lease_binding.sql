-- Persist the minted authority lease on every Company admission row.
--
-- DEFECT CLOSED.  The Scribe activation subject is hashed over the ORIGINAL
-- minted lease_expires_at (fleet_kernel/company_scribe_admission.py::
-- scribe_activation_subject), but the admission row's lease_expires_at is
-- MUTABLE: renew_live_admission moves it forward on every heartbeat renewal.
-- The supervisor's database replay queries reconstructed an initial-mode
-- binding from admission.lease_expires_at, so any initial-mode admission that
-- was renewed and later revoked failed exact subject re-verification during
-- crash replay.  Recovery-mode admissions were immune only because their
-- recovery intent persists the minted lease.  This forward-only migration
-- persists the minted lease for BOTH authority modes in the admission row
-- itself, immutable after insert, and both supervisor replay queries read it
-- for all modes.
--
-- BACKFILL EXACTNESS.  Recovery-mode rows backfill from their recovery
-- intent's persisted minted lease.  Initial-mode rows backfill from the
-- approved subject payload that recorded the minted lease at admission time:
-- Scribe rows from m5_approval_decisions.authority_subject_payload
-- (activation_binding.lease_expires_at), generic Company-role rows from
-- kernel_company_scribe_admission-generalized
-- kernel_company_role_activation_subjects.subject_payload (lease_expires_at)
-- -- both exact even for renewed rows.  Only a row with none of those
-- records (no such row class is known) falls back to its current
-- lease_expires_at, which is exact only if it was never renewed; such a
-- row's replay behavior is unchanged (fails closed exactly as before).
--
-- The row guards revalidate live worker/session/package/mandate dependencies
-- on EVERY update, which correctly refuses writes to historical revoked rows.
-- The backfill below writes only the new column with values derived from the
-- rows' own recorded state, inside this single migration transaction, so the
-- guards are disabled for the backfill statement alone and re-enabled before
-- the transaction continues.

ALTER TABLE kernel_company_scribe_admissions
    ADD COLUMN authority_lease_expires_at TIMESTAMPTZ;

ALTER TABLE kernel_company_scribe_admissions
    DISABLE TRIGGER kernel_company_scribe_admission_guard;
ALTER TABLE kernel_company_scribe_admissions
    DISABLE TRIGGER kernel_company_role_admission_guard;

UPDATE kernel_company_scribe_admissions admission
   SET authority_lease_expires_at=COALESCE(
        (SELECT recovery.lease_expires_at
           FROM kernel_company_scribe_recovery_intents recovery
          WHERE recovery.id=admission.recovery_intent_id),
        (SELECT (decision.authority_subject_payload
                   ->'activation_binding'->>'lease_expires_at')::TIMESTAMPTZ
           FROM m5_approval_decisions decision
          WHERE decision.tenant_id=admission.company_id
            AND decision.approval_id=admission.authority_approval_id),
        (SELECT (subject.subject_payload->>'lease_expires_at')::TIMESTAMPTZ
           FROM kernel_company_role_activation_subjects subject
          WHERE subject.company_id=admission.company_id
            AND subject.approval_id=admission.authority_approval_id),
        admission.lease_expires_at
   );

ALTER TABLE kernel_company_scribe_admissions
    ENABLE TRIGGER kernel_company_scribe_admission_guard;
ALTER TABLE kernel_company_scribe_admissions
    ENABLE TRIGGER kernel_company_role_admission_guard;

ALTER TABLE kernel_company_scribe_admissions
    ALTER COLUMN authority_lease_expires_at SET NOT NULL;
ALTER TABLE kernel_company_scribe_admissions
    ADD CONSTRAINT kernel_company_scribe_admissions_authority_lease_check CHECK (
        authority_lease_expires_at <= lease_expires_at
    );

-- The Scribe-row validator pins immutable identity as an ENUMERATED tuple
-- (unlike the generic role validator's to_jsonb subtraction, which makes the
-- new column immutable automatically).  Replace it to (1) add
-- authority_lease_expires_at to the immutable identity tuple, (2) require
-- the persisted value to equal the minted lease at insert time, and (3)
-- scope the insert epoch-monotonicity check to the Scribe role, matching the
-- per-role epoch semantics the generic validator has carried since DDL 031.
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
BEGIN
    IF TG_OP = 'UPDATE' THEN
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
        IF OLD.state = 'live' AND NEW.state <> 'live' THEN
            IF NEW.heartbeat_at IS DISTINCT FROM OLD.heartbeat_at
               OR NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at THEN
                RAISE EXCEPTION
                    'Company Scribe admission closure cannot rewrite its clocks';
            END IF;
            IF NEW.state='expired' AND OLD.lease_expires_at > now() THEN
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
        NEW.heartbeat_at > now() + interval '5 seconds'
        OR NEW.lease_expires_at <= NEW.heartbeat_at
        OR NEW.lease_expires_at > NEW.heartbeat_at + interval '5 minutes'
    ) THEN
        RAISE EXCEPTION 'Company Scribe admission lease exceeds the bounded horizon';
    END IF;

    IF TG_OP = 'INSERT' THEN
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
       OR worker_session.lease_expires_at <= now()
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

