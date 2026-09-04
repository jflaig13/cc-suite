"""Byte-honest M5 shadow for dispatch intents and FODL approval emissions.

Legacy JSONL and markdown remain authoritative. Historical prose is never
guessed into an approval decision: only structured emit rows become pending
Approval records, while every legacy byte remains renderable and replayable.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


_ID_NAMESPACE = uuid.UUID("5a1188e5-3aba-4136-a729-e8978e2099e4")
DISPATCH_TYPES = frozenset({"oil_dispatch", "task_handoff", "fodl"})
DISPATCH_STATUSES = frozenset({
    "pending", "delivered", "skipped", "blocked", "resolved", "closed",
})
TERMINAL_DISPATCH_STATUSES = frozenset({"delivered", "skipped", "resolved", "closed"})
FODL_EMIT_FIELDS = frozenset({
    "ts", "fodl_id", "originating_agent", "arbiter_consulted", "tier", "edg",
    "time_sensitivity", "if_no_decision_by", "title_length", "problem_statement_length",
})


class DispatchApprovalMigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LegacyJsonlRow:
    source_key: str
    identity: str
    ordinal: int
    line: str
    sha256: str
    value: dict[str, Any]
    row_type: str
    status: str


@dataclass(frozen=True, slots=True)
class LegacyFodlAuditRow:
    source_key: str
    ordinal: int
    line: str
    sha256: str
    value: dict[str, Any]
    fodl_id: str | None


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    snapshot_id: uuid.UUID
    tenant_id: str
    cycle_key: str
    dispatch_sha256: str
    dispatch_render_sha256: str
    fodl_audit_sha256: str
    fodl_audit_render_sha256: str
    fodl_current_sha256: str
    dispatch_rows: int
    fodl_audit_rows: int
    changed_rows: int
    verdict: str
    differences: tuple[str, ...]


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_ID_NAMESPACE, "\x1f".join(parts))


def _event_key(kind: str, *parts: str) -> str:
    encoded = json.dumps([kind, *parts], ensure_ascii=False, separators=(",", ":")).encode()
    return f"m5-dispatch-approval:{kind}:{hashlib.sha256(encoded).hexdigest()}"


def _aware(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DispatchApprovalMigrationError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DispatchApprovalMigrationError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DispatchApprovalMigrationError(f"{field} must be timezone-aware")
    return parsed


def _jsonl(data: bytes, *, surface: str) -> list[tuple[int, str, dict[str, Any]]]:
    if data and not data.endswith(b"\n"):
        raise DispatchApprovalMigrationError(f"{surface} has a torn trailing line")
    rows: list[tuple[int, str, dict[str, Any]]] = []
    raw_lines = data[:-1].split(b"\n") if data else []
    for ordinal, raw_line in enumerate(raw_lines):
        try:
            line = raw_line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise DispatchApprovalMigrationError(f"{surface} is not UTF-8") from exc
        if not line.strip():
            raise DispatchApprovalMigrationError(f"{surface} row {ordinal + 1} is blank")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DispatchApprovalMigrationError(
                f"{surface} row {ordinal + 1} is invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise DispatchApprovalMigrationError(
                f"{surface} row {ordinal + 1} is not an object"
            )
        rows.append((ordinal, line, value))
    return rows


def _dispatch_opened(value: Mapping[str, Any], *, row_number: int | None = None) -> datetime:
    opened_raw = value.get("opened_ts_utc")
    legacy_raw = value.get("ts")
    prefix = f"dispatch row {row_number} " if row_number is not None else "dispatch "
    if not opened_raw and not legacy_raw:
        raise DispatchApprovalMigrationError(f"{prefix}has no opened timestamp")
    opened = _aware(opened_raw, "opened_ts_utc") if opened_raw else None
    legacy = _aware(legacy_raw, "ts") if legacy_raw else None
    if opened is not None and legacy is not None and opened != legacy:
        raise DispatchApprovalMigrationError(
            f"{prefix}opened_ts_utc and ts disagree"
        )
    return opened or legacy  # type: ignore[return-value]


def parse_dispatch_bytes(data: bytes) -> tuple[LegacyJsonlRow, ...]:
    parsed: list[LegacyJsonlRow] = []
    occurrences: dict[str, int] = {}
    identity_lines: dict[str, str] = {}
    explicit_ids: set[str] = set()
    for ordinal, line, value in _jsonl(data, surface="dispatch intents"):
        declared = [value.get(field) for field in ("intent_type", "type") if value.get(field)]
        if len(set(declared)) != 1 or declared[0] not in DISPATCH_TYPES:
            raise DispatchApprovalMigrationError(
                f"dispatch row {ordinal + 1} has an invalid or conflicting type"
            )
        row_type = str(declared[0])
        status = value.get("status")
        if not isinstance(status, str) or status not in DISPATCH_STATUSES:
            raise DispatchApprovalMigrationError(f"dispatch row {ordinal + 1} status is invalid")
        for field in ("owner", "subject", "action", "expected_artifact", "deadline_utc"):
            if not isinstance(value.get(field), str) or not value[field].strip():
                raise DispatchApprovalMigrationError(
                    f"dispatch row {ordinal + 1} field {field} is empty"
                )
        opened = _dispatch_opened(value, row_number=ordinal + 1)
        deadline = _aware(value.get("deadline_utc"), "deadline_utc")
        if deadline <= opened:
            raise DispatchApprovalMigrationError(
                f"dispatch row {ordinal + 1} deadline is not after open"
            )
        explicit = value.get("intent_id")
        if explicit is not None and (not isinstance(explicit, str) or not explicit.strip()):
            raise DispatchApprovalMigrationError(
                f"dispatch row {ordinal + 1} intent_id is invalid"
            )
        identity = explicit or "legacy:" + hashlib.sha256(json.dumps(
            [row_type, value["owner"], value["subject"], value.get("source_oil", "")],
            ensure_ascii=False, separators=(",", ":"),
        ).encode()).hexdigest()
        if explicit:
            if explicit in explicit_ids:
                raise DispatchApprovalMigrationError(
                    f"dispatch row {ordinal + 1} duplicates explicit intent_id {explicit!r}"
                )
            explicit_ids.add(explicit)
        elif identity in identity_lines and identity_lines[identity] != line:
            raise DispatchApprovalMigrationError(
                f"dispatch row {ordinal + 1} has ambiguous duplicate legacy identity"
            )
        identity_lines.setdefault(identity, line)
        occurrence = occurrences.get(identity, 0) + 1
        occurrences[identity] = occurrence
        source_key = identity if explicit else f"{identity}::occurrence:{occurrence}"
        parsed.append(LegacyJsonlRow(
            source_key, identity, ordinal, line,
            hashlib.sha256(line.encode("utf-8")).hexdigest(), dict(value), row_type, status,
        ))
    return tuple(parsed)


def parse_fodl_audit_bytes(data: bytes) -> tuple[LegacyFodlAuditRow, ...]:
    parsed: list[LegacyFodlAuditRow] = []
    occurrences: dict[str, int] = {}
    explicit_fodl_ids: set[str] = set()
    for ordinal, line, value in _jsonl(data, surface="FODL audit"):
        fodl_id = value.get("fodl_id")
        if fodl_id is not None:
            if set(value) != FODL_EMIT_FIELDS:
                raise DispatchApprovalMigrationError(
                    f"FODL emit row {ordinal + 1} schema is not exact"
                )
            if not isinstance(fodl_id, str) or not fodl_id.startswith("FODL-"):
                raise DispatchApprovalMigrationError(
                    f"FODL emit row {ordinal + 1} ID is invalid"
                )
            _aware(value.get("ts"), "FODL emit ts")
            if value.get("tier") not in {"S", "A", "B", "C"}:
                raise DispatchApprovalMigrationError(
                    f"FODL emit row {ordinal + 1} tier is invalid"
                )
            if isinstance(value.get("edg"), bool) or value.get("edg") not in range(5):
                raise DispatchApprovalMigrationError(
                    f"FODL emit row {ordinal + 1} EDG is invalid"
                )
            if fodl_id in explicit_fodl_ids:
                raise DispatchApprovalMigrationError(
                    f"FODL emit row {ordinal + 1} duplicates explicit ID {fodl_id!r}"
                )
            explicit_fodl_ids.add(fodl_id)
            identity = fodl_id
        else:
            identity = "record:" + hashlib.sha256(line.encode("utf-8")).hexdigest()
        occurrence = occurrences.get(identity, 0) + 1
        occurrences[identity] = occurrence
        source_key = identity if fodl_id is not None else f"{identity}::occurrence:{occurrence}"
        parsed.append(LegacyFodlAuditRow(
            source_key, ordinal, line,
            hashlib.sha256(line.encode("utf-8")).hexdigest(), dict(value), fodl_id,
        ))
    return tuple(parsed)


def _render(lines: Sequence[Mapping[str, Any]]) -> bytes:
    if not lines:
        return b""
    return ("\n".join(str(row["legacy_line"]) for row in lines) + "\n").encode("utf-8")


def _objective_state(status: str) -> str:
    if status == "blocked":
        return "blocked"
    if status in TERMINAL_DISPATCH_STATUSES:
        return "done"
    return "active"


def _run_state(status: str) -> str:
    return {
        "pending": "scheduled", "blocked": "failed", "delivered": "succeeded",
        "skipped": "cancelled", "resolved": "succeeded", "closed": "succeeded",
    }[status]


def _append_event(
    conn: psycopg.Connection, *, tenant_id: str, event_type: str,
    payload: Mapping[str, Any], event_key: str, objective_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> None:
    event_id = _id(tenant_id, event_key, "event")
    outbox_id = _id(tenant_id, event_key, "outbox")
    existing = conn.execute(
        "SELECT e.event_type, e.payload, e.objective_id, e.run_id, o.id AS outbox_id, "
        "o.destination, o.payload AS outbox_payload FROM events e "
        "LEFT JOIN kernel_outbox o ON o.tenant_id = e.tenant_id AND o.event_id = e.id "
        "WHERE e.tenant_id = %s AND e.id = %s",
        (tenant_id, event_id),
    ).fetchone()
    if existing is not None:
        if (
            existing["event_type"] != event_type
            or existing["payload"] != dict(payload)
            or existing["objective_id"] != objective_id
            or existing["run_id"] != run_id
            or existing["outbox_id"] != outbox_id
            or existing["destination"] != "m5-shadow:dispatch-approval"
            or existing["outbox_payload"] != dict(payload)
        ):
            raise DispatchApprovalMigrationError("M5 event idempotency collision")
        return
    seq = conn.execute(
        "INSERT INTO event_stream_heads (tenant_id, next_seq, outbox_start_seq) VALUES (%s, 2, 1) "
        "ON CONFLICT (tenant_id) DO UPDATE SET next_seq = event_stream_heads.next_seq + 1 "
        "RETURNING next_seq - 1 AS stream_seq",
        (tenant_id,),
    ).fetchone()["stream_seq"]
    conn.execute(
        "INSERT INTO events (id, tenant_id, schema_version, stream_seq, event_type, payload, "
        "occurred_at, objective_id, run_id) VALUES (%s, %s, 1, %s, %s, %s, now(), %s, %s)",
        (event_id, tenant_id, seq, event_type, Jsonb(dict(payload)), objective_id, run_id),
    )
    conn.execute(
        "INSERT INTO kernel_outbox (id, tenant_id, event_id, stream_seq, destination, adapter, "
        "payload, idempotency_key) VALUES (%s, %s, %s, %s, 'm5-shadow:dispatch-approval', "
        "'m5-shadow:dispatch-approval', %s, %s)",
        (outbox_id, tenant_id, event_id, seq, Jsonb(dict(payload)), event_key),
    )


def _upsert_objective(
    conn: psycopg.Connection, *, tenant_id: str, objective_id: uuid.UUID,
    title: str, owner: str, state: str, opened: datetime, deadline: datetime, now: datetime,
) -> None:
    conn.execute(
        "INSERT INTO objectives (id, tenant_id, schema_version, title, state, owner_role, "
        "strategic_objective_ref, created_at, updated_at, deadline) "
        "VALUES (%s, %s, 1, %s, %s, %s, 'SO-fleet-reliability', %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, state = EXCLUDED.state, "
        "owner_role = EXCLUDED.owner_role, "
        "updated_at = EXCLUDED.updated_at, deadline = EXCLUDED.deadline "
        "WHERE objectives.tenant_id = EXCLUDED.tenant_id",
        (objective_id, tenant_id, title, state, owner, opened, now, deadline),
    )


def _upsert_pending_approval(
    conn: psycopg.Connection, *, approval_id: uuid.UUID, tenant_id: str,
    objective_id: uuid.UUID, requested_by: str, requested_at: datetime, rationale: str,
) -> None:
    existing = conn.execute(
        "SELECT tenant_id, objective_id, requested_by, requested_at, decision, rationale "
        "FROM approvals WHERE id = %s FOR UPDATE",
        (approval_id,),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO approvals (id, tenant_id, schema_version, objective_id, requested_by, "
            "decision, requested_at, rationale) VALUES (%s, %s, 1, %s, %s, 'pending', %s, %s)",
            (approval_id, tenant_id, objective_id, requested_by, requested_at, rationale),
        )
        return
    expected = (tenant_id, objective_id, requested_by, requested_at, rationale)
    actual = (
        existing["tenant_id"], existing["objective_id"], existing["requested_by"],
        existing["requested_at"], existing["rationale"],
    )
    if existing["decision"] != "pending" and actual != expected:
        raise DispatchApprovalMigrationError("decided approval request bytes changed")
    if existing["decision"] == "pending":
        conn.execute(
            "UPDATE approvals SET tenant_id = %s, objective_id = %s, requested_by = %s, "
            "requested_at = %s, rationale = %s WHERE id = %s",
            (*expected, approval_id),
        )


def _sync_dispatch_rows(
    conn: psycopg.Connection, *, tenant_id: str, cycle_key: str,
    rows: Sequence[LegacyJsonlRow], now: datetime,
) -> int:
    existing_rows = conn.execute(
        "SELECT source_key, objective_id, run_id, approval_id, legacy_ordinal, legacy_sha256, "
        "legacy_type, legacy_status, present FROM m5_dispatch_bindings "
        "WHERE tenant_id = %s FOR UPDATE",
        (tenant_id,),
    ).fetchall()
    existing = {str(row["source_key"]): row for row in existing_rows}
    current = {row.source_key for row in rows}
    changed = 0
    for row in rows:
        previous = existing.get(row.source_key)
        if (
            previous is not None
            and previous["legacy_status"] in TERMINAL_DISPATCH_STATUSES
            and row.status != previous["legacy_status"]
        ):
            raise DispatchApprovalMigrationError(
                f"terminal dispatch {row.identity!r} changed status"
            )
        if previous is not None and previous["legacy_type"] != row.row_type:
            raise DispatchApprovalMigrationError(
                f"dispatch {row.identity!r} changed between Run and Approval types"
            )
        row_changed = (
            previous is None or previous["legacy_sha256"] != row.sha256
            or previous["legacy_ordinal"] != row.ordinal
            or previous["present"] is not True
        )
        changed += int(row_changed)
        value = row.value
        opened = _dispatch_opened(value)
        deadline = _aware(value.get("deadline_utc"), "dispatch deadline")
        objective_id = _id(tenant_id, "dispatch", row.source_key, "objective")
        _upsert_objective(
            conn, tenant_id=tenant_id, objective_id=objective_id,
            title=str(value["subject"]), owner=str(value["owner"]),
            state=_objective_state(row.status), opened=opened, deadline=deadline, now=now,
        )
        run_id: uuid.UUID | None = None
        approval_id: uuid.UUID | None = None
        if row.row_type == "fodl":
            approval_id = _id(tenant_id, "dispatch", row.source_key, "approval")
            _upsert_pending_approval(
                conn, approval_id=approval_id, tenant_id=tenant_id,
                objective_id=objective_id, requested_by="legacy-dispatch", requested_at=opened,
                rationale=(
                    f"Legacy status {row.status}; no founder decision inferred from dispatch prose."
                ),
            )
        else:
            run_id = _id(tenant_id, "dispatch", row.source_key, "run")
            conn.execute(
                "INSERT INTO runs (id, tenant_id, schema_version, objective_id, state, "
                "worker_identity, attempt_number, started_at, ended_at, dispatch_key) "
                "VALUES (%s, %s, 1, %s, %s, %s, 1, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, "
                "worker_identity = EXCLUDED.worker_identity, started_at = EXCLUDED.started_at, "
                "ended_at = CASE WHEN EXCLUDED.state IN ('scheduled', 'running') THEN NULL "
                "ELSE COALESCE(runs.ended_at, EXCLUDED.ended_at) END "
                "WHERE runs.tenant_id = EXCLUDED.tenant_id",
                (
                    run_id, tenant_id, objective_id, _run_state(row.status), str(value["owner"]),
                    opened if row.status != "pending" else None,
                    now if row.status in TERMINAL_DISPATCH_STATUSES or row.status == "blocked" else None,
                    row.source_key,
                ),
            )
        conn.execute(
            "INSERT INTO m5_dispatch_bindings (tenant_id, source_key, dispatch_identity, "
            "objective_id, run_id, approval_id, legacy_ordinal, legacy_line, legacy_sha256, "
            "legacy_type, legacy_status, present, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s) "
            "ON CONFLICT (tenant_id, source_key) DO UPDATE SET "
            "dispatch_identity = EXCLUDED.dispatch_identity, objective_id = EXCLUDED.objective_id, "
            "run_id = EXCLUDED.run_id, approval_id = EXCLUDED.approval_id, "
            "legacy_ordinal = EXCLUDED.legacy_ordinal, "
            "legacy_line = EXCLUDED.legacy_line, legacy_sha256 = EXCLUDED.legacy_sha256, "
            "legacy_type = EXCLUDED.legacy_type, legacy_status = EXCLUDED.legacy_status, "
            "present = TRUE, synced_at = EXCLUDED.synced_at",
            (
                tenant_id, row.source_key, row.identity, objective_id, run_id, approval_id,
                row.ordinal, row.line, row.sha256, row.row_type, row.status, now,
            ),
        )
        if row_changed:
            payload = {
                "source_key": row.source_key, "dispatch_identity": row.identity,
                "legacy_ordinal": row.ordinal, "legacy_line": row.line,
                "legacy_sha256": row.sha256, "legacy_type": row.row_type,
                "legacy_status": row.status, "present": True,
            }
            _append_event(
                conn, tenant_id=tenant_id, event_type="dispatch.state", payload=payload,
                event_key=_event_key("dispatch-state", row.source_key, cycle_key, row.sha256),
                objective_id=objective_id, run_id=run_id,
            )
    for source_key, previous in existing.items():
        if source_key in current or previous["present"] is not True:
            continue
        changed += 1
        conn.execute(
            "UPDATE m5_dispatch_bindings SET present = FALSE, synced_at = %s "
            "WHERE tenant_id = %s AND source_key = %s",
            (now, tenant_id, source_key),
        )
        conn.execute(
            "UPDATE objectives SET state = 'stale', updated_at = %s "
            "WHERE tenant_id = %s AND id = %s",
            (now, tenant_id, previous["objective_id"]),
        )
        if previous["run_id"] is not None:
            conn.execute(
                "UPDATE runs SET state = 'cancelled', ended_at = COALESCE(ended_at, %s) "
                "WHERE tenant_id = %s AND id = %s AND state IN ('scheduled', 'running')",
                (now, tenant_id, previous["run_id"]),
            )
        _append_event(
            conn, tenant_id=tenant_id, event_type="dispatch.removed",
            payload={"source_key": source_key, "present": False},
            event_key=_event_key("dispatch-removed", source_key, cycle_key),
            objective_id=previous["objective_id"], run_id=previous["run_id"],
        )
    return changed


def _sync_fodl_audit_rows(
    conn: psycopg.Connection, *, tenant_id: str, cycle_key: str,
    rows: Sequence[LegacyFodlAuditRow], now: datetime,
) -> int:
    existing_rows = conn.execute(
        "SELECT source_key, fodl_id, objective_id, approval_id, legacy_ordinal, legacy_sha256, "
        "is_emit, present "
        "FROM m5_fodl_audit_bindings WHERE tenant_id = %s FOR UPDATE",
        (tenant_id,),
    ).fetchall()
    existing = {str(row["source_key"]): row for row in existing_rows}
    current = {row.source_key for row in rows}
    changed = 0
    for row in rows:
        previous = existing.get(row.source_key)
        row_changed = (
            previous is None or previous["legacy_sha256"] != row.sha256
            or previous["legacy_ordinal"] != row.ordinal
            or previous["present"] is not True
        )
        changed += int(row_changed)
        objective_id: uuid.UUID | None = None
        approval_id: uuid.UUID | None = None
        if row.fodl_id is not None:
            requested_at = _aware(row.value.get("ts"), "FODL emit ts")
            objective_id = _id(tenant_id, "fodl-audit", row.source_key, "objective")
            approval_id = _id(tenant_id, "fodl-audit", row.source_key, "approval")
            _upsert_objective(
                conn, tenant_id=tenant_id, objective_id=objective_id,
                title=row.fodl_id, owner="Founder", state="blocked", opened=requested_at,
                deadline=requested_at, now=now,
            )
            _upsert_pending_approval(
                conn, approval_id=approval_id, tenant_id=tenant_id,
                objective_id=objective_id,
                requested_by=str(row.value["originating_agent"]), requested_at=requested_at,
                rationale="Structured legacy FODL emission; awaiting a typed founder decision.",
            )
        conn.execute(
            "INSERT INTO m5_fodl_audit_bindings (tenant_id, source_key, fodl_id, objective_id, "
            "approval_id, legacy_ordinal, legacy_line, legacy_sha256, is_emit, present, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s) "
            "ON CONFLICT (tenant_id, source_key) DO UPDATE SET fodl_id = EXCLUDED.fodl_id, "
            "objective_id = EXCLUDED.objective_id, approval_id = EXCLUDED.approval_id, "
            "legacy_ordinal = EXCLUDED.legacy_ordinal, "
            "legacy_line = EXCLUDED.legacy_line, legacy_sha256 = EXCLUDED.legacy_sha256, "
            "is_emit = EXCLUDED.is_emit, present = TRUE, synced_at = EXCLUDED.synced_at",
            (
                tenant_id, row.source_key, row.fodl_id, objective_id, approval_id,
                row.ordinal, row.line, row.sha256, row.fodl_id is not None, now,
            ),
        )
        if row_changed:
            _append_event(
                conn, tenant_id=tenant_id, event_type="fodl.audit.state",
                payload={
                    "source_key": row.source_key, "fodl_id": row.fodl_id,
                    "legacy_ordinal": row.ordinal, "legacy_line": row.line,
                    "legacy_sha256": row.sha256, "is_emit": row.fodl_id is not None,
                    "present": True,
                },
                event_key=_event_key("fodl-audit-state", row.source_key, cycle_key, row.sha256),
                objective_id=objective_id,
            )
    for source_key, previous in existing.items():
        if source_key in current or previous["present"] is not True:
            continue
        changed += 1
        conn.execute(
            "UPDATE m5_fodl_audit_bindings SET present = FALSE, synced_at = %s "
            "WHERE tenant_id = %s AND source_key = %s",
            (now, tenant_id, source_key),
        )
        _append_event(
            conn, tenant_id=tenant_id, event_type="fodl.audit.removed",
            payload={"source_key": source_key, "present": False},
            event_key=_event_key("fodl-audit-removed", source_key, cycle_key),
            objective_id=previous["objective_id"],
        )
    return changed


def sync_legacy_surfaces(
    conninfo: dict, *, tenant_id: str, cycle_key: str, dispatch_data: bytes,
    fodl_audit_data: bytes, fodl_current_data: bytes,
    compared_at: datetime | None = None,
) -> SnapshotResult:
    if not isinstance(tenant_id, str) or not tenant_id.strip() or not cycle_key.strip():
        raise DispatchApprovalMigrationError("tenant_id and cycle_key are required")
    dispatch_rows = parse_dispatch_bytes(dispatch_data)
    audit_rows = parse_fodl_audit_bytes(fodl_audit_data)
    try:
        fodl_current_data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DispatchApprovalMigrationError("FODL current markdown is not UTF-8") from exc
    now = compared_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise DispatchApprovalMigrationError("compared_at must be timezone-aware")
    dispatch_sha = hashlib.sha256(dispatch_data).hexdigest()
    audit_sha = hashlib.sha256(fodl_audit_data).hexdigest()
    current_sha = hashlib.sha256(fodl_current_data).hexdigest()
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"m5-dispatch-approval:{tenant_id}",),
            )
            prior = conn.execute(
                "SELECT * FROM m5_surface_snapshots WHERE tenant_id = %s AND cycle_key = %s",
                (tenant_id, cycle_key),
            ).fetchone()
            if prior is not None:
                if (
                    prior["dispatch_sha256"] != dispatch_sha
                    or prior["fodl_audit_sha256"] != audit_sha
                    or prior["fodl_current_sha256"] != current_sha
                ):
                    raise DispatchApprovalMigrationError(
                        "cycle_key replay carries different legacy surface bytes"
                    )
                return SnapshotResult(
                    prior["id"], tenant_id, cycle_key, prior["dispatch_sha256"],
                    prior["dispatch_render_sha256"], prior["fodl_audit_sha256"],
                    prior["fodl_audit_render_sha256"], prior["fodl_current_sha256"],
                    prior["dispatch_rows"], prior["fodl_audit_rows"], prior["changed_rows"],
                    prior["verdict"], tuple(prior["differences"]),
                )
            changed = _sync_dispatch_rows(
                conn, tenant_id=tenant_id, cycle_key=cycle_key, rows=dispatch_rows, now=now,
            )
            changed += _sync_fodl_audit_rows(
                conn, tenant_id=tenant_id, cycle_key=cycle_key, rows=audit_rows, now=now,
            )
            previous_doc = conn.execute(
                "SELECT legacy_sha256 FROM m5_fodl_documents WHERE tenant_id = %s FOR UPDATE",
                (tenant_id,),
            ).fetchone()
            if previous_doc is None or previous_doc["legacy_sha256"] != current_sha:
                changed += 1
                conn.execute(
                    "INSERT INTO m5_fodl_documents (tenant_id, legacy_bytes, legacy_sha256, synced_at) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id) DO UPDATE SET "
                    "legacy_bytes = EXCLUDED.legacy_bytes, legacy_sha256 = EXCLUDED.legacy_sha256, "
                    "synced_at = EXCLUDED.synced_at",
                    (tenant_id, fodl_current_data, current_sha, now),
                )
                _append_event(
                    conn, tenant_id=tenant_id, event_type="fodl.document",
                    payload={
                        "legacy_b64": base64.b64encode(fodl_current_data).decode(),
                        "legacy_sha256": current_sha,
                    },
                    event_key=_event_key("fodl-document", cycle_key, current_sha),
                )
            dispatch_render = _render(conn.execute(
                "SELECT legacy_line FROM m5_dispatch_bindings WHERE tenant_id = %s "
                "AND present IS TRUE ORDER BY legacy_ordinal, source_key",
                (tenant_id,),
            ).fetchall())
            audit_render = _render(conn.execute(
                "SELECT legacy_line FROM m5_fodl_audit_bindings WHERE tenant_id = %s "
                "AND present IS TRUE ORDER BY legacy_ordinal, source_key",
                (tenant_id,),
            ).fetchall())
            dispatch_render_sha = hashlib.sha256(dispatch_render).hexdigest()
            audit_render_sha = hashlib.sha256(audit_render).hexdigest()
            differences: list[str] = []
            if dispatch_render != dispatch_data:
                differences.append("dispatch_byte_mismatch")
            if audit_render != fodl_audit_data:
                differences.append("fodl_audit_byte_mismatch")
            document = conn.execute(
                "SELECT legacy_bytes FROM m5_fodl_documents WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchone()
            if document is None or bytes(document["legacy_bytes"]) != fodl_current_data:
                differences.append("fodl_current_byte_mismatch")
            if differences:
                raise DispatchApprovalMigrationError(
                    "M5 parity divergence; shadow transaction rolled back: "
                    + ", ".join(differences)
                )
            verdict = "clean"
            snapshot_id = _id(tenant_id, cycle_key, "snapshot")
            conn.execute(
                "INSERT INTO m5_surface_snapshots (id, tenant_id, cycle_key, dispatch_sha256, "
                "dispatch_render_sha256, fodl_audit_sha256, fodl_audit_render_sha256, "
                "fodl_current_sha256, dispatch_rows, fodl_audit_rows, changed_rows, verdict, "
                "differences, compared_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s)",
                (
                    snapshot_id, tenant_id, cycle_key, dispatch_sha, dispatch_render_sha,
                    audit_sha, audit_render_sha, current_sha, len(dispatch_rows), len(audit_rows),
                    changed, verdict, Jsonb(differences), now,
                ),
            )
    return SnapshotResult(
        snapshot_id, tenant_id, cycle_key, dispatch_sha, dispatch_render_sha, audit_sha,
        audit_render_sha, current_sha, len(dispatch_rows), len(audit_rows), changed,
        verdict, tuple(differences),
    )


def render_kernel_surfaces(conninfo: dict, *, tenant_id: str) -> tuple[bytes, bytes, bytes]:
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        dispatch = _render(conn.execute(
            "SELECT legacy_line FROM m5_dispatch_bindings WHERE tenant_id = %s "
            "AND present IS TRUE ORDER BY legacy_ordinal, source_key", (tenant_id,),
        ).fetchall())
        audit = _render(conn.execute(
            "SELECT legacy_line FROM m5_fodl_audit_bindings WHERE tenant_id = %s "
            "AND present IS TRUE ORDER BY legacy_ordinal, source_key", (tenant_id,),
        ).fetchall())
        document = conn.execute(
            "SELECT legacy_bytes FROM m5_fodl_documents WHERE tenant_id = %s", (tenant_id,),
        ).fetchone()
    return dispatch, audit, bytes(document["legacy_bytes"]) if document else b""


def replay_legacy_from_events(
    conninfo: dict, *, tenant_id: str,
) -> tuple[bytes, bytes, bytes]:
    event_types = (
        "dispatch.state", "dispatch.removed", "fodl.audit.state",
        "fodl.audit.removed", "fodl.document",
    )
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        events = conn.execute(
            "SELECT stream_seq, event_type, payload FROM events WHERE tenant_id = %s "
            "AND event_type = ANY(%s) ORDER BY stream_seq",
            (tenant_id, list(event_types)),
        ).fetchall()
    dispatch: dict[str, dict[str, Any]] = {}
    audit: dict[str, dict[str, Any]] = {}
    document = b""
    for event in events:
        payload = event["payload"]
        if not isinstance(payload, dict):
            raise DispatchApprovalMigrationError("M5 replay payload is not an object")
        event_type = event["event_type"]
        if event_type == "fodl.document":
            if set(payload) != {"legacy_b64", "legacy_sha256"}:
                raise DispatchApprovalMigrationError("FODL document replay schema is invalid")
            try:
                raw = base64.b64decode(payload["legacy_b64"], validate=True)
            except (TypeError, ValueError) as exc:
                raise DispatchApprovalMigrationError("FODL document replay bytes are invalid") from exc
            if hashlib.sha256(raw).hexdigest() != payload["legacy_sha256"]:
                raise DispatchApprovalMigrationError("FODL document replay digest is invalid")
            document = raw
            continue
        target = dispatch if event_type.startswith("dispatch.") else audit
        if event_type.endswith("removed"):
            if set(payload) != {"source_key", "present"} or payload.get("present") is not False:
                raise DispatchApprovalMigrationError("M5 removal replay schema is invalid")
            source_key = payload.get("source_key")
            if not isinstance(source_key, str) or source_key not in target:
                raise DispatchApprovalMigrationError("M5 removal replay lacks active state")
            target.pop(source_key)
            continue
        required = {
            "source_key", "legacy_ordinal", "legacy_line", "legacy_sha256", "present",
        }
        required |= (
            {"dispatch_identity", "legacy_type", "legacy_status"}
            if event_type == "dispatch.state" else {"fodl_id", "is_emit"}
        )
        if set(payload) != required or payload.get("present") is not True:
            raise DispatchApprovalMigrationError("M5 state replay schema is invalid")
        source_key = payload.get("source_key")
        line = payload.get("legacy_line")
        ordinal = payload.get("legacy_ordinal")
        if (
            not isinstance(source_key, str) or not source_key
            or not isinstance(line, str)
            or isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0
            or hashlib.sha256(line.encode("utf-8")).hexdigest() != payload.get("legacy_sha256")
        ):
            raise DispatchApprovalMigrationError("M5 replay line identity is invalid")
        target[source_key] = {
            "source_key": source_key, "legacy_line": line, "legacy_ordinal": ordinal,
        }
    def rendered(state: dict[str, dict[str, Any]]) -> bytes:
        ordinals = [row["legacy_ordinal"] for row in state.values()]
        if len(set(ordinals)) != len(ordinals) or set(ordinals) != set(range(len(ordinals))):
            raise DispatchApprovalMigrationError("M5 replay ordinals are not unique and contiguous")
        return _render(sorted(state.values(), key=lambda row: row["legacy_ordinal"]))
    return rendered(dispatch), rendered(audit), document


def parity_gate_status(
    conninfo: dict, *, tenant_id: str, required_cycles: int = 50,
) -> dict[str, Any]:
    if required_cycles < 1:
        raise ValueError("required_cycles must be positive")
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT cycle_key, verdict, dispatch_sha256, dispatch_render_sha256, "
            "fodl_audit_sha256, fodl_audit_render_sha256, compared_at "
            "FROM m5_surface_snapshots WHERE tenant_id = %s "
            "ORDER BY compared_at DESC, cycle_key DESC LIMIT %s",
            (tenant_id, required_cycles),
        ).fetchall()
    consecutive = 0
    for row in rows:
        if (
            row["verdict"] != "clean"
            or row["dispatch_sha256"] != row["dispatch_render_sha256"]
            or row["fodl_audit_sha256"] != row["fodl_audit_render_sha256"]
        ):
            break
        consecutive += 1
    return {
        "ready": consecutive >= required_cycles,
        "required_cycles": required_cycles,
        "consecutive_clean": consecutive,
        "latest_cycle_key": str(rows[0]["cycle_key"]) if rows else "",
    }
