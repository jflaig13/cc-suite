# SPDX-License-Identifier: MPL-2.0
"""Fail-closed M7 kernel projection while legacy role queues remain rollback."""
from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from fleet_kernel.m7.errors import (
    RoleQueueMigrationError,
    RoleQueueParityError,
    RoleQueueShadowLagError,
)
from fleet_kernel.m7.legacy_surfaces import discover_role_queues, read_stable_role_queue


@dataclass(frozen=True, slots=True)
class KernelRoleQueueRow:
    ordinal: int
    raw: bytes
    event_type: str
    content: str
    metadata: dict[str, Any]
    message_kind: str


@dataclass(frozen=True, slots=True)
class VerifiedRoleQueue:
    data: bytes
    cursor_line: int
    rows: tuple[KernelRoleQueueRow, ...]


MAX_PARITY_AGE = timedelta(hours=24)
PARITY_BUCKET_SECONDS = 180
SHADOW_SYNC_SLO = timedelta(seconds=180)


def _normalize_bytea(value: object) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise RoleQueueMigrationError("M7 kernel projection bytea type is invalid")
    return bytes(value)


def _count_spaced_cycle_times(cycle_times: list[Any]) -> int:
    """Greedily count complete cycles separated by the full soak interval."""
    counted = 0
    last = None
    spacing = timedelta(seconds=PARITY_BUCKET_SECONDS)
    for cycle_at in sorted(cycle_times):
        if last is None or cycle_at - last >= spacing:
            counted += 1
            last = cycle_at
    return counted


