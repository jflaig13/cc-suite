# SPDX-License-Identifier: MPL-2.0
"""Stable, read-only legacy-surface capture for the M5 shadow observer."""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fleet_kernel.m5.dispatch_approval_migration import (
    DispatchApprovalMigrationError,
    SnapshotResult,
    render_kernel_surfaces,
    replay_legacy_from_events,
    sync_legacy_surfaces,
)


@dataclass(frozen=True, slots=True)
class LegacySurfacePaths:
    dispatch_intents: Path
    fodl_audit: Path
    fodl_current: Path


def _read_regular(path: Path, surface: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise DispatchApprovalMigrationError(f"{surface} is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise DispatchApprovalMigrationError(f"{surface} must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def read_stable_surfaces(
    paths: LegacySurfacePaths, *, attempts: int = 5,
) -> tuple[bytes, bytes, bytes]:
    """Return one cross-file cut whose forward and reverse reads agree."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for _ in range(attempts):
        forward = (
            _read_regular(paths.dispatch_intents, "dispatch intents"),
            _read_regular(paths.fodl_audit, "FODL audit"),
            _read_regular(paths.fodl_current, "FODL current markdown"),
        )
        reverse = (
            _read_regular(paths.fodl_current, "FODL current markdown"),
            _read_regular(paths.fodl_audit, "FODL audit"),
            _read_regular(paths.dispatch_intents, "dispatch intents"),
        )
        if forward == tuple(reversed(reverse)):
            return forward
    raise DispatchApprovalMigrationError(
        "M5 legacy surfaces did not stabilize across an anchored read"
    )


def sync_authoritative_files(
    conninfo: dict, *, tenant_id: str, paths: LegacySurfacePaths,
    cycle_key: str = "",
) -> SnapshotResult:
    if render_kernel_surfaces(conninfo, tenant_id=tenant_id) != replay_legacy_from_events(
        conninfo, tenant_id=tenant_id,
    ):
        raise DispatchApprovalMigrationError(
            "M5 event history does not reproduce the current kernel projection"
        )
    dispatch, audit, current = read_stable_surfaces(paths)
    digest = hashlib.sha256(dispatch + b"\0" + audit + b"\0" + current).hexdigest()
    key = cycle_key or (
        datetime.now(timezone.utc).isoformat(timespec="microseconds") + ":" + digest[:12]
    )
    result = sync_legacy_surfaces(
        conninfo, tenant_id=tenant_id, cycle_key=key, dispatch_data=dispatch,
        fodl_audit_data=audit, fodl_current_data=current,
    )
    if render_kernel_surfaces(conninfo, tenant_id=tenant_id) != replay_legacy_from_events(
        conninfo, tenant_id=tenant_id,
    ):
        raise DispatchApprovalMigrationError(
            "M5 event history failed post-sync projection verification"
        )
    return result
