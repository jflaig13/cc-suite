# SPDX-License-Identifier: MPL-2.0
"""One locked authoritative-file-to-kernel M4 synchronization cycle."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fleet_kernel.m4.handoff_migration import (
    SnapshotResult,
    read_legacy_snapshot,
    sync_legacy_snapshot,
)
from fleet_kernel.m4.locking import handoff_advisory_lock


def sync_authoritative_file(
    conninfo: dict, *, tenant_id: str, legacy_path: Path, cycle_key: str = "",
) -> SnapshotResult:
    """Mirror one exact legacy snapshot while excluding kernel readers/syncs."""
    with handoff_advisory_lock(conninfo, tenant_id=tenant_id, shared=False):
        data, _rows = read_legacy_snapshot(legacy_path)
        key = cycle_key or (
            datetime.now(timezone.utc).isoformat(timespec="microseconds")
            + ":" + hashlib.sha256(data).hexdigest()[:12]
        )
        return sync_legacy_snapshot(
            conninfo, tenant_id=tenant_id, cycle_key=key, data=data,
        )
