# SPDX-License-Identifier: MPL-2.0
"""Fail-closed M6 reads while the legacy envelope stays the rollback surface."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from fleet_kernel.m6.spend_migration import (
    SpendMigrationError,
    parse_spend_bytes,
    render_spend_bytes,
)
from fleet_kernel.m6.supervision import read_stable_spend


@dataclass(frozen=True, slots=True)
class VerifiedSpend:
    data: bytes
    row_count: int
    attributed_rows: int
    unattributed_rows: int


def advisory_lock_identity(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise SpendMigrationError("M6 read tenant_id is required")
    return f"m6-spend:{tenant_id}"


@contextmanager
def m6_read_lock(conninfo: dict, *, tenant_id: str) -> Iterator[None]:
    identity = advisory_lock_identity(tenant_id)
    with psycopg.connect(**conninfo, autocommit=True) as lock_conn:
        acquired = lock_conn.execute(
            "SELECT pg_try_advisory_lock_shared(hashtextextended(%s,0))", (identity,),
        ).fetchone()[0]
        if not acquired:
            raise SpendMigrationError("M6 kernel read lock is busy")
        primary_error: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                unlocked = lock_conn.execute(
                    "SELECT pg_advisory_unlock_shared(hashtextextended(%s,0))", (identity,),
                ).fetchone()[0]
                if not unlocked:
                    raise SpendMigrationError("M6 kernel read lock ownership was lost")
            except Exception as unlock_error:
                if primary_error is not None:
                    primary_error.add_note(f"Additionally, M6 kernel read unlock failed: {unlock_error}")
                else:
                    raise


def read_verified_kernel_spend(
    conninfo: dict, *, tenant_id: str, legacy_path: Path,
) -> VerifiedSpend:
    with m6_read_lock(conninfo, tenant_id=tenant_id):
        legacy = read_stable_spend(legacy_path)
        projected = render_spend_bytes(conninfo, tenant_id=tenant_id)
        digest = hashlib.sha256(legacy).hexdigest()
        with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
            snapshot = conn.execute(
                "SELECT legacy_sha256,kernel_render_sha256,row_count,attributed_rows,"
                "unattributed_rows,verdict FROM m6_spend_snapshots WHERE tenant_id=%s "
                "ORDER BY compared_at DESC,cycle_key DESC LIMIT 1", (tenant_id,),
            ).fetchone()
            statuses = conn.execute(
                "SELECT legacy_ordinal,attribution_status FROM m6_spend_bindings "
                "WHERE tenant_id=%s AND present IS TRUE ORDER BY legacy_ordinal", (tenant_id,),
            ).fetchall()
            m3_replay = conn.execute(
                "SELECT o.payload FROM events e JOIN kernel_outbox o "
                "ON o.tenant_id=e.tenant_id AND o.event_id=e.id "
                "WHERE e.tenant_id=%s AND e.event_type='legacy.surface_snapshot.spend_ledger' "
                "ORDER BY e.stream_seq DESC LIMIT 1", (tenant_id,),
            ).fetchone()
        if snapshot is None:
            raise SpendMigrationError("M6 kernel read has no parity snapshot")
        if (
            snapshot["verdict"] != "clean" or snapshot["legacy_sha256"] != digest
            or snapshot["kernel_render_sha256"] != digest
        ):
            raise SpendMigrationError("M6 latest parity snapshot is not current")
        if projected != legacy:
            raise SpendMigrationError("M6 kernel projection differs from live rollback ledger")
        if m3_replay is None or not isinstance(m3_replay["payload"], dict) or set(
            m3_replay["payload"]
        ) != {"replace_text"} or m3_replay["payload"]["replace_text"].encode() != legacy:
            raise SpendMigrationError("M6 M3 event replay differs from live rollback ledger")
        rows = parse_spend_bytes(projected)
        if (
            snapshot["row_count"] != len(rows)
            or snapshot["attributed_rows"] + snapshot["unattributed_rows"] != len(rows)
        ):
            raise SpendMigrationError("M6 snapshot attribution counts differ from projection")
        seen_attributed = False
        for row in statuses:
            if row["attribution_status"] == "attributed":
                seen_attributed = True
            elif seen_attributed:
                raise SpendMigrationError("M6 unattributed row is not in the historical prefix")
        if sum(row["attribution_status"] == "unattributed_legacy" for row in statuses) != snapshot[
            "unattributed_rows"
        ]:
            raise SpendMigrationError("M6 live attribution counts differ from snapshot")
        return VerifiedSpend(
            projected, len(rows), snapshot["attributed_rows"], snapshot["unattributed_rows"],
        )
