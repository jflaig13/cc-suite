-- Forward-only repair for Company-role targets created after DDL 031.
--
-- DDL 031 granted the two generic admission functions only to principals
-- whose eligible targets already existed while that migration was running.
-- Phase-4 targets are inserted later, so the target transition itself must
-- apply the same grants.  Scribe remains on its compatibility functions.

CREATE FUNCTION kernel_grant_company_role_functions_on_target()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    target_db_role NAME;
BEGIN
    IF NEW.role_type='scribe' OR NEW.state<>'eligible' THEN
        RETURN NEW;
    END IF;

    SELECT principal.db_role
      INTO STRICT target_db_role
      FROM kernel_company_worker_principals principal
     WHERE principal.worker_id=NEW.worker_id
       AND principal.company_id=NEW.company_id
       AND principal.role_type=NEW.role_type;

    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION '
        'kernel_bind_company_role_admission(TEXT,UUID,TEXT,JSONB) TO %I',
        target_db_role
    );
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION '
        'kernel_heartbeat_company_role_admission(TEXT,UUID,TEXT,TIMESTAMPTZ) TO %I',
        target_db_role
    );
    RETURN NEW;
END
$function$;
REVOKE ALL ON FUNCTION kernel_grant_company_role_functions_on_target()
    FROM PUBLIC;

CREATE TRIGGER kernel_company_role_target_function_grants
AFTER INSERT OR UPDATE ON kernel_company_role_admission_targets
FOR EACH ROW
WHEN (NEW.role_type<>'scribe' AND NEW.state='eligible')
EXECUTE FUNCTION kernel_grant_company_role_functions_on_target();
