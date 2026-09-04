-- EP-100 / Evaluation Plane: domain contracts and state semantics.
--
-- Scope: the *execution-state* half of the Evaluation Plane only —
--   kernel_eval_runs, kernel_eval_case_attempts, kernel_eval_grader_results,
--   kernel_eval_qualifications, kernel_eval_waivers.
-- EvalSuiteDefinition / EvalCaseDefinition / EvalTargetManifest are deliberately
-- absent: directive §5.2 keeps definitions in version-controlled immutable
-- packages, and EP-140 owns that format.  Definitions enter this schema only as
-- (identifier, version, manifest sha256) references.
--
-- This is a generalization of kernel_p8_evaluation_snapshots (ddl/011, ~l.329):
-- that table is the same shape — hash-bound, JSONB result body, restricted
-- verdict vocabulary — hardcoded to the fixed 14-gate P8 metric set over a fixed
-- 90-day window.  Here the fixed window becomes a target fingerprint and the
-- fixed metric set becomes an arbitrary (suite_id, suite_version) pair.
--
-- Grader/reviewer independence reuses the DDL 009 pattern verbatim: a
-- `<>` CHECK between the acting family and the subject family, enforced by the
-- database rather than by application code.
--
-- Failure is never a forged row and never an erased row.  Attempts and grader
-- results are append-only; a failed attempt cannot be excluded from aggregation
-- at all, and an infrastructure ERROR attempt can be excluded only with a
-- recorded reason.  Qualification into a qualifying state is refused by trigger
-- unless the underlying grader results actually support it.
--
-- NOT APPLIED.  This file is the EP-100 frozen contract / schema diff
-- deliverable.  Application follows the normal release path after Gate B.

