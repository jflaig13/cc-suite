-- 067: Family-scoped effort vocabulary (founder law 2026-08-12).
--
-- 012 originally CHECKed effort as low|medium|high. This amendment widens
-- the column vocabulary to low|medium|high|xhigh|max and installs the
-- family-admission helpers:
--   Fable  (claude-fable-* / *fable*): low|medium|high|xhigh|max
--   Grok   (grok-* / grok-4.6):        low|medium|high|xhigh  (no max)
--   Codex  (codex-* / other):          low|medium|high
--
-- Does not remap role default_model or bump role default_effort.
-- Not in the Company DDL_ORDER deploy chain; do not apply to Company DB
-- without an explicit founder/Adambot GO.

DO $drop$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT t.relname, c.conname, pg_get_constraintdef(c.oid) AS def
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = 'public'
           AND c.contype = 'c'
           AND t.relname IN (
               'kernel_agent_type_releases',
               'kernel_runtime_workers',
               'kernel_worker_sessions'
           )
           AND pg_get_constraintdef(c.oid) ILIKE '%effort%'
    LOOP
        IF rec.def LIKE '%xhigh%' THEN
            CONTINUE;
        END IF;
        EXECUTE format(
            'ALTER TABLE public.%I DROP CONSTRAINT %I', rec.relname, rec.conname
        );
    END LOOP;
END
$drop$;

DO $add$
BEGIN
    IF to_regclass('public.kernel_agent_type_releases') IS NOT NULL
       AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'kernel_agent_type_releases_default_effort_check'
    ) THEN
        ALTER TABLE public.kernel_agent_type_releases
            ADD CONSTRAINT kernel_agent_type_releases_default_effort_check
            CHECK (default_effort IN ('low', 'medium', 'high', 'xhigh', 'max'));
    END IF;
    IF to_regclass('public.kernel_agent_type_releases') IS NOT NULL
       AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'kernel_agent_type_releases_allowed_effort_levels_check'
    ) THEN
        ALTER TABLE public.kernel_agent_type_releases
            ADD CONSTRAINT kernel_agent_type_releases_allowed_effort_levels_check
            CHECK (
                jsonb_typeof(allowed_effort_levels) = 'array'
                AND allowed_effort_levels <@ '["high","low","max","medium","xhigh"]'::jsonb
            );
    END IF;
    IF to_regclass('public.kernel_runtime_workers') IS NOT NULL
       AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'kernel_runtime_workers_effort_level_check'
    ) THEN
        ALTER TABLE public.kernel_runtime_workers
            ADD CONSTRAINT kernel_runtime_workers_effort_level_check
            CHECK (effort_level IN ('low', 'medium', 'high', 'xhigh', 'max'));
    END IF;
    IF to_regclass('public.kernel_worker_sessions') IS NOT NULL
       AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'kernel_worker_sessions_effort_level_check'
    ) THEN
        ALTER TABLE public.kernel_worker_sessions
            ADD CONSTRAINT kernel_worker_sessions_effort_level_check
            CHECK (effort_level IN ('low', 'medium', 'high', 'xhigh', 'max'));
    END IF;
END
$add$;

CREATE OR REPLACE FUNCTION kernel_model_effort_family(model_id TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        WHEN lower(btrim(COALESCE(model_id, ''))) LIKE '%fable%' THEN 'fable'
        WHEN lower(btrim(COALESCE(model_id, ''))) LIKE 'grok%'
          OR lower(btrim(COALESCE(model_id, ''))) LIKE '%-grok-%'
          OR lower(btrim(COALESCE(model_id, ''))) LIKE 'xai-grok%'
          THEN 'grok'
        WHEN lower(btrim(COALESCE(model_id, ''))) LIKE 'codex%' THEN 'codex'
        ELSE 'other'
    END
$$;

CREATE OR REPLACE FUNCTION kernel_admitted_efforts_for_model(model_id TEXT)
RETURNS TEXT[]
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE kernel_model_effort_family(model_id)
        WHEN 'fable' THEN ARRAY['low', 'medium', 'high', 'xhigh', 'max']
        WHEN 'grok' THEN ARRAY['low', 'medium', 'high', 'xhigh']
        ELSE ARRAY['low', 'medium', 'high']
    END
$$;

CREATE OR REPLACE FUNCTION kernel_effort_admitted_for_model(model_id TEXT, effort TEXT)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT effort = ANY (kernel_admitted_efforts_for_model(model_id))
$$;

CREATE OR REPLACE FUNCTION kernel_validate_agent_release_efforts()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    admitted TEXT[];
    effort TEXT;
    family TEXT;
BEGIN
    family := kernel_model_effort_family(NEW.default_model);
    admitted := kernel_admitted_efforts_for_model(NEW.default_model);
    IF NOT (NEW.default_effort = ANY (admitted)) THEN
        RAISE EXCEPTION
            'effort % is not admitted for model family % (%)',
            NEW.default_effort, family, NEW.default_model;
    END IF;
    FOR effort IN SELECT jsonb_array_elements_text(NEW.allowed_effort_levels)
    LOOP
        IF NOT (effort = ANY (admitted)) THEN
            RAISE EXCEPTION
                'effort % is not admitted for model family % (%)',
                effort, family, NEW.default_model;
        END IF;
    END LOOP;
    IF NOT (NEW.allowed_effort_levels ? NEW.default_effort) THEN
        RAISE EXCEPTION 'default effort must be in the release allowed set';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS kernel_agent_release_effort_guard ON kernel_agent_type_releases;
CREATE TRIGGER kernel_agent_release_effort_guard
BEFORE INSERT ON kernel_agent_type_releases
FOR EACH ROW EXECUTE FUNCTION kernel_validate_agent_release_efforts();

CREATE OR REPLACE FUNCTION kernel_reject_effort_outside_model_family()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    model_id TEXT;
    family TEXT;
BEGIN
    IF TG_TABLE_NAME = 'kernel_runtime_workers' THEN
        model_id := NEW.model_route;
    ELSE
        model_id := NEW.actual_model;
    END IF;
    family := kernel_model_effort_family(model_id);
    IF NOT kernel_effort_admitted_for_model(model_id, NEW.effort_level) THEN
        RAISE EXCEPTION
            'effort % is not admitted for model family % (%)',
            NEW.effort_level, family, model_id;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS kernel_runtime_worker_effort_family_guard ON kernel_runtime_workers;
CREATE TRIGGER kernel_runtime_worker_effort_family_guard
BEFORE INSERT OR UPDATE ON kernel_runtime_workers
FOR EACH ROW EXECUTE FUNCTION kernel_reject_effort_outside_model_family();
DROP TRIGGER IF EXISTS kernel_worker_session_effort_family_guard ON kernel_worker_sessions;
CREATE TRIGGER kernel_worker_session_effort_family_guard
BEFORE INSERT OR UPDATE ON kernel_worker_sessions
FOR EACH ROW EXECUTE FUNCTION kernel_reject_effort_outside_model_family();
