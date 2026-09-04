-- One durable founder activation mandate, exact per-step M5 decisions, and
-- ordered completion receipts.
--
-- Legacy M5 authority rows remain unchanged.  New activation rows reference
-- one closed-scope direct-founder mandate and one exact step subject.  The
-- mandate is reusable only across its ten named ordered steps; idempotency
-- is per step/attempt/effect rather than a globally burned bearer proof.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS kernel_activation_authority_hmac_keys (
    key_id      TEXT PRIMARY KEY CHECK (key_id = 'founder-activation-v1'),
    secret_key  BYTEA NOT NULL CHECK (octet_length(secret_key) = 32),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
REVOKE ALL ON kernel_activation_authority_hmac_keys FROM PUBLIC;
INSERT INTO kernel_activation_authority_hmac_keys (key_id,secret_key)
VALUES (
    'founder-activation-v1',
    decode(
        replace(gen_random_uuid()::TEXT,'-','') ||
        replace(gen_random_uuid()::TEXT,'-',''),
        'hex'
    )
)
ON CONFLICT (key_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS kernel_founder_activation_mandates (
    mandate_id                TEXT PRIMARY KEY
                               CHECK (
                                   mandate_id
                                   ~ '^activation-mandate-[A-Za-z0-9._-]+$'
                               ),
    artifact_sha256           TEXT NOT NULL UNIQUE
                               CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    artifact_bytes            BYTEA NOT NULL,
    artifact                  JSONB NOT NULL
                               CHECK (jsonb_typeof(artifact)='object'),
    issued_at                 TIMESTAMPTZ NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'active'
                               CHECK (status IN ('active','completed','revoked')),
    recorded_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at                 TIMESTAMPTZ,
    authority_verified_by     NAME NOT NULL,
    authority_verification_mac BYTEA NOT NULL
                               CHECK (octet_length(authority_verification_mac)=32),
    CHECK ((status='active') = (closed_at IS NULL))
);
REVOKE ALL ON kernel_founder_activation_mandates FROM PUBLIC;

ALTER TABLE m5_approval_decisions
    ADD COLUMN IF NOT EXISTS mandate_id TEXT,
    ADD COLUMN IF NOT EXISTS mandate_artifact_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS activation_step TEXT,
    ADD COLUMN IF NOT EXISTS activation_step_key TEXT,
    ADD COLUMN IF NOT EXISTS authority_subject_ref TEXT,
    ADD COLUMN IF NOT EXISTS authority_subject_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS authority_subject_payload JSONB,
    ADD COLUMN IF NOT EXISTS authority_verified_by NAME,
    ADD COLUMN IF NOT EXISTS authority_verification_mac BYTEA;

DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='m5_mandated_activation_authority_shape'
    ) THEN
        ALTER TABLE m5_approval_decisions
            ADD CONSTRAINT m5_mandated_activation_authority_shape CHECK (
                (
                    mandate_id IS NULL
                    AND mandate_artifact_sha256 IS NULL
                    AND activation_step IS NULL
                    AND activation_step_key IS NULL
                    AND authority_subject_ref IS NULL
                    AND authority_subject_sha256 IS NULL
                    AND authority_subject_payload IS NULL
                    AND authority_verified_by IS NULL
                    AND authority_verification_mac IS NULL
                )
                OR
                (
                    mandate_id
                    ~ '^activation-mandate-[A-Za-z0-9._-]+$'
                    AND mandate_artifact_sha256 ~ '^[0-9a-f]{64}$'
                    AND activation_step IN (
                        'final-release-install',
                        'privilege-cleanup',
                        'shadow-monday-1','shadow-monday-2','shadow-monday-3',
                        'supervised-live-upload','mini-cutover',
                        'mini-verification','scribe-admission',
                        'scribe-verification'
                    )
                    AND btrim(activation_step_key)<>''
                    AND authority_subject_ref IS NOT NULL
                    AND authority_subject_sha256 ~ '^[0-9a-f]{64}$'
                    AND jsonb_typeof(authority_subject_payload)='object'
                    AND authority_verified_by IS NOT NULL
                    AND octet_length(authority_verification_mac)=32
                )
            );
    END IF;
END
$constraints$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_m5_mandate_step_attempt_global
    ON m5_approval_decisions (mandate_id,activation_step_key)
    WHERE mandate_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_m5_mandate_subject_global
    ON m5_approval_decisions (mandate_id,authority_subject_ref)
    WHERE mandate_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS kernel_founder_activation_step_receipts (
    mandate_id                TEXT NOT NULL,
    activation_step           TEXT NOT NULL CHECK (activation_step IN (
        'final-release-install',
        'privilege-cleanup',
        'shadow-monday-1','shadow-monday-2','shadow-monday-3',
        'supervised-live-upload','mini-cutover','mini-verification',
        'scribe-admission','scribe-verification'
    )),
    activation_step_key       TEXT NOT NULL CHECK (btrim(activation_step_key)<>''),
    authority_tenant_id       TEXT NOT NULL CHECK (btrim(authority_tenant_id)<>''),
    approval_id               UUID NOT NULL,
    authority_subject_ref     TEXT NOT NULL,
    authority_subject_sha256  TEXT NOT NULL
                               CHECK (authority_subject_sha256 ~ '^[0-9a-f]{64}$'),
    receipt_sha256            TEXT NOT NULL
                               CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    receipt_payload           JSONB NOT NULL
                               CHECK (jsonb_typeof(receipt_payload)='object'),
    completed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (mandate_id,activation_step_key),
    UNIQUE (authority_tenant_id,approval_id),
    FOREIGN KEY (mandate_id)
        REFERENCES kernel_founder_activation_mandates (mandate_id),
    FOREIGN KEY (authority_tenant_id,approval_id)
        REFERENCES m5_approval_decisions (tenant_id,approval_id)
);
REVOKE ALL ON kernel_founder_activation_step_receipts FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_founder_mandate_expected_mac(
    p_mandate_id TEXT,p_artifact_sha256 TEXT,p_artifact_bytes BYTEA,
    p_artifact JSONB,p_issued_at TIMESTAMPTZ
)
RETURNS BYTEA
LANGUAGE plpgsql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    authority_key BYTEA;
BEGIN
    SELECT secret_key INTO authority_key
      FROM kernel_activation_authority_hmac_keys
     WHERE key_id='founder-activation-v1';
    IF authority_key IS NULL THEN
        RAISE EXCEPTION 'founder activation authority key is unavailable';
    END IF;
    RETURN kernel_hmac_sha256(
        convert_to(
            concat_ws(
                '|','founder-activation-mandate-v1',p_mandate_id,
                p_artifact_sha256,
                encode(digest(p_artifact_bytes,'sha256'),'hex'),
                encode(
                    digest(convert_to(p_artifact::TEXT,'UTF8'),'sha256'),'hex'
                ),
                p_issued_at::TEXT
            ),
            'UTF8'
        ),
        authority_key
    );
END
$function$;
REVOKE ALL ON FUNCTION kernel_founder_mandate_expected_mac(
    TEXT,TEXT,BYTEA,JSONB,TIMESTAMPTZ
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_founder_mandate_mac(
    p_mandate_id TEXT,p_artifact_sha256 TEXT,p_artifact_bytes BYTEA,
    p_artifact JSONB,p_issued_at TIMESTAMPTZ
)
RETURNS BYTEA
LANGUAGE plpgsql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    caller_is_superuser BOOLEAN := FALSE;
BEGIN
    SELECT rolsuper INTO caller_is_superuser
      FROM pg_roles WHERE rolname=session_user;
    IF session_user::TEXT<>'fleet_kernel_provisioner'
       AND NOT COALESCE(caller_is_superuser,FALSE) THEN
        RAISE EXCEPTION
            'founder mandate attestation requires the isolated provisioner';
    END IF;
    RETURN kernel_founder_mandate_expected_mac(
        p_mandate_id,p_artifact_sha256,p_artifact_bytes,p_artifact,p_issued_at
    );
END
$function$;
REVOKE ALL ON FUNCTION kernel_founder_mandate_mac(
    TEXT,TEXT,BYTEA,JSONB,TIMESTAMPTZ
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_validate_founder_activation_mandate()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    expected_mac BYTEA;
    expected_scope JSONB := '[
        "finish_review_package_and_install_exact_kernel_release",
        "remove_and_verify_absent_mini_nopasswd_rule_after_last_privileged_operation",
        "earn_three_consecutive_real_shadow_mondays",
        "run_one_fenced_supervised_live_workflow",
        "transfer_exact_mini_authority_pointer",
        "verify_mini_canary_real_workflow_and_reconciliation",
        "admit_exactly_one_macbook_scribe_after_mini_activation",
        "verify_scribe_singleton_persistence_and_first_workflow",
        "verify_mini_and_scribe_restart_recovery_rollback_and_terminal_receipts"
    ]'::JSONB;
    expected_constraints JSONB := '[
        "no_gatef_evidence",
        "mini_kernel_before_macbook_scribe",
        "exact_technical_subject_and_receipt_per_step",
        "one_effect_path",
        "one_writable_scribe",
        "company_and_restaurant_fleet_data_isolation",
        "operator_approval_remains_required_where_the_workflow_requires_it",
        "nopasswd_must_be_absent_before_shadow_credit",
        "phase_4_remaining_cc_suite_migration_is_out_of_scope",
        "no_scope_expansion_beyond_ordered_scope"
    ]'::JSONB;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF (
            NEW.mandate_id,NEW.artifact_sha256,NEW.artifact_bytes,
            NEW.artifact,NEW.issued_at,
            NEW.recorded_at,NEW.authority_verified_by,
            NEW.authority_verification_mac
        ) IS DISTINCT FROM (
            OLD.mandate_id,OLD.artifact_sha256,OLD.artifact_bytes,
            OLD.artifact,OLD.issued_at,
            OLD.recorded_at,OLD.authority_verified_by,
            OLD.authority_verification_mac
        ) THEN
            RAISE EXCEPTION 'founder activation mandate identity is immutable';
        END IF;
        IF OLD.status<>'active' AND NEW.status<>OLD.status THEN
            RAISE EXCEPTION 'closed founder activation mandate is terminal';
        END IF;
        IF OLD.status='active' AND NEW.status<>'active' THEN
            NEW.closed_at:=now();
        ELSE
            NEW.closed_at:=OLD.closed_at;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.status<>'active' OR NEW.closed_at IS NOT NULL
       OR NEW.artifact->>'schema'<>'mise.activation-mandate.v1'
       OR NEW.mandate_id
          <>'__CC_SUITE_MANDATE_ID__'
       OR NEW.artifact->>'mandate_id'<>NEW.mandate_id
       OR NEW.artifact_sha256
          <>'__CC_SUITE_MANDATE_ARTIFACT_SHA256__'
       OR encode(digest(NEW.artifact_bytes,'sha256'),'hex')
          <>NEW.artifact_sha256
       OR convert_from(NEW.artifact_bytes,'UTF8')::JSONB
          IS DISTINCT FROM NEW.artifact
       OR (
            SELECT count(*) FROM jsonb_object_keys(NEW.artifact)
          )<>13
       OR NEW.artifact->>'status'<>'active'
       OR NEW.artifact->>'issuer'<>'__CC_SUITE_AUTHORITY_ISSUER__'
       OR NEW.artifact->>'source_kind'<>'direct_founder_instruction'
       OR NEW.artifact->>'quoted_instruction'
          <>'__CC_SUITE_MANDATE_QUOTED_INSTRUCTION__'
       OR NEW.artifact->>'recorded_at'<>'__CC_SUITE_MANDATE_RECORDED_AT__'
       OR NEW.artifact->>'governing_canon'
          <>'__CC_SUITE_MANDATE_GOVERNING_CANON__'
       OR NEW.artifact->>'executable_plan'
          <>'__CC_SUITE_MANDATE_EXECUTABLE_PLAN__'
       OR NEW.artifact->'ordered_scope' IS DISTINCT FROM expected_scope
       OR NEW.artifact->'constraints' IS DISTINCT FROM expected_constraints
       OR NEW.artifact->>'requires_additional_founder_go_within_scope'<>'false'
       OR NEW.artifact->'terminal_condition' IS DISTINCT FROM '{
            "when":"all_ordered_scope_steps_have_verified_completion_receipts_and_final_combined_recovery_receipt_is_verified",
            "terminal_status":"completed",
            "authorization_valid_after_terminal":false,
            "deterministic_recovery_contract":{
              "survives_terminal":true,
              "requires_additional_founder_go":false,
              "scope":"same_exact_release_same_hosts_same_deployments_same_authority_root_monotonic_recovery_epochs_only",
              "forbids":"new_release_new_host_new_deployment_scope_expansion_or_new_effect_authority"
            }
          }'::JSONB
       OR (NEW.artifact->>'recorded_at')::TIMESTAMPTZ
          IS DISTINCT FROM NEW.issued_at THEN
        RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
    END IF;
    expected_mac:=kernel_founder_mandate_expected_mac(
        NEW.mandate_id,NEW.artifact_sha256,NEW.artifact_bytes,
        NEW.artifact,NEW.issued_at
    );
    IF NEW.authority_verification_mac IS DISTINCT FROM expected_mac THEN
        RAISE EXCEPTION 'founder activation mandate lacks provisioner attestation';
    END IF;
    RETURN NEW;