def _replay_events(
    events: list[dict[str, Any]], *, role: str,
    expected: list[tuple[str, int, str]],
) -> bytes:
    replay: dict[str, tuple[int, bytes]] = {}
    expected_keys = {source_key for source_key, _, _ in expected}
    for event in events:
        payload = event["payload"]
        if payload.get("role") != role:
            continue
        source_key = payload.get("source_key")
        if not isinstance(source_key, str):
            raise RoleQueueMigrationError("M7 event replay source identity is invalid")
        if source_key not in expected_keys:
            continue
        if payload.get("replay_schema_version") != 2 or payload.get(
            "queue_epoch"
        ) != "append-only-v1":
            raise RoleQueueMigrationError("M7 event replay version or epoch is invalid")
        try:
            ordinal = payload["legacy_ordinal"]
            raw = base64.b64decode(payload["legacy_b64"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise RoleQueueMigrationError("M7 event replay payload is invalid") from exc
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise RoleQueueMigrationError("M7 event replay ordinal is invalid")
        if hashlib.sha256(raw).hexdigest() != payload.get("legacy_sha256"):
            raise RoleQueueMigrationError("M7 event replay digest is invalid")
        if source_key in replay:
            raise RoleQueueMigrationError("M7 event replay source identity is invalid")
        replay[source_key] = (ordinal, raw)
    if set(replay) != expected_keys:
        raise RoleQueueMigrationError("M7 event replay does not cover current queue epoch")
    ordered = []
    for source_key, ordinal, digest in expected:
        replay_ordinal, raw = replay[source_key]
        if replay_ordinal != ordinal or hashlib.sha256(raw).hexdigest() != digest:
            raise RoleQueueMigrationError("M7 event replay differs from current binding identity")
        ordered.append(raw)
    return b"\n".join(ordered) + (b"\n" if ordered else b"")


def read_verified_kernel_queue(
    conninfo: dict, *, tenant_id: str, role: str, queue_path: Path,
) -> VerifiedRoleQueue:
    """Read kernel rows only when live bytes, cursor, binding, and events agree."""
    if not tenant_id.strip() or not role.strip():
        raise RoleQueueMigrationError("M7 kernel read tenant and role are required")
    shared_dir = queue_path.parent
    identity = f"m7-role-queue:{tenant_id}:{role}"
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            acquired = conn.execute(
                "SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0)) AS locked",
                (identity,),
            ).fetchone()["locked"]
            if not acquired:
                raise RoleQueueMigrationError("M7 kernel read lock is busy")
            before = read_stable_role_queue(shared_dir, role=role, queue_path=queue_path)
            digest = hashlib.sha256(before.data).hexdigest()
            snapshot = conn.execute(
                "SELECT legacy_sha256,kernel_render_sha256,cursor_line,row_count,verdict "
                "FROM m7_role_queue_snapshots WHERE tenant_id=%s AND role=%s "
                "AND legacy_sha256=%s AND compared_at >= now() - %s::interval "
                "ORDER BY compared_at DESC,cycle_key DESC LIMIT 1",
                (tenant_id, role, digest, MAX_PARITY_AGE),
            ).fetchone()
            bindings = conn.execute(
                "SELECT source_key,legacy_ordinal,legacy_bytes,legacy_sha256,event_type,content,"
                "metadata,message_kind "
                "FROM m7_role_queue_bindings WHERE tenant_id=%s AND role=%s "
                "ORDER BY legacy_ordinal,source_key",
                (tenant_id, role),
            ).fetchall()
            source_keys = []
            normalized_raw = []
            for row in bindings:
                if not isinstance(row["source_key"], str):
                    raise RoleQueueMigrationError("M7 kernel projection source identity is invalid")
                source_keys.append(row["source_key"])
                normalized_raw.append(_normalize_bytea(row["legacy_bytes"]))
            events = conn.execute(
                "SELECT payload FROM events WHERE tenant_id=%s "
                "AND event_type='m7.role_queue.replay.v2' "
                "AND payload->>'role'=%s AND payload->>'source_key'=ANY(%s) "
                "ORDER BY stream_seq",
                (tenant_id, role, source_keys),
            ).fetchall() if source_keys else []
            after = read_stable_role_queue(shared_dir, role=role, queue_path=queue_path)
            if before != after:
                raise RoleQueueMigrationError("M7 live queue changed during kernel read proof")
            if snapshot is None or snapshot["verdict"] != "clean":
                latest = conn.execute(
                    "SELECT compared_at,legacy_sha256 FROM m7_role_queue_snapshots "
                    "WHERE tenant_id=%s AND role=%s ORDER BY compared_at DESC LIMIT 1",
                    (tenant_id, role),
                ).fetchone()
                age = "none" if latest is None else str(
                    conn.execute("SELECT now()-%s AS age", (latest["compared_at"],)).fetchone()["age"]
                )
                raise RoleQueueShadowLagError(
                    "M7_SHADOW_LAG: no fresh exact snapshot for live append/cursor; "
                    f"last_snapshot_age={age} shadow_sync_slo_seconds="
                    f"{int(SHADOW_SYNC_SLO.total_seconds())}"
                )
            if (
                snapshot["legacy_sha256"] != digest
                or snapshot["kernel_render_sha256"] != digest
                or snapshot["row_count"] != len(bindings)
                or not 0 <= snapshot["cursor_line"] <= before.cursor_line <= len(bindings)
            ):
                raise RoleQueueParityError("M7 current parity snapshot does not bind projection")
            if [row["legacy_ordinal"] for row in bindings] != list(range(len(bindings))):
                raise RoleQueueMigrationError("M7 kernel projection ordinals are not contiguous")
            if any(raw == b"" for raw in normalized_raw):
                raise RoleQueueMigrationError(
                    "M7 kernel read refuses blank legacy rows with incompatible cursor semantics"
                )
            projected = b"\n".join(normalized_raw)
            projected += b"\n" if bindings else b""
            if projected != before.data:
                raise RoleQueueParityError("M7 kernel projection differs from live queue")
            expected_replay = [
                (row["source_key"], row["legacy_ordinal"], row["legacy_sha256"])
                for row in bindings
            ]
            if _replay_events(events, role=role, expected=expected_replay) != before.data:
                raise RoleQueueParityError("M7 event replay differs from live queue")
            rows_list = []
            for row, raw in zip(bindings, normalized_raw, strict=True):
                if (
                    isinstance(row["legacy_ordinal"], bool)
                    or not isinstance(row["legacy_ordinal"], int)
                    or not isinstance(row["source_key"], str)
                    or not isinstance(row["legacy_sha256"], str)
                    or not isinstance(row["event_type"], str)
                    or not isinstance(row["content"], str)
                    or not isinstance(row["metadata"], dict)
                    or not isinstance(row["message_kind"], str)
                ):
                    raise RoleQueueMigrationError("M7 kernel projection column types are invalid")
                try:
                    raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise RoleQueueMigrationError(
                        "M7 kernel read refuses invalid UTF-8 incompatible with legacy rollback"
                    ) from exc
                rows_list.append(KernelRoleQueueRow(
                    row["legacy_ordinal"], raw, row["event_type"],
                    row["content"], row["metadata"], row["message_kind"],
                ))
            rows = tuple(rows_list)
            return VerifiedRoleQueue(before.data, before.cursor_line, rows)


def parity_gate_status(
    conninfo: dict, *, tenant_id: str, shared_dir: Path,
    required_states: int = 50,
) -> dict[str, int | bool]:
    """Require a current clean state for every role plus a defined fleet window."""
    if required_states < 1:
        raise RoleQueueMigrationError("M7 required parity states must be positive")
    queues = discover_role_queues(shared_dir)
    if not queues:
        raise RoleQueueMigrationError("M7 parity gate found no role queues")
    current = []
    for role, queue_path in queues:
        state = read_stable_role_queue(shared_dir, role=role, queue_path=queue_path)
        current.append((role, hashlib.sha256(state.data).hexdigest(), state.cursor_line))
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        fleet_cycles = conn.execute(
            "SELECT cycle_key,array_agg(DISTINCT role ORDER BY role) AS roles,"
            "max(compared_at) AS cycle_at "
            "FROM m7_role_queue_snapshots WHERE tenant_id=%s AND verdict='clean' "
            "AND cycle_key LIKE 'legacy-fleet-cycle-v1:%%' "
            "AND compared_at >= now() - %s::interval GROUP BY cycle_key",
            (tenant_id, MAX_PARITY_AGE),
        ).fetchall()
        current_roles = sorted(role for role, _, _ in current)
        distinct = _count_spaced_cycle_times([
            row["cycle_at"] for row in fleet_cycles if row["roles"] == current_roles
        ])
        matched = 0
        for role, digest, cursor in current:
            matched += int(conn.execute(
                "SELECT EXISTS(SELECT 1 FROM m7_role_queue_snapshots WHERE tenant_id=%s "
                "AND role=%s AND legacy_sha256=%s AND kernel_render_sha256=%s "
                "AND cursor_line=%s AND verdict='clean' "
                "AND compared_at >= now() - %s::interval) AS present",
                (tenant_id, role, digest, digest, cursor, MAX_PARITY_AGE),
            ).fetchone()["present"])
    return {
        "roles": len(current),
        "current_clean_roles": matched,
        "distinct_clean_states": int(distinct),
        "required_states": required_states,
        "ready": matched == len(current) and distinct >= required_states,
    }


def verify_live_readiness(
    conninfo: dict, *, tenant_id: str, shared_dir: Path,
    required_states: int = 50,
) -> dict[str, int | bool]:
    status = parity_gate_status(
        conninfo, tenant_id=tenant_id, shared_dir=shared_dir,
        required_states=required_states,
    )
    if status["ready"] is not True:
        raise RoleQueueMigrationError(
            f"M7 cutover requires {required_states} distinct clean fleet parity states "
            "and a current state for every role"
        )
    rows = 0
    for role, queue_path in discover_role_queues(shared_dir):
        rows += len(read_verified_kernel_queue(
            conninfo, tenant_id=tenant_id, role=role, queue_path=queue_path,
        ).rows)
    return {**status, "verified_rows": rows}
