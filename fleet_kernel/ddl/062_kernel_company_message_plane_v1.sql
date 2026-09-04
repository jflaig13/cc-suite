-- Kernel Company Message Plane v1 (PATH C1 post-r15)
-- AUTHORIZED: founder end-to-end GO Super Grok Heavy; freeze CLEAR + unhold.
--
-- Scope (explicit):
--   * Durable company-scoped task_handoff / agent_direct send → claim → consume
--   * Admission-bound roles (scribe/cos/…/utility)
--   * Shape A fence documented for ANY future file-plane mutator:
--       flock-before-reserve held through finalize (no reserve-then-flock)
--   * Obligation/outbox RECONCILER BRIDGE is CUT from v1 (r15 CRITICAL)
--
-- Non-goals: multi-role big-bang cutover; file-plane quarantine ownership;
--   shipping inert 061 as-is; weakening T0 payroll basis.
--
-- Depends on: pgcrypto (digest), kernel_company_scribe_admissions.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kernel_company_message_recipient_sequences (
    company_id     TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    recipient_role TEXT NOT NULL CHECK (recipient_role IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
        'cclo','ccgo','ccco','utility'
    )),
    last_sequence  BIGINT NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (company_id, recipient_role)
);
REVOKE ALL ON kernel_company_message_recipient_sequences FROM PUBLIC;

CREATE TABLE IF NOT EXISTS kernel_company_messages (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               TEXT NOT NULL DEFAULT '__CC_SUITE_COMPANY_ID__'
                             CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    sender_admission_id      UUID NOT NULL
                             REFERENCES kernel_company_scribe_admissions (id),
    sender_role              TEXT NOT NULL CHECK (sender_role IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
        'cclo','ccgo','ccco','utility'
    )),
    recipient_role           TEXT NOT NULL CHECK (recipient_role IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
        'cclo','ccgo','ccco','utility'
    )),
    recipient_sequence       BIGINT NOT NULL CHECK (recipient_sequence > 0),
    idempotency_key          TEXT NOT NULL
                             CHECK (btrim(idempotency_key) <> ''
                                    AND octet_length(idempotency_key) <= 200),
    message_kind             TEXT NOT NULL CHECK (message_kind IN (
        'agent_direct','task_handoff','audit_request',
        'reply_classification','arbiter_ask'
    )),
    payload_state            TEXT NOT NULL DEFAULT 'present'
                             CHECK (payload_state IN ('present','expired')),
    message_bytes            BYTEA,
    message_octets           INTEGER NOT NULL
                             CHECK (message_octets BETWEEN 1 AND 65536),
    message_sha256           TEXT NOT NULL
                             CHECK (message_sha256 ~ '^[0-9a-f]{64}$'),
    causal_parent_message_id UUID
                             REFERENCES kernel_company_messages (id),
    causal_root_message_id   UUID NOT NULL,
    causal_depth             SMALLINT NOT NULL
                             CHECK (causal_depth BETWEEN 0 AND 4),
    state                    TEXT NOT NULL DEFAULT 'queued'
                             CHECK (state IN (
                                 'queued','leased','consumed','dead_lettered',
                                 'quarantined'
                             )),
    delivery_attempt_count   SMALLINT NOT NULL DEFAULT 0
                             CHECK (delivery_attempt_count BETWEEN 0 AND 5),
    active_attempt_id        UUID,
    active_lease_id          UUID,
    active_lease_expires_at  TIMESTAMPTZ,
    accepted_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    consumed_at              TIMESTAMPTZ,
    dead_lettered_at         TIMESTAMPTZ,
    dead_letter_reason       TEXT,
    quarantined_at           TIMESTAMPTZ,
    quarantine_reason        TEXT,
    provenance               TEXT NOT NULL DEFAULT 'native'
                             CHECK (provenance IN (
                                 'native','legacy-unverified','legacy-verified'
                             )),
    UNIQUE (company_id, sender_role, idempotency_key),
    UNIQUE (company_id, recipient_role, recipient_sequence),
    CHECK (
        (payload_state = 'present'
            AND message_bytes IS NOT NULL
            AND octet_length(message_bytes) BETWEEN 1 AND 65536
            AND message_octets = octet_length(message_bytes)
            AND message_sha256 = encode(digest(message_bytes, 'sha256'), 'hex'))
        OR
        (payload_state = 'expired'
            AND message_bytes IS NULL
            AND state IN ('consumed','dead_lettered','quarantined'))
    ),
    CHECK (
        (causal_depth = 0
            AND causal_parent_message_id IS NULL
            AND causal_root_message_id = id)
        OR
        (causal_depth > 0 AND causal_parent_message_id IS NOT NULL)
    )
);
REVOKE ALL ON kernel_company_messages FROM PUBLIC;

