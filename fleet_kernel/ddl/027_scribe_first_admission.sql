-- Remove retired Mini/M5 prerequisites from initial MacBook Scribe admission.
-- The admission still requires the exact Company Fleet worker, manifest,
-- installed package/tree, local process identity, and durable worker session.

DO $migration$
DECLARE
    constraint_name NAME;
BEGIN
    FOR constraint_name IN
        SELECT con.conname
          FROM pg_constraint con
         WHERE con.conrelid='kernel_company_scribe_admissions'::regclass
           AND (
               (
                   con.contype='f'
                   AND con.confrelid='m5_approval_decisions'::regclass
               )
               OR (
                   con.contype='c'
                   AND pg_get_constraintdef(con.oid) LIKE '%authority_mode%'
               )
           )
    LOOP
        EXECUTE format(
            'ALTER TABLE kernel_company_scribe_admissions DROP CONSTRAINT %I',
            constraint_name
        );
    END LOOP;
END
$migration$;

ALTER TABLE kernel_company_scribe_admissions
    ADD CONSTRAINT kernel_company_scribe_authority_mode_check
    CHECK (
        authority_mode IN (
            'initial-local-activation',
            'standing-mandate-recovery'
        )
    ),
    ADD CONSTRAINT kernel_company_scribe_authority_lineage_check
    CHECK (
        (
            authority_mode='initial-local-activation'
            AND authority_root_admission_id=id
            AND prior_admission_id IS NULL
            AND recovery_intent_id IS NULL
            AND prior_rollback_receipt_sha256 IS NULL
            AND restart_intent_sha256 IS NULL
        )
        OR
        (
            authority_mode='standing-mandate-recovery'
            AND authority_root_admission_id<>id
            AND prior_admission_id IS NOT NULL
            AND recovery_intent_id IS NOT NULL
            AND prior_rollback_receipt_sha256 IS NOT NULL
            AND restart_intent_sha256 IS NOT NULL
        )
    );

DROP INDEX IF EXISTS idx_kernel_one_initial_scribe_approval;
DROP INDEX IF EXISTS idx_kernel_one_initial_scribe_admission_identity;
CREATE UNIQUE INDEX idx_kernel_one_initial_scribe_admission_identity
    ON kernel_company_scribe_admissions (company_id,authority_approval_id)
    WHERE authority_mode='initial-local-activation';