END
$function$;
DROP TRIGGER IF EXISTS kernel_founder_activation_mandate_guard
    ON kernel_founder_activation_mandates;
CREATE TRIGGER kernel_founder_activation_mandate_guard
BEFORE INSERT OR UPDATE ON kernel_founder_activation_mandates
FOR EACH ROW EXECUTE FUNCTION kernel_validate_founder_activation_mandate();

CREATE OR REPLACE FUNCTION kernel_mandated_step_expected_mac(
    p_tenant_id TEXT,p_approval_id UUID,p_mandate_id TEXT,
    p_mandate_artifact_sha256 TEXT,p_activation_step TEXT,
    p_activation_step_key TEXT,p_authority_subject_ref TEXT,
    p_authority_subject_sha256 TEXT,p_authority_subject_payload JSONB
)
RETURNS BYTEA
LANGUAGE plpgsql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    authority_key BYTEA;
BEGIN
    SELECT secret_key INTO authority_key
      FROM kernel_activation_authority_hmac_keys
     WHERE key_id='founder-activation-v1';
    IF authority_key IS NULL THEN
        RAISE EXCEPTION 'founder activation authority key is unavailable';
    END IF;
    RETURN kernel_hmac_sha256(
        convert_to(
            concat_ws(
                '|','founder-mandated-activation-step-v1',p_tenant_id,
                p_approval_id::TEXT,p_mandate_id,p_mandate_artifact_sha256,
                p_activation_step,p_activation_step_key,
                p_authority_subject_ref,p_authority_subject_sha256,
                encode(
                    digest(
                        convert_to(p_authority_subject_payload::TEXT,'UTF8'),
                        'sha256'
                    ),
                    'hex'
                )
            ),
            'UTF8'
        ),
        authority_key
    );
