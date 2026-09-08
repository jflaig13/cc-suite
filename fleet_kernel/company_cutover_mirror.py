# SPDX-License-Identifier: MPL-2.0
"""Atomic local projection of the database cutover registry for legacy relays.

This is fallback state, not admission or effect authority. The bus is always an
explicit adopter-owned directory; installed source is never a default target.
"""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile


class CutoverMirrorError(RuntimeError):
    pass


def cutover_mirror_path() -> Path:
    configured = os.environ.get("CC_SUITE_CHANNEL_SHARED_DIR", "")
    directory = Path(configured)
    if (
        not configured or not directory.is_absolute() or ".." in directory.parts
        or not directory.is_dir() or directory.resolve() != directory
    ):
        raise CutoverMirrorError(
            "CC_SUITE_CHANNEL_SHARED_DIR must name an existing absolute directory without symlinks"
        )
    return directory / "kernel_cutover.json"


def read_cutover_mirror(path: Path | None = None) -> dict[str, str]:
    """Read a whole atomic projection, defaulting inactive when unavailable."""
    try:
        path = path if path is not None else cutover_mirror_path()
        if path.is_symlink():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
            return raw
    except (OSError, ValueError, CutoverMirrorError):
        pass
    return {}


def _write_locked(path: Path, role: str, state: str) -> None:
    data = read_cutover_mirror(path)
    data[role] = state
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".kernel-cutover-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(json.dumps(data, sort_keys=True, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked_cutover_mirror():
    """Hold one local lock across the canonical DB operation and projection.

    The yielded writer must only be called within this context. Taking the lock
    before reading/changing the DB prevents a delayed snapshot from replacing a
    later rollback. All local database/relay writers share this lock.
    """
    path = cutover_mirror_path()
    # Every cooperating database/relay writer uses the same stable lock inode.
    lock_path = path.with_suffix(".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield lambda role, state: _write_locked(path, role, state)


def write_cutover_mirror(role: str, state: str) -> None:
    """Serialize a direct local projection update."""
    with locked_cutover_mirror() as write:
        write(role, state)
