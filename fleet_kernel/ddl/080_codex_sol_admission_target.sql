-- Permit codex-sol alongside claude-code and grok-cli for Company role
-- admission targets and Scribe admissions. The identity plane maps
-- codex-sol to the CODEX effort family.

ALTER TABLE kernel_company_role_admission_targets
    DROP CONSTRAINT IF EXISTS kernel_company_role_admission_targets_runtime_kind_check;

ALTER TABLE kernel_company_role_admission_targets
    ADD CONSTRAINT kernel_company_role_admission_targets_runtime_kind_check
    CHECK (runtime_kind IN ('claude-code', 'grok-cli', 'codex-sol'));

ALTER TABLE kernel_company_scribe_admissions
    DROP CONSTRAINT IF EXISTS kernel_company_scribe_admissions_runtime_kind_check;

ALTER TABLE kernel_company_scribe_admissions
    ADD CONSTRAINT kernel_company_scribe_admissions_runtime_kind_check
    CHECK (runtime_kind IN ('claude-code', 'grok-cli', 'codex-sol'));
