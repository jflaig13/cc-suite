# SPDX-License-Identifier: MPL-2.0
"""Byte-honest role queues -> kernel Objectives; Slack is observability only."""
from __future__ import annotations

import hashlib
import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from fleet_kernel.m7.errors import RoleQueueMigrationError


_NAMESPACE = uuid.UUID("3b086e8f-28b6-46f6-8720-19a045eb400e")
_CANONICAL = frozenset({"event_type", "content", "meta"})
_FLAT = frozenset({"event_type", "content", "meta", "sender_role", "target_role", "ts"})
_STALL = frozenset({
    "event_type", "target_role", "ts", "handoff_id", "owner_agent", "action", "content",
})
_WORK_TYPES = frozenset({"task_handoff", "audit_request", "arbiter_ask", "handoff_stall_arbiter_ask"})


@dataclass(frozen=True, slots=True)
class LegacyQueueRow:
    source_key: str
    ordinal: int
    line: str
    raw: bytes
    sha256: str
    event_type: str
    content: str
    metadata: dict[str, Any]
    message_kind: str
    occurred_at: datetime | None


@dataclass(frozen=True, slots=True)
class RoleQueueSnapshot:
    snapshot_id: uuid.UUID
    tenant_id: str
    role: str
    cycle_key: str
    legacy_sha256: str
    kernel_render_sha256: str
    cursor_line: int
    row_count: int
    work_rows: int
    pending_rows: int
    observability_rows: int
    quarantined_rows: int
    changed_rows: int
    verdict: str


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, "\x1f".join(parts))


def _aware(value: object, *, ordinal: int, metadata: dict[str, Any]) -> datetime:
    if not isinstance(value, str) or not value.strip():
        metadata["_m7_timestamp_status"] = "missing_legacy"
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ordinal)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        metadata["_m7_timestamp_status"] = "invalid_legacy"
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ordinal)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        metadata["_m7_timestamp_status"] = "naive_assumed_utc"
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize(
    value: Any, *, ordinal: int,
) -> tuple[str, str, dict[str, Any], str, datetime | None]:
    if not isinstance(value, dict):
        return "invalid_legacy", "", {}, "quarantined_legacy", None
    keys = frozenset(value)
    if _CANONICAL <= keys:
        event_type = value.get("event_type")
        content = value.get("content")
        meta = value.get("meta")
        if not isinstance(event_type, str) or not event_type.strip() or not isinstance(
            content, str
        ) or not isinstance(meta, dict):
            return "invalid_legacy", "", {}, "quarantined_legacy", None
        metadata = dict(meta)
        for key in keys - _CANONICAL:
            metadata.setdefault(f"_legacy_top_level_{key}", value[key])
        if _FLAT <= keys:
            metadata.update({key: value[key] for key in ("sender_role", "target_role", "ts")})
    elif _STALL <= keys:
        if not all(isinstance(value.get(key), str) for key in _STALL):
            return "invalid_legacy", "", {}, "quarantined_legacy", None
        event_type = value["event_type"]
        content = value["content"]
        metadata = {key: value[key] for key in keys - {"event_type", "content"}}
    else:
        return "invalid_legacy", "", {}, "quarantined_legacy", None
    declared = isinstance(metadata.get("expected_artifact"), str) and bool(
        metadata["expected_artifact"].strip()
    )
    kind = "work" if event_type in _WORK_TYPES or (
        event_type == "agent_direct" and declared
    ) else "observability"
    stamp = metadata.get("sent_at") or metadata.get("ts") or metadata.get("timestamp")
    return event_type, content, metadata, kind, _aware(
        stamp, ordinal=ordinal, metadata=metadata,
    ) if kind == "work" else None


def parse_queue_bytes(data: bytes) -> tuple[LegacyQueueRow, ...]:
    if data and not data.endswith(b"\n"):
        raise RoleQueueMigrationError("role queue has a torn trailing line")
    raw_lines = data[:-1].split(b"\n") if data else []
    occurrences: dict[str, int] = {}
    rows = []
    for ordinal, raw in enumerate(raw_lines):
        try:
            line = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            line = "[non-UTF-8 legacy row; see legacy_bytes]"
            value = None
        else:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                value = None
        digest = hashlib.sha256(raw).hexdigest()
        occurrences[digest] = occurrences.get(digest, 0) + 1
        event_type, content, metadata, kind, occurred_at = _normalize(value, ordinal=ordinal)
        rows.append(LegacyQueueRow(
            f"{digest}::occurrence:{occurrences[digest]}", ordinal, line, raw, digest,
            event_type, content, metadata, kind, occurred_at,
        ))
    return tuple(rows)


