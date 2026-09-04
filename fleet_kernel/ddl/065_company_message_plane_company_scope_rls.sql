-- 065: Company message plane tables must satisfy 998 company workspace isolation:
-- ENABLE + FORCE RLS and a policy named kernel_company_scope on company_id.
-- 061 used definer-scope / party-read policy names; 062 v1 created tables with
-- no RLS. Admission/isolation fixtures fail until every company_id table on the
-- message plane carries the standard company scope policy.

DO $rls$
DECLARE
    table_name TEXT;
    tables TEXT[] := ARRAY[
        'kernel_company_message_recipient_sequences',
        'kernel_company_messages',
        'kernel_company_message_send_receipts',
        'kernel_company_message_delivery_attempts',
        'kernel_company_message_consumption_receipts',
        'kernel_company_message_cutover_registry',
        'kernel_company_message_migration_claims',
        'kernel_company_message_obligations',
        'kernel_company_message_commit_certifications',
        'kernel_company_message_retention_receipts'
    ];
BEGIN
    FOREACH table_name IN ARRAY tables LOOP
        IF to_regclass(format('public.%I', table_name)) IS NULL THEN
            CONTINUE;
        END IF;
        IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_attribute pa
              JOIN pg_catalog.pg_class pc ON pc.oid = pa.attrelid
              JOIN pg_catalog.pg_namespace pn ON pn.oid = pc.relnamespace
             WHERE pn.nspname = 'public'
               AND pc.relname = table_name
               AND pa.attname = 'company_id'
               AND NOT pa.attisdropped
        ) THEN
            RAISE EXCEPTION
                '065 expected company_id on public.%', table_name;
        END IF;

        EXECUTE format(
            'ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name
        );
        EXECUTE format(
            'ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', table_name
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS kernel_company_scope ON public.%I',
            table_name
        );
        -- Match 036 admission policies: fleet_kernel_provisioner is the
        -- SECURITY DEFINER owner and is intentionally absent from
        -- kernel_current_company() principal tables. Without this disjunct,
        -- FORCE RLS rejects the first empty-table INSERT into
        -- kernel_company_message_recipient_sequences from kernel_send_*.
        EXECUTE format(
            'CREATE POLICY kernel_company_scope ON public.%I '
            'USING ('
            'session_user = ''fleet_kernel_provisioner''::NAME '
            'OR company_id = kernel_current_company()'
            ') '
            'WITH CHECK ('
            'session_user = ''fleet_kernel_provisioner''::NAME '
            'OR company_id = kernel_current_company()'
            ')',
            table_name
        );
    END LOOP;
END
$rls$;

-- 062 created SECURITY DEFINER RPCs; Postgres defaults EXECUTE to PUBLIC.
-- Company workspace isolation forbids company principals executing definer
-- RPCs except the deliberately allowed set. Revoke PUBLIC; keep provisioner.
DO $fn$
DECLARE
    fn_sig TEXT;
    sigs TEXT[] := ARRAY[
        'kernel_company_message_v1_assert_role(text)',
        'kernel_company_message_v1_resolve_admission(text,uuid,boolean)',
        'kernel_send_company_message_v1(text,text,text,text,text,uuid,boolean,text)',
        'kernel_claim_company_message_v1(text,uuid,boolean,integer)',
        'kernel_consume_company_message_v1(uuid,text,uuid,text,uuid,boolean)',
        'kernel_company_message_receipt_v1(uuid)'
    ];
BEGIN
    FOREACH fn_sig IN ARRAY sigs LOOP
        IF to_regprocedure(fn_sig) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', fn_sig);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fleet_kernel_provisioner') THEN
            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION %s TO fleet_kernel_provisioner',
                fn_sig
            );
        END IF;
    END LOOP;
END
$fn$;
