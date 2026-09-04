-- Forward-only reconciliation for the 20260731 founder-activation decision
-- validator. DDL 024 remains the immutable historical migration; this
-- replacement preserves its legacy 24-key Mini-first Scribe branch and adds
-- the executed Python Scribe-first contract without rewriting stored rows.

CREATE OR REPLACE FUNCTION kernel_validate_m5_mandated_activation()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    mandate kernel_founder_activation_mandates%ROWTYPE;
    expected_mac BYTEA;
    expected_ref TEXT;
    expected_role TEXT;
    expected_pre JSONB;
    expected_post JSONB;
    expected_binding_count INTEGER;
    prior_step TEXT;
    current_scribe_contract BOOLEAN;
BEGIN
    IF NEW.mandate_id IS NULL THEN
        IF (
            NEW.mandate_artifact_sha256,NEW.activation_step,
            NEW.activation_step_key,NEW.authority_subject_ref,
            NEW.authority_subject_sha256,NEW.authority_subject_payload,
            NEW.authority_verified_by,NEW.authority_verification_mac
        ) IS DISTINCT FROM (NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL) THEN
            RAISE EXCEPTION 'legacy M5 authority cannot claim a partial mandate';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO mandate
      FROM kernel_founder_activation_mandates
     WHERE mandate_id=NEW.mandate_id
     FOR SHARE;
    current_scribe_contract:=NEW.activation_step='scribe-admission'
        AND NEW.authority_subject_payload#>>'{activation_binding,authority_mode}'
            IN ('initial-local-activation','standing-mandate-recovery');
    expected_ref:=format(
        'founder://activation-step/%s/%s/%s',
        NEW.mandate_id,NEW.activation_step,NEW.authority_subject_sha256
    );
    expected_mac:=kernel_mandated_step_expected_mac(
        NEW.tenant_id,NEW.approval_id,NEW.mandate_id,
        NEW.mandate_artifact_sha256,NEW.activation_step,
        NEW.activation_step_key,NEW.authority_subject_ref,
        NEW.authority_subject_sha256,NEW.authority_subject_payload
    );
    expected_role:=CASE NEW.activation_step
        WHEN 'supervised-live-upload' THEN 'browser_navigator'
        WHEN 'scribe-admission' THEN 'scribe'
        WHEN 'scribe-verification' THEN 'scribe'
        ELSE 'ccto'
    END;
    expected_pre:=CASE NEW.activation_step
        WHEN 'final-release-install' THEN
            '{"release_state":"reviewed-held","effect_state":"not-started"}'::JSONB
        WHEN 'privilege-cleanup' THEN
            '{"prior_step":"final-release-install","nopasswd_rule":"present"}'::JSONB
        WHEN 'shadow-monday-1' THEN
            '{"prior_step":"privilege-cleanup","effect_state":"fenced"}'::JSONB
        WHEN 'shadow-monday-2' THEN
            '{"prior_step":"shadow-monday-1","effect_state":"fenced"}'::JSONB
        WHEN 'shadow-monday-3' THEN
            '{"prior_step":"shadow-monday-2","effect_state":"fenced"}'::JSONB
        WHEN 'supervised-live-upload' THEN
            '{"prior_step":"shadow-monday-3","upload_effect":"not-started","submit_effect":"fenced"}'::JSONB
        WHEN 'mini-cutover' THEN
            '{"prior_step":"supervised-live-upload","restaurant_fleet_authority":"legacy-runtime","mini_kernel_authority":"installed-held"}'::JSONB
        WHEN 'mini-verification' THEN
            '{"prior_step":"mini-cutover","restaurant_fleet_authority":"mini-kernel","verification_state":"unverified"}'::JSONB
        WHEN 'scribe-admission' THEN
            CASE WHEN current_scribe_contract THEN
                '{"company_fleet":"active","scribe_authority":"absent"}'::JSONB
            ELSE
                '{"prior_step":"mini-verification","kernel_authority":"mini","scribe_authority":"absent"}'::JSONB
            END
        WHEN 'scribe-verification' THEN
            '{"prior_step":"scribe-admission","scribe_authority":"live","verification_state":"unverified"}'::JSONB
    END;
    expected_post:=CASE NEW.activation_step
        WHEN 'final-release-install' THEN
            '{"release_state":"installed-held","effect_state":"completed"}'::JSONB
        WHEN 'privilege-cleanup' THEN
            '{"step_state":"completed","nopasswd_rule":"absent"}'::JSONB
        WHEN 'shadow-monday-1' THEN
            '{"step_state":"completed","external_effect":"none"}'::JSONB
        WHEN 'shadow-monday-2' THEN
            '{"step_state":"completed","external_effect":"none"}'::JSONB
        WHEN 'shadow-monday-3' THEN
            '{"step_state":"completed","external_effect":"none"}'::JSONB
        WHEN 'supervised-live-upload' THEN
            '{"step_state":"completed-never-submit","external_effect":"upload-completed","submission_effect":"none"}'::JSONB
        WHEN 'mini-cutover' THEN
            '{"restaurant_fleet_authority":"mini-kernel","legacy_runtime":"disabled"}'::JSONB
        WHEN 'mini-verification' THEN
            '{"restaurant_fleet_authority":"mini-kernel","canary":"passed","real_workflow":"reconciled","reconciliation":"passed","restart":"passed","rollback":"passed","recutover":"passed","final_route":"kernel"}'::JSONB
        WHEN 'scribe-admission' THEN
            CASE WHEN current_scribe_contract THEN
                '{"company_fleet":"active","scribe_authority":"live","deployment_id":"__CC_SUITE_SCRIBE_DEPLOYMENT_ID__"}'::JSONB
            ELSE
                '{"kernel_authority":"mini","scribe_authority":"live","deployment_id":"__CC_SUITE_SCRIBE_DEPLOYMENT_ID__"}'::JSONB
            END
        WHEN 'scribe-verification' THEN
            '{"scribe_authority":"live","singleton":"verified","persistence":"verified","restart":"passed","receipts":"verified"}'::JSONB
    END;
    expected_binding_count:=CASE NEW.activation_step
        WHEN 'final-release-install' THEN 7
        WHEN 'privilege-cleanup' THEN 7
        WHEN 'shadow-monday-1' THEN 9
        WHEN 'shadow-monday-2' THEN 9
        WHEN 'shadow-monday-3' THEN 9
        WHEN 'supervised-live-upload' THEN 13
        WHEN 'mini-cutover' THEN 9
        WHEN 'mini-verification' THEN 11
        WHEN 'scribe-admission' THEN
            CASE WHEN current_scribe_contract THEN 23 ELSE 24 END
        WHEN 'scribe-verification' THEN 13
    END;
    IF mandate IS NULL OR mandate.status<>'active'
       OR mandate.artifact_sha256<>NEW.mandate_artifact_sha256
       OR NEW.decision<>'approved' OR NEW.decided_by<>'founder'
       OR NEW.authority_kind<>'founder_direct'
       OR NEW.authority_ref
          <>format('founder://activation-mandate/%s',NEW.mandate_id)
       OR NEW.authority_subject_ref<>expected_ref
       OR NEW.authority_subject_payload->>'schema'
          <>'mise.founder-activation-step-subject.v1'
       OR NEW.authority_subject_payload->>'mandate_id'<>NEW.mandate_id
       OR NEW.authority_subject_payload->>'step'<>NEW.activation_step
       OR NEW.authority_subject_payload->>'attempt_key'<>NEW.activation_step_key
       OR NEW.authority_subject_payload->>'tenant_id'<>NEW.tenant_id
       OR (
            SELECT count(*)
          FROM jsonb_object_keys(NEW.authority_subject_payload)
          )<>11
       OR (
            SELECT count(*)
              FROM jsonb_object_keys(NEW.authority_subject_payload->'release')
          )<>3
       OR (
            SELECT count(*)
              FROM jsonb_object_keys(NEW.authority_subject_payload->'package')
          )<>2
       OR (
            SELECT count(*)
              FROM jsonb_object_keys(
                  NEW.authority_subject_payload->'activation_binding'
              )
          )<>expected_binding_count
       OR NEW.authority_subject_payload->'pre_state' IS DISTINCT FROM expected_pre
       OR NEW.authority_subject_payload->'post_state' IS DISTINCT FROM expected_post
       OR NEW.authority_subject_payload#>>'{release,commit_sha}'
          !~ '^[0-9a-f]{40}$'
       OR NEW.authority_subject_payload#>>'{release,tree_sha}'
          !~ '^[0-9a-f]{40}$'
       OR NEW.authority_subject_payload#>>'{release,digest_sha256}'
          !~ '^[0-9a-f]{64}$'
       OR NEW.authority_subject_payload#>>'{package,sha256}'
          !~ '^[0-9a-f]{64}$'
       OR NEW.authority_subject_payload#>>'{activation_binding,approval_id}'
          <>NEW.approval_id::TEXT
       OR NEW.authority_subject_payload->>'role'<>expected_role
       OR NEW.authority_verification_mac IS DISTINCT FROM expected_mac THEN
        RAISE EXCEPTION
            'M5 activation decision lacks the exact bounded founder mandate';
    END IF;
    IF NEW.activation_step LIKE 'shadow-monday-%' AND (
        NEW.authority_subject_payload#>>'{activation_binding,fence_mode}'
            <>'shadow-no-effect'
        OR extract(
            isodow FROM (
                NEW.authority_subject_payload#>>'{activation_binding,service_date}'
            )::DATE
        )<>1
    ) THEN
        RAISE EXCEPTION 'shadow mandate step must be a real fenced Monday';
    END IF;
    IF NEW.activation_step='supervised-live-upload'
       AND (
            NEW.authority_subject_payload#>>'{activation_binding,mode}'
                <>'never-submit'
            OR NEW.authority_subject_payload#>>
               '{activation_binding,operator_approval_id}' IS NULL
            OR NEW.authority_subject_payload#>>
               '{activation_binding,operator_approval_id}'
               =NEW.approval_id::TEXT
            OR NEW.authority_subject_payload->'pre_state' IS DISTINCT FROM
               '{
                 "prior_step":"shadow-monday-3",
                 "upload_effect":"not-started",
                 "submit_effect":"fenced"
                }'::JSONB
            OR NEW.authority_subject_payload->'post_state' IS DISTINCT FROM
               '{
                 "step_state":"completed-never-submit",
                 "external_effect":"upload-completed",
                 "submission_effect":"none"
                }'::JSONB
       ) THEN
        RAISE EXCEPTION 'supervised live mandate step is never-submit only';
    END IF;
    IF NEW.activation_step='mini-cutover' AND (
        NEW.authority_subject_payload->'pre_state' IS DISTINCT FROM
          '{
            "prior_step":"supervised-live-upload",
            "restaurant_fleet_authority":"legacy-runtime",
            "mini_kernel_authority":"installed-held"
           }'::JSONB
        OR NEW.authority_subject_payload->'post_state' IS DISTINCT FROM
          '{
            "restaurant_fleet_authority":"mini-kernel",
            "legacy_runtime":"disabled"
           }'::JSONB
    ) THEN
        RAISE EXCEPTION 'Mini cutover authority transition is non-canonical';
    END IF;
    IF NEW.activation_step='scribe-admission' AND (
        NEW.authority_subject_payload#>>'{activation_binding,authority_mode}'
            NOT IN (
                'initial-m5-decision','initial-local-activation',
                'standing-mandate-recovery'
            )
        OR NEW.authority_subject_payload#>>'{activation_binding,runtime_lock_path}'
            NOT LIKE '/%'
        OR NEW.authority_subject_payload#>>'{activation_binding,runtime_executable_path}'
            NOT LIKE '/%'
        OR (
            current_scribe_contract
            AND ARRAY(
                SELECT key
                  FROM jsonb_object_keys(
                      NEW.authority_subject_payload->'activation_binding'
                  ) AS key
                 ORDER BY key
            ) IS DISTINCT FROM ARRAY[
                'admission_epoch','admission_id','agent_release_sha256',
                'approval_id','authority_mode','authority_root_admission_id',
                'claude_session_id','deployment_id','lease_expires_at',
                'prior_admission_id','prior_rollback_receipt_sha256',
                'process_id','process_start_fingerprint_sha256',
                'recovery_intent_id','restart_intent_sha256',
                'runtime_boot_id_sha256','runtime_executable_path',
                'runtime_executable_sha256','runtime_host_identity_sha256',
                'runtime_lock_path','task_id','worker_id','worker_session_id'
            ]::TEXT[]
        )
        OR (
            NEW.authority_subject_payload#>>'{activation_binding,authority_mode}'
                IN ('initial-m5-decision','initial-local-activation')
            AND (
                NEW.authority_subject_payload#>>'{activation_binding,authority_root_admission_id}'
                    <>NEW.authority_subject_payload#>>'{activation_binding,admission_id}'
                OR NEW.authority_subject_payload->'activation_binding'
                    ->'prior_admission_id' IS DISTINCT FROM 'null'::JSONB
                OR NEW.authority_subject_payload->'activation_binding'
                    ->'recovery_intent_id' IS DISTINCT FROM 'null'::JSONB
                OR NEW.authority_subject_payload->'activation_binding'
                    ->'prior_rollback_receipt_sha256' IS DISTINCT FROM 'null'::JSONB
                OR NEW.authority_subject_payload->'activation_binding'
                    ->'restart_intent_sha256' IS DISTINCT FROM 'null'::JSONB
            )
        )
        OR (
            NEW.authority_subject_payload#>>'{activation_binding,authority_mode}'
                ='standing-mandate-recovery'
            AND (
                NEW.authority_subject_payload#>>'{activation_binding,authority_root_admission_id}'
                    =NEW.authority_subject_payload#>>'{activation_binding,admission_id}'
                OR NEW.authority_subject_payload#>>'{activation_binding,prior_admission_id}'
                    IS NULL
                OR NEW.authority_subject_payload#>>'{activation_binding,recovery_intent_id}'
                    IS NULL
                OR NEW.authority_subject_payload#>>'{activation_binding,prior_rollback_receipt_sha256}'
                    !~ '^[0-9a-f]{64}$'
                OR NEW.authority_subject_payload#>>'{activation_binding,restart_intent_sha256}'
                    !~ '^[0-9a-f]{64}$'
            )
        )
    ) THEN
        RAISE EXCEPTION
            'founder mandate may only decide an exact Company Scribe admission';
    END IF;
    prior_step:=CASE NEW.activation_step
        WHEN 'final-release-install' THEN NULL
        WHEN 'privilege-cleanup' THEN 'final-release-install'
        WHEN 'shadow-monday-1' THEN 'privilege-cleanup'
        WHEN 'shadow-monday-2' THEN 'shadow-monday-1'
        WHEN 'shadow-monday-3' THEN 'shadow-monday-2'
        WHEN 'supervised-live-upload' THEN 'shadow-monday-3'
        WHEN 'mini-cutover' THEN 'supervised-live-upload'
        WHEN 'mini-verification' THEN 'mini-cutover'
        WHEN 'scribe-admission' THEN
            CASE WHEN current_scribe_contract THEN NULL ELSE 'mini-verification' END
        WHEN 'scribe-verification' THEN 'scribe-admission'
        ELSE '__invalid__'
    END;
    IF prior_step='__invalid__' OR (
        prior_step IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM kernel_founder_activation_step_receipts
             WHERE mandate_id=NEW.mandate_id AND activation_step=prior_step
        )
    ) THEN
        RAISE EXCEPTION 'activation decision is out of mandate order';
    END IF;
    RETURN NEW;
END
$function$;
