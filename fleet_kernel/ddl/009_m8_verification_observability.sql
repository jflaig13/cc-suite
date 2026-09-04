-- M8 Phases 5-6: unavoidable verification and objective observability.

CREATE TABLE IF NOT EXISTS kernel_objective_verification_policy (
    objective_id       UUID PRIMARY KEY REFERENCES objectives(id) ON DELETE CASCADE,
    tenant_id          TEXT NOT NULL,
    subject_family     TEXT NOT NULL,
    enrolled_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (btrim(subject_family) <> '')
);

CREATE TABLE IF NOT EXISTS kernel_verification_obligations (
    id                 UUID PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    objective_id       UUID NOT NULL REFERENCES objectives(id) ON DELETE CASCADE,
    obligation_kind    TEXT NOT NULL,
    required_family    TEXT NOT NULL,
    state              TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','satisfied')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    satisfied_at       TIMESTAMPTZ,
    UNIQUE (tenant_id, objective_id, obligation_kind),
    UNIQUE (id, tenant_id, objective_id),
    CHECK (btrim(obligation_kind) <> '' AND btrim(required_family) <> ''),
    CHECK ((state='open' AND satisfied_at IS NULL) OR
           (state='satisfied' AND satisfied_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS kernel_verification_attestations (
    id                 UUID PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    objective_id       UUID NOT NULL REFERENCES objectives(id) ON DELETE CASCADE,
    obligation_id      UUID NOT NULL UNIQUE,
    verifier_identity  TEXT NOT NULL,
    verifier_family    TEXT NOT NULL,
    subject_family     TEXT NOT NULL,
    verdict            TEXT NOT NULL CHECK (verdict = 'pass'),
    evidence_ref       TEXT NOT NULL,
    evidence_sha256    TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    issued_at          TIMESTAMPTZ NOT NULL,
    signature          BYTEA NOT NULL,
    CHECK (btrim(verifier_identity) <> '' AND btrim(evidence_ref) <> ''),
    CHECK (verifier_family <> subject_family),
    FOREIGN KEY (obligation_id, tenant_id, objective_id)
        REFERENCES kernel_verification_obligations(id, tenant_id, objective_id)
);

CREATE TABLE IF NOT EXISTS kernel_metered_spend (
    id                 UUID PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    objective_id       UUID REFERENCES objectives(id) ON DELETE SET NULL,
    cost_kind          TEXT NOT NULL CHECK (cost_kind IN ('model','tool','retry','verifier')),
    usd_amount         NUMERIC(12,6) NOT NULL CHECK (usd_amount >= 0),
    source_ref         TEXT NOT NULL,
    metered_at         TIMESTAMPTZ NOT NULL,
    CHECK (btrim(source_ref) <> '')
);

CREATE TABLE IF NOT EXISTS kernel_objective_outcomes (
    objective_id          UUID PRIMARY KEY REFERENCES objectives(id) ON DELETE CASCADE,
    tenant_id             TEXT NOT NULL,
    outcome               TEXT NOT NULL,
    reconciliation_status TEXT NOT NULL CHECK (reconciliation_status IN ('pending','matched','mismatch','not_applicable')),
    reconciliation_ref    TEXT NOT NULL DEFAULT '',
    recorded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (btrim(outcome) <> '')
);

CREATE INDEX IF NOT EXISTS idx_kernel_verification_obligations_objective
    ON kernel_verification_obligations (tenant_id, objective_id);
CREATE INDEX IF NOT EXISTS idx_kernel_metered_spend_time
    ON kernel_metered_spend (metered_at, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_metered_spend_source
    ON kernel_metered_spend (tenant_id, source_ref);

CREATE OR REPLACE FUNCTION kernel_guard_obligation_satisfaction()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.state <> 'open' THEN
        RAISE EXCEPTION 'verification obligation must be inserted open'
            USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF (NEW.tenant_id,NEW.objective_id,NEW.obligation_kind,NEW.required_family)
           IS DISTINCT FROM
           (OLD.tenant_id,OLD.objective_id,OLD.obligation_kind,OLD.required_family) THEN
            RAISE EXCEPTION 'verification obligation identity is immutable'
                USING ERRCODE = '23514';
        END IF;
        IF OLD.state='satisfied' AND NEW.state <> 'satisfied' THEN
            RAISE EXCEPTION 'satisfied verification obligation cannot reopen'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.state='satisfied' AND OLD.state <> 'satisfied' AND NOT EXISTS (
            SELECT 1 FROM public.kernel_verification_attestations a
             WHERE a.obligation_id=NEW.id AND a.tenant_id=NEW.tenant_id
               AND a.objective_id=NEW.objective_id AND a.verdict='pass'
        ) THEN
            RAISE EXCEPTION 'verification obligation satisfaction requires PASS attestation'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS kernel_verified_obligation ON kernel_verification_obligations;
CREATE TRIGGER kernel_verified_obligation
BEFORE INSERT OR UPDATE ON kernel_verification_obligations
FOR EACH ROW EXECUTE FUNCTION kernel_guard_obligation_satisfaction();

CREATE OR REPLACE FUNCTION kernel_guard_verified_completion()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF NEW.state = 'done' AND OLD.state IS DISTINCT FROM 'done'
       AND EXISTS (
           SELECT 1 FROM public.kernel_objective_verification_policy p
            WHERE p.objective_id=NEW.id AND p.tenant_id=NEW.tenant_id
       ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.kernel_verification_obligations o
             WHERE o.objective_id=NEW.id AND o.tenant_id=NEW.tenant_id
        ) OR EXISTS (
            SELECT 1 FROM public.kernel_verification_obligations o
             WHERE o.objective_id=NEW.id AND o.tenant_id=NEW.tenant_id
               AND o.state <> 'satisfied'
        ) OR EXISTS (
            SELECT 1 FROM public.kernel_verification_obligations o
             LEFT JOIN public.kernel_verification_attestations a
               ON a.obligation_id=o.id AND a.tenant_id=o.tenant_id
              AND a.objective_id=o.objective_id AND a.verdict='pass'
             WHERE o.objective_id=NEW.id AND o.tenant_id=NEW.tenant_id
               AND a.id IS NULL
        ) THEN
            RAISE EXCEPTION 'objective completion refused: verification obligations unsatisfied'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;

DROP TRIGGER IF EXISTS kernel_verified_completion ON objectives;
CREATE TRIGGER kernel_verified_completion
BEFORE UPDATE OF state ON objectives
FOR EACH ROW EXECUTE FUNCTION kernel_guard_verified_completion();

DO $rls$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'kernel_objective_verification_policy',
        'kernel_verification_obligations',
        'kernel_verification_attestations',
        'kernel_metered_spend',
        'kernel_objective_outcomes'
    ]
    LOOP
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
