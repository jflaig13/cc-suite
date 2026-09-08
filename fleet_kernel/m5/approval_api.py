# SPDX-License-Identifier: MPL-2.0
"""Typed, human-authorized decision and consume-once APIs for M5 approvals."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from fleet_kernel.founder_authority import (
    ActivationStepSubject,
    FounderActivationMandate,
    Phase4FounderActivationMandate,
    UtilityFounderActivationMandate,
)
from fleet_kernel.m5.dispatch_approval_migration import DispatchApprovalMigrationError


_ID_NAMESPACE = uuid.UUID("4330c202-d176-41ce-a581-031deab9ade8")
DECISIONS = frozenset({"approved", "denied"})
AUTHORITY_KINDS = frozenset({
    "founder_direct",
    "signed_conveyance",
    "authenticated_operator",
})


@dataclass(frozen=True, slots=True)
class AuthorityRequest:
    tenant_id: str
    approval_id: uuid.UUID
    decision_key: str
    decision: str
    decided_by: str
    authority_kind: str
    authority_ref: str
    rationale: str


AuthorityVerifier = Callable[[AuthorityRequest], bool]


@dataclass(frozen=True, slots=True)
class DecisionResult:
    tenant_id: str
    approval_id: uuid.UUID
    decision_key: str
    decision: str
    decided_by: str
    authority_kind: str
    authority_ref: str
    rationale: str
    decided_at: datetime
    event_id: uuid.UUID
    outbox_id: uuid.UUID
    mandate_id: str | None = None
    mandate_artifact_sha256: str | None = None
    activation_step: str | None = None
    activation_step_key: str | None = None
    authority_subject_ref: str | None = None
    authority_subject_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ConsumptionResult:
    tenant_id: str
    approval_id: uuid.UUID
    consume_key: str
    consumer: str
    effect_adapter: str
    effect_payload: dict[str, Any]
    consumed_at: datetime
    event_id: uuid.UUID
    outbox_id: uuid.UUID


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_ID_NAMESPACE, "\x1f".join(parts))


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DispatchApprovalMigrationError(f"{field} is required")
    return value.strip()


def _aware(value: datetime | None, field: str) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise DispatchApprovalMigrationError(f"{field} must be timezone-aware")
    return result


def _decision_result(row: Mapping[str, Any]) -> DecisionResult:
    return DecisionResult(
        str(row["tenant_id"]), row["approval_id"], str(row["decision_key"]),
        str(row["decision"]), str(row["decided_by"]), str(row["authority_kind"]),
        str(row["authority_ref"]), str(row["rationale"]), row["decided_at"],
        row["event_id"], row["outbox_id"],
        None if row.get("mandate_id") is None else str(row["mandate_id"]),
        (
            None if row.get("mandate_artifact_sha256") is None
            else str(row["mandate_artifact_sha256"])
        ),
        (
            None if row.get("activation_step") is None
            else str(row["activation_step"])
        ),
        (
            None if row.get("activation_step_key") is None
            else str(row["activation_step_key"])
        ),
        (
            None if row.get("authority_subject_ref") is None
            else str(row["authority_subject_ref"])
        ),
        (
            None if row.get("authority_subject_sha256") is None
            else str(row["authority_subject_sha256"])
        ),
    )


def _consumption_result(row: Mapping[str, Any]) -> ConsumptionResult:
    return ConsumptionResult(
        str(row["tenant_id"]), row["approval_id"], str(row["consume_key"]),
        str(row["consumer"]), str(row["effect_adapter"]),
        dict(row["effect_payload"]), row["consumed_at"], row["event_id"], row["outbox_id"],
    )


def _same_decision(
    result: DecisionResult, *, key: str, decision: str, actor: str,
    authority_kind: str, reference: str, reason: str, event_id: uuid.UUID,
    outbox_id: uuid.UUID, requested_time: datetime, time_was_explicit: bool,
) -> bool:
    return (
        (
            result.decision_key, result.decision, result.decided_by,
            result.authority_kind, result.authority_ref, result.rationale,
            result.event_id, result.outbox_id,
        )
        == (key, decision, actor, authority_kind, reference, reason, event_id, outbox_id)
        and (not time_was_explicit or result.decided_at == requested_time)
        and result.mandate_id is None
    )


def _next_sequence(conn: psycopg.Connection, tenant_id: str) -> int:
    return int(conn.execute(
        "INSERT INTO event_stream_heads (tenant_id, next_seq, outbox_start_seq) "
        "VALUES (%s, 2, 1) ON CONFLICT (tenant_id) DO UPDATE "
        "SET next_seq = event_stream_heads.next_seq + 1 RETURNING next_seq - 1 AS stream_seq",
        (tenant_id,),
    ).fetchone()["stream_seq"])


def _append_event_and_outbox(
    conn: psycopg.Connection, *, tenant_id: str, approval_id: uuid.UUID,
    event_id: uuid.UUID, outbox_id: uuid.UUID, event_type: str,
    event_payload: Mapping[str, Any], destination: str, adapter: str,
    effect_payload: Mapping[str, Any], idempotency_key: str,
) -> None:
    sequence = _next_sequence(conn, tenant_id)
    conn.execute(
        "INSERT INTO events (id, tenant_id, schema_version, stream_seq, event_type, payload, "
        "occurred_at, objective_id) SELECT %s, %s, 1, %s, %s, %s, now(), objective_id "
        "FROM approvals WHERE tenant_id = %s AND id = %s",
        (
            event_id, tenant_id, sequence, event_type, Jsonb(dict(event_payload)),
            tenant_id, approval_id,
        ),
    )
    conn.execute(
        "INSERT INTO kernel_outbox (id, tenant_id, event_id, stream_seq, destination, adapter, "
        "payload, idempotency_key) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            outbox_id, tenant_id, event_id, sequence, destination, adapter,
            Jsonb(dict(effect_payload)), idempotency_key,
        ),
    )


def decide_approval(
    conninfo: dict, *, tenant_id: str, approval_id: uuid.UUID, decision_key: str,
    decision: str, decided_by: str, authority_kind: str, authority_ref: str,
    rationale: str, authority_verifier: AuthorityVerifier,
    decided_at: datetime | None = None,
) -> DecisionResult:
    """Make one pending -> approved/denied transition after human authority verifies."""
    tenant = _required(tenant_id, "tenant_id")
    key = _required(decision_key, "decision_key")
    actor = _required(decided_by, "decided_by")
    reference = _required(authority_ref, "authority_ref")
    reason = _required(rationale, "rationale")
    if decision not in DECISIONS:
        raise DispatchApprovalMigrationError("decision must be approved or denied")
    if authority_kind not in AUTHORITY_KINDS:
        raise DispatchApprovalMigrationError("authority_kind is invalid")
    if not callable(authority_verifier):
        raise DispatchApprovalMigrationError("authority_verifier is required")
    requested_time = _aware(decided_at, "decided_at")
    authority_request = AuthorityRequest(
        tenant, approval_id, key, decision, actor, authority_kind, reference, reason,
    )
    event_id = _id(tenant, str(approval_id), "decision", key, "event")
    outbox_id = _id(tenant, str(approval_id), "decision", key, "outbox")
    # Fast immutable replay and existence check before verification. The
    # verifier must be a pure/bounded proof check and never runs while a DB
    # transaction, advisory lock, or row lock is held.
    with psycopg.connect(**conninfo, row_factory=dict_row) as preflight:
        approval = preflight.execute(
            "SELECT decision FROM approvals WHERE tenant_id = %s AND id = %s",
            (tenant, approval_id),
        ).fetchone()
        existing = preflight.execute(
            "SELECT * FROM m5_approval_decisions WHERE tenant_id = %s AND approval_id = %s",
            (tenant, approval_id),
        ).fetchone()
    if approval is None:
        raise DispatchApprovalMigrationError("approval does not exist in tenant")
    if existing is not None:
        result = _decision_result(existing)
        if not _same_decision(
            result, key=key, decision=decision, actor=actor,
            authority_kind=authority_kind, reference=reference, reason=reason,
            event_id=event_id, outbox_id=outbox_id, requested_time=requested_time,
            time_was_explicit=decided_at is not None,
        ):
            raise DispatchApprovalMigrationError("approval already has a different decision")
        return result
    if approval["decision"] != "pending":
        raise DispatchApprovalMigrationError("approval decision state lacks an audit record")
    try:
        verified = authority_verifier(authority_request)
    except Exception as exc:
        raise DispatchApprovalMigrationError("human authority verification failed") from exc
    if verified is not True:
        raise DispatchApprovalMigrationError("human authority was not verified")

    try:
        with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"m5-approval:{tenant}:{approval_id}",),
            )
            approval = conn.execute(
                "SELECT decision FROM approvals WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant, approval_id),
            ).fetchone()
            if approval is None:
                raise DispatchApprovalMigrationError("approval does not exist in tenant")
            existing = conn.execute(
                "SELECT * FROM m5_approval_decisions WHERE tenant_id = %s AND approval_id = %s",
                (tenant, approval_id),
            ).fetchone()
            if existing is not None:
                result = _decision_result(existing)
                if not _same_decision(
                    result, key=key, decision=decision, actor=actor,
                    authority_kind=authority_kind, reference=reference, reason=reason,
                    event_id=event_id, outbox_id=outbox_id, requested_time=requested_time,
                    time_was_explicit=decided_at is not None,
                ):
                    raise DispatchApprovalMigrationError("approval already has a different decision")
                return result
            key_owner = conn.execute(
                "SELECT approval_id FROM m5_approval_decisions "
                "WHERE tenant_id = %s AND decision_key = %s",
                (tenant, key),
            ).fetchone()
            if key_owner is not None:
                raise DispatchApprovalMigrationError(
                    "decision_key already belongs to another approval (idempotency collision)"
                )
            if authority_kind == "signed_conveyance":
                authority_owner = conn.execute(
                    "SELECT approval_id FROM m5_approval_decisions "
                    "WHERE tenant_id = %s AND authority_kind = 'signed_conveyance' "
                    "AND authority_ref = %s",
                    (tenant, reference),
                ).fetchone()
                if authority_owner is not None:
                    raise DispatchApprovalMigrationError(
                        "signed conveyance was already consumed by another approval"
                    )
            if approval["decision"] != "pending":
                raise DispatchApprovalMigrationError("approval decision state lacks an audit record")
            conn.execute(
                "UPDATE approvals SET decision = %s, decided_by = %s, decided_at = %s, "
                "rationale = %s WHERE tenant_id = %s AND id = %s AND decision = 'pending'",
                (decision, actor, requested_time, reason, tenant, approval_id),
            )
            event_payload = {
                "approval_id": str(approval_id), "decision_key": key, "decision": decision,
                "decided_by": actor, "authority_kind": authority_kind,
                "authority_ref": reference, "rationale": reason,
                "decided_at": requested_time.isoformat(),
            }
            _append_event_and_outbox(
                conn, tenant_id=tenant, approval_id=approval_id, event_id=event_id,
                outbox_id=outbox_id, event_type="approval.decided",
                event_payload=event_payload, destination="m5-approval:audit",
                adapter="m5-approval:audit", effect_payload=event_payload,
                idempotency_key=f"m5-approval-decision:{key}",
            )
            conn.execute(
                "INSERT INTO m5_approval_decisions (tenant_id, approval_id, decision_key, "
                "decision, decided_by, authority_kind, authority_ref, rationale, decided_at, "
                "event_id, outbox_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    tenant, approval_id, key, decision, actor, authority_kind, reference,
                    reason, requested_time, event_id, outbox_id,
                ),
            )
    except psycopg.errors.UniqueViolation as exc:
        constraint = exc.diag.constraint_name or ""
        if "signed_conveyance" in constraint:
            message = "signed conveyance was already consumed by another approval"
        elif "decision_key" in constraint:
            message = "decision_key already belongs to another approval (idempotency collision)"
        else:
            message = "approval decision transaction hit an idempotency collision"
        raise DispatchApprovalMigrationError(message) from exc
    return DecisionResult(
        tenant, approval_id, key, decision, actor, authority_kind, reference, reason,
        requested_time, event_id, outbox_id,
    )


def register_founder_activation_mandate(
    conninfo: dict, mandate: FounderActivationMandate,
) -> dict[str, Any]:
    """Persist the exact standing mandate through the provisioner-only MAC."""
    if not isinstance(mandate, FounderActivationMandate):
        raise DispatchApprovalMigrationError(
            "activation mandate must be a verified FounderActivationMandate"
        )
    return _persist_founder_activation_mandate(conninfo, mandate)


def register_phase4_founder_activation_mandate(
    conninfo: dict, mandate: Phase4FounderActivationMandate,
) -> dict[str, Any]:
    """Persist only the exact Python-pinned Phase-4 mandate artifact."""
    if not isinstance(mandate, Phase4FounderActivationMandate):
        raise DispatchApprovalMigrationError(
            "Phase-4 activation mandate must be a verified "
            "Phase4FounderActivationMandate"
        )
    mandate.require_recordable()
    return _persist_founder_activation_mandate(conninfo, mandate)


def register_utility_founder_activation_mandate(
    conninfo: dict, mandate: UtilityFounderActivationMandate,
) -> dict[str, Any]:
    """Persist only the exact Python-pinned Utility mandate artifact."""
    if not isinstance(mandate, UtilityFounderActivationMandate):
        raise DispatchApprovalMigrationError(
            "Utility activation mandate must be a verified "
            "UtilityFounderActivationMandate"
        )
    mandate.require_recordable()
    return _persist_founder_activation_mandate(conninfo, mandate)


def _persist_founder_activation_mandate(
    conninfo: dict,
    mandate: FounderActivationMandate | Phase4FounderActivationMandate | UtilityFounderActivationMandate,
) -> dict[str, Any]:
    """Write one already-verified mandate through the provisioner MAC path."""
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"founder-activation-mandate:{mandate.mandate_id}",),
        )
        existing = conn.execute(
            "SELECT * FROM kernel_founder_activation_mandates WHERE mandate_id=%s",
            (mandate.mandate_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["artifact_sha256"] != mandate.artifact_sha256
                or bytes(existing["artifact_bytes"]) != mandate.artifact_bytes
                or existing["artifact"] != mandate.artifact
                or existing["issued_at"] != mandate.issued_at
                or existing["status"] != "active"
            ):
                raise DispatchApprovalMigrationError(
                    "founder activation mandate already differs"
                )
            return dict(existing)
        attestation = conn.execute(
            "SELECT kernel_founder_mandate_mac(%s,%s,%s,%s,%s) AS mac,"
            "session_user::TEXT AS verified_by",
            (
                mandate.mandate_id, mandate.artifact_sha256,
                mandate.artifact_bytes, Jsonb(dict(mandate.artifact)),
                mandate.issued_at,
            ),
        ).fetchone()
        row = conn.execute(
            "INSERT INTO kernel_founder_activation_mandates "
            "(mandate_id,artifact_sha256,artifact_bytes,artifact,issued_at,status,"
            "authority_verified_by,authority_verification_mac) "
            "VALUES (%s,%s,%s,%s,%s,'active',%s,%s) RETURNING *",
            (
                mandate.mandate_id, mandate.artifact_sha256,
                mandate.artifact_bytes, Jsonb(dict(mandate.artifact)),
                mandate.issued_at,
                attestation["verified_by"], attestation["mac"],
            ),
        ).fetchone()
    return dict(row)


def _same_mandated_decision(
    result: DecisionResult,
    *,
    key: str,
    reason: str,
    mandate: FounderActivationMandate,
    subject: ActivationStepSubject,
    event_id: uuid.UUID,
    outbox_id: uuid.UUID,
    requested_time: datetime,
    time_was_explicit: bool,
) -> bool:
    return (
        (
            result.decision_key, result.decision, result.decided_by,
            result.authority_kind, result.authority_ref, result.rationale,
            result.event_id, result.outbox_id, result.mandate_id,
            result.mandate_artifact_sha256, result.activation_step,
            result.activation_step_key, result.authority_subject_ref,
            result.authority_subject_sha256,
        )
        == (
            key, "approved", "founder", "founder_direct",
            mandate.authority_ref, reason, event_id, outbox_id,
            mandate.mandate_id, mandate.artifact_sha256, subject.step.value,
            subject.attempt_key, subject.subject_ref, subject.sha256,
        )
        and (not time_was_explicit or result.decided_at == requested_time)
    )


def decide_mandated_activation_step(
    conninfo: dict,
    *,
    mandate: FounderActivationMandate,
    subject: ActivationStepSubject,
    decision_key: str,
    rationale: str,
    decided_at: datetime | None = None,
) -> DecisionResult:
    """Approve one exact in-scope step under the standing founder mandate."""
    if (
        not isinstance(mandate, FounderActivationMandate)
        or not isinstance(subject, ActivationStepSubject)
        or subject.mandate_id != mandate.mandate_id
    ):
        raise DispatchApprovalMigrationError(
            "mandated activation step lacks its exact standing mandate"
        )
    tenant = subject.tenant_id
    approval_id = subject.approval_id
    key = _required(decision_key, "decision_key")
    reason = _required(rationale, "rationale")
    requested_time = _aware(decided_at, "decided_at")
    event_id = _id(tenant, str(approval_id), "decision", key, "event")
    outbox_id = _id(tenant, str(approval_id), "decision", key, "outbox")
    with psycopg.connect(**conninfo, row_factory=dict_row) as preflight:
        approval = preflight.execute(
            "SELECT decision FROM approvals WHERE tenant_id=%s AND id=%s",
            (tenant, approval_id),
        ).fetchone()
        existing = preflight.execute(
            "SELECT * FROM m5_approval_decisions "
            "WHERE tenant_id=%s AND approval_id=%s",
            (tenant, approval_id),
        ).fetchone()
        registered = preflight.execute(
            "SELECT artifact_sha256,artifact_bytes,artifact,status "
            "FROM kernel_founder_activation_mandates WHERE mandate_id=%s",
            (mandate.mandate_id,),
        ).fetchone()
    if (
        registered is None
        or registered["status"] != "active"
        or registered["artifact_sha256"] != mandate.artifact_sha256
        or bytes(registered["artifact_bytes"]) != mandate.artifact_bytes
        or registered["artifact"] != mandate.artifact
    ):
        raise DispatchApprovalMigrationError(
            "standing founder activation mandate is absent or changed"
        )
    if approval is None:
        raise DispatchApprovalMigrationError("approval does not exist in tenant")
    if existing is not None:
        result = _decision_result(existing)
        if not _same_mandated_decision(
            result, key=key, reason=reason, mandate=mandate, subject=subject,
            event_id=event_id, outbox_id=outbox_id,
            requested_time=requested_time, time_was_explicit=decided_at is not None,
        ):
            raise DispatchApprovalMigrationError(
                "approval already has a different decision"
            )
        return result
    if approval["decision"] != "pending":
        raise DispatchApprovalMigrationError(
            "approval decision state lacks an audit record"
        )
    try:
        with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"founder-activation-mandate:{mandate.mandate_id}",),
            )
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"m5-approval:{tenant}:{approval_id}",),
            )
            approval = conn.execute(
                "SELECT decision FROM approvals "
                "WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (tenant, approval_id),
            ).fetchone()
            existing = conn.execute(
                "SELECT * FROM m5_approval_decisions "
                "WHERE tenant_id=%s AND approval_id=%s",
                (tenant, approval_id),
            ).fetchone()
            if existing is not None:
                result = _decision_result(existing)
                if not _same_mandated_decision(
                    result, key=key, reason=reason, mandate=mandate,
                    subject=subject, event_id=event_id, outbox_id=outbox_id,
                    requested_time=requested_time,
                    time_was_explicit=decided_at is not None,
                ):
                    raise DispatchApprovalMigrationError(
                        "approval already has a different decision"
                    )
                return result
            if approval is None or approval["decision"] != "pending":
                raise DispatchApprovalMigrationError(
                    "approval decision state lacks a pending row"
                )
            attestation = conn.execute(
                "SELECT kernel_mandated_step_mac("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s) AS mac,"
                "session_user::TEXT AS verified_by",
                (
                    tenant, approval_id, mandate.mandate_id,
                    mandate.artifact_sha256, subject.step.value,
                    subject.attempt_key, subject.subject_ref, subject.sha256,
                    Jsonb(subject.canonical_payload),
                ),
            ).fetchone()
            conn.execute(
                "UPDATE approvals SET decision='approved',decided_by='founder',"
                "decided_at=%s,rationale=%s "
                "WHERE tenant_id=%s AND id=%s AND decision='pending'",
                (requested_time, reason, tenant, approval_id),
            )
            event_payload = {
                "approval_id": str(approval_id), "decision_key": key,
                "decision": "approved", "decided_by": "founder",
                "authority_kind": "founder_direct",
                "authority_ref": mandate.authority_ref,
                "mandate_id": mandate.mandate_id,
                "mandate_artifact_sha256": mandate.artifact_sha256,
                "activation_step": subject.step.value,
                "activation_step_key": subject.attempt_key,
                "authority_subject_ref": subject.subject_ref,
                "authority_subject_sha256": subject.sha256,
                "rationale": reason, "decided_at": requested_time.isoformat(),
            }
            _append_event_and_outbox(
                conn, tenant_id=tenant, approval_id=approval_id,
                event_id=event_id, outbox_id=outbox_id,
                event_type="approval.decided", event_payload=event_payload,
                destination="m5-approval:audit", adapter="m5-approval:audit",
                effect_payload=event_payload,
                idempotency_key=f"m5-approval-decision:{key}",
            )
            conn.execute(
                "INSERT INTO m5_approval_decisions "
                "(tenant_id,approval_id,decision_key,decision,decided_by,"
                "authority_kind,authority_ref,rationale,decided_at,event_id,outbox_id,"
                "mandate_id,mandate_artifact_sha256,activation_step,"
                "activation_step_key,authority_subject_ref,"
                "authority_subject_sha256,authority_subject_payload,"
                "authority_verified_by,authority_verification_mac) "
                "VALUES (%s,%s,%s,'approved','founder','founder_direct',"
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    tenant, approval_id, key, mandate.authority_ref, reason,
                    requested_time, event_id, outbox_id, mandate.mandate_id,
                    mandate.artifact_sha256, subject.step.value,
                    subject.attempt_key, subject.subject_ref, subject.sha256,
                    Jsonb(subject.canonical_payload), attestation["verified_by"],
                    attestation["mac"],
                ),
            )
    except psycopg.errors.UniqueViolation as exc:
        constraint = exc.diag.constraint_name or ""
        if "mandate_step_attempt" in constraint:
            message = "mandate step attempt already belongs to another approval"
        elif "mandate_subject" in constraint:
            message = "mandate subject already belongs to another approval"
        elif "decision_key" in constraint:
            message = (
                "decision_key already belongs to another approval "
                "(idempotency collision)"
            )
        else:
            message = "mandated activation decision hit an idempotency collision"
        raise DispatchApprovalMigrationError(message) from exc
    return DecisionResult(
        tenant, approval_id, key, "approved", "founder", "founder_direct",
        mandate.authority_ref, reason, requested_time, event_id, outbox_id,
        mandate.mandate_id, mandate.artifact_sha256, subject.step.value,
        subject.attempt_key, subject.subject_ref, subject.sha256,
    )


def require_mandated_activation_step(
    conn: psycopg.Connection,
    *,
    mandate: FounderActivationMandate,
    subject: ActivationStepSubject,
) -> DecisionResult:
    """Fail closed unless the exact standing-mandate M5 decision is live."""
    if subject.mandate_id != mandate.mandate_id:
        raise DispatchApprovalMigrationError("activation subject mandate changed")
    conn.execute(
        "SELECT kernel_require_mandated_activation_step("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            subject.tenant_id, subject.approval_id, mandate.mandate_id,
            mandate.artifact_sha256, subject.step.value, subject.attempt_key,
            subject.subject_ref, subject.sha256, Jsonb(subject.canonical_payload),
        ),
    ).fetchone()
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT * FROM m5_approval_decisions "
            "WHERE tenant_id=%s AND approval_id=%s",
            (subject.tenant_id, subject.approval_id),
        ).fetchone()
    if row is None:
        raise DispatchApprovalMigrationError("mandated activation decision disappeared")
    return _decision_result(row)


def record_activation_step_receipt(
    conn: psycopg.Connection,
    *,
    mandate: FounderActivationMandate,
    subject: ActivationStepSubject,
    receipt_sha256: str,
    receipt_payload: Mapping[str, Any],
) -> None:
    """Persist one immutable exact completion receipt in mandate order."""
    payload = dict(receipt_payload)
    conn.execute(
        "SELECT kernel_record_founder_activation_step_receipt("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            subject.tenant_id, subject.approval_id, mandate.mandate_id,
            mandate.artifact_sha256, subject.step.value, subject.attempt_key,
            subject.subject_ref, subject.sha256, Jsonb(subject.canonical_payload),
            receipt_sha256, Jsonb(payload),
        ),
    )


def consume_approval(
    conninfo: dict, *, tenant_id: str, approval_id: uuid.UUID, consume_key: str,
    consumer: str, effect_adapter: str, effect_payload: Mapping[str, Any],
    consumed_at: datetime | None = None,
    _connection: psycopg.Connection | None = None,
) -> ConsumptionResult:
    """Consume one approved decision by atomically enqueueing one idempotent effect."""
    tenant = _required(tenant_id, "tenant_id")
    key = _required(consume_key, "consume_key")
    actor = _required(consumer, "consumer")
    adapter = _required(effect_adapter, "effect_adapter")
    if not isinstance(effect_payload, Mapping) or not effect_payload:
        raise DispatchApprovalMigrationError("effect_payload must be a non-empty object")
    try:
        canonical = json.loads(json.dumps(
            dict(effect_payload), sort_keys=True, separators=(",", ":"), allow_nan=False,
        ))
    except (TypeError, ValueError) as exc:
        raise DispatchApprovalMigrationError("effect_payload must be JSON serializable") from exc
    if not isinstance(canonical, dict):
        raise DispatchApprovalMigrationError("effect_payload must be a JSON object")
    requested_time = _aware(consumed_at, "consumed_at")
    event_id = _id(tenant, str(approval_id), "consume", key, "event")
    outbox_id = _id(tenant, str(approval_id), "consume", key, "outbox")
    try:
        connection_context = (
            nullcontext(_connection)
            if _connection is not None
            else psycopg.connect(**conninfo, row_factory=dict_row)
        )
        with connection_context as conn:
            if conn is None:
                raise DispatchApprovalMigrationError(
                    "approval consumption connection is unavailable"
                )
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"m5-approval:{tenant}:{approval_id}",),
            )
            approval = conn.execute(
                "SELECT decision FROM approvals WHERE tenant_id = %s AND id = %s FOR UPDATE",
                (tenant, approval_id),
            ).fetchone()
            if approval is None:
                raise DispatchApprovalMigrationError("approval does not exist in tenant")
            existing = conn.execute(
                "SELECT * FROM m5_approval_consumptions WHERE tenant_id = %s AND approval_id = %s",
                (tenant, approval_id),
            ).fetchone()
            if existing is not None:
                result = _consumption_result(existing)
                expected = (key, actor, adapter, canonical, event_id, outbox_id)
                actual = (
                    result.consume_key, result.consumer, result.effect_adapter,
                    result.effect_payload, result.event_id, result.outbox_id,
                )
                if actual != expected or (consumed_at is not None and result.consumed_at != requested_time):
                    raise DispatchApprovalMigrationError("approval was already consumed differently")
                return result
            key_owner = conn.execute(
                "SELECT approval_id FROM m5_approval_consumptions "
                "WHERE tenant_id = %s AND consume_key = %s",
                (tenant, key),
            ).fetchone()
            if key_owner is not None:
                raise DispatchApprovalMigrationError(
                    "consume_key already belongs to another approval (idempotency collision)"
                )
            if approval["decision"] != "approved":
                raise DispatchApprovalMigrationError("only an approved decision can be consumed")
            decision_record = conn.execute(
                "SELECT decision FROM m5_approval_decisions "
                "WHERE tenant_id = %s AND approval_id = %s",
                (tenant, approval_id),
            ).fetchone()
            if decision_record is None or decision_record["decision"] != "approved":
                raise DispatchApprovalMigrationError(
                    "approved decision lacks a verified authority record"
                )
            event_payload = {
                "approval_id": str(approval_id), "consume_key": key, "consumer": actor,
                "effect_adapter": adapter, "effect_payload_sha256": hashlib.sha256(
                    json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(), "consumed_at": requested_time.isoformat(),
            }
            outbox_payload = {
                "approval_id": str(approval_id), "consume_key": key,
                "consumer": actor, "effect": canonical,
            }
            _append_event_and_outbox(
                conn, tenant_id=tenant, approval_id=approval_id, event_id=event_id,
                outbox_id=outbox_id, event_type="approval.consumed",
                event_payload=event_payload, destination=adapter, adapter=adapter,
                effect_payload=outbox_payload, idempotency_key=f"m5-approval-consume:{key}",
            )
            conn.execute(
                "INSERT INTO m5_approval_consumptions (tenant_id, approval_id, consume_key, "
                "consumer, effect_adapter, effect_payload, consumed_at, event_id, outbox_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    tenant, approval_id, key, actor, adapter, Jsonb(canonical),
                    requested_time, event_id, outbox_id,
                ),
            )
    except psycopg.errors.UniqueViolation as exc:
        constraint = exc.diag.constraint_name or ""
        message = (
            "consume_key already belongs to another approval (idempotency collision)"
            if "consume_key" in constraint
            else "approval consume transaction hit an idempotency collision"
        )
        raise DispatchApprovalMigrationError(message) from exc
    return ConsumptionResult(
        tenant, approval_id, key, actor, adapter, canonical, requested_time, event_id, outbox_id,
    )