def _render(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"\n".join(bytes(row["legacy_bytes"]) for row in rows) + (b"\n" if rows else b"")


def _append_event(
    conn: psycopg.Connection, *, tenant_id: str, role: str, row: LegacyQueueRow,
    objective_id: uuid.UUID | None,
) -> None:
    event_id = _id(tenant_id, role, row.source_key, "event")
    event_type = "m7.role_work.ingested" if row.message_kind == "work" else (
        "m7.legacy.quarantined" if row.message_kind == "quarantined_legacy" else (
            "m7.slack.observed" if row.event_type == "slack_message" else "m7.queue.observed"
        )
    )
    payload = {
        "role": role, "source_key": row.source_key, "legacy_ordinal": row.ordinal,
        "legacy_sha256": row.sha256, "source_event_type": row.event_type,
        "message_kind": row.message_kind,
    }
    existing = conn.execute(
        "SELECT event_type,payload,objective_id FROM events WHERE tenant_id=%s AND id=%s",
        (tenant_id, event_id),
    ).fetchone()
    if existing is not None:
        if existing["event_type"] != event_type or existing["payload"] != payload or existing[
            "objective_id"
        ] != objective_id:
            raise RoleQueueMigrationError("M7 event idempotency collision")
        return
    seq = conn.execute(
        "INSERT INTO event_stream_heads (tenant_id,next_seq,outbox_start_seq) VALUES (%s,2,1) "
        "ON CONFLICT (tenant_id) DO UPDATE SET next_seq=event_stream_heads.next_seq+1 "
        "RETURNING next_seq-1 AS stream_seq", (tenant_id,),
    ).fetchone()["stream_seq"]
    conn.execute(
        "INSERT INTO events (id,tenant_id,schema_version,stream_seq,event_type,payload,"
        "occurred_at,objective_id) VALUES (%s,%s,1,%s,%s,%s,%s,%s)",
        (event_id, tenant_id, seq, event_type, Jsonb(payload),
         row.occurred_at or datetime.now(timezone.utc), objective_id),
    )


def _append_replay_event(
    conn: psycopg.Connection, *, tenant_id: str, role: str, row: LegacyQueueRow,
) -> None:
    """Versioned exact replay record for the current append-only queue epoch.

    This separate v2 event backfills safely when a database already contains
    pre-v2 ingestion events: the old event identity and payload never change.
    Rotation/truncation is not absorbed into this epoch; the append-only guard
    refuses it until a future explicit epoch transition is implemented.
    """
    event_id = _id(tenant_id, role, row.source_key, "replay-v2")
    payload = {
        "replay_schema_version": 2,
        "queue_epoch": "append-only-v1",
        "role": role,
        "source_key": row.source_key,
        "legacy_ordinal": row.ordinal,
        "legacy_sha256": row.sha256,
        "legacy_b64": base64.b64encode(row.raw).decode("ascii"),
    }
    existing = conn.execute(
        "SELECT event_type,payload FROM events WHERE tenant_id=%s AND id=%s",
        (tenant_id, event_id),
    ).fetchone()
    if existing is not None:
        if existing["event_type"] != "m7.role_queue.replay.v2" or existing["payload"] != payload:
            raise RoleQueueMigrationError("M7 replay event idempotency collision")
        return
    seq = conn.execute(
        "INSERT INTO event_stream_heads (tenant_id,next_seq,outbox_start_seq) VALUES (%s,2,1) "
        "ON CONFLICT (tenant_id) DO UPDATE SET next_seq=event_stream_heads.next_seq+1 "
        "RETURNING next_seq-1 AS stream_seq", (tenant_id,),
    ).fetchone()["stream_seq"]
    conn.execute(
        "INSERT INTO events (id,tenant_id,schema_version,stream_seq,event_type,payload,"
        "occurred_at) VALUES (%s,%s,2,%s,'m7.role_queue.replay.v2',%s,%s)",
        (event_id, tenant_id, seq, Jsonb(payload), datetime.now(timezone.utc)),
    )


def _append_delivery_event(
    conn: psycopg.Connection, *, tenant_id: str, role: str, row: LegacyQueueRow,
    objective_id: uuid.UUID, occurred_at: datetime,
) -> None:
    event_id = _id(tenant_id, role, row.source_key, "delivered-event")
    payload = {
        "role": role, "source_key": row.source_key, "legacy_ordinal": row.ordinal,
    }
    existing = conn.execute(
        "SELECT event_type,payload,objective_id FROM events WHERE tenant_id=%s AND id=%s",
        (tenant_id, event_id),
    ).fetchone()
    if existing is not None:
        if existing["event_type"] != "m7.role_work.delivered" or existing[
            "payload"
        ] != payload or existing["objective_id"] != objective_id:
            raise RoleQueueMigrationError("M7 delivery event idempotency collision")
        return
    seq = conn.execute(
        "INSERT INTO event_stream_heads (tenant_id,next_seq,outbox_start_seq) VALUES (%s,2,1) "
        "ON CONFLICT (tenant_id) DO UPDATE SET next_seq=event_stream_heads.next_seq+1 "
        "RETURNING next_seq-1 AS stream_seq", (tenant_id,),
    ).fetchone()["stream_seq"]
    conn.execute(
        "INSERT INTO events (id,tenant_id,schema_version,stream_seq,event_type,payload,"
        "occurred_at,objective_id) VALUES (%s,%s,1,%s,'m7.role_work.delivered',%s,%s,%s)",
        (event_id, tenant_id, seq, Jsonb(payload), occurred_at, objective_id),
    )


def sync_role_queue_bytes(
    conninfo: dict, *, tenant_id: str, role: str, cycle_key: str,
    data: bytes, cursor_line: int, compared_at: datetime | None = None,
) -> RoleQueueSnapshot:
    if not tenant_id.strip() or not role.strip() or not cycle_key.strip():
        raise RoleQueueMigrationError("tenant_id, role, and cycle_key are required")
    rows = parse_queue_bytes(data)
    if isinstance(cursor_line, bool) or not isinstance(cursor_line, int) or not 0 <= cursor_line <= len(rows):
        raise RoleQueueMigrationError("role cursor is outside the queue")
    now = compared_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise RoleQueueMigrationError("compared_at must be timezone-aware")
    digest = hashlib.sha256(data).hexdigest()
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"m7-role-queue:{tenant_id}:{role}",),
            )
            prior = conn.execute(
                "SELECT * FROM m7_role_queue_snapshots WHERE tenant_id=%s AND role=%s AND cycle_key=%s",
                (tenant_id, role, cycle_key),
            ).fetchone()
            if prior is not None:
                if prior["legacy_sha256"] != digest or prior["cursor_line"] != cursor_line:
                    raise RoleQueueMigrationError("M7 cycle replay carries different queue state")
                return RoleQueueSnapshot(
                    prior["id"], tenant_id, role, cycle_key, prior["legacy_sha256"],
                    prior["kernel_render_sha256"], prior["cursor_line"], prior["row_count"],
                    prior["work_rows"], prior["pending_rows"], prior["observability_rows"],
                    prior["quarantined_rows"], prior["changed_rows"], prior["verdict"],
                )
            high_water = conn.execute(
                "SELECT MAX(cursor_line) AS cursor_line FROM m7_role_queue_snapshots "
                "WHERE tenant_id=%s AND role=%s", (tenant_id, role),
            ).fetchone()["cursor_line"]
            if high_water is not None and cursor_line < high_water:
                raise RoleQueueMigrationError("M7 role cursor cannot move backward")
            existing_rows = conn.execute(
                "SELECT * FROM m7_role_queue_bindings WHERE tenant_id=%s AND role=%s "
                "ORDER BY legacy_ordinal FOR UPDATE", (tenant_id, role),
            ).fetchall()
            if len(rows) < len(existing_rows) or [r.raw for r in rows[:len(existing_rows)]] != [
                bytes(r["legacy_bytes"]) for r in existing_rows
            ]:
                raise RoleQueueMigrationError("legacy role queue is not append-only")
            existing = {row["source_key"]: row for row in existing_rows}
            changed = work = pending = observed = quarantined = 0
            for row in rows:
                objective_id = _id(tenant_id, role, row.source_key, "objective") if row.message_kind == "work" else None
                state = "delivered" if row.ordinal < cursor_line else "pending"
                delivery = state if row.message_kind == "work" else (
                    "observed" if row.message_kind == "observability" else "quarantined"
                )
                previous = existing.get(row.source_key)
                if previous is not None and previous["legacy_sha256"] != row.sha256:
                    raise RoleQueueMigrationError("M7 queue identity changed bytes")
                if previous is not None and previous["delivery_state"] == "delivered" and delivery == "pending":
                    raise RoleQueueMigrationError("M7 role cursor attempted to reopen delivered work")
                changed += int(previous is None or previous["delivery_state"] != delivery)
                work += int(row.message_kind == "work")
                pending += int(delivery == "pending")
                observed += int(row.message_kind == "observability")
                quarantined += int(row.message_kind == "quarantined_legacy")
                if objective_id is not None:
                    title = (row.content.strip().splitlines() or [row.event_type])[0][:500]
                    strategic = str(
                        row.metadata.get("source_oil") or row.metadata.get("handoff_id")
                        or row.metadata.get("send_id") or f"role-queue:{role}"
                    )
                    conn.execute(
                        "INSERT INTO objectives (id,tenant_id,schema_version,title,state,owner_role,"
                        "strategic_objective_ref,created_at,updated_at) VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (id) DO NOTHING",
                        (objective_id, tenant_id, title, "done" if delivery == "delivered" else "active",
                         role, strategic, row.occurred_at, now),
                    )
                    objective = conn.execute(
                        "SELECT tenant_id,title,owner_role,strategic_objective_ref,created_at,state "
                        "FROM objectives WHERE id=%s", (objective_id,),
                    ).fetchone()
                    expected_state = "done" if delivery == "delivered" else "active"
                    if (
                        objective["tenant_id"] != tenant_id or objective["title"] != title
                        or objective["owner_role"] != role
                        or objective["strategic_objective_ref"] != strategic
                        or objective["created_at"] != row.occurred_at
                    ):
                        raise RoleQueueMigrationError("M7 objective idempotency collision")
                    if objective["state"] not in {"active", "done"}:
                        raise RoleQueueMigrationError(
                            "M7 shadow objective has unsupported external state: "
                            f"{objective['state']}"
                        )
                    if objective["state"] != expected_state:
                        if objective["state"] == "done" and expected_state == "active":
                            raise RoleQueueMigrationError("M7 delivered objective cannot reopen")
                        conn.execute(
                            "UPDATE objectives SET state=%s,updated_at=%s WHERE id=%s",
                            (expected_state, now, objective_id),
                        )
                    _append_event(
                        conn, tenant_id=tenant_id, role=role, row=row,
                        objective_id=objective_id,
                    )
                    _append_replay_event(conn, tenant_id=tenant_id, role=role, row=row)
                    if delivery == "delivered":
                        _append_delivery_event(
                            conn, tenant_id=tenant_id, role=role, row=row,
                            objective_id=objective_id, occurred_at=now,
                        )
                else:
                    _append_event(
                        conn, tenant_id=tenant_id, role=role, row=row,
                        objective_id=None,
                    )
                    _append_replay_event(conn, tenant_id=tenant_id, role=role, row=row)
                conn.execute(
                    "INSERT INTO m7_role_queue_bindings (tenant_id,role,source_key,legacy_ordinal,"
                    "legacy_line,legacy_bytes,legacy_sha256,event_type,content,metadata,message_kind,objective_id,"
                    "delivery_state,synced_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (tenant_id,role,source_key) DO UPDATE SET "
                    "delivery_state=EXCLUDED.delivery_state,synced_at=EXCLUDED.synced_at",
                    (tenant_id, role, row.source_key, row.ordinal, row.line, row.raw, row.sha256,
                     row.event_type, row.content, Jsonb(row.metadata), row.message_kind,
                     objective_id, delivery, now),
                )
            rendered = _render(conn.execute(
                "SELECT legacy_bytes FROM m7_role_queue_bindings WHERE tenant_id=%s AND role=%s "
                "ORDER BY legacy_ordinal,source_key", (tenant_id, role),
            ).fetchall())
            if rendered != data:
                raise RoleQueueMigrationError("M7 role queue parity divergence")
            snapshot_id = _id(tenant_id, role, cycle_key, "snapshot")
            conn.execute(
                "INSERT INTO m7_role_queue_snapshots (id,tenant_id,role,cycle_key,legacy_sha256,"
                "kernel_render_sha256,cursor_line,row_count,work_rows,pending_rows,observability_rows,"
                "quarantined_rows,changed_rows,verdict,compared_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'clean',%s)",
                (snapshot_id, tenant_id, role, cycle_key, digest, digest, cursor_line, len(rows),
                 work, pending, observed, quarantined, changed, now),
            )
    return RoleQueueSnapshot(
        snapshot_id, tenant_id, role, cycle_key, digest, digest, cursor_line, len(rows),
        work, pending, observed, quarantined, changed, "clean",
    )


def render_role_queue_bytes(conninfo: dict, *, tenant_id: str, role: str) -> bytes:
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        return _render(conn.execute(
            "SELECT legacy_bytes FROM m7_role_queue_bindings WHERE tenant_id=%s AND role=%s "
            "ORDER BY legacy_ordinal,source_key", (tenant_id, role),
        ).fetchall())
