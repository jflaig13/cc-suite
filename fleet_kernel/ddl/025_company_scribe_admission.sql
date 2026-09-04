-- MacBook Company Fleet Scribe package and single-writer admission.
--
-- This migration deliberately creates no admission.  A package starts held,
-- and only an exact, separately authorized activation transaction may create
-- the one live __CC_SUITE_SCRIBE_DEPLOYMENT_ID__ admission.  Nothing in this schema depends on a
-- Restaurant Fleet host, the Mac mini, or retired Gate-F evidence.

CREATE TABLE IF NOT EXISTS kernel_company_scribe_admission_keys (
    company_id  TEXT PRIMARY KEY CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    secret_key  BYTEA NOT NULL CHECK (octet_length(secret_key) = 32),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
REVOKE ALL ON kernel_company_scribe_admission_keys FROM PUBLIC;
INSERT INTO kernel_company_scribe_admission_keys (company_id,secret_key)
VALUES (
    '__CC_SUITE_COMPANY_ID__',
    decode(
        replace(gen_random_uuid()::TEXT,'-','') ||
        replace(gen_random_uuid()::TEXT,'-',''),
        'hex'
    )
)
ON CONFLICT (company_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS kernel_company_scribe_packages (
    id                       UUID PRIMARY KEY,
    company_id               TEXT NOT NULL CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    deployment_id            TEXT NOT NULL CHECK (deployment_id = '__CC_SUITE_SCRIBE_DEPLOYMENT_ID__'),
    kernel_release_sha256    TEXT NOT NULL
                             CHECK (kernel_release_sha256 ~ '^[0-9a-f]{64}$'),
    package_sha256           TEXT NOT NULL
                             CHECK (package_sha256 ~ '^[0-9a-f]{64}$'),
    source_commit_sha        TEXT NOT NULL
                             CHECK (source_commit_sha ~ '^[0-9a-f]{40}$'),
    source_tree_sha          TEXT NOT NULL
                             CHECK (source_tree_sha ~ '^[0-9a-f]{40}$'),
    host_kind                TEXT NOT NULL DEFAULT 'macbook'
                             CHECK (host_kind = 'macbook'),
    state                    TEXT NOT NULL DEFAULT 'held'
                             CHECK (state IN ('held','installed','retired')),
    installed_root           TEXT NOT NULL CHECK (installed_root LIKE '/%'),
    package_manifest         JSONB NOT NULL
                             CHECK (jsonb_typeof(package_manifest) = 'object'),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    installed_at             TIMESTAMPTZ,
    retired_at               TIMESTAMPTZ,
    UNIQUE (id,company_id),
    UNIQUE (company_id,deployment_id,package_sha256),
    CHECK ((state = 'installed') = (installed_at IS NOT NULL)),
    CHECK ((state = 'retired') = (retired_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_one_installed_company_scribe_package
    ON kernel_company_scribe_packages (company_id,deployment_id)
    WHERE state = 'installed';
REVOKE ALL ON kernel_company_scribe_packages FROM PUBLIC;

CREATE TABLE IF NOT EXISTS kernel_company_scribe_admissions (
    id                               UUID PRIMARY KEY,
    company_id                       TEXT NOT NULL
                                     CHECK (company_id = '__CC_SUITE_COMPANY_ID__'),
    deployment_id                    TEXT NOT NULL
                                     CHECK (deployment_id = '__CC_SUITE_SCRIBE_DEPLOYMENT_ID__'),
    worker_id                        UUID NOT NULL,
    worker_session_id                UUID NOT NULL,
    package_id                       UUID NOT NULL,
    agent_release_sha256             TEXT NOT NULL
                                     CHECK (agent_release_sha256 ~ '^[0-9a-f]{64}$'),
    kernel_release_sha256            TEXT NOT NULL
                                     CHECK (kernel_release_sha256 ~ '^[0-9a-f]{64}$'),
    package_sha256                   TEXT NOT NULL
                                     CHECK (package_sha256 ~ '^[0-9a-f]{64}$'),
    runtime_kind                     TEXT NOT NULL
                                     CHECK (runtime_kind = 'claude-code'),
    task_id                          TEXT NOT NULL CHECK (btrim(task_id) <> ''),
    claude_session_id                TEXT NOT NULL
                                     CHECK (btrim(claude_session_id) <> ''),
    process_id                       BIGINT NOT NULL CHECK (process_id > 1),
    process_start_fingerprint_sha256 TEXT NOT NULL
                                     CHECK (
                                         process_start_fingerprint_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    repo_commit_sha                  TEXT NOT NULL
                                     CHECK (repo_commit_sha ~ '^[0-9a-f]{40}$'),
    repo_tree_sha                    TEXT NOT NULL
                                     CHECK (repo_tree_sha ~ '^[0-9a-f]{40}$'),
    lease_token_sha256               TEXT NOT NULL
                                     CHECK (lease_token_sha256 ~ '^[0-9a-f]{64}$'),
    admission_epoch                  BIGINT NOT NULL CHECK (admission_epoch > 0),
    mandate_id                       TEXT NOT NULL
                                     CHECK (
                                         mandate_id
                                         ~ '^activation-mandate-[A-Za-z0-9._-]+$'
                                     ),
    mandate_artifact_sha256          TEXT NOT NULL
                                     CHECK (
                                         mandate_artifact_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    authority_approval_id            UUID NOT NULL,
    authority_subject_ref            TEXT NOT NULL
                                     CHECK (btrim(authority_subject_ref) <> ''),
    authority_subject_sha256         TEXT NOT NULL
                                     CHECK (
                                         authority_subject_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    mini_activation_receipt_sha256   TEXT NOT NULL
                                     CHECK (
                                         mini_activation_receipt_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    host_identity_sha256             TEXT NOT NULL
                                     CHECK (host_identity_sha256 ~ '^[0-9a-f]{64}$'),
    host_boot_id_sha256              TEXT NOT NULL
                                     CHECK (host_boot_id_sha256 ~ '^[0-9a-f]{64}$'),
    runtime_executable_path          TEXT NOT NULL
                                     CHECK (
                                         runtime_executable_path LIKE '/%'
                                         AND btrim(runtime_executable_path) <> ''
                                     ),
    runtime_executable_sha256        TEXT NOT NULL
                                     CHECK (
                                         runtime_executable_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    runtime_lock_path                TEXT NOT NULL
                                     CHECK (
                                         runtime_lock_path LIKE '/%'
                                         AND btrim(runtime_lock_path) <> ''
                                     ),
    authority_mode                   TEXT NOT NULL
                                     CHECK (
                                         authority_mode IN (
                                             'initial-local-activation',
                                             'standing-mandate-recovery'
                                         )
                                     ),
    authority_root_admission_id      UUID NOT NULL,
    prior_admission_id               UUID,
    recovery_intent_id               UUID,
    prior_rollback_receipt_sha256    TEXT
                                     CHECK (
                                         prior_rollback_receipt_sha256 IS NULL
                                         OR prior_rollback_receipt_sha256
                                            ~ '^[0-9a-f]{64}$'
                                     ),
    restart_intent_sha256            TEXT
                                     CHECK (
                                         restart_intent_sha256 IS NULL
                                         OR restart_intent_sha256
                                            ~ '^[0-9a-f]{64}$'
                                     ),
    state                            TEXT NOT NULL DEFAULT 'live'
                                     CHECK (state IN ('live','revoked','expired')),
    admitted_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_expires_at                 TIMESTAMPTZ NOT NULL,
    closed_at                        TIMESTAMPTZ,
    UNIQUE (id,company_id),
    UNIQUE (company_id,admission_epoch),
    UNIQUE (mandate_id,authority_subject_ref),
    FOREIGN KEY (worker_id,company_id)
        REFERENCES kernel_runtime_workers (id,tenant_id),
    FOREIGN KEY (worker_session_id)
        REFERENCES kernel_worker_sessions (id),
    FOREIGN KEY (package_id,company_id)
        REFERENCES kernel_company_scribe_packages (id,company_id),
    FOREIGN KEY (authority_root_admission_id,company_id)
        REFERENCES kernel_company_scribe_admissions (id,company_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (prior_admission_id,company_id)
        REFERENCES kernel_company_scribe_admissions (id,company_id),
    CHECK (lease_expires_at > heartbeat_at),
    CHECK (lease_expires_at <= heartbeat_at + interval '5 minutes'),
    CHECK ((state = 'live') = (closed_at IS NULL)),
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
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_one_live_company_scribe_admission
    ON kernel_company_scribe_admissions (company_id)
    WHERE state = 'live';
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_one_initial_scribe_approval
    ON kernel_company_scribe_admissions (company_id,authority_approval_id)
    WHERE authority_mode='initial-local-activation';
REVOKE ALL ON kernel_company_scribe_admissions FROM PUBLIC;

CREATE TABLE IF NOT EXISTS kernel_company_scribe_recovery_intents (
    id                               UUID PRIMARY KEY,
    company_id                       TEXT NOT NULL
                                     CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    deployment_id                    TEXT NOT NULL
                                     CHECK (deployment_id='__CC_SUITE_SCRIBE_DEPLOYMENT_ID__'),
    mandate_id                       TEXT NOT NULL,
    mandate_artifact_sha256          TEXT NOT NULL
                                     CHECK (
                                         mandate_artifact_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    authority_root_admission_id      UUID NOT NULL,
    prior_admission_id               UUID NOT NULL,
    next_admission_epoch             BIGINT NOT NULL
                                     CHECK (next_admission_epoch > 1),
    worker_id                        UUID NOT NULL,
    worker_session_id                UUID NOT NULL,
    package_id                       UUID NOT NULL,
    agent_release_sha256             TEXT NOT NULL
                                     CHECK (agent_release_sha256 ~ '^[0-9a-f]{64}$'),
    kernel_release_sha256            TEXT NOT NULL
                                     CHECK (kernel_release_sha256 ~ '^[0-9a-f]{64}$'),
    package_sha256                   TEXT NOT NULL
                                     CHECK (package_sha256 ~ '^[0-9a-f]{64}$'),
    repo_commit_sha                  TEXT NOT NULL
                                     CHECK (repo_commit_sha ~ '^[0-9a-f]{40}$'),
    repo_tree_sha                    TEXT NOT NULL
                                     CHECK (repo_tree_sha ~ '^[0-9a-f]{40}$'),
    host_identity_sha256             TEXT NOT NULL
                                     CHECK (host_identity_sha256 ~ '^[0-9a-f]{64}$'),
    mini_activation_receipt_sha256   TEXT NOT NULL
                                     CHECK (
                                         mini_activation_receipt_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    prior_rollback_receipt_sha256    TEXT NOT NULL
                                     CHECK (
                                         prior_rollback_receipt_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    restart_intent_sha256            TEXT NOT NULL
                                     CHECK (restart_intent_sha256 ~ '^[0-9a-f]{64}$'),
    bootstrap_request_sha256         TEXT NOT NULL
                                     CHECK (bootstrap_request_sha256 ~ '^[0-9a-f]{64}$'),
    admission_id                     UUID NOT NULL,
    authority_approval_id            UUID NOT NULL,
    authority_subject_ref            TEXT NOT NULL
                                     CHECK (btrim(authority_subject_ref) <> ''),
    authority_subject_sha256         TEXT NOT NULL
                                     CHECK (
                                         authority_subject_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    task_id                          TEXT NOT NULL CHECK (btrim(task_id) <> ''),
    claude_session_id                TEXT NOT NULL
                                     CHECK (btrim(claude_session_id) <> ''),
    process_id                       BIGINT NOT NULL CHECK (process_id > 1),
    process_start_fingerprint_sha256 TEXT NOT NULL
                                     CHECK (
                                         process_start_fingerprint_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    host_boot_id_sha256              TEXT NOT NULL
                                     CHECK (host_boot_id_sha256 ~ '^[0-9a-f]{64}$'),
    runtime_executable_path          TEXT NOT NULL
                                     CHECK (
                                         runtime_executable_path LIKE '/%'
                                         AND btrim(runtime_executable_path) <> ''
                                     ),
    runtime_executable_sha256        TEXT NOT NULL
                                     CHECK (
                                         runtime_executable_sha256
                                         ~ '^[0-9a-f]{64}$'
                                     ),
    runtime_lock_path                TEXT NOT NULL
                                     CHECK (
                                         runtime_lock_path LIKE '/%'
                                         AND btrim(runtime_lock_path) <> ''
                                     ),
    lease_token_sha256               TEXT NOT NULL
                                     CHECK (lease_token_sha256 ~ '^[0-9a-f]{64}$'),
    lease_expires_at                 TIMESTAMPTZ NOT NULL,
    state                            TEXT NOT NULL DEFAULT 'prepared'
                                     CHECK (
                                         state IN (
                                             'prepared','consumed','abandoned'
                                         )
                                     ),
    prepared_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at                      TIMESTAMPTZ,
    abandoned_at                     TIMESTAMPTZ,
    restarted_admission_id           UUID,
    UNIQUE (id,company_id),
    UNIQUE (company_id,admission_id),
    FOREIGN KEY (authority_root_admission_id,company_id)
        REFERENCES kernel_company_scribe_admissions (id,company_id),
    FOREIGN KEY (prior_admission_id,company_id)
        REFERENCES kernel_company_scribe_admissions (id,company_id),
    FOREIGN KEY (worker_id,company_id)
        REFERENCES kernel_runtime_workers (id,tenant_id),
    FOREIGN KEY (worker_session_id)
        REFERENCES kernel_worker_sessions (id),
    FOREIGN KEY (package_id,company_id)
        REFERENCES kernel_company_scribe_packages (id,company_id),
    CHECK (
        (state='prepared' AND consumed_at IS NULL
                          AND abandoned_at IS NULL
                          AND restarted_admission_id IS NULL)
        OR
        (state='consumed' AND consumed_at IS NOT NULL
                          AND abandoned_at IS NULL
                          AND restarted_admission_id=admission_id)
        OR
        (state='abandoned' AND consumed_at IS NULL
                           AND abandoned_at IS NOT NULL
                           AND restarted_admission_id IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_one_open_scribe_recovery_intent
    ON kernel_company_scribe_recovery_intents (
        company_id,prior_admission_id
    )
    WHERE state IN ('prepared','consumed');
CREATE UNIQUE INDEX IF NOT EXISTS idx_kernel_one_scribe_recovery_epoch
    ON kernel_company_scribe_recovery_intents (
        company_id,next_admission_epoch
    )
    WHERE state IN ('prepared','consumed');
REVOKE ALL ON kernel_company_scribe_recovery_intents FROM PUBLIC;

DO $recovery_intent_fk$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname='kernel_company_scribe_admission_recovery_intent_fkey'
           AND conrelid='kernel_company_scribe_admissions'::REGCLASS
    ) THEN
        ALTER TABLE kernel_company_scribe_admissions
        ADD CONSTRAINT kernel_company_scribe_admission_recovery_intent_fkey
        FOREIGN KEY (recovery_intent_id,company_id)
        REFERENCES kernel_company_scribe_recovery_intents (id,company_id)
        DEFERRABLE INITIALLY DEFERRED;
    END IF;
END
$recovery_intent_fk$;

CREATE OR REPLACE FUNCTION kernel_guard_company_scribe_recovery_intent()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    bound_session kernel_worker_sessions%ROWTYPE;
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'prepared'
           OR NEW.prepared_at > now() + interval '5 seconds'
           OR NEW.lease_expires_at<=now()
           OR NEW.lease_expires_at>now()+interval '5 minutes'
           OR EXISTS (
                SELECT 1
                  FROM kernel_company_scribe_admissions admission
                 WHERE admission.company_id=NEW.company_id
                   AND admission.state='live'
           ) THEN
            RAISE EXCEPTION
                'Company Scribe recovery intent is stale or conflicts with a live admission';
        END IF;
        RETURN NEW;
    END IF;
    IF (
        NEW.id,NEW.company_id,NEW.deployment_id,NEW.mandate_id,
        NEW.mandate_artifact_sha256,NEW.authority_root_admission_id,
        NEW.prior_admission_id,NEW.next_admission_epoch,NEW.worker_id,
        NEW.worker_session_id,NEW.package_id,NEW.agent_release_sha256,
        NEW.kernel_release_sha256,NEW.package_sha256,NEW.repo_commit_sha,
        NEW.repo_tree_sha,NEW.host_identity_sha256,
        NEW.mini_activation_receipt_sha256,
        NEW.prior_rollback_receipt_sha256,NEW.restart_intent_sha256,
        NEW.bootstrap_request_sha256,NEW.admission_id,
        NEW.authority_approval_id,NEW.authority_subject_ref,
        NEW.authority_subject_sha256,NEW.task_id,NEW.claude_session_id,
        NEW.process_id,NEW.process_start_fingerprint_sha256,
        NEW.host_boot_id_sha256,NEW.runtime_executable_path,
        NEW.runtime_executable_sha256,NEW.runtime_lock_path,
        NEW.lease_token_sha256,NEW.lease_expires_at,NEW.prepared_at
    ) IS DISTINCT FROM (
        OLD.id,OLD.company_id,OLD.deployment_id,OLD.mandate_id,
        OLD.mandate_artifact_sha256,OLD.authority_root_admission_id,
        OLD.prior_admission_id,OLD.next_admission_epoch,OLD.worker_id,
        OLD.worker_session_id,OLD.package_id,OLD.agent_release_sha256,
        OLD.kernel_release_sha256,OLD.package_sha256,OLD.repo_commit_sha,
        OLD.repo_tree_sha,OLD.host_identity_sha256,
        OLD.mini_activation_receipt_sha256,
        OLD.prior_rollback_receipt_sha256,OLD.restart_intent_sha256,
        OLD.bootstrap_request_sha256,OLD.admission_id,
        OLD.authority_approval_id,OLD.authority_subject_ref,
        OLD.authority_subject_sha256,OLD.task_id,OLD.claude_session_id,
        OLD.process_id,OLD.process_start_fingerprint_sha256,
        OLD.host_boot_id_sha256,OLD.runtime_executable_path,
        OLD.runtime_executable_sha256,OLD.runtime_lock_path,
        OLD.lease_token_sha256,OLD.lease_expires_at,OLD.prepared_at
    ) THEN
        RAISE EXCEPTION 'Company Scribe recovery intent identity is immutable';
    END IF;
    IF OLD.state<>'prepared' OR NEW.state NOT IN ('consumed','abandoned') THEN
        RAISE EXCEPTION 'Company Scribe recovery intent transition is invalid';
    END IF;
    IF NEW.state='consumed' THEN
        IF NEW.restarted_admission_id<>NEW.admission_id
           OR NOT EXISTS (
                SELECT 1
                  FROM kernel_company_scribe_admissions admission
                 WHERE admission.id=NEW.admission_id
                   AND admission.company_id=NEW.company_id
                   AND admission.recovery_intent_id=NEW.id
                   AND admission.state='live'
           ) THEN
            RAISE EXCEPTION
                'Company Scribe recovery intent can only be consumed by its live admission';
        END IF;
        NEW.consumed_at:=now();
        NEW.abandoned_at:=NULL;
    ELSE
        SELECT *
          INTO bound_session
          FROM kernel_worker_sessions
         WHERE id=NEW.worker_session_id
           AND tenant_id=NEW.company_id
         FOR SHARE;
        IF bound_session IS NULL
           OR bound_session.state<>'ended'
           OR OLD.lease_expires_at>now()
           OR NEW.restarted_admission_id IS NOT NULL THEN
            RAISE EXCEPTION
                'Company Scribe recovery intent cannot abandon a live candidate';
        END IF;
        NEW.consumed_at:=NULL;
        NEW.abandoned_at:=now();
    END IF;
    RETURN NEW;
END
$function$;
DROP TRIGGER IF EXISTS kernel_company_scribe_recovery_intent_guard
    ON kernel_company_scribe_recovery_intents;
CREATE TRIGGER kernel_company_scribe_recovery_intent_guard
BEFORE INSERT OR UPDATE ON kernel_company_scribe_recovery_intents
FOR EACH ROW EXECUTE FUNCTION kernel_guard_company_scribe_recovery_intent();

CREATE OR REPLACE FUNCTION kernel_validate_company_scribe_package()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'held'
           OR NEW.installed_at IS NOT NULL
           OR NEW.retired_at IS NOT NULL THEN
            RAISE EXCEPTION 'new Company Scribe package must start held';
        END IF;
        RETURN NEW;
    END IF;
    IF (
        NEW.id,NEW.company_id,NEW.deployment_id,NEW.kernel_release_sha256,
        NEW.package_sha256,NEW.source_commit_sha,NEW.source_tree_sha,
        NEW.host_kind,NEW.installed_root,NEW.package_manifest,NEW.created_at
    ) IS DISTINCT FROM (
        OLD.id,OLD.company_id,OLD.deployment_id,OLD.kernel_release_sha256,
        OLD.package_sha256,OLD.source_commit_sha,OLD.source_tree_sha,
        OLD.host_kind,OLD.installed_root,OLD.package_manifest,OLD.created_at
    ) THEN
        RAISE EXCEPTION 'Company Scribe package content is immutable';
    END IF;
    IF OLD.state = 'retired' AND NEW.state <> 'retired' THEN
        RAISE EXCEPTION 'retired Company Scribe package is terminal';
    END IF;
    IF NEW.state = OLD.state AND (
        NEW.installed_at IS DISTINCT FROM OLD.installed_at
        OR NEW.retired_at IS DISTINCT FROM OLD.retired_at
    ) THEN
        RAISE EXCEPTION 'Company Scribe package timestamps require a state transition';
    END IF;
    NEW.installed_at := CASE
        WHEN NEW.state = 'installed' THEN COALESCE(OLD.installed_at,now())
        ELSE NULL
    END;
    NEW.retired_at := CASE
        WHEN NEW.state = 'retired' THEN COALESCE(OLD.retired_at,now())
        ELSE NULL
    END;
    RETURN NEW;
END
$function$;
DROP TRIGGER IF EXISTS kernel_company_scribe_package_guard
    ON kernel_company_scribe_packages;
CREATE TRIGGER kernel_company_scribe_package_guard
BEFORE INSERT OR UPDATE ON kernel_company_scribe_packages
FOR EACH ROW EXECUTE FUNCTION kernel_validate_company_scribe_package();

CREATE OR REPLACE FUNCTION kernel_validate_company_scribe_admission()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    worker RECORD;
    worker_session RECORD;
    package RECORD;
    mandate kernel_founder_activation_mandates%ROWTYPE;
    final_receipt kernel_founder_activation_step_receipts%ROWTYPE;
    root_admission kernel_company_scribe_admissions%ROWTYPE;
    prior_admission kernel_company_scribe_admissions%ROWTYPE;
    prior_session kernel_worker_sessions%ROWTYPE;
    recovery_intent kernel_company_scribe_recovery_intents%ROWTYPE;
    previous_epoch BIGINT;
    root_exact BOOLEAN;
    prior_exact BOOLEAN;
    recovery_intent_exact BOOLEAN;
    terminal_recovery_exact BOOLEAN;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF (
            NEW.id,NEW.company_id,NEW.deployment_id,NEW.worker_id,
            NEW.worker_session_id,NEW.package_id,NEW.agent_release_sha256,
            NEW.kernel_release_sha256,NEW.package_sha256,NEW.runtime_kind,
            NEW.task_id,NEW.claude_session_id,NEW.process_id,
            NEW.process_start_fingerprint_sha256,NEW.repo_commit_sha,
            NEW.repo_tree_sha,NEW.lease_token_sha256,NEW.admission_epoch,
            NEW.mandate_id,NEW.mandate_artifact_sha256,
            NEW.authority_approval_id,NEW.authority_subject_ref,
            NEW.authority_subject_sha256,NEW.mini_activation_receipt_sha256,
            NEW.host_identity_sha256,NEW.host_boot_id_sha256,
            NEW.runtime_executable_path,NEW.runtime_executable_sha256,
            NEW.runtime_lock_path,NEW.authority_mode,
            NEW.authority_root_admission_id,NEW.prior_admission_id,
            NEW.recovery_intent_id,NEW.prior_rollback_receipt_sha256,
            NEW.restart_intent_sha256,
            NEW.admitted_at
        ) IS DISTINCT FROM (
            OLD.id,OLD.company_id,OLD.deployment_id,OLD.worker_id,
            OLD.worker_session_id,OLD.package_id,OLD.agent_release_sha256,
            OLD.kernel_release_sha256,OLD.package_sha256,OLD.runtime_kind,
            OLD.task_id,OLD.claude_session_id,OLD.process_id,
            OLD.process_start_fingerprint_sha256,OLD.repo_commit_sha,
            OLD.repo_tree_sha,OLD.lease_token_sha256,OLD.admission_epoch,
            OLD.mandate_id,OLD.mandate_artifact_sha256,
            OLD.authority_approval_id,OLD.authority_subject_ref,
            OLD.authority_subject_sha256,OLD.mini_activation_receipt_sha256,
            OLD.host_identity_sha256,OLD.host_boot_id_sha256,
            OLD.runtime_executable_path,OLD.runtime_executable_sha256,
            OLD.runtime_lock_path,OLD.authority_mode,
            OLD.authority_root_admission_id,OLD.prior_admission_id,
            OLD.recovery_intent_id,OLD.prior_rollback_receipt_sha256,
            OLD.restart_intent_sha256,
            OLD.admitted_at
        ) THEN
            RAISE EXCEPTION 'Company Scribe admission identity is immutable';
        END IF;
        IF OLD.state <> 'live' AND NEW.state <> OLD.state THEN
            RAISE EXCEPTION 'closed Company Scribe admission is terminal';
        END IF;
        IF NEW.heartbeat_at < OLD.heartbeat_at
           OR NEW.lease_expires_at < OLD.lease_expires_at THEN
            RAISE EXCEPTION 'Company Scribe admission clocks cannot move backward';
        END IF;
        IF OLD.state = 'live' AND NEW.state <> 'live' THEN
            IF NEW.heartbeat_at IS DISTINCT FROM OLD.heartbeat_at
               OR NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at THEN
                RAISE EXCEPTION
                    'Company Scribe admission closure cannot rewrite its clocks';
            END IF;
            IF NEW.state='expired' AND OLD.lease_expires_at > now() THEN
                RAISE EXCEPTION
                    'live Company Scribe admission cannot be marked expired early';
            END IF;
            NEW.closed_at := now();
            -- Closing is the recovery path when a process/session/package or
            -- mandate dependency is stale. Requiring those stale dependencies
            -- to remain live here would strand the partial-unique singleton
            -- row forever and make a safe epoch restart impossible.
            RETURN NEW;
        ELSIF NEW.state = 'live' THEN
            NEW.closed_at := NULL;
        END IF;
    ELSIF NEW.state <> 'live' OR NEW.closed_at IS NOT NULL THEN
        RAISE EXCEPTION 'new Company Scribe admission must start live';
    END IF;
    IF NEW.state='live' AND (
        NEW.heartbeat_at > now() + interval '5 seconds'
        OR NEW.lease_expires_at <= NEW.heartbeat_at
        OR NEW.lease_expires_at > NEW.heartbeat_at + interval '5 minutes'
    ) THEN
        RAISE EXCEPTION 'Company Scribe admission lease exceeds the bounded horizon';
    END IF;

    IF TG_OP = 'INSERT' THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended('__CC_SUITE_COMPANY_ID__:scribe-admission',0)
        );
        SELECT max(admission_epoch)
          INTO previous_epoch
          FROM kernel_company_scribe_admissions
         WHERE company_id=NEW.company_id;
        IF NEW.admission_epoch <> COALESCE(previous_epoch,0) + 1 THEN
            RAISE EXCEPTION 'Company Scribe admission epoch must be exactly monotonic';
        END IF;
    END IF;

    SELECT worker_row.*,manifest.status AS manifest_status
      INTO worker
      FROM kernel_runtime_workers worker_row
      JOIN kernel_tenant_fleet_manifests manifest
        ON manifest.id=worker_row.manifest_id
       AND manifest.tenant_id=worker_row.tenant_id
     WHERE worker_row.id=NEW.worker_id
       AND worker_row.tenant_id=NEW.company_id
     FOR SHARE OF worker_row,manifest;
    IF worker.id IS NULL
       OR worker.fleet_type <> 'company'
       OR worker.role_type <> 'scribe'
       OR worker.status <> 'active'
       OR worker.manifest_status <> 'active'
       OR worker.agent_release_sha256 <> NEW.agent_release_sha256 THEN
        RAISE EXCEPTION 'Company Scribe admission requires the active Scribe worker';
    END IF;

    SELECT *
      INTO worker_session
      FROM kernel_worker_sessions
     WHERE id=NEW.worker_session_id
       AND tenant_id=NEW.company_id
       AND worker_id=NEW.worker_id
     FOR SHARE;
    IF worker_session.id IS NULL
       OR worker_session.role_type <> 'scribe'
       OR worker_session.agent_release_sha256 <> NEW.agent_release_sha256
       OR worker_session.state <> 'active'
       OR worker_session.lease_expires_at <= now()
       OR encode(
            digest(convert_to(worker_session.lease_token,'UTF8'),'sha256'),
            'hex'
          ) <> NEW.lease_token_sha256
       OR NEW.lease_expires_at > worker_session.lease_expires_at THEN
        RAISE EXCEPTION 'Company Scribe admission requires the exact live worker session lease';
    END IF;

    SELECT *
      INTO package
      FROM kernel_company_scribe_packages
     WHERE id=NEW.package_id
       AND company_id=NEW.company_id
     FOR SHARE;
    IF package.id IS NULL
       OR package.state <> 'installed'
       OR package.deployment_id <> NEW.deployment_id
       OR package.kernel_release_sha256 <> NEW.kernel_release_sha256
       OR package.package_sha256 <> NEW.package_sha256
       OR package.source_commit_sha <> NEW.repo_commit_sha
       OR package.source_tree_sha <> NEW.repo_tree_sha THEN
        RAISE EXCEPTION 'Company Scribe admission requires the exact installed package and tree';
    END IF;

    SELECT *
      INTO mandate
      FROM kernel_founder_activation_mandates
     WHERE mandate_id=NEW.mandate_id
     FOR SHARE;
    IF mandate.mandate_id IS NULL
       OR mandate.status NOT IN ('active','completed')
       OR mandate.artifact_sha256<>NEW.mandate_artifact_sha256
       OR mandate.authority_verification_mac IS DISTINCT FROM
          kernel_founder_mandate_expected_mac(
              mandate.mandate_id,mandate.artifact_sha256,
              mandate.artifact_bytes,mandate.artifact,mandate.issued_at
          ) THEN
        RAISE EXCEPTION
            'Company Scribe admission lacks the exact active mandate';
    END IF;

    IF NEW.authority_mode='initial-local-activation' THEN
        IF TG_OP='INSERT' AND mandate.status<>'active' THEN
            RAISE EXCEPTION
                'initial Company Scribe admission requires the active mandate';
        END IF;
        IF NEW.authority_approval_id<>NEW.id
           OR NEW.mini_activation_receipt_sha256<>repeat('0',64) THEN
            RAISE EXCEPTION
                'initial Company Scribe admission identity is not local-only';
        END IF;
    ELSE
        SELECT *
          INTO root_admission
          FROM kernel_company_scribe_admissions
         WHERE id=NEW.authority_root_admission_id
           AND company_id=NEW.company_id
         FOR SHARE;
        SELECT *
          INTO prior_admission
          FROM kernel_company_scribe_admissions
         WHERE id=NEW.prior_admission_id
           AND company_id=NEW.company_id
         FOR SHARE;
        IF prior_admission.id IS NOT NULL THEN
            SELECT *
              INTO prior_session
              FROM kernel_worker_sessions
             WHERE id=prior_admission.worker_session_id
               AND tenant_id=NEW.company_id
             FOR SHARE;
        END IF;
        SELECT *
          INTO recovery_intent
          FROM kernel_company_scribe_recovery_intents
         WHERE id=NEW.recovery_intent_id
           AND company_id=NEW.company_id
         FOR UPDATE;
        IF mandate.status='completed' THEN
            SELECT *
              INTO final_receipt
              FROM kernel_founder_activation_step_receipts
             WHERE mandate_id=NEW.mandate_id
               AND activation_step='scribe-verification'
             FOR SHARE;
        END IF;
        root_exact := root_admission.id IS NOT NULL
            AND root_admission.authority_mode='initial-local-activation'
            AND root_admission.authority_root_admission_id=root_admission.id
            AND root_admission.mandate_id=NEW.mandate_id
            AND root_admission.mandate_artifact_sha256
                =NEW.mandate_artifact_sha256
            AND root_admission.worker_id=NEW.worker_id
            AND root_admission.package_id=NEW.package_id
            AND root_admission.agent_release_sha256=NEW.agent_release_sha256
            AND root_admission.kernel_release_sha256
                =NEW.kernel_release_sha256
            AND root_admission.package_sha256=NEW.package_sha256
            AND root_admission.repo_commit_sha=NEW.repo_commit_sha
            AND root_admission.repo_tree_sha=NEW.repo_tree_sha
            AND root_admission.host_identity_sha256=NEW.host_identity_sha256
            AND root_admission.mini_activation_receipt_sha256
                =NEW.mini_activation_receipt_sha256
            AND root_admission.runtime_executable_path
                =NEW.runtime_executable_path
            AND root_admission.runtime_executable_sha256
                =NEW.runtime_executable_sha256
            AND root_admission.runtime_lock_path=NEW.runtime_lock_path
            AND root_admission.authority_approval_id
                =NEW.authority_approval_id;
        prior_exact := prior_admission.id IS NOT NULL
            AND prior_admission.state='revoked'
            AND prior_admission.authority_root_admission_id
                =NEW.authority_root_admission_id
            AND prior_admission.worker_id=NEW.worker_id
            AND prior_admission.package_id=NEW.package_id
            AND prior_admission.agent_release_sha256=NEW.agent_release_sha256
            AND prior_admission.kernel_release_sha256
                =NEW.kernel_release_sha256
            AND prior_admission.package_sha256=NEW.package_sha256
            AND prior_admission.repo_commit_sha=NEW.repo_commit_sha
            AND prior_admission.repo_tree_sha=NEW.repo_tree_sha
            AND prior_admission.host_identity_sha256=NEW.host_identity_sha256
            AND prior_admission.mini_activation_receipt_sha256
                =NEW.mini_activation_receipt_sha256
            AND prior_admission.runtime_executable_path
                =NEW.runtime_executable_path
            AND prior_admission.runtime_executable_sha256
                =NEW.runtime_executable_sha256
            AND prior_admission.runtime_lock_path=NEW.runtime_lock_path
            AND prior_admission.admission_epoch+1=NEW.admission_epoch
            AND prior_session.id IS NOT NULL
            AND prior_session.state='ended';
        recovery_intent_exact := recovery_intent.id IS NOT NULL
            AND (
                (TG_OP='INSERT' AND recovery_intent.state='prepared')
                OR (TG_OP='UPDATE' AND recovery_intent.state='consumed')
            )
            AND (
                TG_OP='UPDATE' OR (
                recovery_intent.mandate_id,
                recovery_intent.mandate_artifact_sha256,
                recovery_intent.authority_root_admission_id,
                recovery_intent.prior_admission_id,
                recovery_intent.next_admission_epoch,
                recovery_intent.worker_id,recovery_intent.worker_session_id,
                recovery_intent.package_id,
                recovery_intent.agent_release_sha256,
                recovery_intent.kernel_release_sha256,
                recovery_intent.package_sha256,
                recovery_intent.repo_commit_sha,
                recovery_intent.repo_tree_sha,
                recovery_intent.host_identity_sha256,
                recovery_intent.mini_activation_receipt_sha256,
                recovery_intent.prior_rollback_receipt_sha256,
                recovery_intent.restart_intent_sha256,
                recovery_intent.admission_id,
                recovery_intent.authority_approval_id,
                recovery_intent.authority_subject_ref,
                recovery_intent.authority_subject_sha256,
                recovery_intent.task_id,
                recovery_intent.claude_session_id,
                recovery_intent.process_id,
                recovery_intent.process_start_fingerprint_sha256,
                recovery_intent.host_boot_id_sha256,
                recovery_intent.runtime_executable_path,
                recovery_intent.runtime_executable_sha256,
                recovery_intent.runtime_lock_path,
                recovery_intent.lease_token_sha256,
                recovery_intent.lease_expires_at
              ) IS NOT DISTINCT FROM (
                NEW.mandate_id,NEW.mandate_artifact_sha256,
                NEW.authority_root_admission_id,NEW.prior_admission_id,
                NEW.admission_epoch,NEW.worker_id,NEW.worker_session_id,
                NEW.package_id,NEW.agent_release_sha256,
                NEW.kernel_release_sha256,NEW.package_sha256,
                NEW.repo_commit_sha,NEW.repo_tree_sha,
                NEW.host_identity_sha256,
                NEW.mini_activation_receipt_sha256,
                NEW.prior_rollback_receipt_sha256,
                NEW.restart_intent_sha256,NEW.id,
                NEW.authority_approval_id,NEW.authority_subject_ref,
                NEW.authority_subject_sha256,NEW.task_id,
                NEW.claude_session_id,NEW.process_id,
                NEW.process_start_fingerprint_sha256,
                NEW.host_boot_id_sha256,NEW.runtime_executable_path,
                NEW.runtime_executable_sha256,NEW.runtime_lock_path,
                NEW.lease_token_sha256,NEW.lease_expires_at
              )
            );
        terminal_recovery_exact := mandate.status='active' OR (
            mandate.status='completed'
            AND final_receipt.mandate_id IS NOT NULL
            AND final_receipt.receipt_payload->>'status'='completed'
            AND final_receipt.receipt_payload->>'singleton'='verified'
            AND final_receipt.receipt_payload->>'persistence'='verified'
            AND final_receipt.receipt_payload->>'restart'='passed'
            AND final_receipt.receipt_payload->>'receipts'='verified'
        );
        IF root_exact IS NOT TRUE
           OR prior_exact IS NOT TRUE
           OR recovery_intent_exact IS NOT TRUE
           OR terminal_recovery_exact IS NOT TRUE THEN
            RAISE EXCEPTION
                'Company Scribe recovery lacks its exact standing authority chain'
                USING DETAIL=format(
                    'root=%s prior=%s intent=%s terminal=%s',
                    root_exact,prior_exact,
                    recovery_intent_exact,terminal_recovery_exact
                );
        END IF;
    END IF;
    RETURN NEW;
END
$function$;
DROP TRIGGER IF EXISTS kernel_company_scribe_admission_guard
    ON kernel_company_scribe_admissions;
CREATE TRIGGER kernel_company_scribe_admission_guard
BEFORE INSERT OR UPDATE ON kernel_company_scribe_admissions
FOR EACH ROW EXECUTE FUNCTION kernel_validate_company_scribe_admission();

CREATE OR REPLACE FUNCTION kernel_consume_company_scribe_recovery_intent()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
    IF NEW.authority_mode='standing-mandate-recovery' THEN
        UPDATE kernel_company_scribe_recovery_intents
           SET state='consumed',restarted_admission_id=NEW.id
         WHERE id=NEW.recovery_intent_id
           AND company_id=NEW.company_id
           AND state='prepared';
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Company Scribe recovery intent was not atomically consumed';
        END IF;
    END IF;
    RETURN NEW;
END
$function$;
DROP TRIGGER IF EXISTS kernel_company_scribe_recovery_intent_consume
    ON kernel_company_scribe_admissions;
CREATE TRIGGER kernel_company_scribe_recovery_intent_consume
AFTER INSERT ON kernel_company_scribe_admissions
FOR EACH ROW EXECUTE FUNCTION kernel_consume_company_scribe_recovery_intent();

CREATE OR REPLACE FUNCTION kernel_company_scribe_context_proof(
    p_admission_id UUID,p_admission_epoch BIGINT,p_db_role NAME,p_txid BIGINT
)
RETURNS TEXT
LANGUAGE plpgsql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    admission_key BYTEA;
BEGIN
    SELECT secret_key
      INTO admission_key
      FROM kernel_company_scribe_admission_keys
     WHERE company_id='__CC_SUITE_COMPANY_ID__';
    IF admission_key IS NULL THEN
        RAISE EXCEPTION 'Company Scribe admission context key is unavailable';
    END IF;
    RETURN encode(
        kernel_hmac_sha256(
            convert_to(
                concat_ws(
                    '|','company-scribe-admission-v1',p_admission_id::TEXT,
                    p_admission_epoch::TEXT,p_db_role::TEXT,p_txid::TEXT
                ),
                'UTF8'
            ),
            admission_key
        ),
        'hex'
    );
END
$function$;
REVOKE ALL ON FUNCTION kernel_company_scribe_context_proof(
    UUID,BIGINT,NAME,BIGINT
) FROM PUBLIC;

DROP FUNCTION IF EXISTS kernel_bind_company_scribe_admission(
    UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,BIGINT
);
DROP FUNCTION IF EXISTS kernel_bind_company_scribe_admission(
    UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,BIGINT,
    TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT
);
CREATE OR REPLACE FUNCTION kernel_bind_company_scribe_admission(
    p_admission_id UUID,
    p_lease_token TEXT,
    p_deployment_id TEXT,
    p_agent_release_sha256 TEXT,
    p_kernel_release_sha256 TEXT,
    p_package_sha256 TEXT,
    p_task_id TEXT,
    p_claude_session_id TEXT,
    p_process_id BIGINT,
    p_process_start_fingerprint_sha256 TEXT,
    p_repo_commit_sha TEXT,
    p_repo_tree_sha TEXT,
    p_admission_epoch BIGINT,
    p_mandate_id TEXT,
    p_mandate_artifact_sha256 TEXT,
    p_authority_approval_id UUID,
    p_authority_subject_ref TEXT,
    p_authority_subject_sha256 TEXT,
    p_mini_activation_receipt_sha256 TEXT,
    p_host_identity_sha256 TEXT,
    p_host_boot_id_sha256 TEXT,
    p_runtime_executable_path TEXT,
    p_runtime_executable_sha256 TEXT,
    p_runtime_lock_path TEXT,
    p_authority_mode TEXT,
    p_authority_root_admission_id UUID,
    p_prior_admission_id UUID,
    p_recovery_intent_id UUID,
    p_prior_rollback_receipt_sha256 TEXT,
    p_restart_intent_sha256 TEXT
)
RETURNS VOID
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    admission kernel_company_scribe_admissions%ROWTYPE;
    txid BIGINT;
    proof TEXT;
BEGIN
    SELECT *
      INTO admission
      FROM kernel_company_scribe_admissions
     WHERE id=p_admission_id
       AND company_id='__CC_SUITE_COMPANY_ID__'
     FOR SHARE;
    IF admission IS NULL
       OR admission.state <> 'live'
       OR admission.lease_expires_at <= now()
       OR admission.heartbeat_at > now() + interval '5 seconds'
       OR admission.deployment_id <> p_deployment_id
       OR admission.agent_release_sha256 <> p_agent_release_sha256
       OR admission.kernel_release_sha256 <> p_kernel_release_sha256
       OR admission.package_sha256 <> p_package_sha256
       OR admission.task_id <> p_task_id
       OR admission.claude_session_id <> p_claude_session_id
       OR admission.process_id <> p_process_id
       OR admission.process_start_fingerprint_sha256
          <> p_process_start_fingerprint_sha256
       OR admission.repo_commit_sha <> p_repo_commit_sha
       OR admission.repo_tree_sha <> p_repo_tree_sha
       OR admission.admission_epoch <> p_admission_epoch
       OR admission.mandate_id <> p_mandate_id
       OR admission.mandate_artifact_sha256 <> p_mandate_artifact_sha256
       OR admission.authority_approval_id <> p_authority_approval_id
       OR admission.authority_subject_ref <> p_authority_subject_ref
       OR admission.authority_subject_sha256 <> p_authority_subject_sha256
       OR admission.mini_activation_receipt_sha256
          <> p_mini_activation_receipt_sha256
       OR admission.host_identity_sha256 <> p_host_identity_sha256
       OR admission.host_boot_id_sha256 <> p_host_boot_id_sha256
       OR admission.runtime_executable_path <> p_runtime_executable_path
       OR admission.runtime_executable_sha256 <> p_runtime_executable_sha256
       OR admission.runtime_lock_path <> p_runtime_lock_path
       OR admission.authority_mode <> p_authority_mode
       OR admission.authority_root_admission_id
          <> p_authority_root_admission_id
       OR admission.prior_admission_id
          IS DISTINCT FROM p_prior_admission_id
       OR admission.recovery_intent_id
          IS DISTINCT FROM p_recovery_intent_id
       OR admission.prior_rollback_receipt_sha256
          IS DISTINCT FROM p_prior_rollback_receipt_sha256
       OR admission.restart_intent_sha256
          IS DISTINCT FROM p_restart_intent_sha256
       OR encode(
            digest(convert_to(p_lease_token,'UTF8'),'sha256'),
            'hex'
          ) <> admission.lease_token_sha256
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_company_worker_principals principal
             WHERE principal.worker_id=admission.worker_id
               AND principal.company_id=admission.company_id
               AND principal.role_type='scribe'
               AND principal.db_role=session_user::NAME
       )
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_worker_sessions worker_session
             WHERE worker_session.id=admission.worker_session_id
               AND worker_session.worker_id=admission.worker_id
               AND worker_session.tenant_id=admission.company_id
               AND worker_session.state='active'
               AND worker_session.lease_expires_at > now()
               AND encode(
                    digest(
                        convert_to(worker_session.lease_token,'UTF8'),
                        'sha256'
                    ),
                    'hex'
                   )=admission.lease_token_sha256
       )
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_founder_activation_mandates mandate
             WHERE mandate.mandate_id=admission.mandate_id
               AND mandate.artifact_sha256=admission.mandate_artifact_sha256
               AND mandate.status IN ('active','completed')
       ) THEN
        RAISE EXCEPTION 'Company Scribe admission binding is invalid or fenced';
    END IF;
    txid := txid_current();
    proof := kernel_company_scribe_context_proof(
        admission.id,admission.admission_epoch,session_user::NAME,txid
    );
    PERFORM set_config('mise.scribe_admission_id',admission.id::TEXT,true);
    PERFORM set_config(
        'mise.scribe_admission_epoch',admission.admission_epoch::TEXT,true
    );
    PERFORM set_config('mise.scribe_admission_proof',proof,true);
END
$function$;
REVOKE ALL ON FUNCTION kernel_bind_company_scribe_admission(
    UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,BIGINT,
    TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,UUID,
    UUID,TEXT,TEXT
) FROM PUBLIC;
DO $grant_company_scribe_bind$
DECLARE
    scribe_db_role NAME;
BEGIN
    SELECT db_role
      INTO scribe_db_role
      FROM kernel_company_worker_principals
     WHERE company_id='__CC_SUITE_COMPANY_ID__'
       AND role_type='scribe';
    -- A brand-new database has no registered workers yet.  In that bootstrap
    -- shape PUBLIC remains revoked and company isolation grants this function
    -- after the Scribe principal is registered.  An activation deployment on
    -- an already-provisioned company database repairs the registered role now.
    IF scribe_db_role IS NOT NULL THEN
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION '
            'kernel_bind_company_scribe_admission('
            'UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,TEXT,'
            'BIGINT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,'
            'TEXT,UUID,UUID,UUID,TEXT,TEXT) TO %I',
            scribe_db_role
        );
    END IF;
END
$grant_company_scribe_bind$;

CREATE OR REPLACE FUNCTION kernel_heartbeat_company_scribe_admission(
    p_admission_id UUID,p_lease_token TEXT,p_lease_expires_at TIMESTAMPTZ
)
RETURNS VOID
LANGUAGE plpgsql
VOLATILE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    admission kernel_company_scribe_admissions%ROWTYPE;
BEGIN
    SELECT *
      INTO admission
      FROM kernel_company_scribe_admissions
     WHERE id=p_admission_id
       AND company_id='__CC_SUITE_COMPANY_ID__'
     FOR UPDATE;
    IF admission IS NULL
       OR admission.state <> 'live'
       OR admission.lease_expires_at <= now()
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_founder_activation_mandates mandate
             WHERE mandate.mandate_id=admission.mandate_id
               AND mandate.artifact_sha256=admission.mandate_artifact_sha256
               AND mandate.status IN ('active','completed')
       )
       OR encode(
            digest(convert_to(p_lease_token,'UTF8'),'sha256'),
            'hex'
          ) <> admission.lease_token_sha256
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_company_worker_principals principal
             WHERE principal.worker_id=admission.worker_id
               AND principal.company_id=admission.company_id
               AND principal.role_type='scribe'
               AND principal.db_role=session_user::NAME
       )
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_worker_sessions worker_session
             WHERE worker_session.id=admission.worker_session_id
               AND worker_session.worker_id=admission.worker_id
               AND worker_session.tenant_id=admission.company_id
               AND worker_session.state='active'
               AND worker_session.lease_expires_at >= p_lease_expires_at
       )
       OR p_lease_expires_at <= admission.lease_expires_at THEN
        RAISE EXCEPTION 'Company Scribe admission heartbeat is invalid or fenced';
    END IF;
    UPDATE kernel_company_scribe_admissions
       SET heartbeat_at=now(),lease_expires_at=p_lease_expires_at
     WHERE id=admission.id;
END
$function$;
REVOKE ALL ON FUNCTION kernel_heartbeat_company_scribe_admission(
    UUID,TEXT,TIMESTAMPTZ
) FROM PUBLIC;
DO $grant_company_scribe_heartbeat$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
         WHERE rolname='fleet_kernel_provisioner'
    ) THEN
        GRANT EXECUTE ON FUNCTION kernel_heartbeat_company_scribe_admission(
            UUID,TEXT,TIMESTAMPTZ
        ) TO fleet_kernel_provisioner;
    END IF;
END
$grant_company_scribe_heartbeat$;

CREATE OR REPLACE FUNCTION kernel_require_company_scribe_admission()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
DECLARE
    admission kernel_company_scribe_admissions%ROWTYPE;
    bound_id UUID;
    bound_epoch BIGINT;
    supplied_proof TEXT;
    expected_proof TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM kernel_company_worker_principals principal
         WHERE principal.company_id='__CC_SUITE_COMPANY_ID__'
           AND principal.role_type='scribe'
           AND principal.db_role=session_user::NAME
    ) THEN
        RETURN NEW;
    END IF;
    BEGIN
        bound_id := current_setting('mise.scribe_admission_id',true)::UUID;
        bound_epoch := current_setting(
            'mise.scribe_admission_epoch',true
        )::BIGINT;
        supplied_proof := current_setting(
            'mise.scribe_admission_proof',true
        );
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'Company Scribe write requires an exact transaction admission';
    END;
    SELECT *
      INTO admission
      FROM kernel_company_scribe_admissions
     WHERE id=bound_id
       AND company_id='__CC_SUITE_COMPANY_ID__'
     FOR SHARE;
    expected_proof := kernel_company_scribe_context_proof(
        bound_id,bound_epoch,session_user::NAME,txid_current()
    );
    IF admission IS NULL
       OR admission.state <> 'live'
       OR admission.lease_expires_at <= now()
       OR admission.admission_epoch <> bound_epoch
       OR admission.process_id <= 1
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_founder_activation_mandates mandate
             WHERE mandate.mandate_id=admission.mandate_id
               AND mandate.artifact_sha256=admission.mandate_artifact_sha256
               AND mandate.status IN ('active','completed')
       )
       OR supplied_proof IS NULL
       OR NOT (
            supplied_proof::BYTEA
            = expected_proof::BYTEA
       )
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_worker_sessions worker_session
             WHERE worker_session.id=admission.worker_session_id
               AND worker_session.worker_id=admission.worker_id
               AND worker_session.tenant_id=admission.company_id
               AND worker_session.state='active'
               AND worker_session.lease_expires_at > now()
       ) THEN
        RAISE EXCEPTION 'Company Scribe write admission is missing, stale, or revoked';
    END IF;
    RETURN NEW;
END
$function$;
REVOKE ALL ON FUNCTION kernel_require_company_scribe_admission() FROM PUBLIC;

DROP TRIGGER IF EXISTS kernel_00_company_scribe_admission_guard
    ON kernel_company_objectives;
CREATE TRIGGER kernel_00_company_scribe_admission_guard
BEFORE INSERT OR UPDATE ON kernel_company_objectives
FOR EACH ROW EXECUTE FUNCTION kernel_require_company_scribe_admission();

DROP TRIGGER IF EXISTS kernel_00_company_scribe_admission_guard
    ON kernel_company_objective_events;
CREATE TRIGGER kernel_00_company_scribe_admission_guard
BEFORE INSERT ON kernel_company_objective_events
FOR EACH ROW EXECUTE FUNCTION kernel_require_company_scribe_admission();

ALTER TABLE kernel_company_scribe_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_scribe_packages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS kernel_company_scope ON kernel_company_scribe_packages;
CREATE POLICY kernel_company_scope ON kernel_company_scribe_packages
    USING (
        company_id = kernel_current_company()
        OR session_user='fleet_kernel_provisioner'::NAME
    )
    WITH CHECK (
        company_id = kernel_current_company()
        OR session_user='fleet_kernel_provisioner'::NAME
    );

ALTER TABLE kernel_company_scribe_admissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_scribe_admissions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS kernel_company_scope ON kernel_company_scribe_admissions;
CREATE POLICY kernel_company_scope ON kernel_company_scribe_admissions
    USING (
        company_id = kernel_current_company()
        OR session_user='fleet_kernel_provisioner'::NAME
    )
    WITH CHECK (
        company_id = kernel_current_company()
        OR session_user='fleet_kernel_provisioner'::NAME
    );

ALTER TABLE kernel_company_scribe_recovery_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_scribe_recovery_intents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS kernel_company_scope
    ON kernel_company_scribe_recovery_intents;
CREATE POLICY kernel_company_scope
    ON kernel_company_scribe_recovery_intents
    USING (
        company_id = kernel_current_company()
        OR session_user='fleet_kernel_provisioner'::NAME
    )
    WITH CHECK (
        company_id = kernel_current_company()
        OR session_user='fleet_kernel_provisioner'::NAME
    );
