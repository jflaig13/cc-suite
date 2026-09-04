"""Stable, read-only capture for append-only M7 role queue shadowing.

Cycle identity deliberately follows queue bytes plus the monotonic role cursor. A
manual truncate-and-rewrite back to an earlier byte-identical state is outside
the legacy queue contract and is refused by the migration's append/cursor guards.
"""
from __future__ import annotations

import hashlib
import time
from datetime import timedelta
from pathlib import Path

import psycopg

from fleet_kernel.m7.legacy_surfaces import (
    StableRoleQueue,
    discover_role_queues,
    read_stable_role_queue,
)
from fleet_kernel.m7.role_queue_migration import (
    RoleQueueMigrationError,
    RoleQueueSnapshot,
    sync_role_queue_bytes,
)


def sync_shared_directory(
    conninfo: dict, *, tenant_id: str, shared_dir: Path,
) -> tuple[RoleQueueSnapshot, ...]:
    states: tuple[StableRoleQueue, ...] | None = None
    for attempt in range(5):
        queues = discover_role_queues(shared_dir)
        if not queues:
            raise RoleQueueMigrationError("M7 shadow found no role queues")
        first = tuple(read_stable_role_queue(
            shared_dir, role=role, queue_path=path,
        ) for role, path in queues)
        if queues != discover_role_queues(shared_dir):
            time.sleep(0.02 * (attempt + 1))
            continue
        second = tuple(read_stable_role_queue(
            shared_dir, role=role, queue_path=path,
        ) for role, path in queues)
        if first == second:
            states = first
            break
        time.sleep(0.02 * (attempt + 1))
    if states is None:
        raise RoleQueueMigrationError("M7 fleet role-queue state did not stabilize")
    fleet_hasher = hashlib.sha256()
    for state in states:
        fleet_hasher.update(state.role.encode())
        fleet_hasher.update(b"\0")
        fleet_hasher.update(state.data)
        fleet_hasher.update(b"\0cursor:")
        fleet_hasher.update(str(state.cursor_line).encode())
        fleet_hasher.update(b"\0")
    cycle_key = (
        f"legacy-fleet-cycle-v1:{time.time_ns()}:{fleet_hasher.hexdigest()}"
    )
    snapshots = tuple(sync_role_queue_bytes(
        conninfo, tenant_id=tenant_id, role=state.role,
        cycle_key=cycle_key, data=state.data, cursor_line=state.cursor_line,
    ) for state in states)
    with psycopg.connect(**conninfo) as conn:
        conn.execute(
            "DELETE FROM m7_role_queue_snapshots WHERE tenant_id=%s "
            "AND compared_at < now() - %s::interval",
            (tenant_id, timedelta(days=7)),
        )
    return snapshots