CREATE INDEX IF NOT EXISTS idx_kcm_v1_recipient_claim
    ON kernel_company_messages (recipient_role, recipient_sequence)
    WHERE state IN ('queued', 'leased');
CREATE INDEX IF NOT EXISTS idx_kcm_v1_pair_backlog
    ON kernel_company_messages (sender_role, recipient_role, state);

CREATE TABLE IF NOT EXISTS kernel_company_message_send_receipts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id          TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    message_id          UUID NOT NULL UNIQUE
                        REFERENCES kernel_company_messages (id),
    sender_admission_id UUID NOT NULL
                        REFERENCES kernel_company_scribe_admissions (id),
    sender_role         TEXT NOT NULL,
    recipient_role      TEXT NOT NULL,
    recipient_sequence  BIGINT NOT NULL,
    idempotency_key     TEXT NOT NULL,
    message_sha256      TEXT NOT NULL CHECK (message_sha256 ~ '^[0-9a-f]{64}$'),
    message_octets      INTEGER NOT NULL,
    accepted_at         TIMESTAMPTZ NOT NULL,
    UNIQUE (company_id, sender_role, idempotency_key)
);
REVOKE ALL ON kernel_company_message_send_receipts FROM PUBLIC;

CREATE TABLE IF NOT EXISTS kernel_company_message_delivery_attempts (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id             TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    message_id             UUID NOT NULL
                           REFERENCES kernel_company_messages (id),
    attempt_number         SMALLINT NOT NULL CHECK (attempt_number BETWEEN 1 AND 5),
    recipient_role         TEXT NOT NULL,
    consuming_admission_id UUID NOT NULL
                           REFERENCES kernel_company_scribe_admissions (id),
    lease_id               UUID NOT NULL UNIQUE,
    lease_token_sha256     TEXT NOT NULL CHECK (lease_token_sha256 ~ '^[0-9a-f]{64}$'),
    state                  TEXT NOT NULL DEFAULT 'open'
                           CHECK (state IN ('open','consumed','expired','dead_lettered')),
    leased_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_expires_at       TIMESTAMPTZ NOT NULL,
    closed_at              TIMESTAMPTZ,
    UNIQUE (message_id, attempt_number),
    CHECK (lease_expires_at > leased_at),
    CHECK (
        (state = 'open' AND closed_at IS NULL)
        OR (state <> 'open' AND closed_at IS NOT NULL)
    )
);
REVOKE ALL ON kernel_company_message_delivery_attempts FROM PUBLIC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_kcm_v1_one_open_attempt
    ON kernel_company_message_delivery_attempts (message_id)
    WHERE state = 'open';

CREATE TABLE IF NOT EXISTS kernel_company_message_consumption_receipts (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id             TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    message_id             UUID NOT NULL UNIQUE
                           REFERENCES kernel_company_messages (id),
    delivery_attempt_id    UUID NOT NULL UNIQUE
                           REFERENCES kernel_company_message_delivery_attempts (id),
    consuming_admission_id UUID NOT NULL
                           REFERENCES kernel_company_scribe_admissions (id),
    recipient_role         TEXT NOT NULL,
    message_sha256         TEXT NOT NULL CHECK (message_sha256 ~ '^[0-9a-f]{64}$'),
    message_octets         INTEGER NOT NULL,
    bytes_proof            TEXT NOT NULL CHECK (bytes_proof ~ '^[0-9a-f]{64}$'),
    consumed_at            TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON kernel_company_message_consumption_receipts FROM PUBLIC;

-- Cutover registry (four-state) for migration + one-role cutover
CREATE TABLE IF NOT EXISTS kernel_company_message_cutover_registry (
    company_id     TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    role_type      TEXT NOT NULL CHECK (role_type IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
        'cclo','ccgo','ccco','utility'
    )),
    state          TEXT NOT NULL DEFAULT 'file_bus_only'
                   CHECK (state IN (
                       'file_bus_only','dual_write','kernel_primary','rolled_back'
                   )),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_by     TEXT NOT NULL DEFAULT 'deployer',
    notes          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (company_id, role_type)
);
REVOKE ALL ON kernel_company_message_cutover_registry FROM PUBLIC;