CREATE TABLE IF NOT EXISTS kernel_eval_runs (
    id                     UUID PRIMARY KEY,
    tenant_id              TEXT NOT NULL,
    schema_version         INTEGER NOT NULL DEFAULT 1 CHECK (schema_version >= 1),
    suite_id               TEXT NOT NULL,
    suite_version          TEXT NOT NULL,
    suite_manifest_sha256  TEXT NOT NULL CHECK (suite_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    target_fingerprint     TEXT NOT NULL CHECK (target_fingerprint ~ '^[0-9a-f]{64}$'),
    environment            TEXT NOT NULL,
    execution_mode         TEXT NOT NULL CHECK (execution_mode IN
                               ('deny_effects','simulated','test_account','shadow','supervised_live')),
    state                  TEXT NOT NULL DEFAULT 'created' CHECK (state IN
                               ('created','target_frozen','ready','running','grading',
                                'review_required','completed','cancelled','error','blocked')),
    actor_identity         TEXT NOT NULL,
    actor_family           TEXT NOT NULL,
    attempt_policy         JSONB NOT NULL,
    recovered_from_run_id  UUID,
    spend_microusd         BIGINT NOT NULL DEFAULT 0 CHECK (spend_microusd >= 0),
    latency_ms             BIGINT NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    provider_error_count   INTEGER NOT NULL DEFAULT 0 CHECK (provider_error_count >= 0),
    tool_error_count       INTEGER NOT NULL DEFAULT 0 CHECK (tool_error_count >= 0),
    evidence_ref           TEXT NOT NULL DEFAULT '',
    evidence_sha256        TEXT CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at             TIMESTAMPTZ,
    heartbeat_at           TIMESTAMPTZ,
    ended_at               TIMESTAMPTZ,
    -- Universal Tenant Isolation Invariant: a blank tenant is missing data,
    -- and missing data fails loudly rather than landing in a shared bucket.
    CHECK (btrim(tenant_id) <> ''),
    UNIQUE (id, tenant_id),
    CHECK (btrim(suite_id) <> '' AND btrim(suite_version) <> ''),
    CHECK (btrim(environment) <> '' AND btrim(actor_identity) <> '' AND btrim(actor_family) <> ''),
    CHECK (recovered_from_run_id IS NULL OR recovered_from_run_id <> id),
    -- A terminal run has an end; a non-terminal run does not claim one.
    CHECK ((state IN ('completed','cancelled','error') AND ended_at IS NOT NULL)
        OR (state NOT IN ('completed','cancelled','error') AND ended_at IS NULL)),
    -- A run that has reached RUNNING has a start.  Naming the post-start states
    -- rather than the pre-start ones is load-bearing: BLOCKED, CANCELLED and
    -- ERROR are all reachable before execution ever begins (a target that will
    -- not freeze, an unmet dependency, an abort from CREATED), and the
    -- transition trigger below permits exactly those edges.
    CHECK (state NOT IN ('running','grading','review_required','completed')
        OR started_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS kernel_eval_case_attempts (
    id                     UUID PRIMARY KEY,
    tenant_id              TEXT NOT NULL,
    run_id                 UUID NOT NULL,
    schema_version         INTEGER NOT NULL DEFAULT 1 CHECK (schema_version >= 1),
    case_id                TEXT NOT NULL,
    case_version           TEXT NOT NULL,
    case_manifest_sha256   TEXT NOT NULL CHECK (case_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    attempt_number         INTEGER NOT NULL CHECK (attempt_number >= 1),
    sampling_config        JSONB NOT NULL,
    input_sha256           TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    fixture_sha256         TEXT NOT NULL CHECK (fixture_sha256 ~ '^[0-9a-f]{64}$'),
    trajectory_ref         TEXT NOT NULL,
    trajectory_sha256      TEXT NOT NULL CHECK (trajectory_sha256 ~ '^[0-9a-f]{64}$'),
    final_output_ref       TEXT NOT NULL DEFAULT '',
    forbidden_effect_attempts INTEGER NOT NULL DEFAULT 0 CHECK (forbidden_effect_attempts >= 0),
    result                 TEXT NOT NULL CHECK (result IN ('pass','fail','error','inconclusive')),
    counted_in_aggregate   BOOLEAN NOT NULL DEFAULT TRUE,
    exclusion_reason       TEXT NOT NULL DEFAULT '',
    retry_of_attempt_id    UUID,
    cost_microusd          BIGINT NOT NULL DEFAULT 0 CHECK (cost_microusd >= 0),
    latency_ms             BIGINT NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    started_at             TIMESTAMPTZ NOT NULL,
    ended_at               TIMESTAMPTZ NOT NULL,
    recorded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Universal Tenant Isolation Invariant: a blank tenant is missing data,
    -- and missing data fails loudly rather than landing in a shared bucket.
    CHECK (btrim(tenant_id) <> ''),
    UNIQUE (id, tenant_id),
    -- Retries append a new attempt number; they never overwrite a prior one.
    UNIQUE (tenant_id, run_id, case_id, attempt_number),
    CHECK (btrim(case_id) <> '' AND btrim(case_version) <> '' AND btrim(trajectory_ref) <> ''),
    CHECK (started_at <= ended_at),
    CHECK (retry_of_attempt_id IS NULL OR retry_of_attempt_id <> id),
    CHECK (attempt_number = 1 OR retry_of_attempt_id IS NOT NULL),
    -- No failed behavioral attempt may be silently excluded from aggregation.
    -- Only an infrastructure ERROR may be excluded, and only with a reason.
    CHECK (counted_in_aggregate
           OR (result = 'error' AND btrim(exclusion_reason) <> '')),
    CHECK (counted_in_aggregate = FALSE OR btrim(exclusion_reason) = ''),
    FOREIGN KEY (run_id, tenant_id) REFERENCES kernel_eval_runs(id, tenant_id),
    FOREIGN KEY (retry_of_attempt_id, tenant_id)
        REFERENCES kernel_eval_case_attempts(id, tenant_id)
);

CREATE TABLE IF NOT EXISTS kernel_eval_grader_results (
    id                     UUID PRIMARY KEY,
    tenant_id              TEXT NOT NULL,
    attempt_id             UUID NOT NULL,
    schema_version         INTEGER NOT NULL DEFAULT 1 CHECK (schema_version >= 1),
    grader_id              TEXT NOT NULL,
    grader_version         TEXT NOT NULL,
    -- Directive §11.1-11.4 vocabulary, plus human adjudication.
    grader_kind            TEXT NOT NULL CHECK (grader_kind IN
                               ('structural','trajectory','outcome','semantic','human')),
    grader_family          TEXT NOT NULL,
    subject_family         TEXT NOT NULL,
    result                 TEXT NOT NULL CHECK (result IN ('pass','fail','error','inconclusive')),
    criticality            TEXT NOT NULL CHECK (criticality IN
                               ('critical','noncritical','advisory')),
    reason                 TEXT NOT NULL,
    evidence_ref           TEXT NOT NULL DEFAULT '',
    evidence_sha256        TEXT CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
    error_detail           TEXT NOT NULL DEFAULT '',
    judge_model_id         TEXT NOT NULL DEFAULT '',
    rubric_id              TEXT NOT NULL DEFAULT '',
    rubric_version         TEXT NOT NULL DEFAULT '',
    rubric_sha256          TEXT CHECK (rubric_sha256 IS NULL OR rubric_sha256 ~ '^[0-9a-f]{64}$'),
    adjudicator_identity   TEXT NOT NULL DEFAULT '',
    adjudicator_family     TEXT NOT NULL DEFAULT '',
    adjudicated_at         TIMESTAMPTZ,
    graded_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Universal Tenant Isolation Invariant: a blank tenant is missing data,
    -- and missing data fails loudly rather than landing in a shared bucket.
    CHECK (btrim(tenant_id) <> ''),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, attempt_id, grader_id, grader_version),
    CHECK (btrim(grader_id) <> '' AND btrim(grader_version) <> ''),
    CHECK (btrim(reason) <> ''),
    -- Evidence binds to grades, not to failures to grade.  A PASS or FAIL is a
    -- claim about the subject's behavior and must be reproducible from secured
    -- evidence (§10.3).  An ERROR is a claim about the harness and an
    -- INCONCLUSIVE is the absence of a claim; requiring an artifact for either
    -- would make the fail-closed `_safe_gate` verdict unrecordable exactly when
    -- the evidence store is the thing that broke — turning a loud durable
    -- failure back into silence.  They carry error_detail and reason instead.
    CHECK (result IN ('error','inconclusive')
        OR (btrim(evidence_ref) <> '' AND evidence_sha256 IS NOT NULL)),
    -- Evidence is either wholly absent or wholly hash-bound, never half: a ref
    -- with no digest is an unverifiable pointer.
    CHECK ((btrim(evidence_ref) = '' AND evidence_sha256 IS NULL)
        OR (btrim(evidence_ref) <> '' AND evidence_sha256 IS NOT NULL)),
    -- DDL 009 kernel_verification_attestations pattern: independence is a
    -- database constraint, not an application convention. Exempts semantic
    -- graders specifically (Gate B pass 2 P2-N5, narrowed by pass 3 P3-N1): a
    -- semantic grader's family derives from its judge model, so judging a
    -- Claude target with a Claude judge — advisory by construction (§4.2,
    -- CHECK below) — must remain recordable. Scoped to grader_kind, not
    -- criticality alone: semantic implies advisory but not the reverse, so a
    -- criticality-only exemption would let any grader kind dodge independence
    -- by merely declaring itself advisory.
    CHECK (grader_kind = 'semantic' OR grader_family <> subject_family),
    -- Gate B pass 3 finding P3-N3: btrim, not `= ''`, so whitespace-only
    -- identities agree with the in-process layers (`strip` / `_nonblank`).
    CHECK (btrim(adjudicator_identity) = '' OR adjudicator_family <> subject_family),
    CHECK ((btrim(adjudicator_identity) = '' AND adjudicated_at IS NULL)
        OR (btrim(adjudicator_identity) <> '' AND adjudicated_at IS NOT NULL)),
    -- A semantic grade is meaningless without its judge and rubric identity.
    CHECK (grader_kind <> 'semantic'
        OR (btrim(judge_model_id) <> '' AND btrim(rubric_id) <> ''
            AND btrim(rubric_version) <> '' AND rubric_sha256 IS NOT NULL)),
    -- §4.2: semantic grading stays advisory until it has a human-reviewed
    -- calibration set, known disagreement behavior, prompt-injection testing,
    -- versioned judge configuration, and an adjudication path.  Until EP-610
    -- delivers those, a judge model structurally cannot fail a release.
    -- Relaxing this is a deliberate migration, not a caller's choice.
    CHECK (grader_kind <> 'semantic' OR criticality = 'advisory'),
    CHECK (result <> 'error' OR btrim(error_detail) <> ''),
    FOREIGN KEY (attempt_id, tenant_id)
        REFERENCES kernel_eval_case_attempts(id, tenant_id)
);

CREATE TABLE IF NOT EXISTS kernel_eval_qualifications (
    id                     UUID PRIMARY KEY,
    tenant_id              TEXT NOT NULL,
    run_id                 UUID NOT NULL,
    schema_version         INTEGER NOT NULL DEFAULT 1 CHECK (schema_version >= 1),
    suite_id               TEXT NOT NULL,
    suite_version          TEXT NOT NULL,
    target_fingerprint     TEXT NOT NULL CHECK (target_fingerprint ~ '^[0-9a-f]{64}$'),
    state                  TEXT NOT NULL DEFAULT 'pending' CHECK (state IN
                               ('pending','advisory_qualified','qualified','rejected',
                                'stale','expired','waived','error')),
    gate_mode              TEXT NOT NULL CHECK (gate_mode IN ('advisory','warning','blocking')),
    inconclusive_policy    TEXT NOT NULL CHECK (inconclusive_policy IN ('block','review')),
    -- Case-level and criticality-level detail is preserved here; the plane never
    -- compresses a qualification into a single score (directive §7.3).
    case_results           JSONB NOT NULL,
    aggregate_metrics      JSONB NOT NULL,
    critical_failure_count INTEGER NOT NULL CHECK (critical_failure_count >= 0),
    error_count            INTEGER NOT NULL CHECK (error_count >= 0),
    inconclusive_count     INTEGER NOT NULL CHECK (inconclusive_count >= 0),
    forbidden_effect_count INTEGER NOT NULL DEFAULT 0 CHECK (forbidden_effect_count >= 0),
    baseline_qualification_id UUID,
    reviewer_identity      TEXT NOT NULL DEFAULT '',
    reviewer_family        TEXT NOT NULL DEFAULT '',
    subject_family         TEXT NOT NULL,
    receipt_ref            TEXT NOT NULL DEFAULT '',
    receipt_sha256         TEXT CHECK (receipt_sha256 IS NULL OR receipt_sha256 ~ '^[0-9a-f]{64}$'),
    snapshot_sha256        TEXT NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    expires_at             TIMESTAMPTZ,
    invalidated_at         TIMESTAMPTZ,
    invalidation_reason    TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at             TIMESTAMPTZ,
    -- Universal Tenant Isolation Invariant: a blank tenant is missing data,
    -- and missing data fails loudly rather than landing in a shared bucket.
    CHECK (btrim(tenant_id) <> ''),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, run_id),
    CHECK (btrim(suite_id) <> '' AND btrim(suite_version) <> '' AND btrim(subject_family) <> ''),
    CHECK (baseline_qualification_id IS NULL OR baseline_qualification_id <> id),
    CHECK (reviewer_identity = '' OR reviewer_family <> subject_family),
    CHECK ((reviewer_identity = '' AND reviewer_family = '')
        OR (reviewer_identity <> '' AND reviewer_family <> '')),
    -- §7.3.1/§7.3.2: a critical failure or an unresolved infrastructure error
    -- prevents qualification.  Thresholds live in aggregate_metrics and cannot
    -- reach past these two counters.
    CHECK (state NOT IN ('qualified','advisory_qualified')
           OR (critical_failure_count = 0 AND error_count = 0)),
    -- §7.3.3: a required INCONCLUSIVE case blocks, or routes to a named reviewer.
    CHECK (state NOT IN ('qualified','advisory_qualified')
           OR inconclusive_count = 0
           OR (inconclusive_policy = 'review' AND reviewer_identity <> '')),
    -- §7.3.5: a forbidden high-risk effect attempt cannot be qualified over.
    CHECK (state NOT IN ('qualified','advisory_qualified') OR forbidden_effect_count = 0),
    -- An advisory gate never produces a full QUALIFIED verdict.
    CHECK (gate_mode <> 'advisory' OR state <> 'qualified'),
    CHECK ((state = 'pending' AND decided_at IS NULL)
        OR (state <> 'pending' AND decided_at IS NOT NULL)),
    CHECK ((state IN ('stale','expired') AND invalidated_at IS NOT NULL
            AND btrim(invalidation_reason) <> '')
        OR (state NOT IN ('stale','expired') AND invalidated_at IS NULL
            AND btrim(invalidation_reason) = '')),
    FOREIGN KEY (run_id, tenant_id) REFERENCES kernel_eval_runs(id, tenant_id),
    FOREIGN KEY (baseline_qualification_id, tenant_id)
        REFERENCES kernel_eval_qualifications(id, tenant_id)
);

-- At most one live qualifying verdict per (tenant, suite, target).  A
-- requalification is a new row, never an edit of the old one.
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_eval_qualifications_live
    ON kernel_eval_qualifications (tenant_id, suite_id, suite_version, target_fingerprint)
    WHERE state IN ('qualified','advisory_qualified');

CREATE INDEX IF NOT EXISTS idx_kernel_eval_runs_target
    ON kernel_eval_runs (tenant_id, target_fingerprint, suite_id);
CREATE INDEX IF NOT EXISTS idx_kernel_eval_case_attempts_run
    ON kernel_eval_case_attempts (tenant_id, run_id, case_id);
CREATE INDEX IF NOT EXISTS idx_kernel_eval_grader_results_attempt
    ON kernel_eval_grader_results (tenant_id, attempt_id);

CREATE TABLE IF NOT EXISTS kernel_eval_waivers (
    id                     UUID PRIMARY KEY,
    tenant_id              TEXT NOT NULL,
    qualification_id       UUID NOT NULL,
    schema_version         INTEGER NOT NULL DEFAULT 1 CHECK (schema_version >= 1),
    target_fingerprint     TEXT NOT NULL CHECK (target_fingerprint ~ '^[0-9a-f]{64}$'),
    -- Exactly one failed requirement, named: suite/case/grader triple.
    failed_suite_id        TEXT NOT NULL,
    failed_case_id         TEXT NOT NULL,
    failed_grader_id       TEXT NOT NULL,
    founder_approval_id    UUID NOT NULL REFERENCES approvals(id),
    approver_identity      TEXT NOT NULL,
    rationale              TEXT NOT NULL,
    evidence_ref           TEXT NOT NULL,
    evidence_sha256        TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    expires_at             TIMESTAMPTZ,
    release_bound_to       TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Universal Tenant Isolation Invariant: a blank tenant is missing data,
    -- and missing data fails loudly rather than landing in a shared bucket.
    CHECK (btrim(tenant_id) <> ''),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, qualification_id, failed_suite_id, failed_case_id, failed_grader_id),
    CHECK (btrim(failed_suite_id) <> '' AND btrim(failed_case_id) <> ''
           AND btrim(failed_grader_id) <> ''),
    CHECK (btrim(approver_identity) <> '' AND btrim(rationale) <> ''
           AND btrim(evidence_ref) <> ''),
    -- Permanent silent waivers are prohibited: every waiver is time-limited or
    -- release-limited, and both bounds may not be absent.
    CHECK (expires_at IS NOT NULL OR btrim(release_bound_to) <> ''),
    FOREIGN KEY (qualification_id, tenant_id)
        REFERENCES kernel_eval_qualifications(id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_kernel_eval_waivers_qualification
    ON kernel_eval_waivers (tenant_id, qualification_id);

CREATE OR REPLACE FUNCTION kernel_eval_refuse_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'evaluation attempt and grader evidence is append-only'
        USING ERRCODE = '23514';
END $$;

CREATE OR REPLACE FUNCTION kernel_eval_guard_run_transition()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    legal BOOLEAN;
BEGIN
    IF (NEW.tenant_id,NEW.suite_id,NEW.suite_version,NEW.suite_manifest_sha256,
        NEW.target_fingerprint,NEW.execution_mode,NEW.actor_identity,
        NEW.actor_family,NEW.attempt_policy,NEW.created_at)
       IS DISTINCT FROM
       (OLD.tenant_id,OLD.suite_id,OLD.suite_version,OLD.suite_manifest_sha256,
        OLD.target_fingerprint,OLD.execution_mode,OLD.actor_identity,
        OLD.actor_family,OLD.attempt_policy,OLD.created_at) THEN
        RAISE EXCEPTION 'eval run identity and frozen target are immutable'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.state IN ('completed','cancelled','error') AND NEW.state <> OLD.state THEN
        RAISE EXCEPTION 'terminal eval run cannot leave its terminal state'
            USING ERRCODE = '23514';
    END IF;
    legal := NEW.state = OLD.state
        OR (OLD.state NOT IN ('completed','cancelled','error')
            AND NEW.state IN ('cancelled','error'))
        OR (OLD.state = 'created'          AND NEW.state IN ('target_frozen','blocked'))
        OR (OLD.state = 'target_frozen'    AND NEW.state IN ('ready','blocked'))
        OR (OLD.state = 'ready'            AND NEW.state IN ('running','blocked'))
        OR (OLD.state = 'running'          AND NEW.state IN ('grading','blocked'))
        OR (OLD.state = 'grading'          AND NEW.state IN ('review_required','completed','blocked'))
        OR (OLD.state = 'review_required'  AND NEW.state IN ('completed','blocked'))
        OR (OLD.state = 'blocked'          AND NEW.state = 'ready');
    IF NOT legal THEN
        RAISE EXCEPTION 'illegal eval run transition % -> %', OLD.state, NEW.state
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION kernel_eval_guard_qualification()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    legal BOOLEAN;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF (NEW.tenant_id,NEW.run_id,NEW.suite_id,NEW.suite_version,
            NEW.target_fingerprint,NEW.subject_family,NEW.snapshot_sha256,
            NEW.created_at)
           IS DISTINCT FROM
           (OLD.tenant_id,OLD.run_id,OLD.suite_id,OLD.suite_version,
            OLD.target_fingerprint,OLD.subject_family,OLD.snapshot_sha256,
            OLD.created_at) THEN
            RAISE EXCEPTION 'qualification identity is immutable'
                USING ERRCODE = '23514';
        END IF;
        legal := NEW.state = OLD.state
            OR (OLD.state = 'pending' AND NEW.state IN
                ('advisory_qualified','qualified','rejected','error'))
            OR (OLD.state IN ('qualified','advisory_qualified','waived')
                AND NEW.state IN ('stale','expired'))
            OR (OLD.state = 'rejected' AND NEW.state IN ('waived','stale'));
        IF NOT legal THEN
            RAISE EXCEPTION 'illegal qualification transition % -> %', OLD.state, NEW.state
                USING ERRCODE = '23514';
        END IF;
    END IF;

    -- A qualifying verdict must be supported by the grader evidence actually on
    -- record for this run.  Absence of evidence is not a pass.
    IF NEW.state IN ('qualified','advisory_qualified') THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.kernel_eval_case_attempts a
             WHERE a.run_id = NEW.run_id AND a.tenant_id = NEW.tenant_id
               AND a.counted_in_aggregate
        ) THEN
            RAISE EXCEPTION 'qualification refused: no counted case attempts on record'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.kernel_eval_grader_results g
              JOIN public.kernel_eval_case_attempts a
                ON a.id = g.attempt_id AND a.tenant_id = g.tenant_id
             WHERE a.run_id = NEW.run_id AND a.tenant_id = NEW.tenant_id
               AND a.counted_in_aggregate
               AND g.criticality = 'critical'
               AND g.result IN ('fail','error')
        ) THEN
            RAISE EXCEPTION 'qualification refused: critical grader failure on record'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.kernel_eval_case_attempts a
             WHERE a.run_id = NEW.run_id AND a.tenant_id = NEW.tenant_id
               AND a.counted_in_aggregate
               AND (a.result = 'error' OR a.forbidden_effect_attempts > 0)
        ) THEN
            RAISE EXCEPTION 'qualification refused: unresolved error or forbidden effect attempt'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    -- WAIVED requires an actual waiver row bound to this qualification.
    IF NEW.state = 'waived' AND NOT EXISTS (
        SELECT 1 FROM public.kernel_eval_waivers w
         WHERE w.qualification_id = NEW.id AND w.tenant_id = NEW.tenant_id
    ) THEN
        RAISE EXCEPTION 'waived qualification requires an approved waiver'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS kernel_eval_run_transition ON kernel_eval_runs;
