"""Dependency-free, stable reads of legacy M7 queue and role-cursor files."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from fleet_kernel.m7.errors import RoleQueueMigrationError


_QUEUE = re.compile(r"queue_([a-z][a-z0-9-]*)\.jsonl\Z")


@dataclass(frozen=True, slots=True)
class StableRoleQueue:
    role: str
    data: bytes
    cursor_line: int


def _read_regular(path: Path, *, missing: bytes | None = None) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if missing is not None:
            return missing
        raise RoleQueueMigrationError(f"required M7 surface is missing: {path.name}")
    except OSError as exc:
        raise RoleQueueMigrationError(f"M7 surface is unsafe: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RoleQueueMigrationError(f"M7 surface is not a regular file: {path.name}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def discover_role_queues(shared_dir: Path) -> tuple[tuple[str, Path], ...]:
    if shared_dir.is_symlink() or not shared_dir.is_dir():
        raise RoleQueueMigrationError("M7 shared queue directory is missing or unsafe")
    found = []
    for path in shared_dir.iterdir():
        match = _QUEUE.fullmatch(path.name)
        if match is not None:
            if path.is_symlink() or not path.is_file():
                raise RoleQueueMigrationError(f"M7 queue path is unsafe: {path.name}")
            found.append((match.group(1), path))
    return tuple(sorted(found))


def _cursor(raw: bytes, role: str) -> int:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoleQueueMigrationError(f"M7 role cursor is invalid JSON: {role}") from exc
    if not isinstance(value, dict) or set(value) != {"line"} or isinstance(
        value.get("line"), bool
    ) or not isinstance(value.get("line"), int) or value["line"] < 0:
        raise RoleQueueMigrationError(f"M7 role cursor schema is invalid: {role}")
    return value["line"]


def read_stable_role_queue(
    shared_dir: Path, *, role: str, queue_path: Path, attempts: int = 5,
) -> StableRoleQueue:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    cursor_path = shared_dir / f"cursor_{role}_role.json"
    last_error: RoleQueueMigrationError | None = None
    for _ in range(attempts):
        queue_first = _read_regular(queue_path)
        cursor_first = _read_regular(cursor_path, missing=b'{"line":0}')
        queue_second = _read_regular(queue_path)
        cursor_second = _read_regular(cursor_path, missing=b'{"line":0}')
        if queue_first != queue_second or cursor_first != cursor_second:
            continue
        try:
            return StableRoleQueue(role, queue_first, _cursor(cursor_first, role))
        except RoleQueueMigrationError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RoleQueueMigrationError(f"M7 queue/cursor pair did not stabilize: {role}")