-- Migration claim ledger (legacy channel → kernel)
CREATE TABLE IF NOT EXISTS kernel_company_message_migration_claims (
    claim_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id         TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    source_path        TEXT NOT NULL,
    source_row_sha256  TEXT NOT NULL CHECK (source_row_sha256 ~ '^[0-9a-f]{64}$'),
    kernel_message_id  UUID REFERENCES kernel_company_messages (id),
    state              TEXT NOT NULL DEFAULT 'claimed'
                       CHECK (state IN ('claimed','imported','quarantined','skipped')),
    claimed_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    detail             TEXT NOT NULL DEFAULT '',
    UNIQUE (company_id, source_path, source_row_sha256)
);
REVOKE ALL ON kernel_company_message_migration_claims FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Core RPCs (SECURITY DEFINER; maintenance principal may rehearse)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION kernel_company_message_v1_assert_role(p_role TEXT)
RETURNS VOID
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p_role IS NULL OR p_role NOT IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
        'cclo','ccgo','ccco','utility'
    ) THEN
        RAISE EXCEPTION 'invalid company message role %', p_role
            USING ERRCODE = '22023';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION kernel_company_message_v1_resolve_admission(
    p_role TEXT,
    p_admission_id UUID,
    p_require_live BOOLEAN DEFAULT FALSE
) RETURNS UUID
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_id UUID;
    v_role TEXT;
    v_state TEXT;
