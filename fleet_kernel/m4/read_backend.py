"""Fail-closed M4 kernel read projection while the JSONL remains authoritative."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from fleet_kernel.m4.handoff_migration import (
    HandoffMigrationError,
    parse_legacy_bytes,
    read_legacy_snapshot,
    replay_legacy_from_events,
)
from fleet_kernel.m4.locking import handoff_advisory_lock


def read_verified_kernel_rows(
    conninfo: dict, *, tenant_id: str, legacy_path: Path,
) -> list[dict[str, Any]]:
    """Read kernel rows only when every live rollback surface agrees exactly.

    The advisory lock spans the legacy read, kernel projection, snapshot check,
    and event replay. It is the same lock used by the shadow sync CLI, so a
    read can never observe a half-updated migration cycle.
    """
    with handoff_advisory_lock(conninfo, tenant_id=tenant_id, shared=True):
        legacy_bytes, _legacy_rows = read_legacy_snapshot(legacy_path)
        with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
            bindings = conn.execute(
                "SELECT legacy_line FROM m4_handoff_bindings "
                "WHERE tenant_id = %s AND present IS TRUE "
                "ORDER BY legacy_ordinal, handoff_id",
                (tenant_id,),
            ).fetchall()
            snapshot = conn.execute(
                "SELECT legacy_sha256, kernel_render_sha256, verdict "
                "FROM m4_handoff_snapshots WHERE tenant_id = %s "
                "ORDER BY compared_at DESC, cycle_key DESC LIMIT 1",
                (tenant_id,),
            ).fetchone()
        rendered = (
            ("\n".join(str(row["legacy_line"]) for row in bindings) + "\n").encode()
            if bindings else b""
        )
        digest = hashlib.sha256(legacy_bytes).hexdigest()
        if snapshot is None:
            raise HandoffMigrationError("M4 handoff read has no parity snapshot")
        if (
            snapshot["verdict"] != "clean"
            or snapshot["legacy_sha256"] != digest
            or snapshot["kernel_render_sha256"] != digest
        ):
            raise HandoffMigrationError("M4 handoff latest parity snapshot is not current")
        if rendered != legacy_bytes:
            raise HandoffMigrationError("M4 handoff kernel projection differs from legacy")
        if replay_legacy_from_events(conninfo, tenant_id=tenant_id) != legacy_bytes:
            raise HandoffMigrationError("M4 handoff event replay differs from legacy")
        parsed = parse_legacy_bytes(legacy_bytes)
        return [json.loads(row.line) for row in parsed]
