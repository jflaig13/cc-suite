-- KERNEL COMMS DESIGN FREEZE DRAFT -- REVISION 15. DO NOT APPLY.
--
-- Founder ruling commit 0eea5b3144ceac26d536ba3cd0c6ac2a173e7c4c (durable
-- artifact: cc_execs/verification/activation_mandate/
-- 20260806__founder-hold-expansion-until-wiring.md, amended by clarification
-- commit f1bb0aa1) holds CC-Suite role activations and authorizes the comms
-- BUILD to proceed. This file is a review subject only. It creates no table,
-- function, policy, grant, principal, admission, process, launch agent,
-- message, or receipt.
--
-- Revision 15 closes the SIXTH unanimous-0-CRITICAL round (r14: Codex
-- 0C/0H/2M/0L NOT CLEAR; arch 0C/0H/1M/3L; verif 0C/0H/0M/3L -- the
-- first round with ZERO HIGHs from any lens; NOT CLEAR on the Codex
-- lens; the zero-issues-twice convergence counter not started), whose
-- converged medium pair found two ORDERING seams. The r15 closure
-- (design Sections 6.1/4.1/3.3.1, this file's SQL byte-identical):
-- (Codex R14-M1 = arch F-1) the GENERATION-1 DISCRIMINATOR -- reserve
-- converts an accepted PASS obligation to reserved before the
-- flock-held scan runs, so the recovery-pass allowed-set check alone
-- could let a planted pre-existing pointer be scan-adopted at
-- generation 1 without quarantining; classification now ALSO binds to
-- the pre-reserve state inside the protocol: at a FIRST reserve
-- (generation 1, where no protocol predecessor can exist) ANY found
-- row quarantines with the pre-reserve receipt class before the
-- lawful appender writes its own row -- never finalized against,
-- never ledger-opened, never counted toward the duplicate rule;
-- found-row adoption stays lawful ONLY at takeover generations (>= 2).
-- Race-safe by construction: the discriminator consults only the
-- generation the reserve CAS just returned plus the worker's own
-- flock-held scan. The r14 contracts (allowed-set predicate, distinct
-- receipt classes, quarantine exclusion) stand unweakened. Fixtures:
-- PASS-before-scan (planted accepted pointer, PASS before any
-- recovery pass, reserve wins at generation 1 -- the row must
-- quarantine) + the inverse takeover-adoption control; mutants:
-- remove the discriminator / over-widen it to takeover generations.
-- (Codex R14-M2, adjacent to arch's store-loss LOW) the
-- RECORD-DURABLE-BEFORE-RESERVE write-ahead ordering -- the
-- first-enumeration record must be persisted, fsynced, and read back
-- BEFORE reserve/takeover, any enumeration work, or any file effect
-- for that id; failure leaves the id unprocessed and alarms, so a
-- pre-record crash can no longer defer the anchor and reserve/crash
-- repetition cannot starve the emission alarm. Fixtures: the
-- initial-record crash boundary + churn-with-pre-record-crash;
-- mutant: permit reserve before the record (churn fixture silenced --
-- RED). Also closed, the r14 LOW set: the record's NAMED restart leg;
-- store loss LOUD (quarantine-on-corrupt; store-loss alarm on a
-- missing store; conservative re-anchor disclosed by that alarm,
-- never silent); the expected_artifact premise aligned to the frozen
-- nonempty-text validation (opaque candidate path; a never-resolving
-- value = the absent-artifact/ambiguity classes; no path shape
-- assumed); the resolved-symlink disposition (stat semantics -- a
-- symlink resolving to a readable regular file IS a hit; dangling or
-- non-regular resolution = ambiguity); journal quarantine RETIRES the
-- row from the presence set (a fresh fetch lawfully re-journals; the
-- quarantined row stays receipted and out of the walk set).
--
-- Revision-14 lineage: revision 14 closed the FIFTH
-- unanimous-0-CRITICAL round (r13: Codex 0C/1H/2M/1L; arch
-- 0C/0H/3M/2L; verif 0C/0H/1M/3L), whose one HIGH (Codex R13-H1)
-- found the completion half of the owner-broker join assigned to a
-- predicate with no discovery mechanism. The r14 closure pinned: the
-- COMPLETION-DISCOVERY contract -- source = the broker's own journal
-- row (the durable map from kernel_message_id to the obligation's
-- single acceptance-validated expected_artifact path); hit = a
-- readable regular file durably present at that path (unit identity
-- from the journal, never parsed out of the artifact); validation
-- stays with the ledger CAS's owner/evidence gates (discovery only
-- SCHEDULES; a false positive is at most a refused, alarmed,
-- idempotent CAS attempt); cursor = the journal itself
-- (level-triggered per tick; restart = journal replay); ambiguity
-- (path present but not a readable regular file) never schedules and
-- raises the discovery-ambiguity alarm; retry bounded per tick,
-- idempotent, retiring at terminal closure. The BROKER JOURNAL
-- contract -- versioned rows, flocked single-contiguous-write + fsync
-- + read-back before the fetch returns, presence-keyed idempotency,
-- explicit unit states with terminal retirement, quarantine-and-alarm
-- on torn/corrupt/unknown-version rows (never silent skip), a stable
-- role-keyed path surviving broker replacement, recovery before
-- readiness. The EMISSION-BOUND alarm re-anchored on evidence its
-- actor can lawfully read: the reconciler's own durable write-once
-- first-enumeration record per kernel_message_id (takeover-invariant
-- by construction; restart-survivable beside the re-hash checkpoint;
-- accepted-PENDING coverage explicitly delegated to the
-- certification-lag + sweeper-liveness alarms; the 4.1 containment
-- sentence corrected -- the alarm monitors non-emission, not lease
-- absence). The alien-pointer check restated as an ALLOWED-SET
-- predicate (protocol-consistent only for reserved/open_requested
-- obligations; no-obligation, accepted-state, and cancelled-state
-- pointers quarantine with distinct receipts; quarantined rows are
-- receipted OUT of the scan-and-duplicate accounting). The join's
-- convergence claim scoped honestly (a never-fetching owner is the
-- named alarmed exception); the two DDL lineage class-retirement
-- claims rewritten as site-specific history; the harness
-- time-compression recipe pinned inline.
--
-- Revision-13 lineage: revision 13 closed the FOURTH
-- unanimous-0-CRITICAL round (r12: Codex
-- 0C/1H/2M/1L; arch 0C/1H/3M/2L; verif 0C/1H/2M/3L), whose lenses
-- converged on ONE HIGH (Codex R12-H1 = arch F-1 = verif F-1): the
-- level-trigger's retry leg was assigned to the maintenance reconciler,
-- which this file's own five-function grant surface and the design's
-- owner-only pin bar from materializing, on a fetch predicate with no
-- durable record. The r13 closure (design Section 6.1) reassigns the
-- retry to the OWNER role's broker: its first task_handoff receipt
-- fetch durably journals the fetched obligation content (the persisted
-- fetch half), its own periodic tick retries materialization and
-- re-drives a refused completion CAS from that journal under its own
-- live admission, and the reconciler's recovery pass DETECTS AND ALARMS
-- ONLY -- no sixth maintenance function, no maintenance content path,
-- this file's SQL byte-identical. Also closed (converged r12 M/L set):
-- the orphan alarm re-anchors takeover-invariantly on the obligation's
-- immutable accepted_at (the 4.1 emission bound; reserved_at is the
-- fencing epoch only -- takeover churn cannot starve the alarm; churn
-- fixture added); the alien-pointer check is ONE predicate (quarantine
-- only when NO obligation row exists in ANY state; the append-then-
-- crash reserved-state pointer must NOT quarantine; a cancelled-
-- obligation pointer is protocol-impossible and quarantines as
-- out-of-protocol); the materialization authorization predicate gains
-- its negative battery (sender fetch, accepted/reserved state, foreign/
-- absent pointer key -- each asserting zero ledger mutation, with
-- drop-owner-check and drop-state-check mutants); both r12-new alarms
-- gain suppression/inversion mutants + below-threshold-silence
-- assertions; materialization idempotency is PRESENCE-KEYED (an
-- already-materialized row is never rewritten); the e2e fixture asserts
-- the TERMINAL closed state across every ordering and crash boundary;
-- and the design's hold-2 round state + label sites are corrected.
--
-- Revision-12 lineage: the precision pass on the THIRD unanimous-0-CRITICAL
-- round (r11: Codex 0C/1H/0M/3L; arch 0C/1H/1M/3L; verif 0C/0H/3M/4L),
-- closing the converged materialization-lifecycle HIGH (Codex R11-H1 =
-- arch A-1): materialization is LEVEL-TRIGGERED (every receipt fetch
-- completes a content-pending row; the retry leg r13-corrected), its
-- write is disciplined (ledger flock, idempotent per kernel_message_id,
-- read-back, receipted), authorization pinned (owner-only,
-- open_requested-only), the content-pending state carries a 4.1
-- liveness bound + alarm, the completion CAS refuses content-pending
-- rows (alarmed), the drain receipt is defined as ONE artifact (the
-- pointer-form ledger row's durable existence; Codex R11-L3 tail
-- wording aligned), orphan/alien pointers quarantine on the recovery
-- pass, the variant-2 mutant is unmasked by scoping the scan rule to
-- strictly-interior lines, the e2e fixture gets a named harness + both
-- orderings + concurrency + bounded-deadline observables, the design
-- Status header carries the revision (that header's own stale label
-- repaired; the class is checklist-enforced, never retired), the 4.1
-- containment clause is numerically
-- corrected (recovery bound 20 min > 15-min trigger cap), the
-- four-state sentence is scoped to the single-row configuration, and
-- the role-grants comment counts five functions (R11-L2).
--
-- Revision-11 lineage: the precision pass on the SECOND unanimous-0-CRITICAL
-- round (r10: Codex 0C/1H/1M/1L; arch 0C/0H/1M/4L; verif 0C/0H/1M/3L).
-- Load-bearing r11 closures: (Codex R10-H1) the bare pointer gains its
-- executable content path -- the broker's kernel_message_receipt returns
-- the admission-bound obligation projection for task_handoff messages
-- (the role principal's existing fifth grant; no maintenance widening),
-- the ledger sweep is adapted to open pointer-form rows, the owner's
-- first receipt fetch materializes content, with the end-to-end fixture;
-- (arch r10 M-1) tail integrity strengthened to final-LINE-parses (the
-- interior-corrupt newline-terminated tear is the named fourth state)
-- plus the scan's mid-file malformed-line quarantine rule; (Codex
-- R10-M1) the re-hash checkpoint advances only behind a durable drain
-- receipt -- undrained rows stay in coverage every pass; the bare
-- pointer's canonical serialization is pinned byte-exactly (37 bytes);
-- reconciler cadence/recovery-bound constants enter the 4.1 ratification
-- table; the two transitively-carried bites are stated de jure; that
-- round's stale verdict-label site is repaired (the class remains
-- checklist-enforced, never retired); the obligations-table trigger
-- comment is corrected.
--
-- Revision-10 lineage: the repair pass on the unanimous-0-CRITICAL revision-9
-- three-lens round (Codex 0C/1H/4M/2L; architecture 0C/3H/1M/3L;
-- verification-design 0C/1H/3M/4L -- the founder's cut-on-any-CRITICAL
-- pre-commitment did NOT fire; the bridge stands).  Executed by the
-- producing window under the founder's 2026-08-07 fleet-offline
-- directive (no work queued to the CC-Suite; this window finishes the
-- work itself).  Load-bearing r10 repairs: (F-2) the shared admission
-- helper's ambiguous role_type -- every plane entry function was DOA
-- since revision 2 -- alias-qualified; (F-3) claim's obligation-cancel
-- ambiguous message_id alias-qualified; (F-1) the evidence-binding FK
-- moved to ALTER TABLE after the certifications table so the migration
-- can actually apply; (verif r9 H-1) the TORN-APPEND third crash state
-- specified -- tail-integrity check, single-contiguous-write + fsync +
-- read-back append discipline, open_requested re-hash coverage, fixture
-- + mutant; (Codex R9-M3) the OPEN-REQUEST file row is a bare pointer
-- keyed by kernel_message_id -- the reservation pair never leaves the
-- database; (R9-H1/M-1) trigger-layer mutation vehicles named with
-- correct observables; (R9-M2) enumerator VOLATILE; (R9-M4) the
-- design's grants bullets carry the five-function surface; the fourth
-- stale aclexplode site purged from the build tail; the design's
-- finalize signature, forever-uniqueness prose, INSERT-leg and
-- existence-check mutant observables corrected.  The r9 freeze
-- evidence's three overstated claims are corrected in the r10 evidence
-- (records of events are never rewritten).
--
-- Revision-9 lineage: the founder-adjudicated CLOSURE PASS on the revision-8
-- three-lens verdicts (founder ruling 2026-08-07, session-witnessed): the
-- lenses split 1-2 on whether the lapsed-owner-finalize softness was
-- CRITICAL (Codex R8-C1: yes; fresh-context architecture and
-- verification-design lenses: no, cleanly serialized), all three agreeing
-- on the underlying fact; the founder adjudicated NO-CRITICAL and
-- authorized one closure pass on the convergent finding set -- which
-- ADOPTS Codex's substantive fix regardless (lease lapse now terminates
-- finalization authority at both layers).  PRE-COMMITMENT RIDING THIS
-- REVISION: any subsystem CRITICAL from ANY lens in the next three-lens
-- round cuts the obligation bridge from v1, no debate.  Closure set:
-- R8-C1 substance (lease-liveness legs at function AND trigger, distinct
-- error identities); arch HIGH-1 / Codex R8-H1 (reconciler principal
-- NAMED: provisioner-owned launchd job connecting as the maintenance
-- principal, mirroring the sweeper; drainable-obligations enumerator is
-- the sole discovery surface, bare ids only; four-function-plus-enumerator
-- grant surface stated and audited); verif H-2 / Codex R8-M1 / arch LOW-2
-- (receipt serialization PINNED: canonical bytes = the exact appended
-- line including its terminating newline, UTF-8; receipt bound to byte
-- offset; the recovery scan is the named re-hash checker with an alarm);
-- arch MEDIUM-1 (the party-facing receipt function projects AWAY the
-- reservation triple -- the coordination credential never leaves the
-- protocol); verif H-3 (guard INSERT leg: obligations are born
-- 'accepted'); verif H-1 (per-layer mutation pairs with distinct error
-- identities for every double-enforced property); arch MEDIUM-2 / verif
-- M-4 (duplicate-file-row outcome specified: quarantine-all + alarm,
-- mirroring the projector); verif M-3 (OPEN-REQUEST persistence + drain
-- idempotency stated as structural contract); arch HIGH-2 / verif M-2 /
-- Codex R8-L1 (stale posture-validation narrative purged in all four
-- sites); verif L-2 (cancel_reason verdict-derived, trigger-verified);
-- verif L-3 (uniqueness prose corrected to at-any-instant).
--
-- Revision-8 lineage: the founder-authorized STRUCTURAL rework of the
-- obligation/outbox-reconciler subsystem after revision 7 drew the
-- plateau signal:
--   cc_execs/verification/codex_reviews/20260807__kernel-comms-design-review-r7.md
--     (R7-C1: reservation_id had no uniqueness constraint; finalize proved
--      stored-column immutability, not caller possession; recovery had no
--      fencing or takeover CAS.  R7-L1: obligation-receipt test plan
--      contradicted the raise-versus-empty contract.  Residuals: R6-H1
--      posture sweep cannot prove cluster-wide/credential posture of a
--      reused role; R6-H2 cancellation reason unbound to its evidence.)
-- Load-bearing revision-8 changes -- the reconciler gains a real
-- identity-and-ownership primitive:
--   * reservation_id is DATABASE-GENERATED (gen_random_uuid inside the
--     reserve function, never caller-supplied) and UNIQUE across all
--     obligations -- cross-obligation reservation reuse is a database
--     impossibility, so one file row identifies at most one obligation;
--   * reserve is a narrow SECURITY DEFINER function performing ONE atomic
--     compare-and-set (accepted, OR reserved with a LAPSED lease) that
--     returns the reservation and its fencing generation only to the
--     single winner; every loser raises;
--   * every reservation carries a bounded lease
--     (reservation_lease_expires_at, <=15 min trigger-enforced) and a
--     monotonic fencing generation that increments on every reserve
--     INCLUDING recovery takeover -- a delayed predecessor or second
--     recovery worker holding an older generation is permanently fenced;
--   * finalize is a narrow SECURITY DEFINER function taking the
--     reservation AND generation as independent presented arguments; its
--     CAS WHERE clause is the possession proof, and the receipt it binds
--     is the sha256 of the exact appended file row bytes (an immutable,
--     independently re-checkable physical receipt);
--   * the guard trigger admits reserved/open_requested transitions ONLY
--     under the protocol functions' transaction-local handshake (the
--     send-receipt derivation idiom) -- direct owner UPDATEs refuse;
--   * the file append is idempotent under the obligation's message_id key
--     while holding the ledger flock: an appender scans-then-appends, so a
--     fenced predecessor's late append is FOUND and finalized by the new
--     owner, never duplicated (design Section 6.1);
--   * cancellation binds BY ID to the exact FAIL/UNCERTIFIABLE acceptance
--     certification (cancelled_certification_id, trigger-verified) --
--     closing the R6-H2 reason-binding residual;
--   * pre-existing kernel_company_message_maintenance role reuse is
--     REFUSED OUTRIGHT (R6-H1 closed structurally: the migration admits
--     only a role it creates itself; the unprovable cluster-wide posture
--     class is eliminated, not enumerated);
--   * the design's obligation-receipt test row now expects a RAISE for
--     missing/stale/unbound admission (R7-L1), preserving empty results
--     for admitted nonparties and filtered states.
--
-- The only executable statement is the no-op notice immediately below. The
-- proposed migration body is enclosed in one block comment. Removing that
-- comment is itself a separately authorized build action and is not approved
-- by this draft.

DO $kernel_comms_design_hold$
BEGIN
    RAISE NOTICE 'HELD: kernel Company message plane draft is non-executable';
END
$kernel_comms_design_hold$;

