-- M8 structural tenant isolation: database principals beneath application scope.

DO $$
DECLARE
    bootstrap_is_superuser BOOLEAN;
    server_version_num INTEGER := current_setting('server_version_num')::INTEGER;
    stale_member NAME;
BEGIN
    IF server_version_num < 160000 THEN
        RAISE EXCEPTION 'Fleet Kernel requires PostgreSQL 16 or newer';
    END IF;
    SELECT rolsuper INTO bootstrap_is_superuser
      FROM pg_roles WHERE rolname=session_user;
    IF NOT bootstrap_is_superuser AND session_user::TEXT <> 'fleet_kernel_provisioner' THEN
        RAISE EXCEPTION 'managed PostgreSQL bootstrap requires fleet_kernel_provisioner';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname='fleet_kernel_maintenance'
          AND (rolsuper OR rolreplication OR rolbypassrls)
    ) THEN
        RAISE EXCEPTION 'legacy maintenance role is unsafe; a superuser must run ALTER ROLE '
            'fleet_kernel_maintenance NOSUPERUSER NOREPLICATION NOBYPASSRLS and grant '
            'fleet_kernel_provisioner membership WITH ADMIN OPTION before retrying';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fleet_kernel_maintenance') THEN
        CREATE ROLE fleet_kernel_maintenance LOGIN NOINHERIT NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fleet_kernel_telemetry') THEN
        CREATE ROLE fleet_kernel_telemetry LOGIN NOINHERIT NOBYPASSRLS;
    END IF;
    ALTER ROLE fleet_kernel_maintenance LOGIN NOINHERIT NOCREATEDB NOCREATEROLE;
    ALTER ROLE fleet_kernel_telemetry LOGIN NOINHERIT NOCREATEDB NOCREATEROLE;
    IF EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname='fleet_kernel_telemetry'
          AND (rolsuper OR rolreplication OR rolbypassrls)
    ) THEN
        RAISE EXCEPTION 'fleet_kernel_telemetry must be NOSUPERUSER NOREPLICATION NOBYPASSRLS';
    END IF;
    IF NOT bootstrap_is_superuser AND NOT EXISTS (
        SELECT 1
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid=membership.roleid
          JOIN pg_roles member ON member.oid=membership.member
         WHERE granted.rolname='fleet_kernel_maintenance'
           AND member.rolname='fleet_kernel_provisioner'
           AND membership.admin_option
    ) THEN
        RAISE EXCEPTION 'fleet_kernel_provisioner lacks ADMIN OPTION on '
            'fleet_kernel_maintenance; a superuser must grant it WITH ADMIN OPTION';
    END IF;
    FOR stale_member IN
        SELECT member.rolname
          FROM pg_auth_members membership
          JOIN pg_roles granted ON granted.oid=membership.roleid
          JOIN pg_roles member ON member.oid=membership.member
         WHERE granted.rolname='fleet_kernel_maintenance'
           AND member.rolname<>'fleet_kernel_provisioner'
    LOOP
        RAISE EXCEPTION 'stale maintenance member %; revoke fleet_kernel_maintenance '
            'before retrying',stale_member;
    END LOOP;
END $$;

CREATE TABLE IF NOT EXISTS kernel_tenant_principals (
    tenant_id      TEXT PRIMARY KEY,
    db_role        NAME NOT NULL UNIQUE,
    tenant_schema  NAME NOT NULL UNIQUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
REVOKE ALL ON kernel_tenant_principals FROM PUBLIC;

CREATE TABLE IF NOT EXISTS kernel_privileged_access_audit (
    id             UUID PRIMARY KEY,
    principal      NAME NOT NULL DEFAULT session_user,
    objective_id   UUID NOT NULL,
    reason         TEXT NOT NULL,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (principal IN ('fleet_kernel_maintenance', 'fleet_kernel_telemetry'))
);
REVOKE ALL ON kernel_privileged_access_audit FROM PUBLIC;

CREATE TABLE IF NOT EXISTS kernel_message_sequences (
    tenant_id      TEXT NOT NULL,
    sender         TEXT NOT NULL,
    last_sequence  BIGINT NOT NULL CHECK (last_sequence > 0),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, sender)
);

CREATE OR REPLACE VIEW kernel_telemetry_summary
WITH (security_barrier=true)
AS
SELECT tenant_id, count(*)::BIGINT AS objective_count,
       count(*) FILTER (WHERE state='done')::BIGINT AS done_count
  FROM objectives
 GROUP BY tenant_id;
