-- Forward-only Phase-4 founder-mandate validator branch.
--
-- DDL 024 remains immutable historical truth.  This replacement preserves
-- the complete 20260731 legacy branch and adds one exact, digest-pinned
-- `mise.cc-suite-activation-mandate.v1` branch.  Existing rows remain
-- updateable only through the unchanged status-transition rules and are not
-- reinterpreted through the new insert branch.

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
    array_field TEXT;
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

    IF NEW.artifact->>'schema'='mise.cc-suite-activation-mandate.v1' THEN
        IF NEW.status<>'active' OR NEW.closed_at IS NOT NULL
           -- PHASE4_PINNED_MANDATE_ID_BEGIN
           OR NEW.mandate_id
              <>'__CC_SUITE_PHASE4_MANDATE_ID__'
           OR NEW.artifact->>'mandate_id'<>NEW.mandate_id
           -- PHASE4_PINNED_MANDATE_ID_END
           OR NEW.artifact_sha256
              <>'__CC_SUITE_PHASE4_MANDATE_ARTIFACT_SHA256__'
           OR encode(digest(NEW.artifact_bytes,'sha256'),'hex')
              <>NEW.artifact_sha256
           OR convert_from(NEW.artifact_bytes,'UTF8')::JSONB
              IS DISTINCT FROM NEW.artifact
           OR ARRAY(
                SELECT key FROM jsonb_object_keys(NEW.artifact) AS key
                 ORDER BY key
              ) IS DISTINCT FROM ARRAY[
                'company_id','constraints','executable_plan',
                'governing_canon','host_kind','issuer','mandate_id',
                'ordered_roles','ordered_scope','preserved_live_subject',
                'quoted_instruction','receipt_requirements','recorded_at',
                'requires_additional_founder_go_within_scope',
                'rollback_policy','schema','source_kind','status',
                'terminal_condition'
              ]::TEXT[]
           OR NEW.artifact->>'status'<>'active'
           OR NEW.artifact->>'issuer'<>'__CC_SUITE_AUTHORITY_ISSUER__'
           OR NEW.artifact->>'source_kind'<>'direct_founder_instruction'
           OR btrim(COALESCE(NEW.artifact->>'quoted_instruction',''))=''
           OR NEW.artifact->>'company_id'<>'__CC_SUITE_COMPANY_ID__'
           OR NEW.artifact->>'host_kind'<>'macbook'
           OR jsonb_typeof(
                NEW.artifact->'requires_additional_founder_go_within_scope'
              )<>'boolean'
           OR (NEW.artifact->>'recorded_at')::TIMESTAMPTZ
              IS DISTINCT FROM NEW.issued_at THEN
            RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
        END IF;

        IF jsonb_typeof(NEW.artifact->'governing_canon')<>'object'
           OR ARRAY(
                SELECT key
                  FROM jsonb_object_keys(
                      NEW.artifact->'governing_canon'
                  ) AS key
                 ORDER BY key
              ) IS DISTINCT FROM ARRAY['path','sha256']::TEXT[]
           OR btrim(COALESCE(
                NEW.artifact#>>'{governing_canon,path}',''
              ))=''
           OR NEW.artifact#>>'{governing_canon,sha256}'
              !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
        END IF;

        IF jsonb_typeof(NEW.artifact->'executable_plan')<>'object'
           OR ARRAY(
                SELECT key
                  FROM jsonb_object_keys(
                      NEW.artifact->'executable_plan'
                  ) AS key
                 ORDER BY key
              ) IS DISTINCT FROM ARRAY[
                'commit_sha','path','tree_sha'
              ]::TEXT[]
           OR btrim(COALESCE(
                NEW.artifact#>>'{executable_plan,path}',''
              ))=''
           OR NEW.artifact#>>'{executable_plan,commit_sha}'
              !~ '^[0-9a-f]{40}$'
           OR NEW.artifact#>>'{executable_plan,tree_sha}'
              !~ '^[0-9a-f]{40}$' THEN
            RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
        END IF;

        IF jsonb_typeof(NEW.artifact->'preserved_live_subject')<>'object'
           OR ARRAY(
                SELECT key
                  FROM jsonb_object_keys(
                      NEW.artifact->'preserved_live_subject'
                  ) AS key
                 ORDER BY key
              ) IS DISTINCT FROM ARRAY[
                'admission_epoch','deployment_id','package_sha256',
                'preservation_rule','process_id'
              ]::TEXT[]
           OR NEW.artifact#>>'{preserved_live_subject,deployment_id}'
              <>'__CC_SUITE_SCRIBE_DEPLOYMENT_ID__'
           OR NEW.artifact#>>'{preserved_live_subject,package_sha256}'
              !~ '^[0-9a-f]{64}$'
           OR (NEW.artifact#>>'{preserved_live_subject,admission_epoch}')::BIGINT
              <=0
           OR (NEW.artifact#>>'{preserved_live_subject,process_id}')::BIGINT
              <=1
           OR btrim(COALESCE(
                NEW.artifact#>>'{preserved_live_subject,preservation_rule}',''
              ))='' THEN
            RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
        END IF;

        IF jsonb_typeof(NEW.artifact->'ordered_roles')<>'array'
           OR jsonb_array_length(NEW.artifact->'ordered_roles')=0
           OR EXISTS (
                SELECT 1
                  FROM jsonb_array_elements(
                      NEW.artifact->'ordered_roles'
                  ) AS item
                 WHERE jsonb_typeof(item)<>'object'
              ) THEN
            RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
        END IF;
        IF EXISTS (
            SELECT 1
              FROM jsonb_array_elements(
                  NEW.artifact->'ordered_roles'
              ) AS item
             WHERE ARRAY(
                    SELECT key FROM jsonb_object_keys(item) AS key
                     ORDER BY key
                   ) IS DISTINCT FROM ARRAY[
                    'deployment_id','role_type','worker_id'
                   ]::TEXT[]
                OR jsonb_typeof(item->'role_type')<>'string'
                OR jsonb_typeof(item->'deployment_id')<>'string'
                OR jsonb_typeof(item->'worker_id')<>'string'
                OR item->>'role_type' NOT IN (
                    'cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo',
                    'ccgo','ccco'
                )
                OR btrim(item->>'deployment_id')=''
                OR btrim(item->>'deployment_id')<>item->>'deployment_id'
        ) THEN
            RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
        END IF;
        IF (
            SELECT count(DISTINCT item->>'role_type')
              FROM jsonb_array_elements(NEW.artifact->'ordered_roles') AS item
        )<>jsonb_array_length(NEW.artifact->'ordered_roles')
        OR (
            SELECT count(DISTINCT item->>'deployment_id')
              FROM jsonb_array_elements(NEW.artifact->'ordered_roles') AS item
        )<>jsonb_array_length(NEW.artifact->'ordered_roles')
        OR (
            SELECT count(DISTINCT (item->>'worker_id')::UUID)
              FROM jsonb_array_elements(NEW.artifact->'ordered_roles') AS item
        )<>jsonb_array_length(NEW.artifact->'ordered_roles') THEN
            RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
        END IF;

        FOREACH array_field IN ARRAY ARRAY[
            'ordered_scope','constraints','receipt_requirements'
        ]::TEXT[] LOOP
            IF jsonb_typeof(NEW.artifact->array_field)<>'array'
               OR jsonb_array_length(NEW.artifact->array_field)=0
               OR EXISTS (
                    SELECT 1
                      FROM jsonb_array_elements(
                          NEW.artifact->array_field
                      ) AS item
                     WHERE jsonb_typeof(item)<>'string'
                        OR btrim(item#>>'{}')=''
               )
               OR (
                    SELECT count(DISTINCT item#>>'{}')
                      FROM jsonb_array_elements(
                          NEW.artifact->array_field
                      ) AS item
                  )<>jsonb_array_length(NEW.artifact->array_field) THEN
                RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
            END IF;
        END LOOP;

        IF jsonb_typeof(NEW.artifact->'terminal_condition')<>'object'
           OR ARRAY(
                SELECT key
                  FROM jsonb_object_keys(
                      NEW.artifact->'terminal_condition'
                  ) AS key
                 ORDER BY key
              ) IS DISTINCT FROM ARRAY[
                'authorization_valid_after_terminal','completion_rule',
                'recovery_only_contract','terminal_status'
              ]::TEXT[]
           OR btrim(COALESCE(
                NEW.artifact#>>'{terminal_condition,completion_rule}',''
              ))=''
           OR NEW.artifact#>>'{terminal_condition,terminal_status}'
              <>'completed'
           OR NEW.artifact#>'{terminal_condition,authorization_valid_after_terminal}'
              IS DISTINCT FROM 'false'::JSONB
           OR jsonb_typeof(
                NEW.artifact#>'{terminal_condition,recovery_only_contract}'
              )<>'object'
           OR ARRAY(
                SELECT key
                  FROM jsonb_object_keys(
                      NEW.artifact#>'{terminal_condition,recovery_only_contract}'
                  ) AS key
                 ORDER BY key
              ) IS DISTINCT FROM ARRAY[
                'monotonic_epochs_only','same_authority_root',
                'same_deployment','same_host','same_release','same_role'
              ]::TEXT[]
           OR EXISTS (
                SELECT 1
                  FROM jsonb_each(
                      NEW.artifact#>'{terminal_condition,recovery_only_contract}'
                  ) AS axis
                 WHERE axis.value IS DISTINCT FROM 'true'::JSONB
              ) THEN
            RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
        END IF;

        IF jsonb_typeof(NEW.artifact->'rollback_policy')<>'object'
           OR ARRAY(
                SELECT key
                  FROM jsonb_object_keys(
                      NEW.artifact->'rollback_policy'
                  ) AS key
                 ORDER BY key
              ) IS DISTINCT FROM ARRAY[
                'fence_order','per_role_target','proof_required'
              ]::TEXT[]
           OR EXISTS (
                SELECT 1
                  FROM jsonb_each(
                      NEW.artifact->'rollback_policy'
                  ) AS policy
                 WHERE jsonb_typeof(policy.value)<>'string'
                    OR btrim(policy.value#>>'{}')=''
              ) THEN
            RAISE EXCEPTION 'founder activation mandate artifact is incomplete';
        END IF;
    ELSE
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
