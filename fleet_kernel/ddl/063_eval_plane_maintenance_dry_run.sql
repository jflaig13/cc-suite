-- Closeout E1: allow fleet_kernel_provisioner to exercise eval tables ONLY for
-- the synthetic maintenance tenant `mise-eval-plane` (FORCE RLS still on).
-- Does not grant access to real restaurant tenant rows.
-- AUTHORIZED: GO CLOSEOUT OMNIBUS E1-E2. NO eval-block-deploy.

DO $e1$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'kernel_eval_runs',
        'kernel_eval_case_attempts',
        'kernel_eval_grader_results',
        'kernel_eval_qualifications',
        'kernel_eval_waivers'
    ] LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS kernel_eval_maintenance_dry_run ON public.%I',
            table_name
        );
        EXECUTE format(
            'CREATE POLICY kernel_eval_maintenance_dry_run ON public.%I
               FOR ALL TO fleet_kernel_provisioner
               USING (tenant_id = %L)
               WITH CHECK (tenant_id = %L)',
            table_name,
            'mise-eval-plane',
            'mise-eval-plane'
        );
    END LOOP;
END
$e1$;