END
$function$;
REVOKE ALL ON FUNCTION kernel_mandated_step_expected_mac(
    TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_mandated_step_mac(
    p_tenant_id TEXT,p_approval_id UUID,p_mandate_id TEXT,
    p_mandate_artifact_sha256 TEXT,p_activation_step TEXT,
    p_activation_step_key TEXT,p_authority_subject_ref TEXT,
    p_authority_subject_sha256 TEXT,p_authority_subject_payload JSONB
)
RETURNS BYTEA
LANGUAGE plpgsql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    caller_is_superuser BOOLEAN := FALSE;
BEGIN
    SELECT rolsuper INTO caller_is_superuser
      FROM pg_roles WHERE rolname=session_user;
    IF session_user::TEXT<>'fleet_kernel_provisioner'
       AND NOT COALESCE(caller_is_superuser,FALSE) THEN
        RAISE EXCEPTION
            'mandated activation step attestation requires the isolated provisioner';
    END IF;
    RETURN kernel_mandated_step_expected_mac(
        p_tenant_id,p_approval_id,p_mandate_id,p_mandate_artifact_sha256,
        p_activation_step,p_activation_step_key,p_authority_subject_ref,
        p_authority_subject_sha256,p_authority_subject_payload
    );
END
$function$;
REVOKE ALL ON FUNCTION kernel_mandated_step_mac(
    TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB
) FROM PUBLIC;

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
            '{"prior_step":"mini-verification","kernel_authority":"mini","scribe_authority":"absent"}'::JSONB
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
            '{"kernel_authority":"mini","scribe_authority":"live","deployment_id":"__CC_SUITE_SCRIBE_DEPLOYMENT_ID__"}'::JSONB
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
        WHEN 'scribe-admission' THEN 24
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
            <>'initial-m5-decision'
        OR NEW.authority_subject_payload#>>'{activation_binding,authority_root_admission_id}'
            <>NEW.authority_subject_payload#>>'{activation_binding,admission_id}'
        OR NEW.authority_subject_payload->'activation_binding'
            ->'prior_admission_id' IS DISTINCT FROM 'null'::JSONB
        OR NEW.authority_subject_payload->'activation_binding'
            ->'recovery_intent_id' IS DISTINCT FROM 'null'::JSONB
        OR NEW.authority_subject_payload->'activation_binding'
            ->'prior_rollback_receipt_sha256' IS DISTINCT FROM 'null'::JSONB
        OR NEW.authority_subject_payload->'activation_binding'
            ->'restart_intent_sha256' IS DISTINCT FROM 'null'::JSONB
        OR NEW.authority_subject_payload#>>'{activation_binding,runtime_lock_path}'
            NOT LIKE '/%'
        OR NEW.authority_subject_payload#>>'{activation_binding,runtime_executable_path}'
            NOT LIKE '/%'
    ) THEN
        RAISE EXCEPTION
            'M5 may only decide the exact initial Company Scribe admission';
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
        WHEN 'scribe-admission' THEN 'mini-verification'
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
DROP TRIGGER IF EXISTS kernel_m5_mandated_activation_guard
    ON m5_approval_decisions;