REVOKE ALL ON kernel_telemetry_summary FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_read_telemetry(
    p_objective_id UUID,
    p_reason TEXT
)
RETURNS TABLE (audit_id UUID, summary JSONB)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    access_id UUID := gen_random_uuid();
BEGIN
    IF session_user::text <> 'fleet_kernel_telemetry' THEN
        RAISE EXCEPTION 'telemetry accessor requires fleet_kernel_telemetry session_user'
            USING ERRCODE = '42501';
    END IF;
    IF p_objective_id IS NULL OR btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'telemetry access requires objective and reason'
            USING ERRCODE = '22023';
    END IF;
    -- PostgreSQL server logging is non-transactional. This structured record
    -- survives a caller ROLLBACK, while the table row remains queryable for
    -- ordinary committed access.
    RAISE LOG 'KERNEL_PRIVILEGED_ACCESS %', jsonb_build_object(
        'audit_id', access_id,
        'principal', session_user,
        'objective_id', p_objective_id,
        'reason', p_reason
    )::text;
    INSERT INTO public.kernel_privileged_access_audit
        (id, principal, objective_id, reason)
    VALUES (access_id, session_user, p_objective_id, p_reason);
    RETURN QUERY
    SELECT access_id,
           COALESCE(jsonb_agg(to_jsonb(telemetry_row)), '[]'::jsonb)
      FROM public.kernel_telemetry_summary AS telemetry_row;
END
$function$;
REVOKE ALL ON FUNCTION kernel_read_telemetry(UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kernel_read_telemetry(UUID, TEXT) TO fleet_kernel_telemetry;

CREATE OR REPLACE FUNCTION kernel_current_tenant()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT tenant_id
      FROM public.kernel_tenant_principals
     WHERE db_role = session_user::name
$$;
REVOKE ALL ON FUNCTION kernel_current_tenant() FROM PUBLIC;

DO $rls$
DECLARE
    table_name TEXT;
BEGIN
    FOR table_name IN
        SELECT DISTINCT c.table_name
          FROM information_schema.columns c
          JOIN pg_catalog.pg_class pc ON pc.relname=c.table_name
          JOIN pg_catalog.pg_namespace pn ON pn.oid=pc.relnamespace
         WHERE c.table_schema='public'
           AND pn.nspname='public'
           AND pc.relkind IN ('r','p')
           AND c.column_name='tenant_id'
           AND c.table_name NOT IN (
               'kernel_tenant_principals',
               'kernel_p8_writer_principals',
               'kernel_p8_evaluator_principals'
           )
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS kernel_tenant_scope ON public.%I', table_name);
        EXECUTE format('DROP POLICY IF EXISTS kernel_maintenance_all ON public.%I', table_name);
        EXECUTE format('DROP POLICY IF EXISTS kernel_provisioner_all ON public.%I', table_name);
        EXECUTE format(
            'CREATE POLICY kernel_tenant_scope ON public.%I '
            'USING (tenant_id = kernel_current_tenant()) '
            'WITH CHECK (tenant_id = kernel_current_tenant())',
            table_name
        );
        EXECUTE format(
            'CREATE POLICY kernel_maintenance_all ON public.%I '
            'TO fleet_kernel_maintenance USING (true) WITH CHECK (true)',
            table_name
        );
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fleet_kernel_provisioner') THEN
            EXECUTE format(
                'CREATE POLICY kernel_provisioner_all ON public.%I '
                'TO fleet_kernel_provisioner USING (true) WITH CHECK (true)',
                table_name
            );
        END IF;
        EXECUTE format('REVOKE ALL ON public.%I FROM fleet_kernel_maintenance',table_name);
        EXECUTE format('GRANT SELECT,INSERT ON public.%I TO fleet_kernel_maintenance',table_name);
        IF table_name=ANY(ARRAY[
            'objectives','runs','ownership_claims','approvals','tool_invocations',
            'kernel_leases','event_stream_heads','kernel_outbox',
            'm3_quota_parks','m4_handoff_bindings','m5_dispatch_bindings',
            'm5_fodl_audit_bindings','m5_fodl_documents','m6_spend_bindings',
            'm7_role_queue_bindings'
        ]) THEN
            EXECUTE format('GRANT UPDATE ON public.%I TO fleet_kernel_maintenance',table_name);
        END IF;
        IF table_name=ANY(ARRAY['kernel_leases','m7_role_queue_snapshots']) THEN
            EXECUTE format('GRANT DELETE ON public.%I TO fleet_kernel_maintenance',table_name);
        END IF;
    END LOOP;
END
$rls$;
