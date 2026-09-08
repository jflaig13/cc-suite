# SPDX-License-Identifier: MPL-2.0
"""Private, atomic M5 read-authority pointer."""
from __future__ import annotations

import json
import fcntl
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from fleet_kernel.m5.dispatch_approval_migration import DispatchApprovalMigrationError


POINTER_FIELDS = frozenset({"schema_version", "target", "source_commit"})


@dataclass(frozen=True, slots=True)
class M5ReadPointer:
    target: str
    source_commit: str


@contextmanager
def pointer_read_lock(path: Path) -> Iterator[None]:
    """Hold the shared side of the cutover lock for one authority-bound read."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DispatchApprovalMigrationError("M5 read-pointer lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def pointer_record(*, target: str, source_commit: str) -> dict[str, object]:
    if target not in {"legacy", "kernel"}:
        raise DispatchApprovalMigrationError("M5 read pointer target is invalid")
    if (
        not isinstance(source_commit, str) or len(source_commit) != 40
        or any(c not in "0123456789abcdef" for c in source_commit)
    ):
        raise DispatchApprovalMigrationError("M5 read pointer source commit is invalid")
    return {"schema_version": 1, "target": target, "source_commit": source_commit}


def encode_pointer(record: dict[str, object]) -> bytes:
    pointer_record(target=str(record.get("target", "")), source_commit=str(record.get("source_commit", "")))
    if set(record) != POINTER_FIELDS or record.get("schema_version") != 1:
        raise DispatchApprovalMigrationError("M5 read pointer schema is invalid")
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load_pointer(path: Path) -> M5ReadPointer:
    """A missing pointer is the safe pre-cutover legacy default."""
    if not path.exists() and not path.is_symlink():
        return M5ReadPointer("legacy", "")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DispatchApprovalMigrationError("M5 read pointer is unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise DispatchApprovalMigrationError(
                "M5 read pointer must be a user-owned mode 0600 regular file"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DispatchApprovalMigrationError("M5 read pointer is invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != POINTER_FIELDS:
        raise DispatchApprovalMigrationError("M5 read pointer schema is invalid")
    pointer_record(target=value.get("target"), source_commit=value.get("source_commit"))
    if value.get("schema_version") != 1:
        raise DispatchApprovalMigrationError("M5 read pointer schema is invalid")
    return M5ReadPointer(str(value["target"]), str(value["source_commit"]))
