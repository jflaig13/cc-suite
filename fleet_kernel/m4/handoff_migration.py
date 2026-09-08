# SPDX-License-Identifier: MPL-2.0
"""Byte-honest legacy handoff shadow into Objective + Ownership state.

M4 begins shadow-only: the existing JSONL is authoritative and the protected
handoff sweep keeps reading it. A snapshot is parsed strictly, mapped in one
Postgres transaction, rendered back from the kernel bindings, and recorded as
CLEAN only when the exact bytes and row counts match. No malformed/torn input
is silently skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


_ID_NAMESPACE = uuid.UUID("c419285a-5a4d-49b0-b3fb-852a5900d43e")
LEGACY_STATUSES = frozenset({"open", "closed", "escalated"})
REQUIRED_CORE_FIELDS = frozenset({
    "handoff_id", "from_agent", "owner_agent", "action", "expected_artifact",
    "opened_ts_utc", "deadline_utc", "status",
})
OPTIONAL_TEXT_FIELDS = frozenset({"closed_ts_utc", "evidence_ptr", "note"})
KNOWN_FIELDS = REQUIRED_CORE_FIELDS | OPTIONAL_TEXT_FIELDS
TERMINAL_STATUSES = frozenset({"closed", "escalated"})
ADVISORY_LOCK_PREFIX = "mise:fleet-kernel:m4:handoff-shadow:"


class HandoffMigrationError(RuntimeError):
    pass


def advisory_lock_identity(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise HandoffMigrationError("M4 handoff tenant ID is required")
    return ADVISORY_LOCK_PREFIX + tenant_id


@dataclass(frozen=True, slots=True)
class LegacyHandoffRow:
    handoff_id: str
    source_key: str
    ordinal: int
    line: str
    sha256: str
    value: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    snapshot_id: uuid.UUID
    tenant_id: str
    cycle_key: str
    legacy_sha256: str
    kernel_render_sha256: str
    legacy_rows: int
    kernel_rows: int
    changed_rows: int
    verdict: str
    differences: tuple[str, ...]


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_ID_NAMESPACE, "\x1f".join(parts))


def _event_key(kind: str, *parts: str) -> str:
    encoded = json.dumps([kind, *parts], ensure_ascii=False, separators=(",", ":")).encode()
    return f"m4-handoff:{kind}:{hashlib.sha256(encoded).hexdigest()}"


def _aware_timestamp(value: object, field: str, *, optional: bool = False) -> datetime | None:
    if optional and value == "":
        return None
    if not isinstance(value, str) or not value:
        raise HandoffMigrationError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HandoffMigrationError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HandoffMigrationError(f"{field} must be timezone-aware")
    return parsed


def _validate_row(
    value: object, *, ordinal: int, line: str, occurrence: int,
) -> LegacyHandoffRow:
    if not isinstance(value, dict):
        raise HandoffMigrationError(f"legacy row {ordinal + 1} is not an object")
    missing = REQUIRED_CORE_FIELDS - set(value)
    unexpected = set(value) - KNOWN_FIELDS
    if missing or unexpected:
        raise HandoffMigrationError(
            f"legacy row {ordinal + 1} schema is invalid; "
            f"missing={sorted(missing)} unexpected={sorted(unexpected)}"
        )
    value = {**{field: "" for field in OPTIONAL_TEXT_FIELDS}, **value}
    for field in (
        "handoff_id", "from_agent", "owner_agent", "action", "expected_artifact",
        "opened_ts_utc", "deadline_utc", "status",
    ):
        if not isinstance(value[field], str) or not value[field].strip():
            raise HandoffMigrationError(f"legacy row {ordinal + 1} field {field} is empty")
    if value["status"] not in LEGACY_STATUSES:
        raise HandoffMigrationError(f"legacy row {ordinal + 1} status is invalid")
    for field in ("closed_ts_utc", "evidence_ptr", "note"):
        if not isinstance(value[field], str):
            raise HandoffMigrationError(f"legacy row {ordinal + 1} field {field} is not text")
    opened = _aware_timestamp(value["opened_ts_utc"], "opened_ts_utc")
    deadline = _aware_timestamp(value["deadline_utc"], "deadline_utc")
    closed = _aware_timestamp(value["closed_ts_utc"], "closed_ts_utc", optional=True)
    if deadline <= opened:
        raise HandoffMigrationError(f"legacy row {ordinal + 1} deadline is not after open")
    if value["status"] == "open" and closed is not None:
        raise HandoffMigrationError(f"legacy row {ordinal + 1} open status has a closed time")
    return LegacyHandoffRow(
        str(value["handoff_id"]), f"{value['handoff_id']}::occurrence:{occurrence}", ordinal, line,
        hashlib.sha256(line.encode("utf-8")).hexdigest(), dict(value),
    )


def parse_legacy_bytes(data: bytes) -> tuple[LegacyHandoffRow, ...]:
    if data and not data.endswith(b"\n"):
        raise HandoffMigrationError("legacy handoff ledger has a torn trailing line")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HandoffMigrationError("legacy handoff ledger is not UTF-8") from exc
    rows: list[LegacyHandoffRow] = []
    occurrences: dict[str, int] = {}
    for ordinal, line in enumerate(text.splitlines()):
        if not line.strip():
            raise HandoffMigrationError(f"legacy row {ordinal + 1} is blank")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HandoffMigrationError(f"legacy row {ordinal + 1} is invalid JSON") from exc
        handoff_id = str(value.get("handoff_id", "")) if isinstance(value, dict) else ""
        occurrence = occurrences.get(handoff_id, 0) + 1
        occurrences[handoff_id] = occurrence
        row = _validate_row(value, ordinal=ordinal, line=line, occurrence=occurrence)
        rows.append(row)
    return tuple(rows)


def read_legacy_snapshot(path: Path) -> tuple[bytes, tuple[LegacyHandoffRow, ...]]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise HandoffMigrationError("legacy handoff ledger is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise HandoffMigrationError("legacy handoff ledger must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read()
    finally:
        os.close(descriptor)
    return data, parse_legacy_bytes(data)


def _objective_state(status: str, *, present: bool = True) -> str:
    if not present:
        return "stale"
    return {"open": "active", "closed": "done", "escalated": "blocked"}[status]


def _render_rows(rows: Sequence[Mapping[str, Any]]) -> bytes:
    if not rows:
        return b""
    return ("\n".join(str(row["legacy_line"]) for row in rows) + "\n").encode("utf-8")


def _append_event(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    objective_id: uuid.UUID,
    event_type: str,
    payload: Mapping[str, Any],
    event_key: str,
) -> None:
    event_id = _id(tenant_id, event_key, "event")
    outbox_id = _id(tenant_id, event_key, "outbox")
    existing = conn.execute(
        "SELECT e.event_type, e.payload, o.id AS outbox_id, o.destination, o.payload AS outbox_payload "
        "FROM events e LEFT JOIN kernel_outbox o ON o.tenant_id = e.tenant_id "
        "AND o.event_id = e.id WHERE e.tenant_id = %s AND e.id = %s",
        (tenant_id, event_id),
    ).fetchone()
    if existing is not None:
        if (
            existing["event_type"] != event_type
            or existing["payload"] != dict(payload)
            or existing["outbox_id"] != outbox_id
            or existing["destination"] != "m4-shadow:handoff-state"
            or existing["outbox_payload"] != dict(payload)
        ):
            raise HandoffMigrationError("handoff event idempotency collision")
        return
    seq = conn.execute(
        "INSERT INTO event_stream_heads (tenant_id, next_seq, outbox_start_seq) VALUES (%s, 2, 1) "
        "ON CONFLICT (tenant_id) DO UPDATE SET next_seq = event_stream_heads.next_seq + 1 "
        "RETURNING next_seq - 1 AS stream_seq",
        (tenant_id,),
    ).fetchone()["stream_seq"]
    conn.execute(
        "INSERT INTO events (id, tenant_id, schema_version, stream_seq, event_type, payload, "
        "occurred_at, objective_id) VALUES (%s, %s, 1, %s, %s, %s, now(), %s)",
        (event_id, tenant_id, seq, event_type, Jsonb(dict(payload)), objective_id),
    )
    conn.execute(
        "INSERT INTO kernel_outbox (id, tenant_id, event_id, stream_seq, destination, adapter, "
        "payload, idempotency_key) VALUES (%s, %s, %s, %s, 'm4-shadow:handoff-state', "
        "'m4-shadow:handoff-state', %s, %s)",
        (outbox_id, tenant_id, event_id, seq, Jsonb(dict(payload)), event_key),
    )


def sync_legacy_snapshot(
    conninfo: dict,
    *,
    tenant_id: str,
    cycle_key: str,
    data: bytes,
    compared_at: datetime | None = None,
) -> SnapshotResult:
    if not tenant_id.strip() or not cycle_key.strip():
        raise HandoffMigrationError("tenant_id and cycle_key are required")
    rows = parse_legacy_bytes(data)
    now = compared_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise HandoffMigrationError("compared_at must be timezone-aware")
    legacy_sha = hashlib.sha256(data).hexdigest()
    changed = 0
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"m4-handoff:{tenant_id}",),
            )
            prior_snapshot = conn.execute(
                "SELECT id, legacy_sha256, kernel_render_sha256, legacy_rows, kernel_rows, "
                "changed_rows, verdict, differences FROM m4_handoff_snapshots "
                "WHERE tenant_id = %s AND cycle_key = %s",
                (tenant_id, cycle_key),
            ).fetchone()
            if prior_snapshot is not None and prior_snapshot["legacy_sha256"] != legacy_sha:
                raise HandoffMigrationError("cycle_key replay carries different legacy bytes")
            if prior_snapshot is not None:
                return SnapshotResult(
                    prior_snapshot["id"], tenant_id, cycle_key,
                    prior_snapshot["legacy_sha256"], prior_snapshot["kernel_render_sha256"],
                    prior_snapshot["legacy_rows"], prior_snapshot["kernel_rows"],
                    prior_snapshot["changed_rows"], prior_snapshot["verdict"],
                    tuple(prior_snapshot["differences"]),
                )
            current_source_keys = {row.source_key for row in rows}
            current_handoff_ids = {row.handoff_id for row in rows}
            existing_rows = conn.execute(
                "SELECT source_key, handoff_id, objective_id, ownership_claim_id, legacy_ordinal, "
                "legacy_sha256, legacy_status, present FROM m4_handoff_bindings "
                "WHERE tenant_id = %s FOR UPDATE",
                (tenant_id,),
            ).fetchall()
            existing = {str(row["source_key"]): row for row in existing_rows}
            for row in rows:
                value = row.value
                objective_id = _id(tenant_id, row.handoff_id, "objective")
                claim_id = _id(tenant_id, row.handoff_id, "ownership")
                previous = existing.get(row.source_key)
                if previous is not None and previous["legacy_status"] in TERMINAL_STATUSES:
                    if value["status"] != previous["legacy_status"]:
                        raise HandoffMigrationError(
                            f"terminal handoff {row.handoff_id!r} changed status"
                        )
                row_changed = (
                    previous is None
                    or previous["legacy_sha256"] != row.sha256
                    or previous["legacy_ordinal"] != row.ordinal
                    or previous["present"] is not True
                )
                if row_changed:
                    changed += 1
                opened = _aware_timestamp(value["opened_ts_utc"], "opened_ts_utc")
                deadline = _aware_timestamp(value["deadline_utc"], "deadline_utc")
                closed = _aware_timestamp(value["closed_ts_utc"], "closed_ts_utc", optional=True)
                conn.execute(
                    "INSERT INTO objectives (id, tenant_id, schema_version, title, state, owner_role, "
                    "strategic_objective_ref, created_at, updated_at, deadline) "
                    "VALUES (%s, %s, 1, %s, %s, %s, 'SO-fleet-reliability', %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, "
                    "owner_role = EXCLUDED.owner_role, updated_at = EXCLUDED.updated_at, "
                    "deadline = EXCLUDED.deadline WHERE objectives.tenant_id = EXCLUDED.tenant_id",
                    (
                        objective_id, tenant_id, str(value["action"]),
                        _objective_state(str(value["status"])), str(value["owner_agent"]),
                        opened, now, deadline,
                    ),
                )
                conn.execute(
                    "INSERT INTO ownership_claims (id, tenant_id, schema_version, objective_id, "
                    "owner_role, lease_token, acquired_at, expires_at, released_at) "
                    "VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET owner_role = EXCLUDED.owner_role, "
                    "expires_at = EXCLUDED.expires_at, released_at = EXCLUDED.released_at "
                    "WHERE ownership_claims.tenant_id = EXCLUDED.tenant_id",
                    (
                        claim_id, tenant_id, objective_id, str(value["owner_agent"]),
                        f"handoff:{row.handoff_id}", opened, deadline, closed,
                    ),
                )
                conn.execute(
                    "INSERT INTO m4_handoff_bindings (tenant_id, source_key, handoff_id, objective_id, "
                    "ownership_claim_id, legacy_ordinal, legacy_line, legacy_sha256, legacy_status, "
                    "present, synced_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s) "
                    "ON CONFLICT (tenant_id, source_key) DO UPDATE SET "
                    "handoff_id = EXCLUDED.handoff_id, "
                    "legacy_ordinal = EXCLUDED.legacy_ordinal, legacy_line = EXCLUDED.legacy_line, "
                    "legacy_sha256 = EXCLUDED.legacy_sha256, legacy_status = EXCLUDED.legacy_status, "
                    "present = TRUE, synced_at = EXCLUDED.synced_at",
                    (
                        tenant_id, row.source_key, row.handoff_id, objective_id, claim_id, row.ordinal,
                        row.line, row.sha256, str(value["status"]), now,
                    ),
                )
                if row_changed:
                    _append_event(
                        conn,
                        tenant_id=tenant_id,
                        objective_id=objective_id,
                        event_type=f"handoff.{value['status']}",
                        payload={
                            "source_key": row.source_key,
                            "handoff_id": row.handoff_id,
                            "legacy_ordinal": row.ordinal,
                            "legacy_line": row.line,
                            "legacy_sha256": row.sha256,
                            "legacy_status": str(value["status"]),
                            "present": True,
                        },
                        event_key=_event_key("state", row.source_key, cycle_key, row.sha256),
                    )
            for source_key, previous in existing.items():
                if source_key in current_source_keys or previous["present"] is not True:
                    continue
                changed += 1
                conn.execute(
                    "UPDATE m4_handoff_bindings SET present = FALSE, synced_at = %s "
                    "WHERE tenant_id = %s AND source_key = %s",
                    (now, tenant_id, source_key),
                )
                handoff_id = str(previous["handoff_id"])
                if handoff_id not in current_handoff_ids:
                    conn.execute(
                        "UPDATE objectives SET state = 'stale', updated_at = %s "
                        "WHERE tenant_id = %s AND id = %s",
                        (now, tenant_id, previous["objective_id"]),
                    )
                    conn.execute(
                        "UPDATE ownership_claims SET released_at = COALESCE(released_at, %s) "
                        "WHERE tenant_id = %s AND id = %s",
                        (now, tenant_id, previous["ownership_claim_id"]),
                    )
                _append_event(
                    conn,
                    tenant_id=tenant_id,
                    objective_id=previous["objective_id"],
                    event_type="handoff.removed",
                    payload={
                        "source_key": source_key,
                        "handoff_id": handoff_id,
                        "present": False,
                    },
                    event_key=_event_key("removed", source_key, cycle_key),
                )
            rendered_rows = conn.execute(
                "SELECT legacy_line FROM m4_handoff_bindings "
                "WHERE tenant_id = %s AND present IS TRUE ORDER BY legacy_ordinal, handoff_id",
                (tenant_id,),
            ).fetchall()
            rendered = _render_rows(rendered_rows)
            kernel_sha = hashlib.sha256(rendered).hexdigest()
            differences: list[str] = []
            if rendered != data:
                differences.append("byte_mismatch")
            if len(rendered_rows) != len(rows):
                differences.append("row_count_mismatch")
            verdict = "clean" if not differences else "diverged"
            snapshot_id = _id(tenant_id, cycle_key, "snapshot")
            conn.execute(
                "INSERT INTO m4_handoff_snapshots (id, tenant_id, cycle_key, legacy_sha256, "
                "kernel_render_sha256, legacy_rows, kernel_rows, changed_rows, verdict, "
                "differences, compared_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (tenant_id, cycle_key) DO NOTHING",
                (
                    snapshot_id, tenant_id, cycle_key, legacy_sha, kernel_sha, len(rows),
                    len(rendered_rows), changed, verdict, Jsonb(differences), now,
                ),
            )
    return SnapshotResult(
        snapshot_id, tenant_id, cycle_key, legacy_sha, kernel_sha, len(rows),
        len(rendered_rows), changed, verdict, tuple(differences),
    )


def render_kernel_ledger(conninfo: dict, *, tenant_id: str) -> bytes:
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT legacy_line FROM m4_handoff_bindings "
            "WHERE tenant_id = %s AND present IS TRUE ORDER BY legacy_ordinal, handoff_id",
            (tenant_id,),
        ).fetchall()
    return _render_rows(rows)


def replay_legacy_from_events(conninfo: dict, *, tenant_id: str) -> bytes:
    """Rebuild the current legacy file from the sequenced M4 event history."""
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        events = conn.execute(
            "SELECT stream_seq, event_type, payload FROM events "
            "WHERE tenant_id = %s AND event_type IN "
            "('handoff.open', 'handoff.closed', 'handoff.escalated', 'handoff.removed') "
            "ORDER BY stream_seq",
            (tenant_id,),
        ).fetchall()
    state: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event["payload"]
        if not isinstance(payload, dict):
            raise HandoffMigrationError("handoff replay event payload is not an object")
        source_key = payload.get("source_key")
        handoff_id = payload.get("handoff_id")
        if not isinstance(source_key, str) or not source_key:
            raise HandoffMigrationError("handoff replay event lacks source_key")
        if not isinstance(handoff_id, str) or not handoff_id:
            raise HandoffMigrationError("handoff replay event lacks handoff_id")
        if event["event_type"] == "handoff.removed":
            if set(payload) != {"source_key", "handoff_id", "present"}:
                raise HandoffMigrationError("handoff removal event schema is invalid")
            if payload.get("present") is not False:
                raise HandoffMigrationError("handoff removal event is not explicit")
            if source_key not in state:
                raise HandoffMigrationError("handoff removal event has no active source state")
            if handoff_id != state[source_key]["handoff_id"]:
                raise HandoffMigrationError("handoff removal event identity is inconsistent")
            state.pop(source_key)
            continue
        required = {
            "source_key", "handoff_id", "legacy_ordinal", "legacy_line",
            "legacy_sha256", "legacy_status", "present",
        }
        if set(payload) != required or payload.get("present") is not True:
            raise HandoffMigrationError("handoff replay state event schema is invalid")
        line = payload.get("legacy_line")
        ordinal = payload.get("legacy_ordinal")
        legacy_status = payload.get("legacy_status")
        if (
            not isinstance(line, str)
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal < 0
        ):
            raise HandoffMigrationError("handoff replay line or ordinal is invalid")
        if (
            not isinstance(legacy_status, str)
            or legacy_status not in LEGACY_STATUSES
            or event["event_type"] != f"handoff.{legacy_status}"
        ):
            raise HandoffMigrationError("handoff replay legacy status is invalid")
        if hashlib.sha256(line.encode("utf-8")).hexdigest() != payload.get("legacy_sha256"):
            raise HandoffMigrationError("handoff replay line digest is invalid")
        state[source_key] = {
            "handoff_id": handoff_id,
            "legacy_line": line,
            "legacy_ordinal": ordinal,
            "source_key": source_key,
        }
    ordinals = [row["legacy_ordinal"] for row in state.values()]
    if len(set(ordinals)) != len(ordinals) or set(ordinals) != set(range(len(ordinals))):
        raise HandoffMigrationError("handoff replay ordinals are not unique and contiguous")
    ordered = sorted(state.values(), key=lambda row: row["legacy_ordinal"])
    return _render_rows(ordered)


def parity_gate_status(
    conninfo: dict, *, tenant_id: str, required_cycles: int = 50,
) -> dict[str, Any]:
    if required_cycles < 1:
        raise ValueError("required_cycles must be positive")
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT cycle_key, verdict, legacy_sha256, kernel_render_sha256, compared_at "
            "FROM m4_handoff_snapshots WHERE tenant_id = %s "
            "ORDER BY compared_at DESC, cycle_key DESC LIMIT %s",
            (tenant_id, required_cycles),
        ).fetchall()
    consecutive = 0
    for row in rows:
        if row["verdict"] != "clean" or row["legacy_sha256"] != row["kernel_render_sha256"]:
            break
        consecutive += 1
    return {
        "ready": consecutive >= required_cycles,
        "required_cycles": required_cycles,
        "consecutive_clean": consecutive,
        "latest_cycle_key": str(rows[0]["cycle_key"]) if rows else "",
    }
