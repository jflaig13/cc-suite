-- Internal Mise Company Workspace.
--
-- This is deliberately not a customer tenant module.  It has its own
-- principal map, RLS identity function, tables, lifecycle, review receipts,
-- and grants.  No restaurant principal can address this state.

CREATE TABLE IF NOT EXISTS kernel_company_principals (
    company_id  TEXT PRIMARY KEY CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    db_role     NAME NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
REVOKE ALL ON kernel_company_principals FROM PUBLIC;

CREATE TABLE IF NOT EXISTS kernel_company_reviewer_principals (
    company_id          TEXT PRIMARY KEY CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    reviewer_worker_id  UUID NOT NULL UNIQUE,
    db_role             NAME NOT NULL UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (reviewer_worker_id,company_id)
        REFERENCES kernel_runtime_workers (id,tenant_id)
);
REVOKE ALL ON kernel_company_reviewer_principals FROM PUBLIC;

-- Deterministic persistence identity used only after the Company Workspace
-- has verified the CCPO worker's Ed25519 signature.  The reviewer principal
-- can retrieve its registered public key but cannot mint review rows.
CREATE TABLE IF NOT EXISTS kernel_company_review_attestor_principals (
    company_id  TEXT PRIMARY KEY CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    db_role     NAME NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
REVOKE ALL ON kernel_company_review_attestor_principals FROM PUBLIC;

-- A separate signer principal can ask the database to bind a verified review
-- to the database-held secret, but it cannot read that secret or persist a
-- receipt.  The attestor can persist a receipt but cannot mint this MAC.
CREATE TABLE IF NOT EXISTS kernel_company_review_mac_signer_principals (
    company_id  TEXT PRIMARY KEY CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    db_role     NAME NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
REVOKE ALL ON kernel_company_review_mac_signer_principals FROM PUBLIC;

CREATE TABLE IF NOT EXISTS kernel_company_review_hmac_keys (
    company_id  TEXT PRIMARY KEY CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    secret_key  BYTEA NOT NULL CHECK (octet_length(secret_key) = 32),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
REVOKE ALL ON kernel_company_review_hmac_keys FROM PUBLIC;
INSERT INTO kernel_company_review_hmac_keys (company_id,secret_key)
VALUES (
    '__CC_SUITE_COMPANY_ID__',
    decode(
        replace(gen_random_uuid()::TEXT,'-','') ||
        replace(gen_random_uuid()::TEXT,'-',''),
        'hex'
    )
)
ON CONFLICT (company_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS kernel_company_worker_principals (
    worker_id   UUID PRIMARY KEY,
    company_id  TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    role_type   TEXT NOT NULL CHECK (role_type IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco'
    )),
    db_role     NAME NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id,role_type),
    UNIQUE (worker_id,company_id,role_type),
    FOREIGN KEY (worker_id,company_id)
        REFERENCES kernel_runtime_workers (id,tenant_id)
);
REVOKE ALL ON kernel_company_worker_principals FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_current_company()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT company_id FROM (
        SELECT company_id
          FROM public.kernel_company_principals
         WHERE db_role = session_user::name
        UNION ALL
        SELECT company_id
          FROM public.kernel_company_reviewer_principals
         WHERE db_role = session_user::name
        UNION ALL
        SELECT company_id
          FROM public.kernel_company_review_attestor_principals
         WHERE db_role = session_user::name
        UNION ALL
        SELECT company_id
          FROM public.kernel_company_review_mac_signer_principals
         WHERE db_role = session_user::name
        UNION ALL
        SELECT company_id
          FROM public.kernel_company_worker_principals
         WHERE db_role = session_user::name
    ) company_scope
    LIMIT 1
$$;
REVOKE ALL ON FUNCTION kernel_current_company() FROM PUBLIC;

CREATE TABLE IF NOT EXISTS kernel_company_objectives (
    id                   UUID PRIMARY KEY,
    company_id           TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    title                TEXT NOT NULL CHECK (btrim(title) <> ''),
    description          TEXT NOT NULL DEFAULT '',
    owner_role           TEXT NOT NULL CHECK (owner_role IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco'
    )),
    priority             INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    state                TEXT NOT NULL DEFAULT 'queued' CHECK (state IN (
        'queued','active','needs_input','ready_for_review','held','completed','cancelled'
    )),
    work_subject_sha256  TEXT CHECK (work_subject_sha256 ~ '^[0-9a-f]{64}$'),
    due_at               TIMESTAMPTZ,
    version              BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at         TIMESTAMPTZ,
    UNIQUE (id, company_id),
    CHECK ((state = 'completed') = (completed_at IS NOT NULL)),
    CHECK (state <> 'ready_for_review' OR work_subject_sha256 IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_kernel_company_objectives_queue
    ON kernel_company_objectives (company_id,state,priority DESC,updated_at);

CREATE TABLE IF NOT EXISTS kernel_company_objective_events (
    id            UUID PRIMARY KEY,
    company_id    TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    objective_id  UUID NOT NULL,
    event_seq     BIGINT NOT NULL CHECK (event_seq > 0),
    event_kind    TEXT NOT NULL CHECK (event_kind IN (
        'created','transition','schedule','handoff','status','decision',
        'performance','receipt','legacy_import'
    )),
    actor_role    TEXT NOT NULL CHECK (actor_role IN (
        'founder','scribe','cos','ccto','ccpo','ccde','ccro','ccfo',
        'ccmo','cclo','ccgo','ccco'
    )),
    actor_worker_id UUID,
    payload       JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (objective_id,event_seq),
    FOREIGN KEY (objective_id,company_id)
        REFERENCES kernel_company_objectives (id,company_id),
    FOREIGN KEY (actor_worker_id,company_id)
        REFERENCES kernel_runtime_workers (id,tenant_id),
    CHECK (
        (actor_role = 'founder' AND actor_worker_id IS NULL)
        OR (actor_role <> 'founder' AND actor_worker_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_kernel_company_events_recent
    ON kernel_company_objective_events (company_id,recorded_at DESC,id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_company_one_event_per_review
    ON kernel_company_objective_events ((payload->>'receipt_id'))
    WHERE event_kind='receipt';
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_company_one_legacy_source_binding
    ON kernel_company_objective_events ((payload->>'source_ref'),(payload->>'source_sha256'))
    WHERE event_kind='legacy_import';

CREATE TABLE IF NOT EXISTS kernel_company_review_receipts (
    id                       UUID PRIMARY KEY,
    company_id               TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    objective_id             UUID NOT NULL,
    reviewer_worker_id       UUID NOT NULL,
    reviewer_role            TEXT NOT NULL CHECK (reviewer_role = 'ccpo'),
    model                    TEXT NOT NULL CHECK (model IN ('claude-fable-5','claude-fable-5-1')),
    effort                   TEXT NOT NULL CHECK (effort IN ('medium','high')),
    subject_sha256           TEXT NOT NULL CHECK (subject_sha256 ~ '^[0-9a-f]{64}$'),
    verdict                  TEXT NOT NULL CHECK (verdict IN ('CLEAN','CHANGES_REQUIRED')),
    result_uuid              UUID NOT NULL UNIQUE,
    session_id               UUID NOT NULL UNIQUE,
    review_output_sha256     TEXT NOT NULL CHECK (review_output_sha256 ~ '^[0-9a-f]{64}$'),
    signature                BYTEA NOT NULL CHECK (octet_length(signature) > 0),
    verification_mac         BYTEA,
    signature_attestor       NAME,
    signature_verified_at    TIMESTAMPTZ,
    issued_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id,company_id),
    FOREIGN KEY (objective_id,company_id)
        REFERENCES kernel_company_objectives (id,company_id),
    FOREIGN KEY (reviewer_worker_id,company_id)
        REFERENCES kernel_runtime_workers (id,tenant_id)
);
ALTER TABLE kernel_company_review_receipts
    ADD COLUMN IF NOT EXISTS signature_attestor NAME;
ALTER TABLE kernel_company_review_receipts
    ADD COLUMN IF NOT EXISTS signature_verified_at TIMESTAMPTZ;
ALTER TABLE kernel_company_review_receipts
    ADD COLUMN IF NOT EXISTS verification_mac BYTEA;
CREATE INDEX IF NOT EXISTS idx_kernel_company_reviews_subject
    ON kernel_company_review_receipts
        (company_id,objective_id,subject_sha256,issued_at DESC,id);

CREATE OR REPLACE FUNCTION kernel_company_reviewer_public_key(p_worker_id UUID)
RETURNS BYTEA
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT worker.signing_public_key
      FROM public.kernel_runtime_workers worker
      JOIN public.kernel_tenant_fleet_manifests manifest
        ON manifest.id=worker.manifest_id AND manifest.tenant_id=worker.tenant_id
     JOIN public.kernel_agent_type_releases release
        ON release.release_sha256=worker.agent_release_sha256
     WHERE public.kernel_current_company()='__CC_SUITE_COMPANY_ID__'
       AND worker.id=p_worker_id AND worker.tenant_id='__CC_SUITE_COMPANY_ID__'
       AND worker.fleet_type='company' AND worker.role_type='ccpo'
       AND worker.status='active' AND worker.model_route IN ('claude-fable-5','claude-fable-5-1')
       AND manifest.status='active' AND release.can_review
       AND EXISTS (
            SELECT 1 FROM public.kernel_company_reviewer_principals reviewer
             WHERE reviewer.company_id=worker.tenant_id
               AND reviewer.reviewer_worker_id=worker.id
               AND reviewer.db_role=session_user::name
       )
$$;
REVOKE ALL ON FUNCTION kernel_company_reviewer_public_key(UUID) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_hmac_sha256(p_data BYTEA,p_key BYTEA)
RETURNS BYTEA
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog
AS $function$
DECLARE
    normalized_key BYTEA;
    key_block BYTEA;
    inner_pad BYTEA;
    outer_pad BYTEA;
    byte_index INTEGER;
BEGIN
    normalized_key := CASE
        WHEN octet_length(p_key) > 64 THEN sha256(p_key)
        ELSE p_key
    END;
    key_block := normalized_key || decode(
        repeat('00',64-octet_length(normalized_key)),'hex'
    );
    inner_pad := key_block;
    outer_pad := key_block;
    FOR byte_index IN 0..63 LOOP
        inner_pad := set_byte(
            inner_pad,byte_index,get_byte(key_block,byte_index) # 54
        );
        outer_pad := set_byte(
            outer_pad,byte_index,get_byte(key_block,byte_index) # 92
        );
    END LOOP;
    RETURN sha256(outer_pad || sha256(inner_pad || p_data));
END
$function$;
REVOKE ALL ON FUNCTION kernel_hmac_sha256(BYTEA,BYTEA) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_company_review_attestation_payload(
    p_company_id TEXT,p_objective_id UUID,p_reviewer_worker_id UUID,
    p_effort TEXT,p_subject_sha256 TEXT,p_verdict TEXT,
    p_result_uuid UUID,p_session_id UUID,p_review_output_sha256 TEXT,
    p_signature BYTEA
)
RETURNS BYTEA
LANGUAGE sql
STABLE
STRICT
SET search_path = pg_catalog
AS $function$
    SELECT convert_to(
        concat_ws(
            '|','review-attestation-v1',p_company_id,p_objective_id::TEXT,
            p_reviewer_worker_id::TEXT,'ccpo',
            (SELECT model_route FROM public.kernel_runtime_workers
              WHERE id=p_reviewer_worker_id AND tenant_id=p_company_id),p_effort,
            p_subject_sha256,p_verdict,p_result_uuid::TEXT,p_session_id::TEXT,
            p_review_output_sha256,encode(sha256(p_signature),'hex')
        ),
        'UTF8'
    )
$function$;
REVOKE ALL ON FUNCTION kernel_company_review_attestation_payload(
    TEXT,UUID,UUID,TEXT,TEXT,TEXT,UUID,UUID,TEXT,BYTEA
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_company_review_expected_mac(
    p_company_id TEXT,p_objective_id UUID,p_reviewer_worker_id UUID,
    p_effort TEXT,p_subject_sha256 TEXT,p_verdict TEXT,
    p_result_uuid UUID,p_session_id UUID,p_review_output_sha256 TEXT,
    p_signature BYTEA
)
RETURNS BYTEA
LANGUAGE plpgsql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    hmac_key BYTEA;
BEGIN
    SELECT secret_key INTO hmac_key
      FROM public.kernel_company_review_hmac_keys
     WHERE company_id=p_company_id;
    IF hmac_key IS NULL THEN
        RAISE EXCEPTION 'Company review HMAC key is unavailable';
    END IF;
    RETURN public.kernel_hmac_sha256(
        public.kernel_company_review_attestation_payload(
            p_company_id,p_objective_id,p_reviewer_worker_id,p_effort,
            p_subject_sha256,p_verdict,p_result_uuid,p_session_id,
            p_review_output_sha256,p_signature
        ),
        hmac_key
    );
END
$function$;
REVOKE ALL ON FUNCTION kernel_company_review_expected_mac(
    TEXT,UUID,UUID,TEXT,TEXT,TEXT,UUID,UUID,TEXT,BYTEA
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION kernel_company_review_mac(
    p_company_id TEXT,p_objective_id UUID,p_reviewer_worker_id UUID,
    p_effort TEXT,p_subject_sha256 TEXT,p_verdict TEXT,
    p_result_uuid UUID,p_session_id UUID,p_review_output_sha256 TEXT,
    p_signature BYTEA
)
RETURNS BYTEA
LANGUAGE plpgsql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF p_company_id <> '__CC_SUITE_COMPANY_ID__' OR NOT EXISTS (
        SELECT 1 FROM public.kernel_company_review_mac_signer_principals signer
         WHERE signer.company_id=p_company_id
           AND signer.db_role=session_user::name
    ) THEN
        RAISE EXCEPTION 'Company review MAC requires the isolated signer principal';
    END IF;
    RETURN public.kernel_company_review_expected_mac(
        p_company_id,p_objective_id,p_reviewer_worker_id,p_effort,
        p_subject_sha256,p_verdict,p_result_uuid,p_session_id,
        p_review_output_sha256,p_signature
    );
END
$function$;
REVOKE ALL ON FUNCTION kernel_company_review_mac(
    TEXT,UUID,UUID,TEXT,TEXT,TEXT,UUID,UUID,TEXT,BYTEA
) FROM PUBLIC;

ALTER TABLE kernel_company_objectives ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_objectives FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS kernel_company_scope ON kernel_company_objectives;
CREATE POLICY kernel_company_scope ON kernel_company_objectives
    USING (company_id = kernel_current_company())
    WITH CHECK (company_id = kernel_current_company());

ALTER TABLE kernel_company_objective_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_objective_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS kernel_company_scope ON kernel_company_objective_events;
CREATE POLICY kernel_company_scope ON kernel_company_objective_events
    USING (company_id = kernel_current_company())
    WITH CHECK (company_id = kernel_current_company());

ALTER TABLE kernel_company_review_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_review_receipts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS kernel_company_scope ON kernel_company_review_receipts;
CREATE POLICY kernel_company_scope ON kernel_company_review_receipts
    USING (company_id = kernel_current_company())
    WITH CHECK (company_id = kernel_current_company());

CREATE OR REPLACE FUNCTION kernel_validate_company_objective()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $function$
DECLARE
    allowed_from TEXT[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
        NEW.updated_at := NEW.created_at;
        NEW.version := 1;
        NEW.completed_at := NULL;
        IF NEW.state <> 'queued' THEN
            RAISE EXCEPTION 'company objective must start queued';
        END IF;
        IF NOT EXISTS (
            SELECT 1
              FROM kernel_tenant_fleet_manifest_roles role
              JOIN kernel_tenant_fleet_manifests manifest
                ON manifest.id=role.manifest_id AND manifest.tenant_id=role.tenant_id
             WHERE manifest.tenant_id=NEW.company_id
               AND manifest.fleet_type='company' AND manifest.status='active'
               AND role.role_type=NEW.owner_role
        ) THEN
            RAISE EXCEPTION 'company objective owner is outside the active Company Fleet';
        END IF;
        RETURN NEW;
    END IF;
    IF (
        NEW.id,NEW.company_id,NEW.title,NEW.description,NEW.owner_role,
        NEW.priority,NEW.due_at,NEW.created_at
    ) IS DISTINCT FROM (
        OLD.id,OLD.company_id,OLD.title,OLD.description,OLD.owner_role,
        OLD.priority,OLD.due_at,OLD.created_at
    ) THEN
        RAISE EXCEPTION 'company objective identity and assignment are immutable';
    END IF;
    IF OLD.state IN ('completed','cancelled') THEN
        RAISE EXCEPTION 'terminal company objective is immutable';
    END IF;
    allowed_from := CASE OLD.state
        WHEN 'queued' THEN ARRAY['active','held','cancelled']
        WHEN 'active' THEN ARRAY['needs_input','ready_for_review','held','cancelled']
        WHEN 'needs_input' THEN ARRAY['active','held','cancelled']
        WHEN 'ready_for_review' THEN ARRAY['active','completed','held','cancelled']
        WHEN 'held' THEN ARRAY['active','cancelled']
        ELSE ARRAY[]::TEXT[]
    END;
    IF NEW.state = OLD.state OR NOT NEW.state = ANY(allowed_from) THEN
        RAISE EXCEPTION 'invalid company objective state transition % -> %',OLD.state,NEW.state;
    END IF;
    IF NEW.work_subject_sha256 IS DISTINCT FROM OLD.work_subject_sha256
       AND NEW.state <> 'ready_for_review' THEN
        RAISE EXCEPTION 'work subject may change only when entering review';
    END IF;
    IF NEW.state='ready_for_review' AND NEW.work_subject_sha256 IS NULL THEN
        RAISE EXCEPTION 'ready-for-review objective requires an exact work subject';
    END IF;
    IF NEW.state='completed' AND NOT EXISTS (
        SELECT 1 FROM kernel_company_review_receipts review
         WHERE review.company_id=NEW.company_id
           AND review.objective_id=NEW.id
           AND review.subject_sha256=NEW.work_subject_sha256
           AND review.verdict='CLEAN'
           AND review.signature_attestor='mise_company_review_attestor'::name
           AND review.signature_verified_at IS NOT NULL
           AND review.verification_mac IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'completion requires an exact-subject CLEAN CCPO Fable review';
    END IF;
    NEW.version := OLD.version + 1;
    NEW.updated_at := now();
    NEW.completed_at := CASE WHEN NEW.state='completed' THEN NEW.updated_at ELSE NULL END;
    RETURN NEW;
END
$function$;
REVOKE ALL ON FUNCTION kernel_validate_company_objective() FROM PUBLIC;
DROP TRIGGER IF EXISTS kernel_company_objective_guard ON kernel_company_objectives;
CREATE TRIGGER kernel_company_objective_guard
BEFORE INSERT OR UPDATE ON kernel_company_objectives
FOR EACH ROW EXECUTE FUNCTION kernel_validate_company_objective();

CREATE OR REPLACE FUNCTION kernel_validate_company_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $function$
DECLARE
    next_seq BIGINT;
BEGIN
    PERFORM 1 FROM kernel_company_objectives
     WHERE id=NEW.objective_id AND company_id=NEW.company_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'company event requires a company objective';
    END IF;
    IF NEW.actor_role='kernel' THEN
        RAISE EXCEPTION 'Kernel company events require a dedicated Kernel principal';
    END IF;
    IF NEW.event_kind IN ('created','transition') AND pg_trigger_depth() <= 1 THEN
        RAISE EXCEPTION 'objective lifecycle events are emitted only by objective triggers';
    END IF;
    IF NEW.actor_worker_id IS NOT NULL AND NEW.event_kind <> 'receipt' AND NOT EXISTS (
        SELECT 1 FROM kernel_company_worker_principals principal
        JOIN kernel_runtime_workers worker
          ON worker.id=principal.worker_id AND worker.tenant_id=principal.company_id
        JOIN kernel_tenant_fleet_manifests manifest
          ON manifest.id=worker.manifest_id AND manifest.tenant_id=worker.tenant_id
         WHERE worker.id=NEW.actor_worker_id AND worker.tenant_id=NEW.company_id
           AND worker.fleet_type='company' AND worker.role_type=NEW.actor_role
           AND worker.status='active' AND manifest.status='active'
           AND principal.role_type=NEW.actor_role
           AND principal.db_role=session_user::name
    ) THEN
        RAISE EXCEPTION 'company event actor must bind its active registered worker identity';
    END IF;
    IF NEW.actor_worker_id IS NULL AND NOT EXISTS (
        SELECT 1 FROM kernel_company_principals
         WHERE company_id=NEW.company_id AND db_role=session_user::name
    ) THEN
        RAISE EXCEPTION 'founder events require the Company Workspace principal';
    END IF;
    IF NEW.event_kind='legacy_import' AND (
        NEW.actor_role <> 'scribe'
        OR NOT (NEW.payload ?& ARRAY['source_ref','source_sha256'])
        OR btrim(NEW.payload->>'source_ref')=''
        OR NOT (NEW.payload->>'source_sha256' ~ '^[0-9a-f]{64}$')
    ) THEN
        RAISE EXCEPTION 'legacy import requires Scribe and an exact source binding';
    END IF;
    IF NEW.event_kind='receipt' AND (
        NEW.actor_role <> 'ccpo'
        OR NOT EXISTS (
            SELECT 1 FROM kernel_company_review_attestor_principals attestor
             WHERE attestor.company_id=NEW.company_id
               AND attestor.db_role=session_user::name
        )
        OR NOT (NEW.payload ?& ARRAY['receipt_id','subject_sha256','verdict','model'])
        OR NOT EXISTS (
            SELECT 1 FROM kernel_company_review_receipts review
             WHERE review.id=(NEW.payload->>'receipt_id')::UUID
               AND review.company_id=NEW.company_id
               AND review.objective_id=NEW.objective_id
               AND review.reviewer_worker_id=NEW.actor_worker_id
               AND review.subject_sha256=NEW.payload->>'subject_sha256'
               AND review.verdict=NEW.payload->>'verdict'
               AND review.model=NEW.payload->>'model'
        )
    ) THEN
        RAISE EXCEPTION 'review receipt event must derive from its exact formal review row';
    END IF;
    SELECT COALESCE(max(event_seq),0)+1 INTO next_seq
      FROM kernel_company_objective_events
     WHERE objective_id=NEW.objective_id;
    NEW.event_seq := next_seq;
    NEW.recorded_at := now();
    RETURN NEW;
END
$function$;
REVOKE ALL ON FUNCTION kernel_validate_company_event() FROM PUBLIC;
DROP TRIGGER IF EXISTS kernel_company_event_guard ON kernel_company_objective_events;
CREATE TRIGGER kernel_company_event_guard
BEFORE INSERT ON kernel_company_objective_events
FOR EACH ROW EXECUTE FUNCTION kernel_validate_company_event();

CREATE OR REPLACE FUNCTION kernel_emit_company_objective_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $function$
DECLARE
    event_actor_role TEXT;
    event_actor_worker UUID;
    event_payload JSONB;
BEGIN
    SELECT role_type,worker_id INTO event_actor_role,event_actor_worker
      FROM kernel_company_worker_principals
     WHERE company_id=NEW.company_id AND db_role=session_user::name;
    IF event_actor_role IS NULL THEN
        IF EXISTS (
            SELECT 1 FROM kernel_company_principals
             WHERE company_id=NEW.company_id AND db_role=session_user::name
        ) THEN
            event_actor_role := 'founder';
            event_actor_worker := NULL;
        ELSE
            RAISE EXCEPTION 'company objective write lacks a registered actor principal';
        END IF;
    END IF;
    IF TG_OP='INSERT' THEN
        event_payload := jsonb_build_object(
            'owner_role',NEW.owner_role,'priority',NEW.priority
        );
    ELSE
        event_payload := jsonb_build_object(
            'from',OLD.state,'to',NEW.state,'version',NEW.version,
            'work_subject_sha256',NEW.work_subject_sha256
        );
    END IF;
    INSERT INTO kernel_company_objective_events (
        id,company_id,objective_id,event_seq,event_kind,actor_role,actor_worker_id,payload
    ) VALUES (
        gen_random_uuid(),NEW.company_id,NEW.id,1,
        CASE WHEN TG_OP='INSERT' THEN 'created' ELSE 'transition' END,
        event_actor_role,event_actor_worker,event_payload
    );
    RETURN NEW;
END
$function$;
REVOKE ALL ON FUNCTION kernel_emit_company_objective_event() FROM PUBLIC;
DROP TRIGGER IF EXISTS kernel_company_objective_event ON kernel_company_objectives;
CREATE TRIGGER kernel_company_objective_event
AFTER INSERT OR UPDATE ON kernel_company_objectives
FOR EACH ROW EXECUTE FUNCTION kernel_emit_company_objective_event();

CREATE OR REPLACE FUNCTION kernel_validate_company_review()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $function$
DECLARE
    objective_subject TEXT;
    expected_mac BYTEA;
BEGIN
    SELECT work_subject_sha256 INTO objective_subject
      FROM kernel_company_objectives
     WHERE id=NEW.objective_id AND company_id=NEW.company_id
       AND state='ready_for_review' FOR UPDATE;
    IF objective_subject IS NULL OR objective_subject <> NEW.subject_sha256 THEN
        RAISE EXCEPTION 'company review must bind the current ready-for-review subject';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM kernel_company_review_attestor_principals
         WHERE company_id=NEW.company_id AND db_role=session_user::name
    ) THEN
        RAISE EXCEPTION 'formal company review persistence requires the deterministic attestor';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM kernel_runtime_workers worker
        JOIN kernel_tenant_fleet_manifests manifest
          ON manifest.id=worker.manifest_id AND manifest.tenant_id=worker.tenant_id
        JOIN kernel_agent_type_releases release
          ON release.release_sha256=worker.agent_release_sha256
         WHERE worker.id=NEW.reviewer_worker_id
           AND worker.tenant_id=NEW.company_id
           AND worker.fleet_type='company' AND worker.role_type='ccpo'
           AND worker.model_route=NEW.model
           AND worker.status='active' AND worker.model_route IN ('claude-fable-5','claude-fable-5-1')
           AND manifest.status='active' AND release.can_review
    ) THEN
        RAISE EXCEPTION 'formal company review requires the active CCPO Fable worker';
    END IF;
    expected_mac := kernel_company_review_expected_mac(
        NEW.company_id,NEW.objective_id,NEW.reviewer_worker_id,NEW.effort,
        NEW.subject_sha256,NEW.verdict,NEW.result_uuid,NEW.session_id,
        NEW.review_output_sha256,NEW.signature
    );
    IF NEW.verification_mac IS NULL OR NEW.verification_mac <> expected_mac THEN
        RAISE EXCEPTION 'formal company review requires an exact verification MAC';
    END IF;
    NEW.reviewer_role := 'ccpo';
    -- NEW.model was checked against the immutable registered reviewer route.
    NEW.signature_attestor := session_user::name;
    NEW.signature_verified_at := now();
    NEW.issued_at := now();
    RETURN NEW;
END
$function$;
REVOKE ALL ON FUNCTION kernel_validate_company_review() FROM PUBLIC;
DROP TRIGGER IF EXISTS kernel_company_review_guard ON kernel_company_review_receipts;
CREATE TRIGGER kernel_company_review_guard
BEFORE INSERT ON kernel_company_review_receipts
FOR EACH ROW EXECUTE FUNCTION kernel_validate_company_review();

CREATE OR REPLACE FUNCTION kernel_emit_company_review_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $function$
BEGIN
    INSERT INTO kernel_company_objective_events (
        id,company_id,objective_id,event_seq,event_kind,actor_role,actor_worker_id,payload
    ) VALUES (
        gen_random_uuid(),NEW.company_id,NEW.objective_id,1,'receipt','ccpo',
        NEW.reviewer_worker_id,
        jsonb_build_object(
            'receipt_id',NEW.id::TEXT,
            'subject_sha256',NEW.subject_sha256,
            'verdict',NEW.verdict,
            'model',NEW.model
        )
    );
    RETURN NEW;
END
$function$;
REVOKE ALL ON FUNCTION kernel_emit_company_review_event() FROM PUBLIC;
DROP TRIGGER IF EXISTS kernel_company_review_event ON kernel_company_review_receipts;
CREATE TRIGGER kernel_company_review_event
AFTER INSERT ON kernel_company_review_receipts
FOR EACH ROW EXECUTE FUNCTION kernel_emit_company_review_event();

CREATE OR REPLACE FUNCTION kernel_reject_company_append_only_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Company Workspace history and review receipts are append-only';
END
$$;
REVOKE ALL ON FUNCTION kernel_reject_company_append_only_mutation() FROM PUBLIC;
DROP TRIGGER IF EXISTS kernel_company_event_immutable ON kernel_company_objective_events;
CREATE TRIGGER kernel_company_event_immutable
BEFORE UPDATE OR DELETE ON kernel_company_objective_events
FOR EACH ROW EXECUTE FUNCTION kernel_reject_company_append_only_mutation();
DROP TRIGGER IF EXISTS kernel_company_event_truncate_guard ON kernel_company_objective_events;
CREATE TRIGGER kernel_company_event_truncate_guard
BEFORE TRUNCATE ON kernel_company_objective_events
FOR EACH STATEMENT EXECUTE FUNCTION kernel_reject_company_append_only_mutation();
DROP TRIGGER IF EXISTS kernel_company_review_immutable ON kernel_company_review_receipts;
CREATE TRIGGER kernel_company_review_immutable
BEFORE UPDATE OR DELETE ON kernel_company_review_receipts
FOR EACH ROW EXECUTE FUNCTION kernel_reject_company_append_only_mutation();
DROP TRIGGER IF EXISTS kernel_company_review_truncate_guard ON kernel_company_review_receipts;
CREATE TRIGGER kernel_company_review_truncate_guard
BEFORE TRUNCATE ON kernel_company_review_receipts
FOR EACH STATEMENT EXECUTE FUNCTION kernel_reject_company_append_only_mutation();
DROP TRIGGER IF EXISTS kernel_company_objective_delete_guard ON kernel_company_objectives;
CREATE TRIGGER kernel_company_objective_delete_guard
BEFORE DELETE OR TRUNCATE ON kernel_company_objectives
FOR EACH STATEMENT EXECUTE FUNCTION kernel_reject_company_append_only_mutation();

REVOKE ALL ON kernel_company_objectives FROM PUBLIC;
REVOKE ALL ON kernel_company_objective_events FROM PUBLIC;
REVOKE ALL ON kernel_company_review_receipts FROM PUBLIC;

-- Retighten every already-provisioned restaurant principal.  Future tenant
-- provisioning repeats this deny in db_isolation.py.
DO $deny_customer_principals$
DECLARE
    principal NAME;
BEGIN
    FOR principal IN SELECT db_role FROM kernel_tenant_principals LOOP
        EXECUTE format('REVOKE ALL ON public.kernel_company_objectives FROM %I',principal);
        EXECUTE format('REVOKE ALL ON public.kernel_company_objective_events FROM %I',principal);
        EXECUTE format('REVOKE ALL ON public.kernel_company_review_receipts FROM %I',principal);
    END LOOP;
END
$deny_customer_principals$;