CREATE TRIGGER kernel_m5_mandated_activation_guard
BEFORE INSERT ON m5_approval_decisions
FOR EACH ROW EXECUTE FUNCTION kernel_validate_m5_mandated_activation();

CREATE OR REPLACE FUNCTION kernel_require_mandated_activation_step(
    p_tenant_id TEXT,p_approval_id UUID,p_mandate_id TEXT,
    p_mandate_artifact_sha256 TEXT,p_activation_step TEXT,
    p_activation_step_key TEXT,p_authority_subject_ref TEXT,
    p_authority_subject_sha256 TEXT,p_authority_subject_payload JSONB
)
RETURNS JSONB
LANGUAGE plpgsql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    decision m5_approval_decisions%ROWTYPE;
    mandate kernel_founder_activation_mandates%ROWTYPE;
    approval_state TEXT;
    expected_mac BYTEA;
BEGIN
    SELECT * INTO decision
      FROM m5_approval_decisions
     WHERE tenant_id=p_tenant_id AND approval_id=p_approval_id;
    SELECT a.decision INTO approval_state
      FROM approvals AS a
     WHERE a.tenant_id=p_tenant_id AND a.id=p_approval_id;
    SELECT * INTO mandate
      FROM kernel_founder_activation_mandates
     WHERE mandate_id=p_mandate_id;
    expected_mac:=kernel_mandated_step_expected_mac(
        p_tenant_id,p_approval_id,p_mandate_id,p_mandate_artifact_sha256,
        p_activation_step,p_activation_step_key,p_authority_subject_ref,
        p_authority_subject_sha256,p_authority_subject_payload
    );
    IF decision IS NULL OR mandate IS NULL OR mandate.status<>'active'
       OR approval_state<>'approved'
       OR decision.decision<>'approved'
       OR decision.decided_by<>'founder'
       OR decision.authority_kind<>'founder_direct'
       OR decision.authority_ref
          <>format('founder://activation-mandate/%s',p_mandate_id)
       OR decision.mandate_id<>p_mandate_id
       OR decision.mandate_artifact_sha256<>p_mandate_artifact_sha256
       OR decision.activation_step<>p_activation_step
       OR decision.activation_step_key<>p_activation_step_key
       OR decision.authority_subject_ref<>p_authority_subject_ref
       OR decision.authority_subject_sha256<>p_authority_subject_sha256
       OR decision.authority_subject_payload IS DISTINCT FROM p_authority_subject_payload
       OR decision.authority_verification_mac IS DISTINCT FROM expected_mac
       OR mandate.artifact_sha256<>p_mandate_artifact_sha256
       OR mandate.authority_verification_mac IS DISTINCT FROM
          kernel_founder_mandate_expected_mac(
              mandate.mandate_id,mandate.artifact_sha256,
              mandate.artifact_bytes,mandate.artifact,mandate.issued_at
          ) THEN
        RAISE EXCEPTION
            'exact mandated activation step is absent, stale, or forged';
    END IF;
    RETURN jsonb_build_object(
        'mandate_id',decision.mandate_id,
        'mandate_artifact_sha256',decision.mandate_artifact_sha256,
        'activation_step',decision.activation_step,
        'activation_step_key',decision.activation_step_key,
        'authority_subject_ref',decision.authority_subject_ref,
        'authority_subject_sha256',decision.authority_subject_sha256
    );
