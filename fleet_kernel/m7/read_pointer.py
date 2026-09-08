# SPDX-License-Identifier: MPL-2.0
"""Private, atomic M7 role-queue read-authority pointer."""
from __future__ import annotations

import fcntl
import json
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from fleet_kernel.m7.errors import RoleQueueMigrationError


POINTER_FIELDS = frozenset({"schema_version", "target", "source_commit"})


@dataclass(frozen=True, slots=True)
class M7ReadPointer:
    target: str
    source_commit: str


def pointer_record(*, target: str, source_commit: str) -> dict[str, object]:
    if target not in {"legacy", "kernel"}:
        raise RoleQueueMigrationError("M7 read pointer target is invalid")
    if (
        not isinstance(source_commit, str) or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
    ):
        raise RoleQueueMigrationError("M7 read pointer source commit is invalid")
    return {"schema_version": 1, "target": target, "source_commit": source_commit}


def encode_pointer(record: dict[str, object]) -> bytes:
    if set(record) != POINTER_FIELDS or record.get("schema_version") != 1:
        raise RoleQueueMigrationError("M7 read pointer schema is invalid")
    pointer_record(
        target=str(record.get("target", "")),
        source_commit=str(record.get("source_commit", "")),
    )
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def load_pointer(path: Path) -> M7ReadPointer:
    """A missing pointer is the database-independent legacy default."""
    if not path.exists() and not path.is_symlink():
        return M7ReadPointer("legacy", "")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RoleQueueMigrationError("M7 read pointer is unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RoleQueueMigrationError(
                "M7 read pointer must be a user-owned mode 0600 regular file"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read()
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoleQueueMigrationError("M7 read pointer is invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != POINTER_FIELDS:
        raise RoleQueueMigrationError("M7 read pointer schema is invalid")
    pointer_record(target=value.get("target"), source_commit=value.get("source_commit"))
    if value.get("schema_version") != 1:
        raise RoleQueueMigrationError("M7 read pointer schema is invalid")
    return M7ReadPointer(str(value["target"]), str(value["source_commit"]))


@contextmanager
def pointer_read_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RoleQueueMigrationError("M7 read-pointer lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