/*
PROPOSED MIGRATION BODY -- HELD, REVISED PER THREE REVIEWS, NON-EXECUTABLE

Deployment preconditions, intentionally unsatisfied by this draft:

1. The founder ratifies the ruling pin (commit 0eea5b3144ceac26d536ba3cd0c6
   ac2a173e7c4c + clarification f1bb0aa1 + artifact digest) and the ten
   Section-8 design decisions.
2. The exact design, DDL, runtime, broker, tests, and rollback subject
   receive fresh independent CCTO architecture, CCPO verification, and
   whole-subject CMCV verdicts on this revision.
3. The Company database runs with track_commit_timestamp=on, verified by the
   deployer before apply (Section 3.3.2 certification depends on it).
4. A deployer allowlist pins this exact DDL digest and runs it in one outer
   serializable transaction. This file never opens or commits a transaction.
5. No existing Company Scribe or Company-role admission is altered by this
   migration. Runtime cutover is a later, separately authorized operation.
6. The per-role comms broker (credential containment) and the maintenance
   sweeper deployment exist and are independently verified before any
   principal receives EXECUTE.
7. The admission fixture repair (split-fixture pattern, b399ad1f) is complete
   so the plane's ephemeral-Postgres suite is constructible.

The constants below are review defaults, not ratified policy:
  one recipient; 64 KiB per message; 30 messages and 256 KiB per sender ROLE
  LINEAGE per rolling 60 seconds; 120 accepted messages per rolling 60
  seconds fleet-wide; 32 outstanding per sender-recipient pair; 128
  outstanding per recipient; 2 causal children; causal depth 4; 60-second
  delivery lease; 5 delivery attempts; 5-second transaction-age bound;
  sweeper cadence 30 seconds with a 10-minute head-of-line alarm; 90-day
  post-terminal payload retention.

-- ---------------------------------------------------------------------------
-- Advisory-lock registry: two-argument locks under one pinned classid so the
-- plane cannot alias another subsystem's keys, with fixed per-key objids so
-- collisions inside the plane are structurally impossible (review L1).
-- ---------------------------------------------------------------------------

CREATE FUNCTION kernel_company_message_lock_objid(
    p_kind TEXT,p_role TEXT
) RETURNS INTEGER
LANGUAGE plpgsql IMMUTABLE
AS $function$
DECLARE
    role_ordinal INTEGER;
BEGIN
    role_ordinal:=CASE p_role
        WHEN 'scribe' THEN 1 WHEN 'cos' THEN 2 WHEN 'ccto' THEN 3
        WHEN 'ccpo' THEN 4 WHEN 'ccde' THEN 5 WHEN 'ccro' THEN 6
        WHEN 'ccfo' THEN 7 WHEN 'ccmo' THEN 8 WHEN 'cclo' THEN 9
        WHEN 'ccgo' THEN 10 WHEN 'ccco' THEN 11 WHEN 'utility' THEN 12
        ELSE NULL END;
    IF p_kind='company' THEN RETURN 0; END IF;
    IF role_ordinal IS NULL THEN
        RAISE EXCEPTION 'Unknown Company message lock role %',p_role;
    END IF;
    RETURN CASE p_kind
        WHEN 'sender' THEN 100+role_ordinal
        WHEN 'recipient' THEN 200+role_ordinal
        ELSE NULL END;
END
$function$;

-- classid 20260807 is the plane's reserved advisory-lock namespace.

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

CREATE TABLE kernel_company_message_recipient_sequences (
    company_id          TEXT NOT NULL
                        CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    recipient_role      TEXT NOT NULL CHECK (recipient_role IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
        'cclo','ccgo','ccco','utility'
    )),
    last_sequence       BIGINT NOT NULL DEFAULT 0 CHECK (last_sequence>=0),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (company_id,recipient_role)
);
REVOKE ALL ON kernel_company_message_recipient_sequences FROM PUBLIC;

CREATE TABLE kernel_company_messages (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               TEXT NOT NULL DEFAULT '__CC_SUITE_COMPANY_ID__'
                             CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    sender_admission_id      UUID NOT NULL,
    sender_role              TEXT NOT NULL CHECK (sender_role IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
        'cclo','ccgo','ccco','utility'
    )),
    recipient_role           TEXT NOT NULL CHECK (recipient_role IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
        'cclo','ccgo','ccco','utility'
    )),
    recipient_sequence       BIGINT NOT NULL CHECK (recipient_sequence>0),
    idempotency_key          TEXT NOT NULL
                             CHECK (
                                 btrim(idempotency_key)<>''
                                 AND octet_length(idempotency_key)<=200
                             ),
    message_kind             TEXT NOT NULL CHECK (message_kind IN (
        'agent_direct','task_handoff','audit_request',
        'reply_classification','arbiter_ask'
    )),
    -- Payload retention (review H4): digest and octets are permanent proof
    -- fields; the bytes themselves are nullable ONLY in the receipted
    -- post-terminal 'expired' payload state.
    payload_state            TEXT NOT NULL DEFAULT 'present'
                             CHECK (payload_state IN ('present','expired')),
    message_bytes            BYTEA,
    message_octets           INTEGER NOT NULL
                             CHECK (message_octets BETWEEN 1 AND 65536),
    message_sha256           TEXT NOT NULL
                             CHECK (message_sha256~'^[0-9a-f]{64}$'),
    causal_parent_message_id UUID,
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
    -- Role-lineage idempotency: the retry key survives re-admission.
    UNIQUE (company_id,sender_role,idempotency_key),
    UNIQUE (company_id,recipient_role,recipient_sequence),
    FOREIGN KEY (sender_admission_id)
        REFERENCES kernel_company_scribe_admissions (id),
    FOREIGN KEY (causal_parent_message_id)
        REFERENCES kernel_company_messages (id),
    CHECK (
        (payload_state='present'
            AND message_bytes IS NOT NULL
            AND octet_length(message_bytes) BETWEEN 1 AND 65536
            AND message_octets=octet_length(message_bytes)
            AND message_sha256=encode(digest(message_bytes,'sha256'),'hex'))
        OR
        (payload_state='expired'
            AND message_bytes IS NULL
            AND state IN ('consumed','dead_lettered','quarantined'))
    ),
    CHECK (
        (causal_depth=0
            AND causal_parent_message_id IS NULL
            AND causal_root_message_id=id)
        OR
        (causal_depth>0
            AND causal_parent_message_id IS NOT NULL)
    ),
    CHECK (
        (state='queued'
            AND active_attempt_id IS NULL
            AND active_lease_id IS NULL
            AND active_lease_expires_at IS NULL
            AND consumed_at IS NULL
            AND dead_lettered_at IS NULL
            AND dead_letter_reason IS NULL
            AND quarantined_at IS NULL
            AND quarantine_reason IS NULL)
        OR
        (state='leased'
            AND active_attempt_id IS NOT NULL
            AND active_lease_id IS NOT NULL
            AND active_lease_expires_at IS NOT NULL
            AND consumed_at IS NULL
            AND dead_lettered_at IS NULL
            AND dead_letter_reason IS NULL
            AND quarantined_at IS NULL
            AND quarantine_reason IS NULL)
        OR
        (state='consumed'
            AND active_attempt_id IS NULL
            AND active_lease_id IS NULL
            AND active_lease_expires_at IS NULL
            AND consumed_at IS NOT NULL
            AND dead_lettered_at IS NULL
            AND dead_letter_reason IS NULL
            AND quarantined_at IS NULL
            AND quarantine_reason IS NULL)
        OR
        (state='dead_lettered'
            AND active_attempt_id IS NULL
            AND active_lease_id IS NULL
            AND active_lease_expires_at IS NULL
            AND consumed_at IS NULL
            AND dead_lettered_at IS NOT NULL
            AND btrim(dead_letter_reason)<>''
            AND quarantined_at IS NULL
            AND quarantine_reason IS NULL)
        OR
        (state='quarantined'
            AND active_attempt_id IS NULL
            AND active_lease_id IS NULL
            AND active_lease_expires_at IS NULL
            AND consumed_at IS NULL
            AND dead_lettered_at IS NULL
            AND dead_letter_reason IS NULL
            AND quarantined_at IS NOT NULL
            AND btrim(quarantine_reason)<>'')
    )
);
REVOKE ALL ON kernel_company_messages FROM PUBLIC;

CREATE INDEX idx_kernel_company_messages_role_window
    ON kernel_company_messages (sender_role,accepted_at DESC);
CREATE INDEX idx_kernel_company_messages_company_window
    ON kernel_company_messages (company_id,accepted_at DESC);
CREATE INDEX idx_kernel_company_messages_pair_backlog
    ON kernel_company_messages (sender_role,recipient_role,state);
CREATE INDEX idx_kernel_company_messages_recipient_claim
    ON kernel_company_messages (recipient_role,recipient_sequence)
    WHERE state IN ('queued','leased');
CREATE INDEX idx_kernel_company_messages_causal_children
    ON kernel_company_messages (causal_parent_message_id)
    WHERE causal_parent_message_id IS NOT NULL;

CREATE TABLE kernel_company_message_send_receipts (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               TEXT NOT NULL
                             CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    message_id               UUID NOT NULL UNIQUE
                             REFERENCES kernel_company_messages (id),
    sender_admission_id      UUID NOT NULL
                             REFERENCES kernel_company_scribe_admissions (id),
    sender_role              TEXT NOT NULL,
    recipient_role           TEXT NOT NULL,
    recipient_sequence       BIGINT NOT NULL,
    idempotency_key          TEXT NOT NULL,
    message_sha256           TEXT NOT NULL
                             CHECK (message_sha256~'^[0-9a-f]{64}$'),
    message_octets           INTEGER NOT NULL,
    accepted_at              TIMESTAMPTZ NOT NULL,
    UNIQUE (company_id,sender_role,idempotency_key)
);
REVOKE ALL ON kernel_company_message_send_receipts FROM PUBLIC;

-- Typed send outcome (declared after the receipt table it embeds).
CREATE TYPE kernel_company_message_send_outcome AS (
    status               TEXT,
    refusal_code         TEXT,
    violated_bound       TEXT,
    retry_after_seconds  NUMERIC,
    receipt              kernel_company_message_send_receipts
);

CREATE TABLE kernel_company_message_delivery_attempts (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               TEXT NOT NULL
                             CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    message_id               UUID NOT NULL
                             REFERENCES kernel_company_messages (id),
    attempt_number           SMALLINT NOT NULL
                             CHECK (attempt_number BETWEEN 1 AND 5),
    recipient_role           TEXT NOT NULL,
    consuming_admission_id   UUID NOT NULL
                             REFERENCES kernel_company_scribe_admissions (id),
    lease_id                 UUID NOT NULL UNIQUE,
    lease_token_sha256       TEXT NOT NULL
                             CHECK (lease_token_sha256~'^[0-9a-f]{64}$'),
    state                    TEXT NOT NULL DEFAULT 'open'
                             CHECK (state IN (
                                 'open','consumed','expired','dead_lettered'
                             )),
    leased_at                TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_expires_at         TIMESTAMPTZ NOT NULL,
    closed_at                TIMESTAMPTZ,
    UNIQUE (message_id,attempt_number),
    CHECK (lease_expires_at>leased_at),
    CHECK (
        (state='open' AND closed_at IS NULL)
        OR (state<>'open' AND closed_at IS NOT NULL)
    )
);
REVOKE ALL ON kernel_company_message_delivery_attempts FROM PUBLIC;

CREATE UNIQUE INDEX idx_kernel_company_message_one_open_attempt
    ON kernel_company_message_delivery_attempts (message_id)
    WHERE state='open';

CREATE TABLE kernel_company_message_consumption_receipts (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               TEXT NOT NULL
                             CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    message_id               UUID NOT NULL UNIQUE
                             REFERENCES kernel_company_messages (id),
    delivery_attempt_id      UUID NOT NULL UNIQUE
                             REFERENCES kernel_company_message_delivery_attempts (id),
    consuming_admission_id   UUID NOT NULL
                             REFERENCES kernel_company_scribe_admissions (id),
    recipient_role           TEXT NOT NULL,
    message_sha256           TEXT NOT NULL
                             CHECK (message_sha256~'^[0-9a-f]{64}$'),
    message_octets           INTEGER NOT NULL,
    bytes_proof              TEXT NOT NULL
                             CHECK (bytes_proof~'^[0-9a-f]{64}$'),
    consumed_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON kernel_company_message_consumption_receipts FROM PUBLIC;

-- Obligation facts (review H5 / design invariant 13): a task_handoff
-- acceptance atomically records its parsed obligation; the reconciler marks
-- emission. An accepted obligation without an OPEN-REQUEST past the emission
-- bound is an alarmed condition, never a silent orphan.
CREATE TABLE kernel_company_message_obligations (
    message_id               UUID PRIMARY KEY
                             REFERENCES kernel_company_messages (id),
    company_id               TEXT NOT NULL
                             CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    envelope_version         INTEGER NOT NULL CHECK (envelope_version=1),
    -- Review H5 (comment corrected per arch NEW LOW-2): the obligation
    -- owner is structurally the message recipient, enforced at acceptance
    -- by the SEND function's pre-insert block (single enforcement point —
    -- no deferred consistency trigger exists on this table; its guard
    -- trigger fires BEFORE INSERT OR UPDATE OR DELETE (the INSERT leg
    -- enforces born-accepted, verif r9 H-3), and a TRUNCATE statement
    -- trigger rides the plane-wide battery). 'founder' is not
    -- an obligation owner — founder work routes through FODL, never through
    -- the obligation side channel.
    owner_role               TEXT NOT NULL CHECK (owner_role IN (
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
        'cclo','ccgo','ccco','utility'
    )),
    action                   TEXT NOT NULL CHECK (btrim(action)<>''),
    expected_artifact        TEXT NOT NULL CHECK (btrim(expected_artifact)<>''),
    -- Review M1/F-H3: invariant 13 requires a positive numeric deadline. The
    -- unconstrained NUMERIC domain admits NaN and Infinity, both of which
    -- satisfy ">0" (PostgreSQL orders NaN above every finite value) and
    -- would defeat the orphan-alarm's finite overdue boundary; the upper
    -- bound (one year) rejects both by construction, not by special-casing.
    deadline_hours           NUMERIC NOT NULL
                             CHECK (deadline_hours>0 AND deadline_hours<=8760),
    -- Review F-C1 / R6-C1 (CRITICAL, two rounds): 'cancelled' is a terminal
    -- state entered ONLY when the associated message's acceptance
    -- certification returns FAIL or UNCERTIFIABLE. Revision 6 made
    -- accepted->open_requested structurally require a durable PASS row —
    -- correct, but incomplete: that guard covers the DATABASE transition,
    -- not the nontransactional file-plane OPEN-REQUEST append the
    -- transition exists to authorize, and R6-C1 found the two could
    -- diverge across a crash. 'reserved' is the missing intermediate state
    -- (design Section 6.1): the reconciler reserves (PASS-gated, same as
    -- before) BEFORE performing the file append, and finalizes to
    -- open_requested only AFTER confirming the append succeeded — the same
    -- reserve/effect/finalize/recover shape Section 5's projector crash
    -- recovery already uses for the Kernel-message-to-file bridge, applied
    -- here to the obligation-to-file bridge. A crash between reserve and
    -- finalize is recoverable (Section 6.1) without ever having falsely
    -- claimed completion.
    state                    TEXT NOT NULL DEFAULT 'accepted'
                             CHECK (state IN (
                                 'accepted','reserved','open_requested',
                                 'cancelled'
                             )),
    accepted_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    -- Review R7-C1: the reservation is a DATABASE-GENERATED, globally
    -- unique, owner-bound, lease-bounded authorization fact.  It is minted
    -- only inside kernel_reserve_company_message_obligation() (never
    -- caller-supplied), returned once to the single CAS winner, and
    -- presented back as an independent argument to
    -- kernel_finalize_company_message_obligation().  The fencing
    -- generation increments on every reserve INCLUDING recovery takeover:
    -- a delayed predecessor holding an older (reservation, generation)
    -- pair is permanently fenced out of finalize.  The UNIQUE constraint
    -- makes cross-obligation reservation reuse a database impossibility
    -- at any instant (verif L-3: takeover overwrites the reservation, so
    -- historical non-collision is UUID-probabilistic).  r10 (Codex
    -- R9-M3): the pair NEVER leaves the database — the OPEN-REQUEST file
    -- row is a bare pointer keyed by kernel_message_id and embeds no
    -- reservation or generation; recovery and duplicate-quarantine key on
    -- kernel_message_id alone, and database EXECUTE privilege plus the
    -- CAS-returned pair are the sole finalize authorization.
    reservation_id            UUID UNIQUE,
    reservation_generation    BIGINT NOT NULL DEFAULT 0
                              CHECK (reservation_generation>=0),
    reservation_lease_expires_at TIMESTAMPTZ,
    reserved_at                TIMESTAMPTZ,
    open_requested_at        TIMESTAMPTZ,
    -- Review R7-C1 (requirement 6) + verif H-2 / Codex R8-M1 (r9): the
    -- emission receipt is the sha256 of the CANONICAL appended row bytes —
    -- the exact byte sequence written between flock acquisition and
    -- release, UTF-8, one line INCLUDING its terminating \n — bound to the
    -- row's physical position (byte offset of the line's first byte in the
    -- OPEN-REQUEST file at append time).  The re-hash checker (the
    -- reconciler's startup recovery scan, design 6.1) seeks to the offset,
    -- reads through the newline, re-hashes, and alarms on mismatch — an
    -- immutable, independently reproducible physical receipt, never free
    -- text.
    open_request_receipt_sha256 TEXT
                              CHECK (open_request_receipt_sha256 IS NULL
                                     OR open_request_receipt_sha256
                                        ~ '^[0-9a-f]{64}$'),
    open_request_file_offset  BIGINT
                              CHECK (open_request_file_offset IS NULL
                                     OR open_request_file_offset>=0),
    cancelled_at              TIMESTAMPTZ,
    -- Review R6-H2 residual: the durable cancellation record binds to the
    -- exact FAIL/UNCERTIFIABLE acceptance certification that authorized
    -- it, by id — the reason is evidence-bound, never free-standing prose.
    -- r10 (arch F-1): the FK to the certifications table attaches via
    -- ALTER TABLE immediately after that table's creation below — an
    -- inline REFERENCES here named a relation created 148 lines later,
    -- which aborts the migration at this statement.
    cancelled_certification_id UUID,
    cancel_reason             TEXT,
    CHECK (
        (state='accepted' AND reservation_id IS NULL
            AND reservation_generation=0
            AND reservation_lease_expires_at IS NULL
            AND reserved_at IS NULL
            AND open_requested_at IS NULL
            AND open_request_receipt_sha256 IS NULL
            AND open_request_file_offset IS NULL
            AND cancelled_at IS NULL
            AND cancelled_certification_id IS NULL
            AND cancel_reason IS NULL)
        OR (state='reserved' AND reservation_id IS NOT NULL
            AND reservation_generation>=1
            AND reservation_lease_expires_at IS NOT NULL
            AND reserved_at IS NOT NULL
            AND open_requested_at IS NULL
            AND open_request_receipt_sha256 IS NULL
            AND open_request_file_offset IS NULL
            AND cancelled_at IS NULL
            AND cancelled_certification_id IS NULL
            AND cancel_reason IS NULL)
        OR (state='open_requested' AND reservation_id IS NOT NULL
            AND reservation_generation>=1
            AND reserved_at IS NOT NULL
            AND open_requested_at IS NOT NULL
            AND open_request_receipt_sha256 IS NOT NULL
            AND open_request_file_offset IS NOT NULL
            AND cancelled_at IS NULL
            AND cancelled_certification_id IS NULL
            AND cancel_reason IS NULL)
        OR (state='cancelled' AND reservation_id IS NULL
            AND reservation_generation=0
            AND reservation_lease_expires_at IS NULL
            AND reserved_at IS NULL
            AND open_requested_at IS NULL
            AND open_request_receipt_sha256 IS NULL
            AND open_request_file_offset IS NULL
            AND cancelled_at IS NOT NULL
            AND cancelled_certification_id IS NOT NULL
            AND btrim(cancel_reason)<>'')
    )
);
REVOKE ALL ON kernel_company_message_obligations FROM PUBLIC;

-- Commit certifications (design 3.3.2, reviews C2/C1/H1): the durable,
-- read-side proof that an acceptance/acknowledgment commit happened inside
-- the acting admission's lease — and the DELIVERY GATE: claim leases only a
-- PASS head. The commit time is read from the IMMUTABLE witness row's xmin
-- (send receipt for 'acceptance', consumption receipt for 'acknowledgment');
-- subject_id is the message ID for acceptance and the consumption-receipt ID
-- for acknowledgment. The mutable message row is never a timestamp source.
-- UNCERTIFIABLE (NULL commit_timestamp) is the named materialization-
-- impossible verdict; it quarantines like FAIL and alarms.
CREATE TABLE kernel_company_message_commit_certifications (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               TEXT NOT NULL
                             CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    subject_kind             TEXT NOT NULL
                             CHECK (subject_kind IN
                                    ('acceptance','acknowledgment')),
    subject_id               UUID NOT NULL,
    acting_admission_id      UUID NOT NULL
                             REFERENCES kernel_company_scribe_admissions (id),
    commit_timestamp         TIMESTAMPTZ,
    admission_lease_expiry   TIMESTAMPTZ NOT NULL,
    verdict                  TEXT NOT NULL
                             CHECK (verdict IN
                                    ('PASS','FAIL','UNCERTIFIABLE')),
    certified_at             TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (subject_kind,subject_id),
    CHECK ((verdict='UNCERTIFIABLE')=(commit_timestamp IS NULL))
);
REVOKE ALL ON kernel_company_message_commit_certifications FROM PUBLIC;

-- r10 (arch F-1): the obligations table's evidence-binding FK, attached
-- here because the certifications table now exists.
ALTER TABLE kernel_company_message_obligations
    ADD CONSTRAINT kernel_company_message_obligations_cancelled_cert_fk
    FOREIGN KEY (cancelled_certification_id)
    REFERENCES kernel_company_message_commit_certifications (id);

-- Retention receipts (design 3.2 payload transition, review H4).
CREATE TABLE kernel_company_message_retention_receipts (
    message_id               UUID PRIMARY KEY
                             REFERENCES kernel_company_messages (id),
    company_id               TEXT NOT NULL
                             CHECK (company_id='__CC_SUITE_COMPANY_ID__'),
    message_sha256           TEXT NOT NULL
                             CHECK (message_sha256~'^[0-9a-f]{64}$'),
    message_octets           INTEGER NOT NULL,
    terminal_state           TEXT NOT NULL
                             CHECK (terminal_state IN
                                    ('consumed','dead_lettered',
                                     'quarantined')),
    terminal_at              TIMESTAMPTZ NOT NULL,
    expired_at               TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON kernel_company_message_retention_receipts FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Admission helper (unchanged semantics from revision 2: NULL-safe, wall-
-- clock expiry, transaction-age bound; the commit-time PROOF is the
-- certification path, not this helper).
-- ---------------------------------------------------------------------------

CREATE FUNCTION kernel_current_company_message_admission()
RETURNS TABLE (admission_id UUID,role_type TEXT)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    principal kernel_company_worker_principals%ROWTYPE;
    admission kernel_company_scribe_admissions%ROWTYPE;
    bound_id UUID;
    bound_epoch BIGINT;
    supplied_proof TEXT;
    expected_proof TEXT;
BEGIN
    IF clock_timestamp()-now()>interval '5 seconds' THEN
        RAISE EXCEPTION 'Company message operation refused inside an aged transaction';
    END IF;

    SELECT * INTO STRICT principal
      FROM kernel_company_worker_principals
     WHERE company_id='__CC_SUITE_COMPANY_ID__'
       AND db_role=session_user::NAME;

    BEGIN
        IF principal.role_type='scribe' THEN
            bound_id:=current_setting('mise.scribe_admission_id',true)::UUID;
            bound_epoch:=current_setting('mise.scribe_admission_epoch',true)::BIGINT;
            supplied_proof:=current_setting('mise.scribe_admission_proof',true);
            expected_proof:=kernel_company_scribe_context_proof(
                bound_id,bound_epoch,session_user::NAME,txid_current()
            );
        ELSE
            IF current_setting('mise.company_role_type',true)
               IS DISTINCT FROM principal.role_type THEN
                RAISE EXCEPTION 'Company message admission role mismatch';
            END IF;
            bound_id:=current_setting(
                'mise.company_role_admission_id',true
            )::UUID;
            bound_epoch:=current_setting(
                'mise.company_role_admission_epoch',true
            )::BIGINT;
            supplied_proof:=current_setting(
                'mise.company_role_admission_proof',true
            );
            expected_proof:=kernel_company_role_context_proof(
                principal.role_type,bound_id,bound_epoch,
                session_user::NAME,txid_current()
            );
        END IF;
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'Company message operation requires exact transaction admission';
    END;

    IF bound_id IS NULL
       OR bound_epoch IS NULL
       OR supplied_proof IS NULL
       OR expected_proof IS NULL THEN
        RAISE EXCEPTION 'Company message operation requires exact transaction admission';
    END IF;

    -- r10 (arch F-2): alias-qualified — role_type is also this function's
    -- OUT parameter, and the unqualified reference was ambiguous under
    -- plpgsql's default variable_conflict=error, raising on EVERY call and
    -- making all five entry functions DOA.  Present since revision 2;
    -- caught only when a whole-file collision sweep ran.
    SELECT admission_row.* INTO admission
      FROM kernel_company_scribe_admissions admission_row
     WHERE admission_row.id=bound_id
       AND admission_row.company_id='__CC_SUITE_COMPANY_ID__'
       AND admission_row.role_type=principal.role_type
     FOR SHARE;

    IF admission.id IS NULL
       OR admission.state IS DISTINCT FROM 'live'
       OR admission.lease_expires_at IS NULL
       OR admission.lease_expires_at<=clock_timestamp()
       OR admission.admission_epoch IS DISTINCT FROM bound_epoch
       OR supplied_proof IS DISTINCT FROM expected_proof
       OR admission.worker_id IS DISTINCT FROM principal.worker_id
       OR NOT EXISTS (
            SELECT 1
              FROM kernel_worker_sessions worker_session
             WHERE worker_session.id=admission.worker_session_id
               AND worker_session.worker_id=admission.worker_id
               AND worker_session.tenant_id=admission.company_id
               AND worker_session.role_type=admission.role_type
               AND worker_session.state='active'
               AND worker_session.lease_expires_at>clock_timestamp()
       ) THEN
        RAISE EXCEPTION 'Company message admission is missing, stale, or revoked';
    END IF;

    RETURN QUERY SELECT admission.id,admission.role_type;
END
$function$;
REVOKE ALL ON FUNCTION kernel_current_company_message_admission() FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Deferred commit-time guards: DEFENSE for compliant callers. A caller can
-- SET CONSTRAINTS ... IMMEDIATE inside its own transaction, so no completion
-- claim rests on these; the provable fact is the certification path below
-- (review C2).
-- ---------------------------------------------------------------------------

CREATE FUNCTION kernel_assert_company_message_admission_live_at_commit()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    acting_admission_id UUID;
    acting_state TEXT;
    acting_expiry TIMESTAMPTZ;
BEGIN
    IF TG_TABLE_NAME='kernel_company_messages' THEN
        acting_admission_id:=NEW.sender_admission_id;
    ELSE
        acting_admission_id:=NEW.consuming_admission_id;
    END IF;
    SELECT state,lease_expires_at
      INTO acting_state,acting_expiry
      FROM kernel_company_scribe_admissions
     WHERE id=acting_admission_id;
    IF acting_state IS DISTINCT FROM 'live'
       OR acting_expiry IS NULL
       OR acting_expiry<=clock_timestamp() THEN
        RAISE EXCEPTION 'Company message admission lease was not live at commit';
    END IF;
    RETURN NULL;
END
$function$;

CREATE CONSTRAINT TRIGGER kernel_company_message_sender_live_at_commit
AFTER INSERT ON kernel_company_messages
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION
kernel_assert_company_message_admission_live_at_commit();

CREATE CONSTRAINT TRIGGER kernel_company_message_claimant_live_at_commit
AFTER INSERT ON kernel_company_message_delivery_attempts
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION
kernel_assert_company_message_admission_live_at_commit();

CREATE CONSTRAINT TRIGGER kernel_company_message_consumer_live_at_commit
AFTER INSERT ON kernel_company_message_consumption_receipts
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION
kernel_assert_company_message_admission_live_at_commit();

-- ---------------------------------------------------------------------------
-- Send-receipt derivation: transaction-scoped handshake (trigger depth is
-- not provenance — review M1) plus a deferred cross-table consistency check.
-- ---------------------------------------------------------------------------

CREATE FUNCTION kernel_derive_company_message_send_receipt()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
BEGIN
    PERFORM set_config(
        'mise.kernel_send_receipt_derivation',NEW.id::TEXT,true
    );
    INSERT INTO kernel_company_message_send_receipts (
        company_id,message_id,sender_admission_id,sender_role,recipient_role,
        recipient_sequence,idempotency_key,message_sha256,message_octets,
        accepted_at
    ) VALUES (
        NEW.company_id,NEW.id,NEW.sender_admission_id,NEW.sender_role,
        NEW.recipient_role,NEW.recipient_sequence,NEW.idempotency_key,
        NEW.message_sha256,NEW.message_octets,NEW.accepted_at
    );
    PERFORM set_config('mise.kernel_send_receipt_derivation','',true);
    RETURN NULL;
END
$function$;

CREATE TRIGGER kernel_company_message_send_receipt_derive
AFTER INSERT ON kernel_company_messages
FOR EACH ROW EXECUTE FUNCTION kernel_derive_company_message_send_receipt();

CREATE FUNCTION kernel_guard_company_send_receipt_insert()
RETURNS trigger LANGUAGE plpgsql VOLATILE
SET search_path=pg_catalog,public
AS $function$
BEGIN
    IF current_setting('mise.kernel_send_receipt_derivation',true)
       IS DISTINCT FROM NEW.message_id::TEXT THEN
        RAISE EXCEPTION 'Company message send receipts derive only from message inserts';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER kernel_company_send_receipt_derivation_only
BEFORE INSERT ON kernel_company_message_send_receipts
FOR EACH ROW EXECUTE FUNCTION kernel_guard_company_send_receipt_insert();

CREATE FUNCTION kernel_assert_company_send_receipt_consistent()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    message kernel_company_messages%ROWTYPE;
BEGIN
    SELECT * INTO message FROM kernel_company_messages
     WHERE id=NEW.message_id;
    IF message.id IS NULL
       OR message.sender_admission_id
          IS DISTINCT FROM NEW.sender_admission_id
       OR message.sender_role IS DISTINCT FROM NEW.sender_role
       OR message.recipient_role IS DISTINCT FROM NEW.recipient_role
       OR message.recipient_sequence
          IS DISTINCT FROM NEW.recipient_sequence
       OR message.idempotency_key IS DISTINCT FROM NEW.idempotency_key
       OR message.message_sha256 IS DISTINCT FROM NEW.message_sha256
       OR message.message_octets IS DISTINCT FROM NEW.message_octets
       OR message.accepted_at IS DISTINCT FROM NEW.accepted_at THEN
        RAISE EXCEPTION 'Company message send receipt is inconsistent';
    END IF;
    RETURN NULL;
END
$function$;

CREATE CONSTRAINT TRIGGER kernel_company_send_receipt_consistent
AFTER INSERT ON kernel_company_message_send_receipts
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION
kernel_assert_company_send_receipt_consistent();

CREATE FUNCTION kernel_assert_company_consumption_receipt_consistent()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    message kernel_company_messages%ROWTYPE;
    attempt kernel_company_message_delivery_attempts%ROWTYPE;
BEGIN
    SELECT * INTO message FROM kernel_company_messages
     WHERE id=NEW.message_id;
    SELECT * INTO attempt FROM kernel_company_message_delivery_attempts
     WHERE id=NEW.delivery_attempt_id;
    IF message.id IS NULL
       OR attempt.id IS NULL
       OR attempt.message_id IS DISTINCT FROM message.id
       OR message.state IS DISTINCT FROM 'consumed'
       OR attempt.state IS DISTINCT FROM 'consumed'
       OR attempt.consuming_admission_id
          IS DISTINCT FROM NEW.consuming_admission_id
       OR attempt.recipient_role IS DISTINCT FROM NEW.recipient_role
       OR message.message_sha256 IS DISTINCT FROM NEW.message_sha256
       OR message.message_octets IS DISTINCT FROM NEW.message_octets THEN
        RAISE EXCEPTION 'Company message consumption receipt is inconsistent';
    END IF;
    RETURN NULL;
END
$function$;

CREATE CONSTRAINT TRIGGER kernel_company_consumption_receipt_consistent
AFTER INSERT ON kernel_company_message_consumption_receipts
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION
kernel_assert_company_consumption_receipt_consistent();

-- ---------------------------------------------------------------------------
-- Commit-certification helpers (design 3.3.2; reviews C1/H1/H2). One subject
-- per call, each with its own exception containment: the verdict is computed
-- from the IMMUTABLE witness row's xmin against the acting admission's final
-- lease expiry; an uncomputable timestamp is the named UNCERTIFIABLE verdict,
-- never an aborting exception. Idempotent: an existing receipt returns its
-- recorded verdict. Called by claim's inline gate (role sessions) and the
-- maintenance sweeper; internal to the definer surface, granted to no one.
-- ---------------------------------------------------------------------------

CREATE FUNCTION kernel_certify_company_acceptance(p_message_id UUID)
RETURNS TEXT
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    existing kernel_company_message_commit_certifications%ROWTYPE;
    witness kernel_company_message_send_receipts%ROWTYPE;
    -- Review H1: %ROWTYPE mirrors only the table's DECLARED columns; xmin is
    -- a system column and is never a %ROWTYPE field even under SELECT *. It
    -- is fetched as its own scalar via an explicit column reference.
    witness_xmin XID;
    admission_expiry TIMESTAMPTZ;
    commit_ts TIMESTAMPTZ;
    cert_verdict TEXT;
BEGIN
    IF p_message_id IS NULL THEN
        RAISE EXCEPTION 'Company message certification request is outside the closed contract';
    END IF;
    SELECT * INTO existing
      FROM kernel_company_message_commit_certifications
     WHERE subject_kind='acceptance' AND subject_id=p_message_id;
    IF existing.id IS NOT NULL THEN RETURN existing.verdict; END IF;

    SELECT * INTO STRICT witness
      FROM kernel_company_message_send_receipts
     WHERE message_id=p_message_id;
    SELECT xmin INTO STRICT witness_xmin
      FROM kernel_company_message_send_receipts
     WHERE id=witness.id;
    -- Loud, not silent: an invisible or missing admission row is an error
    -- (the maintenance read-path bite proves this path sees admission rows).
    SELECT admission.lease_expires_at INTO STRICT admission_expiry
      FROM kernel_company_scribe_admissions admission
     WHERE admission.id=witness.sender_admission_id;

    BEGIN
        commit_ts:=pg_xact_commit_timestamp(witness_xmin);
        IF commit_ts IS NULL THEN
            cert_verdict:='UNCERTIFIABLE';
        ELSIF commit_ts<=admission_expiry THEN
            cert_verdict:='PASS';
        ELSE
            cert_verdict:='FAIL';
        END IF;
    EXCEPTION WHEN OTHERS THEN
        commit_ts:=NULL;
        cert_verdict:='UNCERTIFIABLE';
    END;

    INSERT INTO kernel_company_message_commit_certifications (
        company_id,subject_kind,subject_id,acting_admission_id,
        commit_timestamp,admission_lease_expiry,verdict
    ) VALUES (
        '__CC_SUITE_COMPANY_ID__','acceptance',p_message_id,
        witness.sender_admission_id,commit_ts,admission_expiry,cert_verdict
    );
    RETURN cert_verdict;
END
$function$;

CREATE FUNCTION kernel_certify_company_acknowledgment(p_receipt_id UUID)
RETURNS TEXT
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    existing kernel_company_message_commit_certifications%ROWTYPE;
    witness kernel_company_message_consumption_receipts%ROWTYPE;
    witness_xmin XID;
    admission_expiry TIMESTAMPTZ;
    commit_ts TIMESTAMPTZ;
    cert_verdict TEXT;
BEGIN
    IF p_receipt_id IS NULL THEN
        RAISE EXCEPTION 'Company message certification request is outside the closed contract';
    END IF;
    SELECT * INTO existing
      FROM kernel_company_message_commit_certifications
     WHERE subject_kind='acknowledgment' AND subject_id=p_receipt_id;
    IF existing.id IS NOT NULL THEN RETURN existing.verdict; END IF;

    SELECT * INTO STRICT witness
      FROM kernel_company_message_consumption_receipts
     WHERE id=p_receipt_id;
    SELECT xmin INTO STRICT witness_xmin
      FROM kernel_company_message_consumption_receipts
     WHERE id=witness.id;
    SELECT admission.lease_expires_at INTO STRICT admission_expiry
      FROM kernel_company_scribe_admissions admission
     WHERE admission.id=witness.consuming_admission_id;

    BEGIN
        commit_ts:=pg_xact_commit_timestamp(witness_xmin);
        IF commit_ts IS NULL THEN
            cert_verdict:='UNCERTIFIABLE';
        ELSIF commit_ts<=admission_expiry THEN
            cert_verdict:='PASS';
        ELSE
            cert_verdict:='FAIL';
        END IF;
    EXCEPTION WHEN OTHERS THEN
        commit_ts:=NULL;
        cert_verdict:='UNCERTIFIABLE';
    END;

    INSERT INTO kernel_company_message_commit_certifications (
        company_id,subject_kind,subject_id,acting_admission_id,
        commit_timestamp,admission_lease_expiry,verdict
    ) VALUES (
        '__CC_SUITE_COMPANY_ID__','acknowledgment',p_receipt_id,
        witness.consuming_admission_id,commit_ts,admission_expiry,cert_verdict
    );
    RETURN cert_verdict;
END
$function$;

-- ---------------------------------------------------------------------------
-- Function surface
-- ---------------------------------------------------------------------------

CREATE FUNCTION kernel_send_company_message(
    p_recipient_role TEXT,
    p_message_kind TEXT,
    p_message_bytes BYTEA,
    p_idempotency_key TEXT,
    p_causal_parent_message_id UUID DEFAULT NULL
)
RETURNS kernel_company_message_send_outcome
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    actor RECORD;
    existing kernel_company_messages%ROWTYPE;
    parent kernel_company_messages%ROWTYPE;
    inserted kernel_company_messages%ROWTYPE;
    receipt kernel_company_message_send_receipts%ROWTYPE;
    outcome kernel_company_message_send_outcome;
    next_sequence BIGINT;
    request_digest TEXT;
    recent_count BIGINT;
    recent_octets BIGINT;
    fleet_count BIGINT;
    oldest_in_window TIMESTAMPTZ;
    envelope JSONB;
BEGIN
    SELECT * INTO STRICT actor
      FROM kernel_current_company_message_admission();

    -- Explicit refusal of NULL and malformed input before any lookup.
    IF p_recipient_role IS NULL
       OR p_message_kind IS NULL
       OR p_message_bytes IS NULL
       OR p_idempotency_key IS NULL
       OR p_recipient_role NOT IN (
           'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
           'cclo','ccgo','ccco','utility'
       )
       OR p_message_kind NOT IN (
           'agent_direct','task_handoff','audit_request',
           'reply_classification','arbiter_ask'
       )
       OR octet_length(p_message_bytes) NOT BETWEEN 1 AND 65536
       OR btrim(p_idempotency_key)=''
       OR octet_length(p_idempotency_key)>200 THEN
        RAISE EXCEPTION 'Company message request is outside the closed contract';
    END IF;

    -- Recipient admissibility (review finding: black-hole recipients).
    IF p_recipient_role<>'scribe' AND NOT EXISTS (
        SELECT 1 FROM kernel_company_role_admission_targets target
         WHERE target.company_id='__CC_SUITE_COMPANY_ID__'
           AND target.role_type=p_recipient_role
           AND target.state='eligible'
    ) THEN
        RAISE EXCEPTION 'Company message recipient has no admissible consumer';
    END IF;

    -- task_handoff obligation envelope: versioned, validated, refused before
    -- any insert (design invariant 13).
    IF p_message_kind='task_handoff' THEN
        BEGIN
            envelope:=convert_from(p_message_bytes,'UTF8')::JSONB;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'Company handoff obligation envelope is unparseable';
        END;
        IF (envelope->>'envelope_version') IS DISTINCT FROM '1'
           OR coalesce(envelope->>'owner_role','') NOT IN (
               'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
               'cclo','ccgo','ccco','utility'
           )
           OR btrim(coalesce(envelope->>'action',''))=''
           OR btrim(coalesce(envelope->>'expected_artifact',''))='' THEN
            RAISE EXCEPTION 'Company handoff obligation envelope is outside the closed contract';
        END IF;
        -- Review H5: the obligation opens for the message's own recipient,
        -- never a third role, a consumer-less role, or the founder.
        IF (envelope->>'owner_role') IS DISTINCT FROM p_recipient_role THEN
            RAISE EXCEPTION 'Company handoff obligation owner must equal the message recipient';
        END IF;
        -- Deadline validated in the pre-insert contract block, not left to
        -- land as a post-insert constraint violation (review L5). Review M1:
        -- the deadline is REQUIRED, matching invariant 13 and the NOT NULL
        -- column — an absent or empty deadline is a contract refusal here,
        -- not a silent NULL default.
        IF NOT (envelope ? 'deadline_hours')
           OR NULLIF(envelope->>'deadline_hours','') IS NULL THEN
            RAISE EXCEPTION 'Company handoff obligation deadline is outside the closed contract';
        END IF;
        -- Review R6-L1: the pre-insert check previously enforced only the
        -- lower bound; the upper bound existed solely in the table CHECK,
        -- so an over-8760 deadline reached a constraint violation instead
        -- of this function's named contract-refusal shape. Both bounds are
        -- enforced here now, matching what the design and freeze evidence
        -- already claimed.
        BEGIN
            IF (envelope->>'deadline_hours')::NUMERIC<=0
               OR (envelope->>'deadline_hours')::NUMERIC>8760 THEN
                RAISE EXCEPTION 'out-of-bound';
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'Company handoff obligation deadline is outside the closed contract';
        END;
    END IF;

    request_digest:=encode(digest(p_message_bytes,'sha256'),'hex');

    -- Serializing locks, fixed order: sender lineage, then company-wide.
    PERFORM pg_advisory_xact_lock(
        20260807,kernel_company_message_lock_objid('sender',actor.role_type)
    );
    PERFORM pg_advisory_xact_lock(
        20260807,kernel_company_message_lock_objid('company',NULL)
    );

    -- Role-lineage idempotency.
    SELECT * INTO existing
      FROM kernel_company_messages
     WHERE company_id='__CC_SUITE_COMPANY_ID__'
       AND sender_role=actor.role_type
       AND idempotency_key=p_idempotency_key
     FOR SHARE;
    IF existing.id IS NOT NULL THEN
        -- Exact-retry identity survives payload retention (review H3): while
        -- bytes are present they compare directly; after the receipted expiry
        -- the presented bytes are hashed against the permanent digest and
        -- octet count. Either way, any changed field conflicts.
        IF existing.recipient_role IS DISTINCT FROM p_recipient_role
           OR existing.message_kind IS DISTINCT FROM p_message_kind
           OR existing.message_sha256 IS DISTINCT FROM request_digest
           OR existing.message_octets
              IS DISTINCT FROM octet_length(p_message_bytes)
           OR (existing.payload_state='present'
               AND existing.message_bytes IS DISTINCT FROM p_message_bytes)
           OR existing.causal_parent_message_id
              IS DISTINCT FROM p_causal_parent_message_id THEN
            RAISE EXCEPTION 'Company message idempotency conflict';
        END IF;
        SELECT * INTO STRICT receipt
          FROM kernel_company_message_send_receipts
         WHERE message_id=existing.id;
        outcome.status:='accepted';
        outcome.receipt:=receipt;
        RETURN outcome;
    END IF;

    -- Capacity bounds: typed non-success outcomes, no row written; windows
    -- key on role lineage; all counts exact under the locks above.
    SELECT count(*),coalesce(sum(message_octets),0),min(accepted_at)
      INTO recent_count,recent_octets,oldest_in_window
      FROM kernel_company_messages
     WHERE sender_role=actor.role_type
       AND accepted_at>clock_timestamp()-interval '60 seconds';
    IF recent_count>=30 THEN
        outcome.status:='refused_limit';
        outcome.refusal_code:='sender-count-rate';
        outcome.violated_bound:='30 messages per role lineage per 60 seconds';
        outcome.retry_after_seconds:=greatest(
            0,60-extract(epoch FROM clock_timestamp()-oldest_in_window)
        );
        RETURN outcome;
    END IF;
    IF recent_octets+octet_length(p_message_bytes)>262144 THEN
        outcome.status:='refused_limit';
        outcome.refusal_code:='sender-byte-rate';
        outcome.violated_bound:='262144 bytes per role lineage per 60 seconds';
        outcome.retry_after_seconds:=greatest(
            0,60-extract(epoch FROM clock_timestamp()-oldest_in_window)
        );
        RETURN outcome;
    END IF;

    SELECT count(*),min(accepted_at)
      INTO fleet_count,oldest_in_window
      FROM kernel_company_messages
     WHERE company_id='__CC_SUITE_COMPANY_ID__'
       AND accepted_at>clock_timestamp()-interval '60 seconds';
    IF fleet_count>=120 THEN
        outcome.status:='refused_limit';
        outcome.refusal_code:='fleet-budget';
        outcome.violated_bound:='120 accepted messages fleet-wide per 60 seconds';
        outcome.retry_after_seconds:=greatest(
            0,60-extract(epoch FROM clock_timestamp()-oldest_in_window)
        );
        RETURN outcome;
    END IF;

    IF (
        SELECT count(*) FROM kernel_company_messages
         WHERE sender_role=actor.role_type
           AND recipient_role=p_recipient_role
           AND state IN ('queued','leased')
    )>=32 THEN
        outcome.status:='refused_limit';
        outcome.refusal_code:='pair-backlog';
        outcome.violated_bound:='32 outstanding per sender-recipient pair';
        RETURN outcome;
    END IF;
    IF (
        SELECT count(*) FROM kernel_company_messages
         WHERE recipient_role=p_recipient_role
           AND state IN ('queued','leased')
    )>=128 THEN
        outcome.status:='refused_limit';
        outcome.refusal_code:='recipient-backlog';
        outcome.violated_bound:='128 outstanding per recipient';
        RETURN outcome;
    END IF;

    IF p_causal_parent_message_id IS NOT NULL THEN
        SELECT * INTO parent
          FROM kernel_company_messages
         WHERE id=p_causal_parent_message_id
         FOR UPDATE;
        IF parent.id IS NULL
           OR parent.state IS DISTINCT FROM 'consumed'
           OR parent.recipient_role IS DISTINCT FROM actor.role_type
           OR NOT EXISTS (
                SELECT 1
                  FROM kernel_company_message_consumption_receipts consumed
                 WHERE consumed.message_id=parent.id
                   AND consumed.recipient_role=actor.role_type
           )
           OR parent.causal_depth>=4 THEN
            RAISE EXCEPTION 'Company message causal parent is unauthorized or exhausted';
        END IF;
        IF (
            SELECT count(*) FROM kernel_company_messages child
             WHERE child.causal_parent_message_id=parent.id
        )>=2 THEN
            RAISE EXCEPTION 'Company message causal child budget exceeded';
        END IF;
    END IF;

    INSERT INTO kernel_company_message_recipient_sequences (
        company_id,recipient_role,last_sequence
    ) VALUES ('__CC_SUITE_COMPANY_ID__',p_recipient_role,1)
    ON CONFLICT (company_id,recipient_role) DO UPDATE
       SET last_sequence=kernel_company_message_recipient_sequences.last_sequence+1,
           updated_at=clock_timestamp()
    RETURNING last_sequence INTO next_sequence;

    inserted.id:=gen_random_uuid();
    INSERT INTO kernel_company_messages (
        id,company_id,sender_admission_id,sender_role,recipient_role,
        recipient_sequence,idempotency_key,message_kind,message_bytes,
        message_octets,message_sha256,causal_parent_message_id,
        causal_root_message_id,causal_depth
    ) VALUES (
        inserted.id,'__CC_SUITE_COMPANY_ID__',actor.admission_id,actor.role_type,
        p_recipient_role,next_sequence,p_idempotency_key,p_message_kind,
        p_message_bytes,octet_length(p_message_bytes),request_digest,
        p_causal_parent_message_id,
        CASE WHEN parent.id IS NULL THEN inserted.id ELSE parent.causal_root_message_id END,
        CASE WHEN parent.id IS NULL THEN 0 ELSE parent.causal_depth+1 END
    ) RETURNING * INTO inserted;

    -- Same-transaction obligation fact for a declared handoff.
    IF p_message_kind='task_handoff' THEN
        INSERT INTO kernel_company_message_obligations (
            message_id,company_id,envelope_version,owner_role,action,
            expected_artifact,deadline_hours
        ) VALUES (
            inserted.id,'__CC_SUITE_COMPANY_ID__',1,
            envelope->>'owner_role',envelope->>'action',
            envelope->>'expected_artifact',
            NULLIF(envelope->>'deadline_hours','')::NUMERIC
        );
    END IF;

    SELECT * INTO STRICT receipt
      FROM kernel_company_message_send_receipts
     WHERE message_id=inserted.id;
    outcome.status:='accepted';
    outcome.receipt:=receipt;
    RETURN outcome;
END
$function$;

CREATE FUNCTION kernel_claim_company_message()
RETURNS TABLE (
    message_id UUID,recipient_sequence BIGINT,message_kind TEXT,
    message_bytes BYTEA,message_sha256 TEXT,sender_admission_id UUID,
    sender_role TEXT,delivery_attempt_id UUID,lease_token TEXT,
    lease_expires_at TIMESTAMPTZ
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    actor RECORD;
    candidate kernel_company_messages%ROWTYPE;
    attempt kernel_company_message_delivery_attempts%ROWTYPE;
    clear_token TEXT;
    cert_verdict TEXT;
BEGIN
    SELECT * INTO STRICT actor
      FROM kernel_current_company_message_admission();
    PERFORM pg_advisory_xact_lock(
        20260807,
        kernel_company_message_lock_objid('recipient',actor.role_type)
    );

    -- Uniform row order (message, then attempt): expire via message rows
    -- first, closing their open attempts in the same pass.
    UPDATE kernel_company_messages message
       SET state=CASE
               WHEN message.delivery_attempt_count>=5 THEN 'dead_lettered'
               ELSE 'queued'
           END,
           active_attempt_id=NULL,
           active_lease_id=NULL,
           active_lease_expires_at=NULL,
           dead_lettered_at=CASE
               WHEN message.delivery_attempt_count>=5 THEN clock_timestamp()
               ELSE NULL
           END,
           dead_letter_reason=CASE
               WHEN message.delivery_attempt_count>=5
               THEN 'delivery-attempt-limit'
               ELSE NULL
           END
     WHERE message.recipient_role=actor.role_type
       AND message.state='leased'
       AND message.active_lease_expires_at<=clock_timestamp();

    UPDATE kernel_company_message_delivery_attempts delivery
       SET state=CASE
               WHEN delivery.attempt_number>=5 THEN 'dead_lettered'
               ELSE 'expired'
           END,
           closed_at=clock_timestamp()
     WHERE delivery.recipient_role=actor.role_type
       AND delivery.state='open'
       AND delivery.lease_expires_at<=clock_timestamp();

    -- Certification-gated head selection (review C1): only a PASS head
    -- leases. A FAIL or UNCERTIFIABLE head quarantines terminally — the
    -- adversarial expired-at-commit send is never deliverable — and the
    -- selection repeats on the next sequence.
    LOOP
        SELECT * INTO candidate
          FROM kernel_company_messages message
         WHERE message.recipient_role=actor.role_type
           AND message.state='queued'
           AND NOT EXISTS (
               SELECT 1 FROM kernel_company_messages lower_message
                WHERE lower_message.recipient_role=message.recipient_role
                  AND lower_message.recipient_sequence<message.recipient_sequence
                  AND lower_message.state IN ('queued','leased')
           )
         ORDER BY message.recipient_sequence
         FOR UPDATE SKIP LOCKED
         LIMIT 1;
        IF candidate.id IS NULL THEN RETURN; END IF;

        cert_verdict:=kernel_certify_company_acceptance(candidate.id);
        EXIT WHEN cert_verdict='PASS';

        UPDATE kernel_company_messages
           SET state='quarantined',
               quarantined_at=clock_timestamp(),
               quarantine_reason=
                   'acceptance-certification-'||lower(cert_verdict)
         WHERE id=candidate.id;
        -- Review F-C1 (CRITICAL): a quarantined message's task_handoff
        -- obligation, if any, is cancelled in the same statement group —
        -- never left 'accepted' where a reconciler race could still open it
        -- before observing the quarantine (the guard trigger's PASS check
        -- is the primary gate; this is belt-and-suspenders honesty in the
        -- obligation's own state).
        -- r10 (arch F-3): alias-qualified — message_id is also claim's OUT
        -- parameter; the unqualified WHERE was ambiguous and aborted the
        -- whole claim transaction on every FAIL/UNCERTIFIABLE head,
        -- breaking the claim-side quarantine path (the sweeper's twin was
        -- always clean).
        UPDATE kernel_company_message_obligations obligation
           SET state='cancelled',
               cancelled_at=clock_timestamp(),
               cancelled_certification_id=(
                   SELECT cert.id
                     FROM kernel_company_message_commit_certifications cert
                    WHERE cert.subject_kind='acceptance'
                      AND cert.subject_id=candidate.id
                      AND cert.verdict IN ('FAIL','UNCERTIFIABLE')
               ),
               cancel_reason=
                   'acceptance-certification-'||lower(cert_verdict)
         WHERE obligation.message_id=candidate.id
           AND obligation.state='accepted';
    END LOOP;

    clear_token:=encode(gen_random_bytes(32),'hex');
    INSERT INTO kernel_company_message_delivery_attempts (
        company_id,message_id,attempt_number,recipient_role,
        consuming_admission_id,lease_id,lease_token_sha256,lease_expires_at
    ) VALUES (
        candidate.company_id,candidate.id,candidate.delivery_attempt_count+1,
        candidate.recipient_role,actor.admission_id,gen_random_uuid(),
        encode(digest(convert_to(clear_token,'UTF8'),'sha256'),'hex'),
        clock_timestamp()+interval '60 seconds'
    ) RETURNING * INTO attempt;

    UPDATE kernel_company_messages
       SET state='leased',delivery_attempt_count=attempt.attempt_number,
           active_attempt_id=attempt.id,active_lease_id=attempt.lease_id,
           active_lease_expires_at=attempt.lease_expires_at
     WHERE id=candidate.id;

    RETURN QUERY SELECT
        candidate.id,candidate.recipient_sequence,candidate.message_kind,
        candidate.message_bytes,candidate.message_sha256,
        candidate.sender_admission_id,candidate.sender_role,
        attempt.id,clear_token,attempt.lease_expires_at;
END
$function$;

CREATE FUNCTION kernel_consume_company_message(
    p_message_id UUID,p_delivery_attempt_id UUID,
    p_lease_token TEXT,p_message_sha256 TEXT,p_bytes_proof TEXT
)
RETURNS kernel_company_message_consumption_receipts
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    actor RECORD;
    message kernel_company_messages%ROWTYPE;
    attempt kernel_company_message_delivery_attempts%ROWTYPE;
    receipt kernel_company_message_consumption_receipts%ROWTYPE;
    presented_token_digest TEXT;
    expected_bytes_proof TEXT;
BEGIN
    -- Review H6: the lease token has exactly the generated shape — 64
    -- lowercase hex characters. An unbounded string is refused BEFORE any
    -- conversion or hashing work.
    IF p_message_id IS NULL
       OR p_delivery_attempt_id IS NULL
       OR p_lease_token IS NULL
       OR p_lease_token!~'^[0-9a-f]{64}$'
       OR p_message_sha256 IS NULL
       OR p_message_sha256!~'^[0-9a-f]{64}$'
       OR p_bytes_proof IS NULL
       OR p_bytes_proof!~'^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Company message consumption request is outside the closed contract';
    END IF;

    SELECT * INTO STRICT actor
      FROM kernel_current_company_message_admission();

    -- The recipient lock serializes consume with claim and the sweeper, so
    -- the lease-expiry boundary cannot interleave (review H3).
    PERFORM pg_advisory_xact_lock(
        20260807,
        kernel_company_message_lock_objid('recipient',actor.role_type)
    );

    SELECT * INTO message FROM kernel_company_messages
     WHERE id=p_message_id FOR UPDATE;
    SELECT * INTO attempt FROM kernel_company_message_delivery_attempts
     WHERE id=p_delivery_attempt_id AND message_id=p_message_id FOR UPDATE;

    IF message.id IS NULL OR attempt.id IS NULL THEN
        RAISE EXCEPTION 'Company message consumption is unauthorized, stale, or changed';
    END IF;

    presented_token_digest:=encode(
        digest(convert_to(p_lease_token,'UTF8'),'sha256'),'hex'
    );

    -- Completed replay verifies token digest and bytes proof. While the
    -- payload is present the proof recomputes from stored bytes; after the
    -- receipted retention transition it matches the receipt's recorded value.
    -- Review H4: a payload-present replay is scoped to the EXACT original
    -- consuming admission (the strongest binding available). A post-
    -- retention replay (90+ days later) cannot use that binding — the
    -- original admission's lease is capped at five minutes and cannot still
    -- be live — so it is authorized by RECIPIENT ROLE LINEAGE instead,
    -- exactly mirroring the send-side idempotency pattern (invariant 4). The
    -- token digest and bytes-proof checks below still require the caller to
    -- possess the original secret token, so lineage scoping widens WHO may
    -- present the proof, never WHAT proves it.
    IF message.state='consumed' THEN
        IF message.payload_state='present' THEN
            SELECT * INTO receipt
              FROM kernel_company_message_consumption_receipts
             WHERE message_id=message.id
               AND delivery_attempt_id=attempt.id
               AND consuming_admission_id=actor.admission_id
               AND message_sha256=p_message_sha256;
        ELSE
            SELECT * INTO receipt
              FROM kernel_company_message_consumption_receipts
             WHERE message_id=message.id
               AND delivery_attempt_id=attempt.id
               AND recipient_role=actor.role_type
               AND message_sha256=p_message_sha256;
        END IF;
        IF receipt.id IS NULL
           OR attempt.lease_token_sha256
              IS DISTINCT FROM presented_token_digest THEN
            RAISE EXCEPTION 'Company message consumption replay conflict';
        END IF;
        IF message.payload_state='present' THEN
            expected_bytes_proof:=encode(
                digest(convert_to(p_lease_token,'UTF8')||message.message_bytes,
                       'sha256'),'hex'
            );
        ELSE
            expected_bytes_proof:=receipt.bytes_proof;
        END IF;
        IF expected_bytes_proof IS DISTINCT FROM p_bytes_proof THEN
            RAISE EXCEPTION 'Company message consumption replay conflict';
        END IF;
        RETURN receipt;
    END IF;

    expected_bytes_proof:=encode(
        digest(convert_to(p_lease_token,'UTF8')||message.message_bytes,
               'sha256'),'hex'
    );

    IF message.recipient_role IS DISTINCT FROM actor.role_type
       OR attempt.recipient_role IS DISTINCT FROM actor.role_type
       OR attempt.consuming_admission_id IS DISTINCT FROM actor.admission_id
       OR message.state IS DISTINCT FROM 'leased'
       OR attempt.state IS DISTINCT FROM 'open'
       OR message.active_attempt_id IS DISTINCT FROM attempt.id
       OR message.active_lease_id IS DISTINCT FROM attempt.lease_id
       OR message.active_lease_expires_at IS NULL
       OR message.active_lease_expires_at<=clock_timestamp()
       OR attempt.lease_expires_at<=clock_timestamp()
       OR message.message_sha256 IS DISTINCT FROM p_message_sha256
       OR attempt.lease_token_sha256 IS DISTINCT FROM presented_token_digest
       OR expected_bytes_proof IS DISTINCT FROM p_bytes_proof THEN
        RAISE EXCEPTION 'Company message consumption is unauthorized, stale, or changed';
    END IF;

    UPDATE kernel_company_messages
       SET state='consumed',active_attempt_id=NULL,active_lease_id=NULL,
           active_lease_expires_at=NULL,consumed_at=clock_timestamp()
     WHERE id=message.id;
    UPDATE kernel_company_message_delivery_attempts
       SET state='consumed',closed_at=clock_timestamp()
     WHERE id=attempt.id;
    INSERT INTO kernel_company_message_consumption_receipts (
        company_id,message_id,delivery_attempt_id,consuming_admission_id,
        recipient_role,message_sha256,message_octets,bytes_proof,consumed_at
    ) VALUES (
        message.company_id,message.id,attempt.id,actor.admission_id,
        actor.role_type,message.message_sha256,message.message_octets,
        p_bytes_proof,clock_timestamp()
    ) RETURNING * INTO receipt;
    RETURN receipt;
END
$function$;

CREATE FUNCTION kernel_company_message_receipt(p_message_id UUID)
RETURNS TABLE (
    send_receipt kernel_company_message_send_receipts,
    current_state TEXT,delivery_attempt_count SMALLINT,
    dead_lettered_at TIMESTAMPTZ,dead_letter_reason TEXT,
    quarantined_at TIMESTAMPTZ,quarantine_reason TEXT,
    payload_state TEXT,
    retention_receipt kernel_company_message_retention_receipts,
    consumption_receipt kernel_company_message_consumption_receipts,
    acceptance_certification TEXT,
    acknowledgment_certification TEXT
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE actor RECORD;
BEGIN
    IF p_message_id IS NULL THEN
        RAISE EXCEPTION 'Company message receipt request is outside the closed contract';
    END IF;
    SELECT * INTO STRICT actor
      FROM kernel_current_company_message_admission();
    RETURN QUERY
    SELECT sent,message.state,message.delivery_attempt_count,
           message.dead_lettered_at,message.dead_letter_reason,
           message.quarantined_at,message.quarantine_reason,
           message.payload_state,retention,consumed,
           coalesce(accept_cert.verdict,'PENDING'),
           CASE WHEN consumed.id IS NULL THEN NULL
                ELSE coalesce(ack_cert.verdict,'PENDING') END
      FROM kernel_company_messages message
      JOIN kernel_company_message_send_receipts sent
        ON sent.message_id=message.id
      LEFT JOIN kernel_company_message_retention_receipts retention
        ON retention.message_id=message.id
      LEFT JOIN kernel_company_message_consumption_receipts consumed
        ON consumed.message_id=message.id
      LEFT JOIN kernel_company_message_commit_certifications accept_cert
        ON accept_cert.subject_kind='acceptance'
       AND accept_cert.subject_id=message.id
      LEFT JOIN kernel_company_message_commit_certifications ack_cert
        ON ack_cert.subject_kind='acknowledgment'
       AND ack_cert.subject_id=consumed.id
     WHERE message.id=p_message_id
       AND actor.role_type IN (message.sender_role,message.recipient_role);
END
$function$;

-- ---------------------------------------------------------------------------
-- Maintenance: liveness + certification (design 3.3.2 and 3.4). Executes as
-- the dedicated kernel_company_message_maintenance principal only.
-- ---------------------------------------------------------------------------

CREATE FUNCTION kernel_sweep_company_message_plane()
RETURNS TABLE (
    swept_role TEXT,attempts_closed INTEGER,messages_requeued INTEGER,
    messages_dead_lettered INTEGER,messages_quarantined INTEGER,
    certifications_written INTEGER,certification_failures INTEGER,
    certification_error BOOLEAN,oldest_nonterminal_age_seconds NUMERIC,
    oldest_unmaterialized_witness_age_seconds NUMERIC
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    role_name TEXT;
    closed_count INTEGER;
    requeued_count INTEGER;
    dead_count INTEGER;
    quarantined_count INTEGER;
    cert_count INTEGER;
    cert_fail INTEGER;
    cert_error BOOLEAN;
    cert_subject RECORD;
    cert_verdict TEXT;
    oldest_age NUMERIC;
    oldest_unmaterialized NUMERIC;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'scribe','cos','ccto','ccpo','ccde','ccro','ccfo','ccmo',
        'cclo','ccgo','ccco','utility'
    ] LOOP
        PERFORM pg_advisory_xact_lock(
            20260807,
            kernel_company_message_lock_objid('recipient',role_name)
        );

        UPDATE kernel_company_messages message
           SET state='queued',
               active_attempt_id=NULL,
               active_lease_id=NULL,
               active_lease_expires_at=NULL
         WHERE message.recipient_role=role_name
           AND message.state='leased'
           AND message.active_lease_expires_at<=clock_timestamp()
           AND message.delivery_attempt_count<5;
        GET DIAGNOSTICS requeued_count=ROW_COUNT;

        UPDATE kernel_company_messages message
           SET state='dead_lettered',
               active_attempt_id=NULL,
               active_lease_id=NULL,
               active_lease_expires_at=NULL,
               dead_lettered_at=clock_timestamp(),
               dead_letter_reason='delivery-attempt-limit'
         WHERE message.recipient_role=role_name
           AND message.state='leased'
           AND message.active_lease_expires_at<=clock_timestamp()
           AND message.delivery_attempt_count>=5;
        GET DIAGNOSTICS dead_count=ROW_COUNT;

        UPDATE kernel_company_message_delivery_attempts delivery
           SET state=CASE
                   WHEN delivery.attempt_number>=5
                   THEN 'dead_lettered' ELSE 'expired' END,
               closed_at=clock_timestamp()
         WHERE delivery.recipient_role=role_name
           AND delivery.state='open'
           AND delivery.lease_expires_at<=clock_timestamp();
        GET DIAGNOSTICS closed_count=ROW_COUNT;

        -- Certification (reviews C1/H1/M2, arch MEDIUM-1/2, verif F5, fresh
        -- H2): per-subject via the helpers (witness-row xmin, UNCERTIFIABLE
        -- containment inside the helper for an uncomputable timestamp),
        -- counted PER ROLE and PER SWEEP. Containment is PER SUBJECT, not
        -- per role: a PL/pgSQL BEGIN/EXCEPTION creates an implicit savepoint,
        -- and rolling one back undoes every database write since the block
        -- opened — a role-wide block would silently discard EARLIER
        -- subjects' successful certification inserts (and the write/failure
        -- counters that survive as plain variables would then overstate what
        -- was actually written) the moment ANY later subject in the same
        -- role's loop raised. Scoping the block to one subject means only
        -- that subject's own (rolled-back) attempt is lost; every prior and
        -- subsequent subject in the role's loop is unaffected, and a counter
        -- increments only on the same success path whose write survives.
        -- This is also the escape-class containment fixture the sweeper
        -- summary's certification_error flag exists to report: an admission
        -- row invisible to the read path, or any other failure the STRICT
        -- reads raise (deliberately outside the helper's OWN inner
        -- UNCERTIFIABLE catch — "loud, not silent") sets cert_error for the
        -- role and leaves that one subject uncertified, still queued for the
        -- next sweep, without touching any other subject.
        quarantined_count:=0;
        cert_count:=0;
        cert_fail:=0;
        cert_error:=FALSE;
        FOR cert_subject IN
            SELECT message.id AS subject_id,message.state AS message_state
              FROM kernel_company_messages message
             WHERE message.recipient_role=role_name
               AND NOT EXISTS (
                   SELECT 1
                     FROM kernel_company_message_commit_certifications c
                    WHERE c.subject_kind='acceptance'
                      AND c.subject_id=message.id
               )
        LOOP
            -- Review F-H2: counters increment ONLY after every statement in
            -- this subject's block has completed without raising — never
            -- immediately after the helper call. A raise anywhere in this
            -- block (including the quarantine/cancel UPDATEs) rolls back
            -- every write this subject made via the block's implicit
            -- savepoint; if the counters had already incremented before
            -- that point, the returned summary would claim a durable write
            -- that no longer exists. Deferring every increment to the last
            -- line means the summary can only ever report what is actually
            -- durable.
            BEGIN
                cert_verdict:=kernel_certify_company_acceptance(
                    cert_subject.subject_id
                );
                IF cert_verdict<>'PASS' AND cert_subject.message_state='queued' THEN
                    UPDATE kernel_company_messages
                       SET state='quarantined',
                           quarantined_at=clock_timestamp(),
                           quarantine_reason=
                               'acceptance-certification-'||lower(cert_verdict)
                     WHERE id=cert_subject.subject_id
                       AND state='queued';
                    -- Review F-C1: cancel any accepted obligation in the
                    -- same subject block as the message's quarantine.
                    UPDATE kernel_company_message_obligations
                       SET state='cancelled',
                           cancelled_at=clock_timestamp(),
                           cancelled_certification_id=(
                               SELECT cert.id
                                 FROM
                                 kernel_company_message_commit_certifications
                                 cert
                                WHERE cert.subject_kind='acceptance'
                                  AND cert.subject_id
                                      =cert_subject.subject_id
                                  AND cert.verdict
                                      IN ('FAIL','UNCERTIFIABLE')
                           ),
                           cancel_reason=
                               'acceptance-certification-'||lower(cert_verdict)
                     WHERE message_id=cert_subject.subject_id
                       AND state='accepted';
                END IF;
                cert_count:=cert_count+1;
                IF cert_verdict<>'PASS' THEN
                    cert_fail:=cert_fail+1;
                    IF cert_subject.message_state='queued' THEN
                        quarantined_count:=quarantined_count+1;
                    END IF;
                END IF;
            EXCEPTION WHEN OTHERS THEN
                cert_error:=TRUE;
            END;
        END LOOP;

        FOR cert_subject IN
            SELECT receipt.id AS subject_id
              FROM kernel_company_message_consumption_receipts receipt
             WHERE receipt.recipient_role=role_name
               AND NOT EXISTS (
                   SELECT 1
                     FROM kernel_company_message_commit_certifications c
                    WHERE c.subject_kind='acknowledgment'
                      AND c.subject_id=receipt.id
               )
        LOOP
            BEGIN
                cert_verdict:=kernel_certify_company_acknowledgment(
                    cert_subject.subject_id
                );
                cert_count:=cert_count+1;
                IF cert_verdict<>'PASS' THEN
                    cert_fail:=cert_fail+1;
                END IF;
            EXCEPTION WHEN OTHERS THEN
                cert_error:=TRUE;
            END;
        END LOOP;

        SELECT coalesce(max(extract(
            epoch FROM clock_timestamp()-message.accepted_at)),0)
          INTO oldest_age
          FROM kernel_company_messages message
         WHERE message.recipient_role=role_name
           AND message.state IN ('queued','leased');

        -- Certification-lag metric (arch MEDIUM-1): oldest witness row for
        -- this role still lacking a durable certification receipt. The
        -- launchd wrapper alarms when this exceeds the 5-minute bound.
        SELECT coalesce(max(witness_age),0) INTO oldest_unmaterialized
          FROM (
            SELECT extract(epoch FROM clock_timestamp()-sent.accepted_at)
                   AS witness_age
              FROM kernel_company_message_send_receipts sent
             WHERE sent.recipient_role=role_name
               AND NOT EXISTS (
                   SELECT 1
                     FROM kernel_company_message_commit_certifications c
                    WHERE c.subject_kind='acceptance'
                      AND c.subject_id=sent.message_id
               )
            UNION ALL
            SELECT extract(epoch FROM clock_timestamp()-consumed.consumed_at)
              FROM kernel_company_message_consumption_receipts consumed
             WHERE consumed.recipient_role=role_name
               AND NOT EXISTS (
                   SELECT 1
                     FROM kernel_company_message_commit_certifications c
                    WHERE c.subject_kind='acknowledgment'
                      AND c.subject_id=consumed.id
               )
          ) witness_ages;

        RETURN QUERY SELECT role_name,closed_count,requeued_count,
            dead_count,quarantined_count,cert_count,cert_fail,cert_error,
            oldest_age,oldest_unmaterialized;
    END LOOP;
END
$function$;

-- Receipted payload-retention transition (review H4): the ONLY legal
-- post-terminal mutation, maintenance-owned, 90-day default pending
-- ratification.
CREATE FUNCTION kernel_expire_company_message_payload(p_message_id UUID)
RETURNS kernel_company_message_retention_receipts
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    message kernel_company_messages%ROWTYPE;
    receipt kernel_company_message_retention_receipts%ROWTYPE;
    terminal_ts TIMESTAMPTZ;
BEGIN
    IF p_message_id IS NULL THEN
        RAISE EXCEPTION 'Company message retention request is outside the closed contract';
    END IF;
    SELECT * INTO message FROM kernel_company_messages
     WHERE id=p_message_id FOR UPDATE;
    terminal_ts:=coalesce(message.consumed_at,message.dead_lettered_at,
                          message.quarantined_at);
    IF message.id IS NULL
       OR message.payload_state IS DISTINCT FROM 'present'
       OR message.state NOT IN ('consumed','dead_lettered','quarantined')
       OR terminal_ts IS NULL
       OR terminal_ts>clock_timestamp()-interval '90 days' THEN
        RAISE EXCEPTION 'Company message payload is not retention-eligible';
    END IF;

    PERFORM set_config(
        'mise.kernel_payload_retention',message.id::TEXT,true
    );
    UPDATE kernel_company_messages
       SET payload_state='expired',message_bytes=NULL
     WHERE id=message.id;
    PERFORM set_config('mise.kernel_payload_retention','',true);

    INSERT INTO kernel_company_message_retention_receipts (
        message_id,company_id,message_sha256,message_octets,
        terminal_state,terminal_at
    ) VALUES (
        message.id,message.company_id,message.message_sha256,
        message.message_octets,message.state,terminal_ts
    ) RETURNING * INTO receipt;
    RETURN receipt;
END
$function$;

REVOKE ALL ON FUNCTION kernel_send_company_message(
    TEXT,TEXT,BYTEA,TEXT,UUID
) FROM PUBLIC;
REVOKE ALL ON FUNCTION kernel_claim_company_message() FROM PUBLIC;
REVOKE ALL ON FUNCTION kernel_consume_company_message(
    UUID,UUID,TEXT,TEXT,TEXT
) FROM PUBLIC;
REVOKE ALL ON FUNCTION kernel_company_message_receipt(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION kernel_sweep_company_message_plane() FROM PUBLIC;
REVOKE ALL ON FUNCTION kernel_expire_company_message_payload(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION kernel_company_message_lock_objid(TEXT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION kernel_certify_company_acceptance(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION kernel_certify_company_acknowledgment(UUID) FROM PUBLIC;
-- The certify helpers are internal to the definer surface: no principal
-- receives EXECUTE on them; claim and the sweeper call them as the owner.

-- The dedicated maintenance principal (design 3.4): NOLOGIN until the
-- provisioner materializes credentials, non-superuser, NOBYPASS,
-- NOREPLICATION. Review R6-H1, closed structurally (r9 sweep of the stale
-- narrative per arch HIGH-2 / verif M-2): a PRE-EXISTING role of this name
-- REFUSES the migration OUTRIGHT — no posture validation exists or is
-- wanted, because no in-database sweep can prove a foreign role's
-- cluster-wide grants, credential material, validity window, connection
-- limit, rolconfig, or inherit behavior.  The migration admits only a role
-- whose entire history it owns; the operator drops or renames a
-- pre-existing role under separate authority and re-runs.  The
-- unprovable-posture class is eliminated, not enumerated.
DO $create_maintenance_principal$
DECLARE
    existing pg_roles%ROWTYPE;
BEGIN
    SELECT * INTO existing FROM pg_roles
     WHERE rolname='kernel_company_message_maintenance';
    -- Review R6-H1, closed structurally: pre-existing-role REUSE IS
    -- REFUSED OUTRIGHT.  The prior revision validated a reused role's
    -- posture with a local-catalog ACL/ownership/membership sweep, and the
    -- review correctly held that no in-database sweep can prove the
    -- cluster-wide, credential, attribute, configuration, and ownership
    -- posture of a role this migration did not create (other databases on
    -- the cluster, password/credential material, validity windows,
    -- connection limits, rolconfig, inherit behavior are all outside its
    -- reach).  Rather than attest the unprovable, the migration accepts
    -- only a role whose entire history it owns: if the name exists at all,
    -- refuse fail-closed and let the operator drop or rename it under
    -- separate authority, then re-run.  The unprovable-posture class is
    -- eliminated, not enumerated.
    IF existing.rolname IS NOT NULL THEN
        RAISE EXCEPTION 'kernel_company_message_maintenance already exists; this migration only admits a role it creates itself -- drop or rename the existing role under separate authority and re-run';
    END IF;
    CREATE ROLE kernel_company_message_maintenance
        NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
        NOREPLICATION;
END
$create_maintenance_principal$;

GRANT EXECUTE ON FUNCTION kernel_sweep_company_message_plane()
    TO kernel_company_message_maintenance;
GRANT EXECUTE ON FUNCTION kernel_expire_company_message_payload(UUID)
    TO kernel_company_message_maintenance;

-- ---------------------------------------------------------------------------
-- Certification read path over the admission substrate (review H2 / arch
-- HIGH-1) — this migration's ONE cross-DDL change. The certify helpers run
-- as the owner (current_user=fleet_kernel_provisioner) but a launchd sweep
-- session's session_user is the maintenance login, and the DDL 036 FORCE-RLS
-- policy on the admission table keys on session_user through registries the
-- maintenance principal is deliberately NOT in (registering it would make it
-- an authority-bearing worker principal). This owner-keyed policy passes
-- exactly that combination — provisioner current_user, maintenance
-- session_user — for SELECT only, company-scoped. It grants no visibility to
-- any other session class and no mutation to anyone. The mutation suite
-- bites the real maintenance session_user path (drop this policy and the
-- maintenance-session certification test observes zero certifications).
CREATE POLICY kernel_company_message_maintenance_admission_read
    ON kernel_company_scribe_admissions
    AS PERMISSIVE FOR SELECT
    TO fleet_kernel_provisioner
    USING (
        company_id='__CC_SUITE_COMPANY_ID__'
        AND session_user='kernel_company_message_maintenance'
    );

-- ---------------------------------------------------------------------------
-- State-machine and immutability guards
-- ---------------------------------------------------------------------------

CREATE FUNCTION kernel_guard_company_message_identity()
RETURNS trigger LANGUAGE plpgsql VOLATILE
SET search_path=pg_catalog,public
AS $function$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Company message rows are never deleted';
    END IF;

    IF (
        NEW.id,NEW.company_id,NEW.sender_admission_id,NEW.sender_role,
        NEW.recipient_role,NEW.recipient_sequence,NEW.idempotency_key,
        NEW.message_kind,NEW.message_octets,NEW.message_sha256,
        NEW.causal_parent_message_id,NEW.causal_root_message_id,
        NEW.causal_depth,NEW.accepted_at
    ) IS DISTINCT FROM (
        OLD.id,OLD.company_id,OLD.sender_admission_id,OLD.sender_role,
        OLD.recipient_role,OLD.recipient_sequence,OLD.idempotency_key,
        OLD.message_kind,OLD.message_octets,OLD.message_sha256,
        OLD.causal_parent_message_id,OLD.causal_root_message_id,
        OLD.causal_depth,OLD.accepted_at
    ) THEN
        RAISE EXCEPTION 'Company message identity is immutable';
    END IF;

    -- The receipted retention transition: the ONLY payload mutation, and it
    -- changes nothing else (review H4).
    IF OLD.payload_state='present' AND NEW.payload_state='expired' THEN
        IF NEW.message_bytes IS NOT NULL
           OR OLD.state NOT IN ('consumed','dead_lettered','quarantined')
           OR NEW.state IS DISTINCT FROM OLD.state
           OR NEW.consumed_at IS DISTINCT FROM OLD.consumed_at
           OR NEW.dead_lettered_at IS DISTINCT FROM OLD.dead_lettered_at
           OR NEW.dead_letter_reason IS DISTINCT FROM OLD.dead_letter_reason
           OR NEW.quarantined_at IS DISTINCT FROM OLD.quarantined_at
           OR NEW.quarantine_reason IS DISTINCT FROM OLD.quarantine_reason
           OR NEW.delivery_attempt_count
              IS DISTINCT FROM OLD.delivery_attempt_count
           OR current_setting('mise.kernel_payload_retention',true)
              IS DISTINCT FROM OLD.id::TEXT THEN
            RAISE EXCEPTION 'Company message payload expires only through the receipted retention path';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.payload_state IS DISTINCT FROM OLD.payload_state
       OR NEW.message_bytes IS DISTINCT FROM OLD.message_bytes THEN
        RAISE EXCEPTION 'Company message bytes are immutable outside the retention path';
    END IF;

    IF NOT (
        OLD.state='queued'
        AND NEW.state='leased'
        AND NEW.delivery_attempt_count=OLD.delivery_attempt_count+1
        AND NEW.active_attempt_id IS NOT NULL
        AND NEW.active_lease_id IS NOT NULL
        AND NEW.active_lease_expires_at>clock_timestamp()
        AND NEW.consumed_at IS NULL
        AND NEW.dead_lettered_at IS NULL
        AND NEW.dead_letter_reason IS NULL
    ) AND NOT (
        OLD.state='leased'
        AND OLD.active_lease_expires_at<=clock_timestamp()
        AND NEW.state='queued'
        AND NEW.delivery_attempt_count=OLD.delivery_attempt_count
        AND NEW.active_attempt_id IS NULL
        AND NEW.active_lease_id IS NULL
        AND NEW.active_lease_expires_at IS NULL
        AND NEW.consumed_at IS NULL
        AND NEW.dead_lettered_at IS NULL
        AND NEW.dead_letter_reason IS NULL
    ) AND NOT (
        OLD.state='leased'
        AND NEW.state='consumed'
        AND NEW.delivery_attempt_count=OLD.delivery_attempt_count
        AND NEW.active_attempt_id IS NULL
        AND NEW.active_lease_id IS NULL
        AND NEW.active_lease_expires_at IS NULL
        AND NEW.consumed_at IS NOT NULL
        AND NEW.dead_lettered_at IS NULL
        AND NEW.dead_letter_reason IS NULL
    ) AND NOT (
        OLD.state='leased'
        AND OLD.active_lease_expires_at<=clock_timestamp()
        AND OLD.delivery_attempt_count=5
        AND NEW.state='dead_lettered'
        AND NEW.delivery_attempt_count=OLD.delivery_attempt_count
        AND NEW.active_attempt_id IS NULL
        AND NEW.active_lease_id IS NULL
        AND NEW.active_lease_expires_at IS NULL
        AND NEW.consumed_at IS NULL
        AND NEW.dead_lettered_at IS NOT NULL
        AND btrim(NEW.dead_letter_reason)<>''
    ) AND NOT (
        -- Certification quarantine (review C1): the only path out of queued
        -- besides leasing; terminal, reasoned, timestamped.
        OLD.state='queued'
        AND NEW.state='quarantined'
        AND NEW.delivery_attempt_count=OLD.delivery_attempt_count
        AND NEW.active_attempt_id IS NULL
        AND NEW.active_lease_id IS NULL
        AND NEW.active_lease_expires_at IS NULL
        AND NEW.consumed_at IS NULL
        AND NEW.dead_lettered_at IS NULL
        AND NEW.dead_letter_reason IS NULL
        AND NEW.quarantined_at IS NOT NULL
        AND btrim(NEW.quarantine_reason)<>''
    ) THEN
        RAISE EXCEPTION 'Illegal Company message state transition';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER kernel_company_message_identity_guard
BEFORE UPDATE OR DELETE ON kernel_company_messages
FOR EACH ROW EXECUTE FUNCTION kernel_guard_company_message_identity();

CREATE FUNCTION kernel_guard_company_message_delivery_attempt()
RETURNS trigger LANGUAGE plpgsql VOLATILE
SET search_path=pg_catalog,public
AS $function$
BEGIN
    IF TG_OP='DELETE' OR (
        NEW.id,NEW.company_id,NEW.message_id,NEW.attempt_number,
        NEW.recipient_role,NEW.consuming_admission_id,NEW.lease_id,
        NEW.lease_token_sha256,NEW.leased_at,NEW.lease_expires_at
    ) IS DISTINCT FROM (
        OLD.id,OLD.company_id,OLD.message_id,OLD.attempt_number,
        OLD.recipient_role,OLD.consuming_admission_id,OLD.lease_id,
        OLD.lease_token_sha256,OLD.leased_at,OLD.lease_expires_at
    ) THEN
        RAISE EXCEPTION 'Company message delivery-attempt identity is immutable';
    END IF;
    IF OLD.state<>'open'
       OR NEW.state NOT IN ('consumed','expired','dead_lettered')
       OR NEW.closed_at IS NULL THEN
        RAISE EXCEPTION 'Illegal Company message delivery-attempt transition';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER kernel_company_message_delivery_attempt_guard
BEFORE UPDATE OR DELETE ON kernel_company_message_delivery_attempts
FOR EACH ROW EXECUTE FUNCTION kernel_guard_company_message_delivery_attempt();

-- Review F-C1/R6-C1/R6-H2 (CRITICAL/HIGH): SECURITY DEFINER so the
-- certification-gate reads below are authoritative regardless of the
-- caller's own table privileges — an outbox reconciler running with only
-- its own narrow grants still cannot bypass the gates by lacking visibility
-- into the certification or message tables. Four legal transitions, each
-- with its own structural predicate; every other transition refuses.
CREATE FUNCTION kernel_guard_company_message_obligation()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
BEGIN
    -- Review verif H-3 (r9): the INSERT path is inside the guard fabric.
    -- Every obligation is born 'accepted' (the send transaction's default
    -- shape); a definer-context INSERT fabricating a reserved,
    -- open_requested, or cancelled row refuses here, and the accepted arm
    -- of the table CHECK then forces the clean initial shape.
    IF TG_OP='INSERT' THEN
        IF NEW.state<>'accepted' THEN
            RAISE EXCEPTION 'Company message obligations are born accepted; fabricated states refuse';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='DELETE' OR (
        NEW.message_id,NEW.company_id,NEW.envelope_version,NEW.owner_role,
        NEW.action,NEW.expected_artifact,NEW.deadline_hours,NEW.accepted_at
    ) IS DISTINCT FROM (
        OLD.message_id,OLD.company_id,OLD.envelope_version,OLD.owner_role,
        OLD.action,OLD.expected_artifact,OLD.deadline_hours,OLD.accepted_at
    ) THEN
        RAISE EXCEPTION 'Company message obligation identity is immutable';
    END IF;

    -- Review R7-C1: every non-cancellation transition is legal ONLY inside
    -- the narrow reserve/finalize protocol functions, proven by the same
    -- transaction-local handshake idiom the send-receipt derivation uses.
    -- A direct owner UPDATE without the handshake refuses — the protocol
    -- functions are the single mutation path, not a convention.
    IF NEW.state IN ('reserved','open_requested')
       AND current_setting('mise.kernel_obligation_protocol',true)
           IS DISTINCT FROM NEW.message_id::TEXT THEN
        RAISE EXCEPTION 'Company message obligation transitions only through the reserve/finalize protocol functions';
    END IF;

    IF OLD.state='accepted' AND NEW.state='reserved' THEN
        -- Structural certification gate (review F-C1): PASS acceptance
        -- certification must exist as a durable row BEFORE the reconciler
        -- may even RESERVE, let alone announce itself to the file-plane
        -- handoff ledger. PENDING (no row yet) and FAIL/UNCERTIFIABLE (row
        -- exists, verdict<>'PASS') both refuse.
        IF NOT EXISTS (
            SELECT 1 FROM kernel_company_message_commit_certifications cert
             WHERE cert.subject_kind='acceptance'
               AND cert.subject_id=NEW.message_id
               AND cert.verdict='PASS'
        ) THEN
            RAISE EXCEPTION 'Company message obligation cannot reserve before acceptance certifies PASS';
        END IF;
        IF NEW.reservation_id IS NULL OR NEW.reserved_at IS NULL
           OR NEW.reservation_generation<>OLD.reservation_generation+1
           OR NEW.reservation_lease_expires_at IS NULL
           OR NEW.reservation_lease_expires_at<=NEW.reserved_at
           OR NEW.reservation_lease_expires_at
              >NEW.reserved_at+interval '15 minutes' THEN
            RAISE EXCEPTION 'Company message obligation reservation is malformed';
        END IF;
    ELSIF OLD.state='reserved' AND NEW.state='reserved' THEN
        -- Review R7-C1 (requirement 7): recovery TAKEOVER.  Legal only
        -- after the incumbent reservation's lease has lapsed, and only as
        -- a fresh reservation at the NEXT fencing generation — the atomic
        -- CAS in the reserve function admits exactly one winner, and the
        -- generation bump permanently fences the delayed predecessor out
        -- of finalize.  A live (unexpired) reservation cannot be taken.
        IF OLD.reservation_lease_expires_at>clock_timestamp() THEN
            RAISE EXCEPTION 'Company message obligation reservation is still leased; takeover refused';
        END IF;
        IF NEW.reservation_id IS NULL
           OR NEW.reservation_id=OLD.reservation_id
           OR NEW.reservation_generation<>OLD.reservation_generation+1
           OR NEW.reserved_at IS NULL
           OR NEW.reservation_lease_expires_at IS NULL
           OR NEW.reservation_lease_expires_at<=NEW.reserved_at
           OR NEW.reservation_lease_expires_at
              >NEW.reserved_at+interval '15 minutes' THEN
            RAISE EXCEPTION 'Company message obligation takeover reservation is malformed';
        END IF;
    ELSIF OLD.state='reserved' AND NEW.state='open_requested' THEN
        -- Review R7-C1: the finalize FUNCTION already proved possession —
        -- its UPDATE matches the presented reservation AND generation in
        -- its WHERE clause, so a stale, duplicate, or fenced caller updates
        -- zero rows and raises before this trigger ever fires.  The
        -- trigger's remaining job is immutability of the winning pair, the
        -- physical-receipt shape, and the second lease-liveness layer.
        IF NEW.reservation_id IS DISTINCT FROM OLD.reservation_id
           OR NEW.reservation_generation
              IS DISTINCT FROM OLD.reservation_generation
           OR NEW.reserved_at IS DISTINCT FROM OLD.reserved_at THEN
            RAISE EXCEPTION 'Company message obligation finalization reservation mismatch';
        END IF;
        -- Review Codex R8-C1 + verif H-1 (r9): the trigger-layer twin of
        -- the finalize function's lease-liveness predicate, with a DISTINCT
        -- error identity so the per-layer mutation pair can observe which
        -- layer refused (function raise: 'stale, fenced, expired, or
        -- unknown reservation'; this raise: 'lease lapsed').  Lease lapse
        -- terminates finalization authority at BOTH layers.
        IF OLD.reservation_lease_expires_at<=clock_timestamp() THEN
            RAISE EXCEPTION 'Company message obligation lease lapsed; finalization authority terminated';
        END IF;
        IF NEW.open_request_receipt_sha256 IS NULL
           OR NEW.open_request_file_offset IS NULL THEN
            RAISE EXCEPTION 'Company message obligation finalization requires the physical append receipt and offset';
        END IF;
    ELSIF OLD.state='accepted' AND NEW.state='cancelled' THEN
        -- Review R6-H2: cancellation is not a bare state flip a future
        -- definer-context caller could invoke at will — it requires the
        -- SAME evidence the acceptance side already produced: the message
        -- itself quarantined, and a durable acceptance certification whose
        -- verdict is exactly FAIL or UNCERTIFIABLE (never PASS, never
        -- absent). A PASS-certified obligation can never be cancelled —
        -- PASS certifications are immutable, so a message can never
        -- quarantine after one exists, making this predicate unsatisfiable
        -- for any obligation that ever reaches 'reserved'.
        IF NOT EXISTS (
            SELECT 1 FROM kernel_company_messages message
             WHERE message.id=NEW.message_id
               AND message.state='quarantined'
        ) OR NOT EXISTS (
            -- Review R6-H2 residual + verif L-2 (r9): the recorded
            -- cancellation binds BY ID to the exact FAIL/UNCERTIFIABLE
            -- acceptance certification for THIS message, and the durable
            -- reason TEXT is derived from that certification's verdict —
            -- the prose can never contradict the bound evidence.
            SELECT 1 FROM kernel_company_message_commit_certifications cert
             WHERE cert.id=NEW.cancelled_certification_id
               AND cert.subject_kind='acceptance'
               AND cert.subject_id=NEW.message_id
               AND cert.verdict IN ('FAIL','UNCERTIFIABLE')
               AND NEW.cancel_reason=
                   'acceptance-certification-'||lower(cert.verdict)
        ) THEN
            RAISE EXCEPTION 'Company message obligation cancellation requires a quarantined message, its own FAIL or UNCERTIFIABLE acceptance certification bound by id, and the verdict-derived reason';
        END IF;
    ELSE
        RAISE EXCEPTION 'Illegal Company message obligation transition';
    END IF;
    RETURN NEW;
END
$function$;

CREATE TRIGGER kernel_company_message_obligation_guard
BEFORE INSERT OR UPDATE OR DELETE ON kernel_company_message_obligations
FOR EACH ROW EXECUTE FUNCTION kernel_guard_company_message_obligation();

-- Review R7-C1 (requirements 1-4, 7): the ONLY reservation mint.  One
-- atomic compare-and-set covers both first reservation (accepted, at
-- generation 0) and recovery takeover (reserved with a LAPSED lease);
-- exactly one caller wins the row update, receives the database-generated
-- reservation and its fencing generation, and every loser -- including a
-- delayed prior owner or a second simultaneous recovery worker -- gets a
-- raise, never a reservation.  The reservation is never caller-supplied
-- and never returned twice.
CREATE FUNCTION kernel_reserve_company_message_obligation(
    p_message_id UUID
)
RETURNS TABLE (reservation_id UUID, reservation_generation BIGINT)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    won_reservation UUID;
    won_generation BIGINT;
BEGIN
    -- Not STRICT (review M2 class): a NULL argument must raise loudly,
    -- never silently return NULL.
    IF p_message_id IS NULL THEN
        RAISE EXCEPTION 'Company message obligation reserve requires a message id';
    END IF;
    PERFORM set_config(
        'mise.kernel_obligation_protocol',p_message_id::TEXT,true
    );
    UPDATE kernel_company_message_obligations obligation
       SET state='reserved',
           reservation_id=gen_random_uuid(),
           reservation_generation=obligation.reservation_generation+1,
           reserved_at=clock_timestamp(),
           reservation_lease_expires_at=
               clock_timestamp()+interval '10 minutes'
     WHERE obligation.message_id=p_message_id
       AND (
            obligation.state='accepted'
            OR (obligation.state='reserved'
                AND obligation.reservation_lease_expires_at
                    <=clock_timestamp())
       )
    RETURNING obligation.reservation_id,obligation.reservation_generation
      INTO won_reservation,won_generation;
    PERFORM set_config('mise.kernel_obligation_protocol','',true);
    IF won_reservation IS NULL THEN
        RAISE EXCEPTION 'Company message obligation reserve lost the compare-and-set or found no reservable obligation';
    END IF;
    RETURN QUERY SELECT won_reservation,won_generation;
END
$function$;
REVOKE ALL ON FUNCTION kernel_reserve_company_message_obligation(UUID)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kernel_reserve_company_message_obligation(UUID)
    TO kernel_company_message_maintenance;

-- Review R7-C1 (requirement 6): finalize takes the reservation AND its
-- fencing generation as independent presented arguments.  The WHERE clause
-- is the possession proof: a caller presenting a reservation it was never
-- granted, a reused reservation (impossible by UNIQUE constraint), or a
-- pre-takeover generation updates zero rows and raises.  The receipt is
-- the sha256 of the exact appended file row bytes.
CREATE FUNCTION kernel_finalize_company_message_obligation(
    p_message_id UUID,
    p_reservation_id UUID,
    p_reservation_generation BIGINT,
    p_open_request_receipt_sha256 TEXT,
    p_open_request_file_offset BIGINT
)
RETURNS VOID
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE
    finalized UUID;
BEGIN
    -- Not STRICT (review M2 class): a NULL argument must raise loudly,
    -- never silently return NULL.
    IF p_message_id IS NULL OR p_reservation_id IS NULL
       OR p_reservation_generation IS NULL
       OR p_open_request_receipt_sha256 IS NULL
       OR p_open_request_file_offset IS NULL THEN
        RAISE EXCEPTION 'Company message obligation finalize requires every argument';
    END IF;
    IF p_open_request_receipt_sha256 !~ '^[0-9a-f]{64}$'
       OR p_open_request_file_offset<0 THEN
        RAISE EXCEPTION 'Company message obligation finalize receipt must be the canonical-row sha256 with its byte offset';
    END IF;
    PERFORM set_config(
        'mise.kernel_obligation_protocol',p_message_id::TEXT,true
    );
    -- Review Codex R8-C1 (founder-adjudicated closure): LEASE LAPSE
    -- TERMINATES FINALIZATION AUTHORITY.  The WHERE requires the presented
    -- reservation's lease to still be LIVE at finalize — a lapsed owner is
    -- refused here even before any takeover exists, so the bounded lease
    -- bounds exactly what the design claims: the whole ownership window,
    -- not merely takeover eligibility.  A lapsed owner's only path back is
    -- winning a fresh reserve (at the next generation) and re-presenting.
    UPDATE kernel_company_message_obligations obligation
       SET state='open_requested',
           open_requested_at=clock_timestamp(),
           open_request_receipt_sha256=p_open_request_receipt_sha256,
           open_request_file_offset=p_open_request_file_offset
     WHERE obligation.message_id=p_message_id
       AND obligation.state='reserved'
       AND obligation.reservation_id=p_reservation_id
       AND obligation.reservation_generation=p_reservation_generation
       AND obligation.reservation_lease_expires_at>clock_timestamp()
    RETURNING obligation.message_id INTO finalized;
    PERFORM set_config('mise.kernel_obligation_protocol','',true);
    IF finalized IS NULL THEN
        RAISE EXCEPTION 'Company message obligation finalize presented a stale, fenced, expired, or unknown reservation';
    END IF;
END
$function$;
REVOKE ALL ON FUNCTION kernel_finalize_company_message_obligation(
    UUID,UUID,BIGINT,TEXT,BIGINT
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kernel_finalize_company_message_obligation(
    UUID,UUID,BIGINT,TEXT,BIGINT
) TO kernel_company_message_maintenance;

-- Review arch HIGH-1 (r9): the reconciler's enumeration surface.  The
-- reconciler runs exactly like the sweeper — a provisioner-owned launchd
-- job connecting AS the maintenance principal — so the maintenance
-- principal's authorized surface is now FOUR protocol/maintenance
-- functions plus this enumerator (design 3.4/6.1 name the same set, and
-- the privilege-diff test audits it).  This definer function is the ONLY
-- discovery path: it returns bare drainable message-ids (accepted with a
-- durable PASS certification, or reserved with a lapsed lease) and no
-- obligation content, no reservation credential, no envelope fields.
CREATE FUNCTION kernel_company_message_drainable_obligations()
RETURNS SETOF UUID
-- r10 (Codex R9-M2): VOLATILE — the result changes with clock_timestamp()
-- (the lapsed-lease arm), so STABLE was an honesty defect even though
-- volatility is unenforced and the reserve CAS re-verifies every id.
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
    SELECT obligation.message_id
      FROM kernel_company_message_obligations obligation
     WHERE (
            obligation.state='accepted'
            AND EXISTS (
                SELECT 1
                  FROM kernel_company_message_commit_certifications cert
                 WHERE cert.subject_kind='acceptance'
                   AND cert.subject_id=obligation.message_id
                   AND cert.verdict='PASS'
            )
        )
        OR (
            obligation.state='reserved'
            AND obligation.reservation_lease_expires_at<=clock_timestamp()
        )
$function$;
REVOKE ALL ON FUNCTION kernel_company_message_drainable_obligations()
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kernel_company_message_drainable_obligations()
    TO kernel_company_message_maintenance;

CREATE FUNCTION kernel_refuse_immutable_message_record_mutation()
RETURNS trigger LANGUAGE plpgsql VOLATILE
AS $function$
BEGIN
    RAISE EXCEPTION 'Company message receipt/history rows are immutable';
END
$function$;

CREATE TRIGGER kernel_company_send_receipt_immutable
BEFORE UPDATE OR DELETE ON kernel_company_message_send_receipts
FOR EACH ROW EXECUTE FUNCTION kernel_refuse_immutable_message_record_mutation();
CREATE TRIGGER kernel_company_consumption_receipt_immutable
BEFORE UPDATE OR DELETE ON kernel_company_message_consumption_receipts
FOR EACH ROW EXECUTE FUNCTION kernel_refuse_immutable_message_record_mutation();
CREATE TRIGGER kernel_company_certification_immutable
BEFORE UPDATE OR DELETE ON kernel_company_message_commit_certifications
FOR EACH ROW EXECUTE FUNCTION kernel_refuse_immutable_message_record_mutation();
CREATE TRIGGER kernel_company_retention_receipt_immutable
BEFORE UPDATE OR DELETE ON kernel_company_message_retention_receipts
FOR EACH ROW EXECUTE FUNCTION kernel_refuse_immutable_message_record_mutation();

-- TRUNCATE refusal on every plane table (statement triggers; row triggers
-- do not fire on TRUNCATE).
CREATE FUNCTION kernel_refuse_company_message_truncate()
RETURNS trigger LANGUAGE plpgsql VOLATILE
AS $function$
BEGIN
    RAISE EXCEPTION 'Company message plane tables refuse TRUNCATE';
END
$function$;

CREATE TRIGGER kernel_company_message_sequences_no_truncate
BEFORE TRUNCATE ON kernel_company_message_recipient_sequences
FOR EACH STATEMENT EXECUTE FUNCTION kernel_refuse_company_message_truncate();
CREATE TRIGGER kernel_company_messages_no_truncate
BEFORE TRUNCATE ON kernel_company_messages
FOR EACH STATEMENT EXECUTE FUNCTION kernel_refuse_company_message_truncate();
CREATE TRIGGER kernel_company_send_receipts_no_truncate
BEFORE TRUNCATE ON kernel_company_message_send_receipts
FOR EACH STATEMENT EXECUTE FUNCTION kernel_refuse_company_message_truncate();
CREATE TRIGGER kernel_company_delivery_attempts_no_truncate
BEFORE TRUNCATE ON kernel_company_message_delivery_attempts
FOR EACH STATEMENT EXECUTE FUNCTION kernel_refuse_company_message_truncate();
CREATE TRIGGER kernel_company_consumption_receipts_no_truncate
BEFORE TRUNCATE ON kernel_company_message_consumption_receipts
FOR EACH STATEMENT EXECUTE FUNCTION kernel_refuse_company_message_truncate();
CREATE TRIGGER kernel_company_obligations_no_truncate
BEFORE TRUNCATE ON kernel_company_message_obligations
FOR EACH STATEMENT EXECUTE FUNCTION kernel_refuse_company_message_truncate();
CREATE TRIGGER kernel_company_certifications_no_truncate
BEFORE TRUNCATE ON kernel_company_message_commit_certifications
FOR EACH STATEMENT EXECUTE FUNCTION kernel_refuse_company_message_truncate();
CREATE TRIGGER kernel_company_retention_receipts_no_truncate
BEFORE TRUNCATE ON kernel_company_message_retention_receipts
FOR EACH STATEMENT EXECUTE FUNCTION kernel_refuse_company_message_truncate();

-- ---------------------------------------------------------------------------
-- RLS. FORCE subjects the NOBYPASS owner to policy, so the definer-scope
-- policy below is what lets the reviewed SECURITY DEFINER functions (which
-- run as the owner) read the full company scope and mutate at all (review
-- C1); the per-role SELECT policies scope direct role reads; role principals
-- hold SELECT privilege only.
-- ---------------------------------------------------------------------------

ALTER TABLE kernel_company_message_recipient_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_recipient_sequences FORCE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_messages FORCE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_send_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_send_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_delivery_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_delivery_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_consumption_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_consumption_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_obligations ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_obligations FORCE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_commit_certifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_commit_certifications FORCE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_retention_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE kernel_company_message_retention_receipts FORCE ROW LEVEL SECURITY;

-- Definer-scope policies: the owner (fleet_kernel_provisioner) operates over
-- the full company scope. The maintenance principal is deliberately ABSENT
-- from every TO-list (arch L3): it acts only through SECURITY DEFINER
-- functions running as the owner, so a policy entry would be dead breadth
-- that a future SELECT grant could silently activate into full-plane
-- visibility including message bytes.
CREATE POLICY kernel_company_message_definer_scope
    ON kernel_company_message_recipient_sequences
    AS PERMISSIVE FOR ALL
    TO fleet_kernel_provisioner
    USING (company_id='__CC_SUITE_COMPANY_ID__')
    WITH CHECK (company_id='__CC_SUITE_COMPANY_ID__');
CREATE POLICY kernel_company_message_definer_scope
    ON kernel_company_messages
    AS PERMISSIVE FOR ALL
    TO fleet_kernel_provisioner
    USING (company_id='__CC_SUITE_COMPANY_ID__')
    WITH CHECK (company_id='__CC_SUITE_COMPANY_ID__');
CREATE POLICY kernel_company_message_definer_scope
    ON kernel_company_message_send_receipts
    AS PERMISSIVE FOR ALL
    TO fleet_kernel_provisioner
    USING (company_id='__CC_SUITE_COMPANY_ID__')
    WITH CHECK (company_id='__CC_SUITE_COMPANY_ID__');
CREATE POLICY kernel_company_message_definer_scope
    ON kernel_company_message_delivery_attempts
    AS PERMISSIVE FOR ALL
    TO fleet_kernel_provisioner
    USING (company_id='__CC_SUITE_COMPANY_ID__')
    WITH CHECK (company_id='__CC_SUITE_COMPANY_ID__');
CREATE POLICY kernel_company_message_definer_scope
    ON kernel_company_message_consumption_receipts
    AS PERMISSIVE FOR ALL
    TO fleet_kernel_provisioner
    USING (company_id='__CC_SUITE_COMPANY_ID__')
    WITH CHECK (company_id='__CC_SUITE_COMPANY_ID__');
CREATE POLICY kernel_company_message_definer_scope
    ON kernel_company_message_obligations
    AS PERMISSIVE FOR ALL
    TO fleet_kernel_provisioner
    USING (company_id='__CC_SUITE_COMPANY_ID__')
    WITH CHECK (company_id='__CC_SUITE_COMPANY_ID__');
CREATE POLICY kernel_company_message_definer_scope
    ON kernel_company_message_commit_certifications
    AS PERMISSIVE FOR ALL
    TO fleet_kernel_provisioner
    USING (company_id='__CC_SUITE_COMPANY_ID__')
    WITH CHECK (company_id='__CC_SUITE_COMPANY_ID__');
CREATE POLICY kernel_company_message_definer_scope
    ON kernel_company_message_retention_receipts
    AS PERMISSIVE FOR ALL
    TO fleet_kernel_provisioner
    USING (company_id='__CC_SUITE_COMPANY_ID__')
    WITH CHECK (company_id='__CC_SUITE_COMPANY_ID__');

-- Actor-role helper (arch review NEW MEDIUM-1): a per-role RLS policy
-- expression runs with the QUERYING role's own privileges — role principals
-- hold no SELECT on kernel_company_worker_principals (REVOKE ALL FROM
-- PUBLIC), so a policy that embeds a direct subquery on that table makes
-- every granted direct role SELECT of a plane table raise permission denied,
-- not merely filter to zero rows. This SECURITY DEFINER helper mirrors DDL
-- 036's kernel_current_company() pattern: it performs the worker-principal
-- lookup as the owner and returns only the caller's own role_type, so the
-- per-role policies below reference nothing the querying role cannot see.
CREATE FUNCTION kernel_company_message_actor_role_type()
RETURNS TEXT
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
    SELECT principal.role_type
      FROM kernel_company_worker_principals principal
     WHERE principal.company_id='__CC_SUITE_COMPANY_ID__'
       AND principal.db_role=session_user::NAME
     LIMIT 1;
$function$;
REVOKE ALL ON FUNCTION kernel_company_message_actor_role_type() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kernel_company_message_actor_role_type() TO PUBLIC;

-- Per-role SELECT policies (every column reference explicitly qualified).
CREATE POLICY kernel_company_message_sender_or_recipient_read
    ON kernel_company_messages FOR SELECT
    USING (
        kernel_company_messages.company_id='__CC_SUITE_COMPANY_ID__'
        AND kernel_company_message_actor_role_type() IN (
            kernel_company_messages.sender_role,
            kernel_company_messages.recipient_role
        )
    );
CREATE POLICY kernel_company_send_receipt_party_read
    ON kernel_company_message_send_receipts FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM kernel_company_messages message
             WHERE message.id=kernel_company_message_send_receipts.message_id
               AND kernel_company_message_actor_role_type()
                   IN (message.sender_role,message.recipient_role)
        )
    );
CREATE POLICY kernel_company_delivery_attempt_recipient_read
    ON kernel_company_message_delivery_attempts FOR SELECT
    USING (
        kernel_company_message_delivery_attempts.recipient_role
        =kernel_company_message_actor_role_type()
    );
CREATE POLICY kernel_company_consumption_receipt_party_read
    ON kernel_company_message_consumption_receipts FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM kernel_company_messages message
             WHERE message.id=
                   kernel_company_message_consumption_receipts.message_id
               AND kernel_company_message_actor_role_type()
                   IN (message.sender_role,message.recipient_role)
        )
    );
-- Review F-C2 / R6-C2 (CRITICAL, two rounds): party visibility alone let a
-- valid-but-unbound, expired, or quarantined role credential read the
-- obligation's parsed handoff content (owner_role, action, expected_artifact)
-- with no admission, lease, or certification predicate — the same class of
-- bypass as the message-bytes fix (C1), applied to the obligation's own
-- payload-derived fields. Revision 6 narrowed this to a PASS-or-cancelled
-- RLS disjunct, but R6-C2 correctly found that `kernel_company_message_
-- actor_role_type()` derives identity from `session_user` alone — no live
-- admission bind — so ANY static party credential, copied or expired,
-- could still read every PASS or cancelled obligation forever. There is
-- deliberately NO per-role RLS SELECT policy and NO table grant on this
-- table (unlike every other plane table): obligation content is reachable
-- ONLY through the admission-bound read function below, matching the
-- message plane's own discipline — payload-adjacent content is never a
-- bare table read.

-- SETOF, not a single ROWTYPE: matching kernel_company_message_receipt's own
-- empty-result-vs-raise convention (Section 7 "Receipt surfacing") — a
-- caller who is not the message's sender/recipient, or whose obligation is
-- neither cancelled nor PASS-certified, gets zero rows, never an exception
-- carrying an all-NULL composite.
CREATE FUNCTION kernel_company_message_obligation_receipt(p_message_id UUID)
RETURNS TABLE (
    message_id UUID, company_id TEXT, envelope_version INTEGER,
    owner_role TEXT, action TEXT, expected_artifact TEXT,
    deadline_hours NUMERIC, state TEXT, accepted_at TIMESTAMPTZ,
    open_requested_at TIMESTAMPTZ, open_request_receipt_sha256 TEXT,
    open_request_file_offset BIGINT, cancelled_at TIMESTAMPTZ,
    cancelled_certification_id UUID, cancel_reason TEXT
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $function$
DECLARE actor RECORD;
BEGIN
    IF p_message_id IS NULL THEN
        RAISE EXCEPTION 'Company message obligation receipt request is outside the closed contract';
    END IF;
    -- Review R6-C2: requires a LIVE admission bind, not merely a registered
    -- session_user — the same requirement kernel_claim_company_message and
    -- kernel_consume_company_message already enforce for message bytes.
    SELECT * INTO STRICT actor
      FROM kernel_current_company_message_admission();
    -- Review arch MEDIUM-1 (r8): the reservation triple (reservation_id,
    -- reservation_generation, reservation_lease_expires_at) is the
    -- reconciler's live coordination credential and is NEVER projected to
    -- message parties — the "returned once to the single CAS winner" claim
    -- holds because this, the only party-facing read surface, withholds it.
    -- Parties receive the obligation's operational content and state only.
    RETURN QUERY
    SELECT obl.message_id,obl.company_id,obl.envelope_version,
           obl.owner_role,obl.action,obl.expected_artifact,
           obl.deadline_hours,obl.state,obl.accepted_at,
           obl.open_requested_at,obl.open_request_receipt_sha256,
           obl.open_request_file_offset,obl.cancelled_at,
           obl.cancelled_certification_id,obl.cancel_reason
      FROM kernel_company_message_obligations obl
      JOIN kernel_company_messages message ON message.id=obl.message_id
     WHERE obl.message_id=p_message_id
       AND actor.role_type IN (message.sender_role,message.recipient_role)
       AND (
           obl.state='cancelled'
           OR EXISTS (
               SELECT 1 FROM kernel_company_message_commit_certifications cert
                WHERE cert.subject_kind='acceptance'
                  AND cert.subject_id=obl.message_id
                  AND cert.verdict='PASS'
           )
       );
END
$function$;
REVOKE ALL ON FUNCTION kernel_company_message_obligation_receipt(UUID)
    FROM PUBLIC;
CREATE POLICY kernel_company_certification_party_read
    ON kernel_company_message_commit_certifications FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM kernel_company_messages message
             WHERE (
                   message.id=
                       kernel_company_message_commit_certifications.subject_id
                OR message.id=(
                     SELECT consumed.message_id
                       FROM kernel_company_message_consumption_receipts consumed
                      WHERE consumed.id=
                            kernel_company_message_commit_certifications.subject_id
                )
               )
               AND kernel_company_message_actor_role_type()
                   IN (message.sender_role,message.recipient_role)
        )
    );
-- No role-facing policy on sequences or retention receipts: role principals
-- never see them; the receipt function reports retention through the
-- message row.

-- Grants: role principals receive SELECT and EXECUTE on the FIVE role
-- functions (send, claim, consume, message receipt, obligation receipt --
-- r12, Codex R11-L2: the loop below grants five); never any DML. Review C1 (CRITICAL): a whole-table SELECT grant
-- on kernel_company_messages let a valid role credential read message_bytes
-- directly — bypassing admission binding, delivery lease, and acceptance
-- certification entirely, since the direct-read policy carries no state or
-- certification predicate. The grant on kernel_company_messages is therefore
-- COLUMN-SCOPED, explicitly excluding message_bytes: metadata (state,
-- timestamps, digest, octet count, quarantine/dead-letter reason, retention
-- state) stays directly observable for operator visibility, but the payload
-- itself is obtainable ONLY through kernel_claim_company_message (an
-- admission-bound lease) — the four broker functions remain the sole byte-
-- bearing surface, matching design invariant 5 and the completion predicate.
DO $grant_kernel_company_message_functions$
DECLARE item RECORD;
BEGIN
    FOR item IN
        SELECT DISTINCT principal.db_role
          FROM kernel_company_role_admission_targets target
          JOIN kernel_company_worker_principals principal
            ON principal.worker_id=target.worker_id
           AND principal.company_id=target.company_id
           AND principal.role_type=target.role_type
         WHERE target.state='eligible'
           AND target.role_type<>'utility'
    LOOP
        EXECUTE format(
            'GRANT SELECT ('
            'id,company_id,sender_admission_id,sender_role,recipient_role,'
            'recipient_sequence,idempotency_key,message_kind,payload_state,'
            'message_octets,message_sha256,causal_parent_message_id,'
            'causal_root_message_id,causal_depth,state,'
            'delivery_attempt_count,active_attempt_id,active_lease_id,'
            'active_lease_expires_at,accepted_at,consumed_at,'
            'dead_lettered_at,dead_letter_reason,quarantined_at,'
            'quarantine_reason'
            ') ON kernel_company_messages TO %I',
            item.db_role
        );
        -- Review R6-C2: kernel_company_message_obligations is deliberately
        -- ABSENT from this table-grant list. Obligation content is
        -- admission-bound, reachable only through the receipt function
        -- below — never a bare table SELECT, matching message_bytes'
        -- own containment.
        EXECUTE format(
            'GRANT SELECT ON '
            'kernel_company_message_send_receipts,'
            'kernel_company_message_delivery_attempts,'
            'kernel_company_message_consumption_receipts,'
            'kernel_company_message_commit_certifications TO %I',
            item.db_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION '
            'kernel_send_company_message(TEXT,TEXT,BYTEA,TEXT,UUID) TO %I',
            item.db_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION '
            'kernel_claim_company_message() TO %I',item.db_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION '
            'kernel_consume_company_message(UUID,UUID,TEXT,TEXT,TEXT) TO %I',
            item.db_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION '
            'kernel_company_message_receipt(UUID) TO %I',item.db_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION '
            'kernel_company_message_obligation_receipt(UUID) TO %I',
            item.db_role
        );
    END LOOP;
END
$grant_kernel_company_message_functions$;
-- NOTE (build obligation, CHOSEN REPAIR — verif F2): this one-shot loop
-- repeats the static-grant defect class DDL 035 closed. The build implements
-- the DDL 035 grant-on-target-insert trigger pattern (dynamic grants when a
-- target becomes eligible after apply); the §7 grant-following-eligibility
-- row binds to that trigger — a role made eligible after 038 applies must be
-- able to execute the plane functions, and breaking the trigger path turns
-- the test RED.

-- Required before unhold but intentionally not encoded in this draft:
-- mutation suite under the PARTITIONED harness (certification, commit-guard,
-- adversarial SET CONSTRAINTS, and concurrency families on the per-test
-- committed disposable database with track_commit_timestamp=on; single-
-- session commit-independent rows may use the outer-transaction mode):
-- NULL battery incl. the receipt function (empty-vs-raise for a non-party
-- caller); commit-guard bites for all three attachments; the certification
-- family (uncertified-claim refusal, FAIL quarantine with bounded wait,
-- claim-before-first-sweep, updated-before-certification witness binding,
-- UNCERTIFIABLE verdict, per-subject sweeper containment on the ESCAPE
-- class distinct from UNCERTIFIABLE, maintenance-session read path via the
-- owner-keyed policy, lag alarm); expired-lease, changed-token, and
-- wrong-digest live-path bites; cross-admission pair-bite (call-time raise
-- + divergent-receipt battery via the legal-transition route); post-
-- retention idempotent SEND retry; post-retention role-lineage CONSUME
-- replay (review H4) with its accepted successor-broker-custody bound
-- (review F-M2 -- not a build gap, a stated limit); oversized-token;
-- obligation owner binding, mandatory bounded deadline (rejecting NaN,
-- Infinity, and over-8760 at BOTH the pre-insert check and the table CHECK,
-- reviews F-H3/R6-L1), and advisor-ask reclassification; obligation
-- RESERVE/FINALIZE/RECOVER protocol (reviews R6-C1/R7-C1/R8 closure set --
-- accepted->reserved refused without a durable PASS row; direct
-- transitions without the protocol-function handshake refused; a
-- fabricated non-accepted INSERT refused by the guard's INSERT leg (verif
-- H-3); same-obligation contention admits ONE reserve winner, bitten by
-- PER-LAYER mutation pairs with distinct error identities (verif H-1:
-- function-CAS loss 'lost the compare-and-set' vs trigger 'still leased;
-- takeover refused' -- removing either layer flips the observed error);
-- cross-obligation reservation reuse refused by UNIQUE constraint;
-- finalize with a stale, foreign, pre-takeover-generation, or
-- LAPSED-LEASE reservation refused at BOTH layers with distinct errors
-- (Codex R8-C1 closure: lease lapse terminates finalization authority --
-- function 'expired' leg vs trigger 'lease lapsed' leg, per-layer
-- mutants); a live lease refuses takeover; a lapsed lease admits exactly
-- ONE takeover winner at generation+1, permanently fencing the delayed
-- predecessor's finalize; two simultaneous recovery workers admit one;
-- crash-recovery scan-and-repair fixtures for the reserve-then-crash
-- (named mutant: skip the reserve-recovery scan arm), append-then-crash,
-- fenced-late-append, TORN-APPEND in BOTH variants (verif r9 H-1 + arch
-- r10 M-1: the non-newline-terminated tail AND the interior-corrupt
-- newline-terminated final line; recovery's final-line-parses check must
-- quarantine-and-alarm each, never scan past or append onto; mutants:
-- remove the tail check / remove the scan's malformed-line rule), the
-- CHECKPOINT-SKIP window (Codex R10-M1: verify, advance, corrupt the
-- undrained row, recover — the mismatch must alarm; mutant skips
-- undrained rows behind the checkpoint), the END-TO-END POINTER path
-- (Codex R10-H1, r13 three-source join + r14 discovery contract: BOTH
-- orderings -- sweep-open -> owner fetch materializes ON FETCH RETURN
-- before any tick; owner-fetch-first with second fetches withheld
-- converges by the owner broker's journal tick -- plus the close-first
-- permutation (external close re-attempts withheld; CAS refused,
-- alarmed, then re-driven by the tick through the pinned discovery
-- path: the artifact durably present at the unit's journaled
-- expected_artifact path) and the broker-restart AND broker-
-- replacement crash boundaries, each asserting the TERMINAL closed
-- ledger state, never materialization alone; a never-fetching owner is
-- the named alarmed exception, asserted non-convergent-but-loud), the
-- COMPLETION-DISCOVERY battery (Codex R13-H1: positive close-through-
-- discovery leg; false-positive leg -- an unrelated or wrong-owner
-- artifact at the journaled path asserted producing ZERO close against
-- the ledger bytes with the refusal alarm observed; ambiguity leg --
-- path present as directory/dangling-symlink/unreadable never
-- schedules and raises the discovery-ambiguity alarm; crash/restart
-- leg resuming discovery from journal replay; r15 legs: the
-- resolved-symlink leg (a symlink resolving to a readable regular
-- file IS a hit -- close-first through it closes terminally; dangling
-- or non-regular resolution rides the ambiguity leg) and the
-- opaque-path leg (a nonempty-text expected_artifact that never
-- resolves behaves as absent -- never schedules, no crash, loud on
-- the content-pending/CAS-refusal fabric); mutants: miskey the
-- discovery path (close-first observed terminally materialized-but-
-- open with the re-drive machinery intact and the valid artifact at
-- the true path), remove the regular-file/readability check, suppress
-- the ambiguity alarm, swap the hit check to non-following lstat
-- semantics (the resolved-symlink leg observed materialized-but-
-- open)), the BROKER-JOURNAL battery (Codex R13-M1:
-- torn-row, corrupt-row, and unknown-version rows quarantine-and-alarm
-- at replay, never silent-skip; duplicate fetch writes no second row;
-- r15: quarantine RETIRES the row from the presence set -- the
-- quarantine-re-fetch fixture observes a fresh fetch writing a NEW
-- row and the unit proceeding, the quarantined row receipted and out
-- of the walk set; mutants: drop read-back-before-return, key the
-- journal per process (broker replacement observed stranding the
-- predecessor's units), drop terminal retirement, count a quarantined
-- row as present (the re-fetch observed writing nothing; the unit
-- stuck -- RED)), and mismatched-physical-receipt
-- windows;
-- the duplicate-file-row fixture asserts the specified
-- quarantine-all-and-alarm outcome (arch MEDIUM-2); the
-- recovery-after-drain fixture bites the OPEN-REQUEST persistence
-- contract (verif M-3); the receipt re-hash checker covers ALL
-- open_requested receipts NOT YET ledger-opened (the drain receipt = the
-- pointer-form ledger row's durable existence; the checkpoint advances
-- only behind it — r12, Codex R11-L3) and is bitten by removing its
-- compare (verif H-2 / arch LOW-2 / verif r9 L-1); the ORPHAN-POINTER
-- check with the ALLOWED-SET predicate (arch r11 A-5, r14-restated per
-- Codex R13-M2 / arch r13 F-5 / verif r13 F-2: a pointer is protocol-
-- consistent ONLY when its obligation is reserved or open_requested;
-- no-obligation, accepted-state, and cancelled-state pointers each
-- quarantine with a DISTINCT receipt class; the append-then-crash
-- reserved-state pointer must NOT quarantine — that leg rides the
-- append-then-crash fixture; quarantined rows are receipted OUT of the
-- flock-held scan and the duplicate rule, so the later lawful appender
-- appends its own row; the reserve-before-alien-scan schedule is
-- closed by step 2's GENERATION-1 DISCRIMINATOR (r15, Codex R14-M1 =
-- arch r14 F-1): any row the flock-held scan finds at a FIRST reserve
-- quarantines as pre-reserve before the appender writes its own row;
-- adoption lawful only at takeover generations; the PASS-before-scan
-- fixture (planted accepted pointer, PASS lands before any recovery
-- pass, reserve wins at generation 1) asserts the row quarantined
-- with the pre-reserve receipt, never finalized against, never
-- ledger-opened, with the inverse takeover-adoption control asserting
-- a genuine fenced-predecessor append adopted at generation >= 2;
-- mutants: remove the check — the planted alien
-- row observed swept into a content-pending ledger-open; remove the
-- accepted arm alone — the planted accepted-state pointer observed NOT
-- quarantining; wrongful adoption — the scan observed finalizing
-- against (or the sweep ledger-opening) a quarantined row; widened-
-- predicate mutant: the reserved-
-- state row observed falsely quarantined; remove the generation-1
-- discriminator — the PASS-before-scan fixture observed adoption at
-- generation 1 — RED; over-widen it to takeover generations — the
-- adoption control observed a false quarantine — RED) and the CONTENT-PENDING alarm
-- + CAS-refusal fixtures ride the recovery pass, each with suppression
-- + threshold-inversion mutants and a below-threshold-silence assertion
-- (verif r12 F-3); the r13 join mutants: disable the owner-broker tick
-- (fast-order row observed stuck past the bound); remove the every-fetch
-- check (the slow-order on-fetch-return assertion fails); drop the
-- journal write or restart replay (stranded content-pending row);
-- drop the completion re-drive (close-first observed terminally
-- materialized-but-open); presence-keyed idempotency mutant (a second
-- write observed MUTATING an already-materialized row); CAS-refusal
-- mutant (a content-pending close observed succeeding); the
-- MATERIALIZATION AUTHORIZATION battery (Codex R12-M1: sender fetch,
-- accepted/reserved-state projection, foreign/absent pointer key each
-- assert ZERO ledger mutation; mutants drop the owner-equality or state
-- check and must land a forbidden write); the EMISSION-BOUND alarm
-- anchored on the reconciler's durable write-once first-enumeration
-- record (r14 actor-readable re-anchor, verif r13 F-1 / arch r13 F-1;
-- r15: the record is a durable WRITE-AHEAD prerequisite — persisted,
-- fsynced, read back BEFORE reserve/takeover, any enumeration work,
-- or any file effect for the id, else the id stays unprocessed and
-- the record-write-failure alarm fires; the initial-record crash
-- fixture (crash between enumerator return and record durability)
-- observes restart establishing the record before any reserve, and
-- the churn-with-pre-record-crash fixture still observes the alarm
-- crossing the original bound; the NAMED restart leg observes a
-- written record surviving reconciler restart and still anchoring;
-- the missing-store fixture observes the store-loss alarm, never a
-- silent re-anchor; the repeated-takeover churn fixture crosses the
-- bound with every reservation young and must alarm;
-- re-key-on-reserved_at mutant
-- observes silence — RED; record-resettability mutant — rewriting the
-- record on later enumeration/takeover — observes churn-fixture
-- silence — RED; reserve-before-record mutant — permitting reserve
-- before the record is durable — observes churn-with-pre-record-crash
-- silence — RED; store-loss-alarm suppression — observes a silent
-- re-anchor — RED; the late-PASS fixture asserts SILENCE at the bound
-- past accepted_at and alarm only past first enumeration);
-- the drainable-obligations enumerator returns bare ids only and appears
-- in the audited grant surface (arch HIGH-1)) and
-- ADMISSION-BOUND
-- READ (review R6-C2 -- kernel_company_message_obligation_receipt refuses
-- a caller with no live admission, not merely an unbound session_user;
-- direct table SELECT must be observed impossible, not merely filtered);
-- obligation CANCELLATION BINDING (review R6-H2 -- accepted->cancelled
-- refused without both a quarantined message and a durable FAIL/
-- UNCERTIFIABLE certification); custody bites; RLS definer/misfilter
-- bites; RLS actor-helper direct-read privilege bite; direct-read byte-
-- containment bite (message_bytes excluded from the column-scoped grant);
-- per-table TRUNCATE battery incl. function-grant diffs; obligation
-- atomicity; retention-transition bites; expiry-boundary concurrency;
-- fence-state race; cross-plane total order; grant-following-eligibility;
-- maintenance-role REFUSE-OUTRIGHT battery (review R6-H1 closed by
-- eliminating the class; r10 purges this list's stale aclexplode()
-- mandate — the fourth site all three r9 lenses charged): the
-- existing-name fixture must observe the migration abort with the NAMED
-- refusal for a pre-existing role of ANY posture (mutant: remove the
-- existence check — the named refusal is replaced by the generic
-- duplicate-role error from the unconditional CREATE ROLE, and the
-- fixture must detect that identity change), plus the attribute-exactness
-- fixture on the created role; per-role certification attribution; sweeper COUNTER
-- DURABILITY (review F-H2 -- fault-injected post-helper failure must not
-- inflate returned counts beyond durable rows).
-- Plus: the per-role comms broker with peer gate and durable journal
-- (delivery evidence + the task_handoff fetched-content half + the
-- materialization/completion tick, under the 3.3.1 journal contract:
-- versioned rows, flocked single-contiguous-write + fsync + read-back
-- before the fetch returns, presence-keyed idempotency, explicit unit
-- states with terminal retirement, quarantine-and-alarm on torn/
-- corrupt/unknown-version rows, stable role-keyed path surviving
-- broker replacement, recovery before readiness; the tick's completion
-- re-drive gated by the 6.1 discovery contract); the
-- maintenance sweeper launchd job, heartbeat, head-of-line, certification-
-- lag, and FAIL alarms (FAIL alarm reads the durable table); the obligation
-- outbox reconciler implementing the reserve/append/finalize/recover
-- protocol (design Section 6.1), whose recovery pass detects and alarms
-- only (emission-bound anchor: the reconciler's durable write-once
-- first-enumeration record, a write-ahead prerequisite to
-- reserve/takeover per r15) and never materializes, with step 2's
-- generation-1 found-row quarantine arm; the
-- migration substrate (four-state receipted cutover
-- registry, claim-based processing ledger with physical row identity and
-- receiver-idempotent effects, writer fence across all writers and the
-- projector with its pinned lock protocol, immutable snapshots, quarantine);
-- per-role dual reader inside the broker; cutover and rollback runbooks;
-- deployment allowlist pin; track_commit_timestamp verification.
*/