BEGIN
    PERFORM kernel_company_message_v1_assert_role(p_role);
    IF p_admission_id IS NOT NULL THEN
        SELECT id, role_type, state INTO v_id, v_role, v_state
          FROM kernel_company_scribe_admissions
         WHERE id = p_admission_id;
        IF v_id IS NULL THEN
            RAISE EXCEPTION 'admission % not found', p_admission_id
                USING ERRCODE = 'P0002';
        END IF;
        IF v_role <> p_role THEN
            RAISE EXCEPTION 'admission role mismatch: expected %, got %', p_role, v_role
                USING ERRCODE = '42501';
        END IF;
        IF p_require_live AND v_state <> 'live' THEN
            RAISE EXCEPTION 'admission % is not live (state=%)', p_admission_id, v_state
                USING ERRCODE = '42501';
        END IF;
        RETURN v_id;
    END IF;
    -- Prefer live; fall back to most recent admission for the role (rehearsal).
    SELECT id INTO v_id
      FROM kernel_company_scribe_admissions
     WHERE role_type = p_role AND state = 'live' AND closed_at IS NULL
     ORDER BY admitted_at DESC
     LIMIT 1;
    IF v_id IS NOT NULL THEN
        RETURN v_id;
    END IF;
    IF p_require_live THEN
        RAISE EXCEPTION 'no live admission for role %', p_role
            USING ERRCODE = '42501';
    END IF;
    SELECT id INTO v_id
      FROM kernel_company_scribe_admissions
     WHERE role_type = p_role
     ORDER BY admitted_at DESC NULLS LAST
     LIMIT 1;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'no admission history for role %', p_role
            USING ERRCODE = 'P0002';
    END IF;
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION kernel_send_company_message_v1(
    p_sender_role TEXT,
    p_recipient_role TEXT,
    p_message_kind TEXT,
    p_payload TEXT,
    p_idempotency_key TEXT,
    p_sender_admission_id UUID DEFAULT NULL,
    p_require_live_admission BOOLEAN DEFAULT FALSE,
    p_provenance TEXT DEFAULT 'native'
) RETURNS TABLE (
    message_id UUID,
    recipient_sequence BIGINT,
    message_sha256 TEXT,
    accepted_at TIMESTAMPTZ,
    status TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_sender_adm UUID;
    v_bytes BYTEA;
    v_octets INTEGER;
    v_sha TEXT;
    v_seq BIGINT;
    v_id UUID;
    v_accepted TIMESTAMPTZ;
    v_existing UUID;
BEGIN
    PERFORM kernel_company_message_v1_assert_role(p_sender_role);
    PERFORM kernel_company_message_v1_assert_role(p_recipient_role);
    IF p_message_kind NOT IN (
        'agent_direct','task_handoff','audit_request',
        'reply_classification','arbiter_ask'
    ) THEN
        RAISE EXCEPTION 'invalid message_kind %', p_message_kind USING ERRCODE = '22023';
    END IF;
    IF p_payload IS NULL OR btrim(p_payload) = '' THEN
        RAISE EXCEPTION 'payload required' USING ERRCODE = '22023';
    END IF;
    IF p_idempotency_key IS NULL OR btrim(p_idempotency_key) = '' THEN
        RAISE EXCEPTION 'idempotency_key required' USING ERRCODE = '22023';
    END IF;
    IF p_provenance NOT IN ('native','legacy-unverified','legacy-verified') THEN
        RAISE EXCEPTION 'invalid provenance' USING ERRCODE = '22023';
    END IF;

    -- Idempotent retry
    SELECT m.id INTO v_existing
      FROM kernel_company_messages m
     WHERE m.company_id = '__CC_SUITE_COMPANY_ID__'
       AND m.sender_role = p_sender_role
       AND m.idempotency_key = p_idempotency_key;
    IF v_existing IS NOT NULL THEN
        RETURN QUERY
        SELECT m.id, m.recipient_sequence, m.message_sha256, m.accepted_at, 'duplicate'::TEXT
          FROM kernel_company_messages m WHERE m.id = v_existing;
        RETURN;
    END IF;

    v_sender_adm := kernel_company_message_v1_resolve_admission(
        p_sender_role, p_sender_admission_id, p_require_live_admission
    );
    v_bytes := convert_to(p_payload, 'UTF8');
    v_octets := octet_length(v_bytes);
    IF v_octets < 1 OR v_octets > 65536 THEN
        RAISE EXCEPTION 'payload size % out of bounds', v_octets USING ERRCODE = '22023';
    END IF;
    v_sha := encode(digest(v_bytes, 'sha256'), 'hex');

    -- Shape A adjacent: sequence update under row lock before insert (single txn)
    INSERT INTO kernel_company_message_recipient_sequences AS s
        (company_id, recipient_role, last_sequence, updated_at)
    VALUES ('__CC_SUITE_COMPANY_ID__', p_recipient_role, 1, clock_timestamp())
    ON CONFLICT (company_id, recipient_role) DO UPDATE
        SET last_sequence = s.last_sequence + 1,
            updated_at = clock_timestamp()
    RETURNING last_sequence INTO v_seq;

    v_id := gen_random_uuid();
    v_accepted := clock_timestamp();

    INSERT INTO kernel_company_messages (
        id, company_id, sender_admission_id, sender_role, recipient_role,
        recipient_sequence, idempotency_key, message_kind, payload_state,
        message_bytes, message_octets, message_sha256,
        causal_parent_message_id, causal_root_message_id, causal_depth,
        state, accepted_at, provenance
    ) VALUES (
        v_id, '__CC_SUITE_COMPANY_ID__', v_sender_adm, p_sender_role, p_recipient_role,
        v_seq, p_idempotency_key, p_message_kind, 'present',
        v_bytes, v_octets, v_sha,
        NULL, v_id, 0,
        'queued', v_accepted, p_provenance
    );

    INSERT INTO kernel_company_message_send_receipts (
        company_id, message_id, sender_admission_id, sender_role, recipient_role,
        recipient_sequence, idempotency_key, message_sha256, message_octets, accepted_at
    ) VALUES (
        '__CC_SUITE_COMPANY_ID__', v_id, v_sender_adm, p_sender_role, p_recipient_role,
        v_seq, p_idempotency_key, v_sha, v_octets, v_accepted
    );

    message_id := v_id;
    recipient_sequence := v_seq;
    message_sha256 := v_sha;
    accepted_at := v_accepted;
    status := 'accepted';
    RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION kernel_claim_company_message_v1(
    p_recipient_role TEXT,
    p_consuming_admission_id UUID DEFAULT NULL,
    p_require_live_admission BOOLEAN DEFAULT FALSE,
    p_lease_seconds INTEGER DEFAULT 120
) RETURNS TABLE (
    message_id UUID,
    sender_role TEXT,
    message_kind TEXT,
    payload TEXT,
    message_sha256 TEXT,
    recipient_sequence BIGINT,
    lease_id UUID,
    lease_expires_at TIMESTAMPTZ,
    status TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_adm UUID;
    v_msg kernel_company_messages%ROWTYPE;
    v_attempt_id UUID;
    v_lease_id UUID;
    v_token TEXT;
    v_token_sha TEXT;
    v_exp TIMESTAMPTZ;
    v_lease_secs INTEGER;
BEGIN
    PERFORM kernel_company_message_v1_assert_role(p_recipient_role);
    v_lease_secs := GREATEST(30, LEAST(COALESCE(p_lease_seconds, 120), 600));
    v_adm := kernel_company_message_v1_resolve_admission(
        p_recipient_role, p_consuming_admission_id, p_require_live_admission
    );

    SELECT * INTO v_msg
      FROM kernel_company_messages m
     WHERE m.company_id = '__CC_SUITE_COMPANY_ID__'
       AND m.recipient_role = p_recipient_role
       AND m.state = 'queued'
     ORDER BY m.recipient_sequence
     FOR UPDATE SKIP LOCKED
     LIMIT 1;

    IF v_msg.id IS NULL THEN
        status := 'empty';
        RETURN NEXT;
        RETURN;
    END IF;

    v_attempt_id := gen_random_uuid();
    v_lease_id := gen_random_uuid();
    v_token := encode(gen_random_bytes(32), 'hex');
    v_token_sha := encode(digest(v_token, 'sha256'), 'hex');
    v_exp := clock_timestamp() + make_interval(secs => v_lease_secs);

    INSERT INTO kernel_company_message_delivery_attempts (
        id, company_id, message_id, attempt_number, recipient_role,
        consuming_admission_id, lease_id, lease_token_sha256,
        state, leased_at, lease_expires_at
    ) VALUES (
        v_attempt_id, '__CC_SUITE_COMPANY_ID__', v_msg.id,
        v_msg.delivery_attempt_count + 1, p_recipient_role,
        v_adm, v_lease_id, v_token_sha,
        'open', clock_timestamp(), v_exp
    );

    UPDATE kernel_company_messages
       SET state = 'leased',
           delivery_attempt_count = delivery_attempt_count + 1,
           active_attempt_id = v_attempt_id,
           active_lease_id = v_lease_id,
           active_lease_expires_at = v_exp
     WHERE id = v_msg.id;

    message_id := v_msg.id;
    sender_role := v_msg.sender_role;
    message_kind := v_msg.message_kind;
    payload := convert_from(v_msg.message_bytes, 'UTF8');
    message_sha256 := v_msg.message_sha256;
    recipient_sequence := v_msg.recipient_sequence;
    lease_id := v_lease_id;
    lease_expires_at := v_exp;
    status := 'leased';
    RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION kernel_consume_company_message_v1(
    p_message_id UUID,
    p_recipient_role TEXT,
    p_lease_id UUID,
    p_bytes_proof TEXT,
    p_consuming_admission_id UUID DEFAULT NULL,
    p_require_live_admission BOOLEAN DEFAULT FALSE
) RETURNS TABLE (
    message_id UUID,
    consumed_at TIMESTAMPTZ,
    status TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_adm UUID;
    v_msg kernel_company_messages%ROWTYPE;
    v_attempt kernel_company_message_delivery_attempts%ROWTYPE;
    v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
    PERFORM kernel_company_message_v1_assert_role(p_recipient_role);
    IF p_bytes_proof IS NULL OR p_bytes_proof !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'bytes_proof must be sha256 hex' USING ERRCODE = '22023';
    END IF;
    v_adm := kernel_company_message_v1_resolve_admission(
        p_recipient_role, p_consuming_admission_id, p_require_live_admission
    );

    SELECT * INTO v_msg
      FROM kernel_company_messages
     WHERE id = p_message_id
     FOR UPDATE;
    IF v_msg.id IS NULL THEN
        RAISE EXCEPTION 'message not found' USING ERRCODE = 'P0002';
    END IF;
    IF v_msg.recipient_role <> p_recipient_role THEN
        RAISE EXCEPTION 'recipient role mismatch' USING ERRCODE = '42501';
    END IF;
    IF v_msg.state = 'consumed' THEN
        -- idempotent
        RETURN QUERY
        SELECT v_msg.id, v_msg.consumed_at, 'already_consumed'::TEXT;
        RETURN;
    END IF;
    IF v_msg.state <> 'leased' OR v_msg.active_lease_id IS DISTINCT FROM p_lease_id THEN
        RAISE EXCEPTION 'message not leased under this lease' USING ERRCODE = '55000';
    END IF;
    IF p_bytes_proof <> v_msg.message_sha256 THEN
        RAISE EXCEPTION 'bytes_proof mismatch' USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_attempt
      FROM kernel_company_message_delivery_attempts a
     WHERE a.message_id = p_message_id AND a.lease_id = p_lease_id
     FOR UPDATE;
    IF v_attempt.id IS NULL OR v_attempt.state <> 'open' THEN
        RAISE EXCEPTION 'open delivery attempt not found' USING ERRCODE = '55000';
    END IF;

    UPDATE kernel_company_message_delivery_attempts a
       SET state = 'consumed', closed_at = v_now
     WHERE a.id = v_attempt.id;

    UPDATE kernel_company_messages m
       SET state = 'consumed',
           consumed_at = v_now,
           active_attempt_id = NULL,
           active_lease_id = NULL,
           active_lease_expires_at = NULL
     WHERE m.id = p_message_id;

    INSERT INTO kernel_company_message_consumption_receipts (
        company_id, message_id, delivery_attempt_id, consuming_admission_id,
        recipient_role, message_sha256, message_octets, bytes_proof, consumed_at
    ) VALUES (
        '__CC_SUITE_COMPANY_ID__', p_message_id, v_attempt.id, v_adm,
        p_recipient_role, v_msg.message_sha256, v_msg.message_octets,
        p_bytes_proof, v_now
    );

    message_id := p_message_id;
    consumed_at := v_now;
    status := 'consumed';
    RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION kernel_company_message_receipt_v1(
    p_message_id UUID
) RETURNS TABLE (
    message_id UUID,
    sender_role TEXT,
    recipient_role TEXT,
    message_kind TEXT,
    state TEXT,
    recipient_sequence BIGINT,
    message_sha256 TEXT,
    accepted_at TIMESTAMPTZ,
    consumed_at TIMESTAMPTZ,
    provenance TEXT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT m.id, m.sender_role, m.recipient_role, m.message_kind, m.state,
           m.recipient_sequence, m.message_sha256, m.accepted_at, m.consumed_at,
           m.provenance
      FROM kernel_company_messages m
     WHERE m.id = p_message_id;
$$;

-- Seed cutover registry: all roles file_bus_only
INSERT INTO kernel_company_message_cutover_registry (company_id, role_type, state, notes)
SELECT '__CC_SUITE_COMPANY_ID__', r, 'file_bus_only', 'seeded by 062 v1'
  FROM unnest(ARRAY[
    'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo','cclo','ccgo','ccco','utility'
  ]) AS r
ON CONFLICT DO NOTHING;

-- Grants: provisioner full execute; company role logins execute RPCs only
GRANT EXECUTE ON FUNCTION kernel_company_message_v1_assert_role(TEXT) TO PUBLIC;
GRANT EXECUTE ON FUNCTION kernel_company_message_v1_resolve_admission(TEXT, UUID, BOOLEAN)
    TO fleet_kernel_provisioner;
GRANT EXECUTE ON FUNCTION kernel_send_company_message_v1(TEXT, TEXT, TEXT, TEXT, TEXT, UUID, BOOLEAN, TEXT)
    TO fleet_kernel_provisioner;
GRANT EXECUTE ON FUNCTION kernel_claim_company_message_v1(TEXT, UUID, BOOLEAN, INTEGER)
    TO fleet_kernel_provisioner;
GRANT EXECUTE ON FUNCTION kernel_consume_company_message_v1(UUID, TEXT, UUID, TEXT, UUID, BOOLEAN)
    TO fleet_kernel_provisioner;
GRANT EXECUTE ON FUNCTION kernel_company_message_receipt_v1(UUID)
    TO fleet_kernel_provisioner;

GRANT SELECT, INSERT, UPDATE ON kernel_company_message_recipient_sequences TO fleet_kernel_provisioner;
GRANT SELECT, INSERT, UPDATE ON kernel_company_messages TO fleet_kernel_provisioner;
GRANT SELECT, INSERT ON kernel_company_message_send_receipts TO fleet_kernel_provisioner;
GRANT SELECT, INSERT, UPDATE ON kernel_company_message_delivery_attempts TO fleet_kernel_provisioner;
GRANT SELECT, INSERT ON kernel_company_message_consumption_receipts TO fleet_kernel_provisioner;
GRANT SELECT, INSERT, UPDATE ON kernel_company_message_cutover_registry TO fleet_kernel_provisioner;
GRANT SELECT, INSERT, UPDATE ON kernel_company_message_migration_claims TO fleet_kernel_provisioner;
