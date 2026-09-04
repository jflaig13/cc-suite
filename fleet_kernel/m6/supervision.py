"""Stable, read-only capture for the M6 spend shadow."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from fleet_kernel.m6.spend_migration import SpendMigrationError, SpendSnapshot, sync_spend_bytes


def _read_regular(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise SpendMigrationError("spend ledger is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SpendMigrationError("spend ledger must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def read_stable_spend(path: Path, *, attempts: int = 5) -> bytes:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for _ in range(attempts):
        first = _read_regular(path)
        if first == _read_regular(path):
            return first
    raise SpendMigrationError("spend ledger did not stabilize across an anchored read")


def sync_authoritative_file(
    conninfo: dict, *, tenant_id: str, path: Path, cycle_key: str = "",
) -> SpendSnapshot:
    data = read_stable_spend(path)
    digest = hashlib.sha256(data).hexdigest()
    key = cycle_key or f"legacy-sha256:{digest}"
    return sync_spend_bytes(
        conninfo, tenant_id=tenant_id, cycle_key=key, data=data,
    )