CREATE TRIGGER kernel_eval_run_transition
BEFORE UPDATE ON kernel_eval_runs
FOR EACH ROW EXECUTE FUNCTION kernel_eval_guard_run_transition();

DROP TRIGGER IF EXISTS kernel_eval_run_no_delete ON kernel_eval_runs;
CREATE TRIGGER kernel_eval_run_no_delete BEFORE DELETE ON kernel_eval_runs
FOR EACH ROW EXECUTE FUNCTION kernel_eval_refuse_mutation();

DROP TRIGGER IF EXISTS kernel_eval_attempt_append_only ON kernel_eval_case_attempts;
CREATE TRIGGER kernel_eval_attempt_append_only
BEFORE UPDATE OR DELETE ON kernel_eval_case_attempts
FOR EACH ROW EXECUTE FUNCTION kernel_eval_refuse_mutation();

DROP TRIGGER IF EXISTS kernel_eval_grader_append_only ON kernel_eval_grader_results;
CREATE TRIGGER kernel_eval_grader_append_only
BEFORE UPDATE OR DELETE ON kernel_eval_grader_results
FOR EACH ROW EXECUTE FUNCTION kernel_eval_refuse_mutation();

DROP TRIGGER IF EXISTS kernel_eval_waiver_append_only ON kernel_eval_waivers;
CREATE TRIGGER kernel_eval_waiver_append_only
BEFORE UPDATE OR DELETE ON kernel_eval_waivers
FOR EACH ROW EXECUTE FUNCTION kernel_eval_refuse_mutation();

DROP TRIGGER IF EXISTS kernel_eval_qualification_guard ON kernel_eval_qualifications;
CREATE TRIGGER kernel_eval_qualification_guard
BEFORE INSERT OR UPDATE ON kernel_eval_qualifications
FOR EACH ROW EXECUTE FUNCTION kernel_eval_guard_qualification();

DROP TRIGGER IF EXISTS kernel_eval_qualification_no_delete ON kernel_eval_qualifications;
CREATE TRIGGER kernel_eval_qualification_no_delete
BEFORE DELETE ON kernel_eval_qualifications
FOR EACH ROW EXECUTE FUNCTION kernel_eval_refuse_mutation();

DO $rls$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'kernel_eval_runs','kernel_eval_case_attempts','kernel_eval_grader_results',
        'kernel_eval_qualifications','kernel_eval_waivers'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS kernel_tenant_scope ON public.%I', table_name);
        EXECUTE format(
            'CREATE POLICY kernel_tenant_scope ON public.%I '
            'USING (tenant_id = kernel_current_tenant()) '
            'WITH CHECK (tenant_id = kernel_current_tenant())',
            table_name
        );
        EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC', table_name);
    END LOOP;
END
$rls$;