END
$function$;
REVOKE ALL ON FUNCTION kernel_require_mandated_activation_step(
    TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_record_founder_activation_step_receipt(
    p_tenant_id TEXT,p_approval_id UUID,p_mandate_id TEXT,
    p_mandate_artifact_sha256 TEXT,p_activation_step TEXT,
    p_activation_step_key TEXT,p_authority_subject_ref TEXT,
    p_authority_subject_sha256 TEXT,p_authority_subject_payload JSONB,
    p_receipt_sha256 TEXT,p_receipt_payload JSONB
)
RETURNS VOID
LANGUAGE plpgsql VOLATILE STRICT SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    prior_step TEXT;
    existing kernel_founder_activation_step_receipts%ROWTYPE;
    final_transition RECORD;
    current_migration RECORD;
    current_pointer RECORD;
    current_epoch RECORD;
    transition_found BOOLEAN := FALSE;
BEGIN
    IF p_activation_step='mini-verification' THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended('m9-authority:' || p_tenant_id,0)
        );
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('founder-activation-mandate:' || p_mandate_id,0)
    );
    SELECT * INTO existing
      FROM kernel_founder_activation_step_receipts
     WHERE mandate_id=p_mandate_id
       AND activation_step_key=p_activation_step_key;
    IF existing IS NOT NULL THEN
        IF (
            existing.authority_tenant_id,existing.approval_id,
            existing.activation_step,existing.authority_subject_ref,
            existing.authority_subject_sha256,existing.receipt_sha256,
            existing.receipt_payload
        ) IS DISTINCT FROM (
            p_tenant_id,p_approval_id,p_activation_step,p_authority_subject_ref,
            p_authority_subject_sha256,p_receipt_sha256,p_receipt_payload
        ) THEN
            RAISE EXCEPTION 'activation step was already completed differently';
        END IF;
        RETURN;
    END IF;
    PERFORM kernel_require_mandated_activation_step(
        p_tenant_id,p_approval_id,p_mandate_id,p_mandate_artifact_sha256,
        p_activation_step,p_activation_step_key,p_authority_subject_ref,
        p_authority_subject_sha256,p_authority_subject_payload
    );
    IF p_receipt_sha256 !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(p_receipt_payload)<>'object'
       OR p_receipt_payload->>'status'<>'completed'
       OR p_receipt_payload->>'activation_step'<>p_activation_step
       OR p_receipt_payload->>'authority_subject_sha256'
          <>p_authority_subject_sha256 THEN
        RAISE EXCEPTION 'activation step completion receipt is incomplete';
    END IF;
    IF p_activation_step='supervised-live-upload' AND (
        p_receipt_payload->>'mode'<>'never-submit'
        OR p_receipt_payload->>'external_effect'<>'upload-completed'
        OR p_receipt_payload->>'submission_effect'<>'none'
    ) THEN
        RAISE EXCEPTION 'supervised live upload receipt must prove never-submit';
    END IF;
    IF p_activation_step='privilege-cleanup'
       AND p_receipt_payload->>'nopasswd_rule'<>'absent' THEN
        RAISE EXCEPTION 'privilege cleanup receipt must prove NOPASSWD absent';
    END IF;
    IF p_activation_step='mini-verification' AND (
        (SELECT count(*) FROM jsonb_object_keys(p_receipt_payload))<>23
        OR p_receipt_payload->>'schema_id' IS DISTINCT FROM
           'mise.mini-verification-completion/v1'
        OR p_receipt_payload->'schema_version' IS DISTINCT FROM '1'::JSONB
        OR p_receipt_payload->>'canary' IS DISTINCT FROM 'passed'
        OR p_receipt_payload->>'real_workflow' IS DISTINCT FROM 'reconciled'
        OR p_receipt_payload->>'reconciliation' IS DISTINCT FROM 'passed'
        OR p_receipt_payload->>'restart' IS DISTINCT FROM 'passed'
        OR p_receipt_payload->>'rollback' IS DISTINCT FROM 'passed'
        OR p_receipt_payload->>'recutover' IS DISTINCT FROM 'passed'
        OR p_receipt_payload->>'final_route' IS DISTINCT FROM 'kernel'
        OR p_receipt_payload->>'final_transition_kind' IS DISTINCT FROM
           'recutover'
        OR p_receipt_payload->>'migration_id' IS DISTINCT FROM
           p_authority_subject_payload#>>'{activation_binding,migration_id}'
        OR p_receipt_payload->>'canary_receipt_sha256' IS DISTINCT FROM
           p_authority_subject_payload#>>
             '{activation_binding,canary_receipt_sha256}'
        OR p_receipt_payload->>'real_workflow_receipt_sha256'
           IS DISTINCT FROM p_authority_subject_payload#>>
             '{activation_binding,real_workflow_receipt_sha256}'
        OR p_receipt_payload->>'reconciliation_receipt_sha256'
           IS DISTINCT FROM p_authority_subject_payload#>>
             '{activation_binding,reconciliation_receipt_sha256}'
        OR p_receipt_payload->>'restart_receipt_sha256' IS DISTINCT FROM
           p_authority_subject_payload#>>
             '{activation_binding,restart_receipt_sha256}'
        OR p_receipt_payload->>'rollback_receipt_sha256' IS DISTINCT FROM
           p_authority_subject_payload#>>
             '{activation_binding,rollback_receipt_sha256}'
        OR p_receipt_payload->>'final_transition_receipt_sha256'
           IS DISTINCT FROM p_authority_subject_payload#>>
             '{activation_binding,final_transition_receipt_sha256}'
        OR COALESCE(
             p_receipt_payload->>'final_authority_epoch',''
           ) !~ '^[1-9][0-9]*$'
        OR jsonb_typeof(
             p_receipt_payload->'final_authority_epoch'
           ) IS DISTINCT FROM 'number'
        OR COALESCE(
             p_receipt_payload->>'final_recovery_epoch',''
           ) !~ '^[1-9][0-9]*$'
        OR jsonb_typeof(
             p_receipt_payload->'final_recovery_epoch'
           ) IS DISTINCT FROM 'number'
        OR COALESCE(
             p_receipt_payload->>'final_dispatch_authority_sha256',''
           )
           !~ '^[0-9a-f]{64}$'
        OR p_receipt_sha256 IS DISTINCT FROM encode(
             sha256(convert_to(
               public.kernel_m9_canonical_jsonb_text(p_receipt_payload),
               'UTF8'
             )),
             'hex'
           )
    ) THEN
        RAISE EXCEPTION 'Mini verification receipt is incomplete';
    END IF;
    IF p_activation_step='mini-verification' THEN
        SELECT recovery_epoch,transition_kind,after_path,authority_epoch,
               receipt_sha256,tenant_id,package_id,package_sha256,
               mandate_id,release_commit_sha,release_tree_sha,
               release_digest_sha256,d13_evidence_sha256
          INTO final_transition
          FROM kernel_mini_authority_transition_receipts
         WHERE migration_id=(
             p_authority_subject_payload#>>
               '{activation_binding,migration_id}'
         )::UUID
         ORDER BY recovery_epoch DESC
         LIMIT 1
         FOR SHARE;
        transition_found:=FOUND;
        SELECT status,authority_plane,package_id,package_sha256,
               candidate_commit
          INTO current_migration
          FROM kernel_mini_runtime_migrations
         WHERE id=(
             p_authority_subject_payload#>>
               '{activation_binding,migration_id}'
         )::UUID
           AND tenant_id=p_tenant_id
         FOR SHARE;
        SELECT active_path,evidence_sha256
          INTO current_pointer
          FROM kernel_m9_cutover_pointer
         WHERE tenant_id=p_tenant_id
         FOR SHARE;
        SELECT authority_epoch,active_path,migration_id
          INTO current_epoch
          FROM kernel_mini_authority_epochs
         WHERE tenant_id=p_tenant_id
         FOR SHARE;
        IF NOT transition_found
           OR final_transition.recovery_epoch<3
           OR final_transition.recovery_epoch%2<>1
           OR final_transition.authority_epoch<3
           OR final_transition.authority_epoch%2<>1
           OR final_transition.authority_epoch
              <>final_transition.recovery_epoch
           OR final_transition.transition_kind IS DISTINCT FROM 'recutover'
           OR final_transition.after_path IS DISTINCT FROM 'kernel'
           OR final_transition.tenant_id IS DISTINCT FROM p_tenant_id
           OR final_transition.mandate_id IS DISTINCT FROM p_mandate_id
           OR final_transition.package_id::TEXT
              IS DISTINCT FROM p_authority_subject_payload#>>'{package,id}'
           OR final_transition.package_sha256
              IS DISTINCT FROM p_authority_subject_payload#>>'{package,sha256}'
           OR final_transition.release_commit_sha
              IS DISTINCT FROM
                 p_authority_subject_payload#>>'{release,commit_sha}'
           OR final_transition.release_tree_sha
              IS DISTINCT FROM
                 p_authority_subject_payload#>>'{release,tree_sha}'
           OR final_transition.release_digest_sha256
              IS DISTINCT FROM
                 p_authority_subject_payload#>>'{release,digest_sha256}'
           OR final_transition.receipt_sha256
              IS DISTINCT FROM
                 p_receipt_payload->>'final_transition_receipt_sha256'
           OR final_transition.authority_epoch
              <>(p_receipt_payload->>'final_authority_epoch')::BIGINT
           OR final_transition.recovery_epoch
              <>(p_receipt_payload->>'final_recovery_epoch')::BIGINT
           OR current_migration.status IS DISTINCT FROM
              'kernel_authoritative'
           OR current_migration.authority_plane IS DISTINCT FROM 'kernel'
           OR current_migration.package_id IS DISTINCT FROM
              final_transition.package_id
           OR current_migration.package_sha256 IS DISTINCT FROM
              final_transition.package_sha256
           OR current_migration.candidate_commit IS DISTINCT FROM
              final_transition.release_commit_sha
           OR current_pointer.active_path IS DISTINCT FROM 'kernel'
           OR current_pointer.evidence_sha256 IS DISTINCT FROM
              final_transition.d13_evidence_sha256
           OR current_epoch.active_path IS DISTINCT FROM 'kernel'
           OR current_epoch.migration_id IS DISTINCT FROM (
               p_authority_subject_payload#>>
                 '{activation_binding,migration_id}'
           )::UUID
           OR current_epoch.authority_epoch IS DISTINCT FROM
              final_transition.authority_epoch THEN
            RAISE EXCEPTION
                'Mini verification lacks exact rollback and recutover state';
        END IF;
    END IF;
    IF p_activation_step='scribe-verification' AND (
        p_receipt_payload->>'singleton'<>'verified'
        OR p_receipt_payload->>'persistence'<>'verified'
        OR p_receipt_payload->>'restart'<>'passed'
        OR p_receipt_payload->>'receipts'<>'verified'
    ) THEN
        RAISE EXCEPTION 'Scribe verification receipt is incomplete';
    END IF;
    prior_step:=CASE p_activation_step
        WHEN 'final-release-install' THEN NULL
        WHEN 'privilege-cleanup' THEN 'final-release-install'
        WHEN 'shadow-monday-1' THEN 'privilege-cleanup'
        WHEN 'shadow-monday-2' THEN 'shadow-monday-1'
        WHEN 'shadow-monday-3' THEN 'shadow-monday-2'
        WHEN 'supervised-live-upload' THEN 'shadow-monday-3'
        WHEN 'mini-cutover' THEN 'supervised-live-upload'
        WHEN 'mini-verification' THEN 'mini-cutover'
        WHEN 'scribe-admission' THEN 'mini-verification'
        WHEN 'scribe-verification' THEN 'scribe-admission'
        ELSE '__invalid__'
    END;
    IF prior_step='__invalid__' OR (
        prior_step IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM kernel_founder_activation_step_receipts
             WHERE mandate_id=p_mandate_id AND activation_step=prior_step
        )
    ) THEN
        RAISE EXCEPTION 'activation step is out of mandate order';
    END IF;
    INSERT INTO kernel_founder_activation_step_receipts (
        mandate_id,activation_step,activation_step_key,authority_tenant_id,
        approval_id,authority_subject_ref,authority_subject_sha256,
        receipt_sha256,receipt_payload
    ) VALUES (
        p_mandate_id,p_activation_step,p_activation_step_key,p_tenant_id,
        p_approval_id,p_authority_subject_ref,p_authority_subject_sha256,
        p_receipt_sha256,p_receipt_payload
    );
    IF p_activation_step='scribe-verification' THEN
        UPDATE kernel_founder_activation_mandates
           SET status='completed'
         WHERE mandate_id=p_mandate_id AND status='active';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'founder activation mandate closed before final receipt';
        END IF;
    END IF;
END
$function$;
REVOKE ALL ON FUNCTION kernel_record_founder_activation_step_receipt(
    TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB,TEXT,JSONB
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_reject_founder_activation_receipt_mutation()
RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    RAISE EXCEPTION 'founder activation completion receipts are immutable';
END
$function$;
DROP TRIGGER IF EXISTS kernel_founder_activation_receipt_immutable
    ON kernel_founder_activation_step_receipts;
CREATE TRIGGER kernel_founder_activation_receipt_immutable
BEFORE UPDATE OR DELETE ON kernel_founder_activation_step_receipts
FOR EACH ROW EXECUTE FUNCTION kernel_reject_founder_activation_receipt_mutation();
DROP TRIGGER IF EXISTS kernel_founder_activation_receipt_truncate_guard
    ON kernel_founder_activation_step_receipts;
CREATE TRIGGER kernel_founder_activation_receipt_truncate_guard
BEFORE TRUNCATE ON kernel_founder_activation_step_receipts
FOR EACH STATEMENT EXECUTE FUNCTION kernel_reject_founder_activation_receipt_mutation();
