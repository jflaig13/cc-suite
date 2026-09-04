CREATE OR REPLACE FUNCTION kernel_validate_company_role_admission()
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
       AND state=CASE WHEN TG_OP='INSERT' THEN 'approved' ELSE 'consumed' END
       FOR UPDATE;
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
