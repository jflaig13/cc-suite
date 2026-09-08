# SPDX-License-Identifier: MPL-2.0
"""Fail-closed M5 kernel reads while legacy files remain rollback surfaces."""
from __future__ import annotations

import hashlib
import fcntl
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from fleet_kernel.m5.dispatch_approval_migration import (
    DispatchApprovalMigrationError,
    parse_dispatch_bytes,
    render_kernel_surfaces,
    replay_legacy_from_events,
)
from fleet_kernel.m5.supervision import LegacySurfacePaths, read_stable_surfaces


@dataclass(frozen=True, slots=True)
class VerifiedM5Surfaces:
    dispatch_intents: bytes
    fodl_audit: bytes
    fodl_current: bytes
    dispatch_rows: tuple[dict[str, Any], ...]


def advisory_lock_identity(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise DispatchApprovalMigrationError("M5 read tenant_id is required")
    return f"m5-dispatch-approval:{tenant_id}"


def dispatch_gap_marker_path(dispatch_path: Path, tenant_id: str) -> Path:
    configured = os.environ.get("FLEET_KERNEL_M5_GAP_ROOT")
    if not configured or not Path(configured).is_absolute():
        raise DispatchApprovalMigrationError("An explicit absolute FLEET_KERNEL_M5_GAP_ROOT is required")
    root = Path(configured)
    identity = hashlib.sha256(
        f"{dispatch_path.resolve()}\0{tenant_id}".encode()
    ).hexdigest()
    return root / f"{identity}.gap"


def _gap_pending(dispatch_path: Path, tenant_id: str) -> bool:
    marker = dispatch_gap_marker_path(dispatch_path, tenant_id)
    if not marker.exists() and not marker.is_symlink():
        return False
    try:
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DispatchApprovalMigrationError("M5 dispatch gap marker is unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DispatchApprovalMigrationError("M5 dispatch gap marker is unsafe")
    finally:
        os.close(descriptor)
    return True


@contextmanager
def dispatch_surface_read_lock(path: Path) -> Iterator[None]:
    """Share the canonical dispatch writer's sibling flock for the full proof."""
    lock = path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise DispatchApprovalMigrationError("M5 dispatch read lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def m5_read_lock(conninfo: dict, *, tenant_id: str) -> Iterator[None]:
    """Take the shared side of the same lock used by M5 sync transactions."""
    identity = advisory_lock_identity(tenant_id)
    with psycopg.connect(**conninfo, autocommit=True) as lock_conn:
        acquired = lock_conn.execute(
            "SELECT pg_try_advisory_lock_shared(hashtextextended(%s, 0))",
            (identity,),
        ).fetchone()[0]
        if not acquired:
            raise DispatchApprovalMigrationError("M5 kernel read lock is busy")
        primary_error: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                unlocked = lock_conn.execute(
                    "SELECT pg_advisory_unlock_shared(hashtextextended(%s, 0))",
                    (identity,),
                ).fetchone()[0]
                if not unlocked:
                    raise DispatchApprovalMigrationError(
                        "M5 kernel read lock ownership was lost"
                    )
            except Exception as unlock_error:
                if primary_error is not None:
                    primary_error.add_note(
                        f"Additionally, M5 kernel read unlock failed: {unlock_error}"
                    )
                else:
                    raise


def read_verified_kernel_surfaces(
    conninfo: dict, *, tenant_id: str, paths: LegacySurfacePaths,
) -> VerifiedM5Surfaces:
    """Return kernel bytes only when every live rollback proof agrees exactly."""
    with dispatch_surface_read_lock(paths.dispatch_intents):
        if _gap_pending(paths.dispatch_intents, tenant_id):
            raise DispatchApprovalMigrationError(
                "M5 kernel read refused while a dual-write gap is pending"
            )
        with m5_read_lock(conninfo, tenant_id=tenant_id):
            legacy = read_stable_surfaces(paths)
            projected = render_kernel_surfaces(conninfo, tenant_id=tenant_id)
            replayed = replay_legacy_from_events(conninfo, tenant_id=tenant_id)
            with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
                snapshot = conn.execute(
                    "SELECT dispatch_sha256, dispatch_render_sha256, fodl_audit_sha256, "
                    "fodl_audit_render_sha256, fodl_current_sha256, verdict "
                    "FROM m5_surface_snapshots WHERE tenant_id = %s "
                    "ORDER BY compared_at DESC, cycle_key DESC LIMIT 1",
                    (tenant_id,),
                ).fetchone()
            if snapshot is None:
                raise DispatchApprovalMigrationError("M5 kernel read has no parity snapshot")
            dispatch_sha = hashlib.sha256(legacy[0]).hexdigest()
            audit_sha = hashlib.sha256(legacy[1]).hexdigest()
            current_sha = hashlib.sha256(legacy[2]).hexdigest()
            if (
                snapshot["verdict"] != "clean"
                or snapshot["dispatch_sha256"] != dispatch_sha
                or snapshot["dispatch_render_sha256"] != dispatch_sha
                or snapshot["fodl_audit_sha256"] != audit_sha
                or snapshot["fodl_audit_render_sha256"] != audit_sha
                or snapshot["fodl_current_sha256"] != current_sha
            ):
                raise DispatchApprovalMigrationError(
                    "M5 latest parity snapshot is not current for all rollback surfaces"
                )
            if projected != legacy:
                raise DispatchApprovalMigrationError(
                    "M5 kernel projection differs from live legacy rollback surfaces"
                )
            if replayed != legacy:
                raise DispatchApprovalMigrationError(
                    "M5 event replay differs from live legacy rollback surfaces"
                )
            rows = tuple(row.value for row in parse_dispatch_bytes(projected[0]))
            return VerifiedM5Surfaces(*projected, rows)
