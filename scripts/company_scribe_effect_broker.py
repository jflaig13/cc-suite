#!/usr/bin/env python3
"""Exact-parent MCP boundary for every writable Company Scribe effect.

The admitted Claude process receives only this server's Unix-socket path.  The
forked daemon retains the admission binding and database route.  Each effect is
re-authorized against the local epoch and the database immediately before it
is committed.  Commands additionally run under a fixed lease deadline and a
separate fail-closed watchdog, so a blocked database probe cannot extend their
write window.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import fcntl
import hashlib
import hmac
import json
import math
import os
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterator, Mapping, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402
from fleet_kernel.configuration import identity_environment

from fleet_kernel.company_scribe_admission import (  # noqa: E402
    CompanyScribeAdmissionError,
    ScribeAdmissionBinding,
    bind_transaction,
    load_binding,
    load_current_lease_pointer,
    executable_sha256,
    process_executable_path,
    process_start_fingerprint,
    validate_database_route,
    verify_local_binding,
)


_MAX_TEXT_BYTES = 8 * 1024 * 1024
_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_REQUEST_BYTES = 9 * 1024 * 1024
_MAX_MCP_LINE_BYTES = 12 * 1024 * 1024
_MAX_JOURNAL_BYTES = 32 * 1024 * 1024
_MAX_COMMAND_ARGV_ITEMS = 256
_COMMAND_GUARDIAN_MODE = "--command-guardian-v2"
_MAX_COMMAND_SECONDS = 300.0
_AUTHORITY_POLL_SECONDS = 0.250
_AUTHORITY_SILENCE_SECONDS = 0.750
_FORBIDDEN_AGENT_NAMES = frozenset({"claude", "claude.exe", "codex"})
_JOURNAL_SCHEMA = "mise.company-scribe-effect-journal.v1"
BROKER_HEALTH_SCHEMA = "mise.company-scribe-broker-health.v1"
_MAX_EFFECT_ID = 200
_MAX_PUBLISHED_COMMAND_CHANGES = 1
_VALID_CHANNEL_ROLES = frozenset({
    "ccto", "ccpo", "ccro", "ccfo", "ccmo", "cclo", "ccgo", "ccco",
    "ccde", "cos", "scribe", "utility",
})
_VALID_CHANNEL_EVENT_TYPES = frozenset({
    "agent_direct", "task_handoff", "audit_request", "reply_classification",
})
_CHANNEL_SEND_META_RESERVED = frozenset({
    "sender_role", "target_role", "sent_at", "send_id",
})
_WORKSPACE_MUTATION_LOCK = threading.RLock()


class _Authority(Protocol):
    def check(self) -> ScribeAdmissionBinding: ...


class _CommandCleanupUncertain(CompanyScribeAdmissionError):
    """A command identity could not be proven absent; keep its journal open."""


class _GuardianFenced(CompanyScribeAdmissionError):
    """The guardian emitted one valid fail-closed terminal frame."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"command guardian fenced execution: {reason}")


def _parent_pid(pid: int) -> int:
    if sys.platform == "darwin":
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "ppid="],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            if result.returncode:
                raise ValueError
            return int(result.stdout.strip())
        except ValueError as exc:
            raise CompanyScribeAdmissionError(
                "effect proxy parent identity is unavailable"
            ) from exc
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        fields = stat_path.read_text(encoding="ascii").split()
        return int(fields[3])
    except (OSError, ValueError, IndexError) as exc:
        raise CompanyScribeAdmissionError(
            "effect proxy parent identity is unavailable"
        ) from exc


def _peer_pid(connection: socket.socket) -> int:
    if sys.platform == "darwin":
        raw = connection.getsockopt(0, 0x002, 4)  # SOL_LOCAL/LOCAL_PEERPID
        return int.from_bytes(raw, sys.byteorder, signed=True)
    if sys.platform.startswith("linux"):
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return int.from_bytes(raw[:4], sys.byteorder, signed=True)
    raise CompanyScribeAdmissionError(
        "effect proxy peer PID is unsupported on this host"
    )


def _peer_is_exact_parent(peer_pid: int, admitted_pid: int) -> bool:
    """True only for a proxy directly exec'd by the admitted Claude PID."""
    return _parent_pid(peer_pid) == admitted_pid


def _process_arguments(pid: int) -> tuple[str, ...]:
    """Read the kernel-owned argv for the peer; never trust a claimed script."""
    if sys.platform == "darwin":
        # CTL_KERN/KERN_PROCARGS2 returns argc, executable, then the original
        # NUL-delimited argv. This binds an interpreter peer to the reviewed
        # immutable proxy script rather than accepting any Python child.
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, pid)
        size = ctypes.c_size_t()
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
            raise CompanyScribeAdmissionError("peer process argv is unavailable")
        if size.value < ctypes.sizeof(ctypes.c_int) or size.value > 1024 * 1024:
            raise CompanyScribeAdmissionError("peer process argv has an unsafe size")
        buffer = ctypes.create_string_buffer(size.value)
        if libc.sysctl(
            mib, 3, buffer, ctypes.byref(size), None, 0,
        ) != 0:
            raise CompanyScribeAdmissionError("peer process argv is unavailable")
        data = bytes(buffer.raw[:size.value])
        argc = int.from_bytes(
            data[:ctypes.sizeof(ctypes.c_int)], sys.byteorder, signed=True,
        )
        if argc <= 0 or argc > 4096:
            raise CompanyScribeAdmissionError("peer process argc is invalid")
        offset = ctypes.sizeof(ctypes.c_int)
        executable_end = data.find(b"\0", offset)
        if executable_end < 0:
            raise CompanyScribeAdmissionError("peer executable argv is malformed")
        offset = executable_end + 1
        while offset < len(data) and data[offset] == 0:
            offset += 1
        values: list[str] = []
        for _ in range(argc):
            end = data.find(b"\0", offset)
            if end < 0:
                raise CompanyScribeAdmissionError("peer argv is truncated")
            values.append(data[offset:end].decode("utf-8", errors="surrogateescape"))
            offset = end + 1
        return tuple(values)
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError as exc:
            raise CompanyScribeAdmissionError("peer process argv is unavailable") from exc
        values = tuple(
            item.decode("utf-8", errors="surrogateescape")
            for item in raw.rstrip(b"\0").split(b"\0")
            if item
        )
        if not values:
            raise CompanyScribeAdmissionError("peer process argv is empty")
        return values
    raise CompanyScribeAdmissionError("peer process argv is unsupported")


@dataclass(frozen=True)
class PeerIdentity:
    capability: str
    pid: int
    parent_pid: int
    process_start_fingerprint_sha256: str
    executable_path: str
    executable_sha256: str
    argv_sha256: str


class PeerGate:
    """Pin one reviewed proxy process to one non-transferable capability."""

    def __init__(
        self,
        *,
        capability: str,
        admitted_pid: int,
        admitted_start_fingerprint: str,
        executable_path: Path,
        executable_digest: str,
        expected_argv: tuple[str, ...],
    ) -> None:
        self.capability = capability
        self.admitted_pid = admitted_pid
        self.admitted_start_fingerprint = admitted_start_fingerprint
        self.executable_path = executable_path.resolve(strict=True)
        self.executable_digest = executable_digest
        self.expected_argv = expected_argv
        if not expected_argv:
            raise CompanyScribeAdmissionError(
                f"{capability} expected argv is empty"
            )
        if executable_sha256(self.executable_path) != executable_digest:
            raise CompanyScribeAdmissionError(
                f"{capability} proxy executable digest changed before bind"
            )
        self._pinned: PeerIdentity | None = None
        self._lock = threading.Lock()

    def _capture(self, peer_pid: int) -> PeerIdentity:
        if (
            process_start_fingerprint(self.admitted_pid)
            != self.admitted_start_fingerprint
        ):
            raise CompanyScribeAdmissionError(
                "admitted Company Scribe process epoch ended"
            )
        parent = _parent_pid(peer_pid)
        if parent != self.admitted_pid:
            raise CompanyScribeAdmissionError(
                f"{self.capability} peer is not the admitted direct child"
            )
        start = process_start_fingerprint(peer_pid)
        executable = process_executable_path(peer_pid)
        digest = executable_sha256(executable)
        if executable != self.executable_path or digest != self.executable_digest:
            raise CompanyScribeAdmissionError(
                f"{self.capability} peer executable is not the reviewed image"
            )
        arguments = _process_arguments(peer_pid)
        if arguments != self.expected_argv:
            raise CompanyScribeAdmissionError(
                f"{self.capability} peer did not launch the reviewed immutable program"
            )
        return PeerIdentity(
            capability=self.capability,
            pid=peer_pid,
            parent_pid=parent,
            process_start_fingerprint_sha256=start,
            executable_path=str(executable),
            executable_sha256=digest,
            argv_sha256=hashlib.sha256(_canonical_bytes(list(arguments))).hexdigest(),
        )

    def _same_reviewed_program(self, left: PeerIdentity, right: PeerIdentity) -> bool:
        return (
            left.capability == right.capability
            and left.parent_pid == right.parent_pid
            and left.executable_path == right.executable_path
            and left.executable_sha256 == right.executable_sha256
            and left.argv_sha256 == right.argv_sha256
        )

    def authenticate(self, connection: socket.socket) -> PeerIdentity:
        observed = self._capture(_peer_pid(connection))
        with self._lock:
            if self._pinned is None:
                self._pinned = observed
            elif self._pinned != observed:
                # Claude's stdio MCP client may close and respawn the proxy
                # after initialize. Pin the reviewed program (parent + image
                # + argv), not one OS pid.
                if not self._same_reviewed_program(self._pinned, observed):
                    raise CompanyScribeAdmissionError(
                        f"{self.capability} capability is already pinned to another proxy"
                    )
                self._pinned = observed
            return self._pinned

    def reverify(self, identity: PeerIdentity) -> None:
        with self._lock:
            if self._pinned != identity:
                raise CompanyScribeAdmissionError(
                    f"{self.capability} connection identity was not pinned"
                )
        observed = self._capture(identity.pid)
        if observed != identity:
            raise CompanyScribeAdmissionError(
                f"{self.capability} proxy PID, start, parent, or executable drifted"
            )


def _stable_binding(binding: ScribeAdmissionBinding) -> dict[str, object]:
    value = binding.to_mapping()
    value.pop("lease_expires_at")
    return value


def _file_signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _hash_open_file(descriptor: int) -> tuple[str, tuple[int, ...]]:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or stat.S_IMODE(before.st_mode) & 0o022
    ):
        raise CompanyScribeAdmissionError(
            "admitted Company Scribe executable is unsafe"
        )
    digest = hashlib.sha256()
    offset = 0
    while offset < before.st_size:
        chunk = os.pread(
            descriptor,
            min(1024 * 1024, before.st_size - offset),
            offset,
        )
        if not chunk:
            raise CompanyScribeAdmissionError(
                "admitted Company Scribe executable read was incomplete"
            )
        digest.update(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    if _file_signature(before) != _file_signature(after):
        raise CompanyScribeAdmissionError(
            "admitted Company Scribe executable changed during hashing"
        )
    return digest.hexdigest(), _file_signature(after)


class EffectAuthority:
    """Private, non-exportable authority retained only by the daemon."""

    def __init__(
        self,
        *,
        admitted_pid: int,
        admitted_start_fingerprint: str,
        binding_path: Path,
        release_package: Path,
        database_url: str,
    ) -> None:
        self.admitted_pid = admitted_pid
        self.admitted_start_fingerprint = admitted_start_fingerprint
        self.binding_path = binding_path
        self.release_package = release_package
        self.database_url = validate_database_route(database_url)
        initial = load_binding(binding_path)
        verify_local_binding(
            initial,
            release_package=release_package,
            expected_pid=admitted_pid,
        )
        if (
            initial.process_start_fingerprint_sha256
            != admitted_start_fingerprint
        ):
            raise CompanyScribeAdmissionError(
                "effect authority belongs to another process epoch"
            )
        self._stable = _stable_binding(initial)
        self._admitted_executable = Path(
            initial.runtime_executable_path
        ).resolve(strict=True)
        self._admitted_executable_sha256 = initial.runtime_executable_sha256
        self._executable_fd = os.open(
            self._admitted_executable,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        observed_sha, self._executable_signature = _hash_open_file(
            self._executable_fd,
        )
        if observed_sha != self._admitted_executable_sha256:
            os.close(self._executable_fd)
            self._executable_fd = -1
            raise CompanyScribeAdmissionError(
                "admitted Company Scribe executable digest changed"
            )
        self._database_lock = threading.Lock()
        self._connection: psycopg.Connection | None = None
        # The daemon is started immediately before the admitted launcher
        # execs Claude. At this point the PID/fingerprint, binding, package,
        # lease, and DB admission must already be exact, but the kernel image
        # is still packaged Python for this bounded pre-exec window. Every
        # actual effect calls check() after exec and requires the Claude image.
        self.check(allow_preexec_image=True)

    def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except psycopg.Error:
                pass

    def close(self) -> None:
        with self._database_lock:
            self._close_connection()
        if self._executable_fd >= 0:
            os.close(self._executable_fd)
            self._executable_fd = -1

    def check(
        self, *, allow_preexec_image: bool = False,
    ) -> ScribeAdmissionBinding:
        """Rebind one short transaction; any uncertainty fails closed."""
        if (
            process_start_fingerprint(self.admitted_pid)
            != self.admitted_start_fingerprint
        ):
            raise CompanyScribeAdmissionError(
                "admitted Company Scribe process epoch ended"
            )
        current_executable = process_executable_path(self.admitted_pid)
        try:
            path_signature = _file_signature(
                self._admitted_executable.lstat()
            )
            descriptor_signature = _file_signature(
                os.fstat(self._executable_fd)
            )
        except OSError as exc:
            raise CompanyScribeAdmissionError(
                "admitted Company Scribe executable image is unavailable"
            ) from exc
        if (
            path_signature != self._executable_signature
            or descriptor_signature != self._executable_signature
            or (
                not allow_preexec_image
                and current_executable != self._admitted_executable
            )
        ):
            raise CompanyScribeAdmissionError(
                "admitted Company Scribe executable image changed"
            )
        current = load_binding(self.binding_path)
        if _stable_binding(current) != self._stable:
            raise CompanyScribeAdmissionError(
                "Company Scribe admission binding changed epochs"
            )
        current_deadline = load_current_lease_pointer(
            self.binding_path.parent / "current-lease.json",
            binding=current,
        )
        if current_deadline <= datetime.now(timezone.utc):
            raise CompanyScribeAdmissionError(
                "Company Scribe effect lease expired"
            )
        with self._database_lock:
            try:
                conn = self._connection
                if conn is None or conn.closed:
                    conn = psycopg.connect(
                        self.database_url,
                        autocommit=True,
                        connect_timeout=1,
                        application_name="company-scribe-effect-fence",
                    )
                    self._connection = conn
                with conn.transaction():
                    conn.execute("SET LOCAL statement_timeout='250ms'")
                    bind_transaction(conn, current)
            except psycopg.Error as exc:
                self._close_connection()
                raise CompanyScribeAdmissionError(
                    "Company Scribe effect admission is revoked or unreachable"
                ) from exc
        return replace(current, lease_expires_at=current_deadline)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _effect_request_sha256(
    capability: str,
    operation: str,
    arguments: Mapping[str, object],
) -> str:
    request_arguments = dict(arguments)
    request_arguments.pop("effect_id", None)
    return hashlib.sha256(_canonical_bytes({
        "capability": capability,
        "operation": operation,
        "arguments": request_arguments,
    })).hexdigest()


def _validate_effect_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_EFFECT_ID
        or not value[0].isalnum()
        or any(not (char.isalnum() or char in "._:-") for char in value)
    ):
        raise CompanyScribeAdmissionError(
            "every mutation requires one stable effect_id"
        )
    return value


def _closed_request_arguments(
    arguments: Mapping[str, object],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    operation: str,
) -> dict[str, object]:
    keys = frozenset(arguments)
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise CompanyScribeAdmissionError(
            f"{operation} contains unknown or missing arguments"
        )
    try:
        encoded = _canonical_bytes({
            key: value for key, value in arguments.items()
            if key != "effect_id"
        })
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise CompanyScribeAdmissionError(
                f"{operation} arguments exceed the closed request bound"
            )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            f"{operation} arguments are not closed JSON"
        ) from exc
    if not isinstance(decoded, dict):
        raise CompanyScribeAdmissionError(
            f"{operation} arguments are malformed"
        )
    return decoded


def _atomic_private_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent,
    )
    try:
        os.fchmod(descriptor, 0o600)
        payload = _canonical_bytes(value) + b"\n"
        if len(payload) > _MAX_JOURNAL_BYTES:
            raise CompanyScribeAdmissionError(
                "effect replay journal record exceeds its readable bound"
            )
        remaining = payload
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("effect journal write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        temporary = ""
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_private_state_directory(parent: Path, name: str) -> Path:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or os.sep in name
    ):
        raise CompanyScribeAdmissionError(
            "private state directory name is malformed"
        )
    parent = parent.resolve(strict=True)
    parent_fd = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        child_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            info = os.fstat(child_fd)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise CompanyScribeAdmissionError(
                    "private state directory is linked or unsafe"
                )
        finally:
            os.close(child_fd)
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "private state directory is linked or unsafe"
        ) from exc
    finally:
        os.close(parent_fd)
    child = parent / name
    if child.is_symlink() or child.resolve(strict=True).parent != parent:
        raise CompanyScribeAdmissionError(
            "private state directory escaped its parent"
        )
    return child


def _directory_identity(path: Path) -> tuple[int, int]:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise CompanyScribeAdmissionError(
                "private state directory changed identity"
            )
        return info.st_dev, info.st_ino
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "private state directory changed identity"
        ) from exc
    finally:
        os.close(descriptor)


def _require_directory_identity(
    path: Path,
    expected: tuple[int, int],
) -> None:
    if path.is_symlink() or _directory_identity(path) != expected:
        raise CompanyScribeAdmissionError(
            "private state directory changed identity"
        )


class MutationJournal:
    """Durable effect-id/request-hash replay authority for every mutation."""

    def __init__(self, state_dir: Path) -> None:
        self.root = _ensure_private_state_directory(
            state_dir, "effect-journal",
        )
        root_info = self.root.lstat()
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or self.root.is_symlink()
            or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) & 0o077
        ):
            raise CompanyScribeAdmissionError(
                "effect replay journal directory is unsafe"
            )
        os.chmod(self.root, 0o700)

    def _stem(self, effect_id: str) -> str:
        return hashlib.sha256(effect_id.encode("utf-8")).hexdigest()

    def record_path(self, effect_id: str) -> Path:
        return self.root / f"{self._stem(_validate_effect_id(effect_id))}.json"

    def _read_record(self, path: Path) -> dict[str, object]:
        descriptor = -1
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) & 0o077
                or before.st_size > _MAX_JOURNAL_BYTES
            ):
                raise CompanyScribeAdmissionError(
                    "effect replay journal record is unsafe"
                )
            chunks: list[bytes] = []
            remaining = before.st_size + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if (
                len(b"".join(chunks)) != before.st_size
                or (
                    before.st_dev, before.st_ino, before.st_mode,
                    before.st_nlink, before.st_size, before.st_mtime_ns,
                    before.st_ctime_ns,
                ) != (
                    after.st_dev, after.st_ino, after.st_mode,
                    after.st_nlink, after.st_size, after.st_mtime_ns,
                    after.st_ctime_ns,
                )
            ):
                raise CompanyScribeAdmissionError(
                    "effect replay journal changed during read"
                )
            decoded = json.loads(b"".join(chunks))
        except FileNotFoundError:
            raise
        except (
            OSError, UnicodeDecodeError, json.JSONDecodeError,
        ) as exc:
            raise CompanyScribeAdmissionError(
                "effect replay journal is unreadable"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(decoded, dict):
            raise CompanyScribeAdmissionError(
                "effect replay journal is malformed"
            )
        return decoded

    def records(self) -> tuple[dict[str, object], ...]:
        records: list[dict[str, object]] = []
        for path in sorted(self.root.iterdir()):
            name = path.name
            if name.startswith(".") or name.endswith(".lock"):
                continue
            if not name.endswith(".json"):
                raise CompanyScribeAdmissionError(
                    "effect replay journal contains an unknown record"
                )
            stem = name[:-5]
            if len(stem) != 64 or any(
                char not in "0123456789abcdef" for char in stem
            ):
                raise CompanyScribeAdmissionError(
                    "effect replay journal record name is malformed"
                )
            record = self._read_record(path)
            effect_id = record.get("effect_id")
            request_sha256 = record.get("request_sha256")
            operation = record.get("operation")
            state = record.get("state")
            if (
                record.get("schema") != _JOURNAL_SCHEMA
                or not isinstance(effect_id, str)
                or self._stem(_validate_effect_id(effect_id)) != stem
                or not isinstance(request_sha256, str)
                or len(request_sha256) != 64
                or any(
                    char not in "0123456789abcdef"
                    for char in request_sha256
                )
                or not isinstance(operation, str)
                or state not in {
                    "prepared", "running", "publishing",
                    "completed", "failed",
                }
            ):
                raise CompanyScribeAdmissionError(
                    "effect replay journal record identity is malformed"
                )
            records.append(record)
        return tuple(records)

    def pending_records(self) -> tuple[dict[str, object], ...]:
        return tuple(
            record for record in self.records()
            if record.get("state") == "prepared"
        )

    @contextmanager
    def locked(
        self,
        *,
        effect_id: str,
        request_sha256: str,
        operation: str,
    ) -> Iterator[tuple[dict[str, object] | None, Callable[[dict[str, object]], None]]]:
        effect_id = _validate_effect_id(effect_id)
        stem = self._stem(effect_id)
        lock_path = self.root / f"{stem}.lock"
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            path = self.root / f"{stem}.json"
            record: dict[str, object] | None
            try:
                decoded = self._read_record(path)
            except FileNotFoundError:
                record = None
            else:
                record = decoded
                if (
                    record.get("schema") != _JOURNAL_SCHEMA
                    or record.get("effect_id") != effect_id
                    or record.get("request_sha256") != request_sha256
                    or record.get("operation") != operation
                ):
                    raise CompanyScribeAdmissionError(
                        "effect_id was already used for a different request"
                    )

            def write(next_record: dict[str, object]) -> None:
                closed = {
                    "schema": _JOURNAL_SCHEMA,
                    "effect_id": effect_id,
                    "request_sha256": request_sha256,
                    "operation": operation,
                    **next_record,
                }
                _atomic_private_json(path, closed)

            yield record, write
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _replayed_result(record: Mapping[str, object]) -> dict[str, object] | None:
    state = record.get("state")
    if state == "completed":
        result = record.get("result")
        if not isinstance(result, dict):
            raise CompanyScribeAdmissionError(
                "completed effect journal lost its result"
            )
        return dict(result)
    if state == "failed":
        raise CompanyScribeAdmissionError(
            f"effect replay remains failed closed: {record.get('error', 'unknown')}"
        )
    return None


def _protected_channel_runtime_state(candidate: PurePosixPath) -> bool:
    parts = tuple(part.casefold() for part in candidate.parts)
    if (
        len(parts) < 3
        or parts[0] != "channels"
        or parts[1] not in {"shared", "scribe", "agent"}
    ):
        return False
    name = parts[-1]
    return (
        name in {
            "event_queue.jsonl",
            "event_queue.processing.jsonl",
            "audit_log.jsonl",
            "delivery_journal.jsonl",
            "poller.heartbeat",
            "relaunch_hook.log",
            "slack_post_registry.jsonl",
            "shadow_suppress_log.jsonl",
            "nudge_watcher_state.json",
        }
        or name.startswith("queue_")
        or name.startswith("audit_")
        or name.startswith("cursor_")
        or name.startswith("poller_cursor_")
        or name.startswith("ready_")
        or name.startswith("init_pending_")
        or name.startswith("transcript_")
        or name.startswith("handoff_open_requests.jsonl")
        or name.endswith("_state.json")
    )


def _relative_path(value: object, *, field: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise CompanyScribeAdmissionError(f"{field} must be a relative path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or candidate == PurePosixPath("."):
        raise CompanyScribeAdmissionError(f"{field} escapes the Company workspace")
    if any(part.casefold() == ".git" for part in candidate.parts):
        raise CompanyScribeAdmissionError(
            f"{field} targets the protected Git control plane"
        )
    if _protected_channel_runtime_state(candidate):
        raise CompanyScribeAdmissionError(
            f"{field} targets protected channel runtime state"
        )
    return candidate


def _workspace_path(
    workspace: Path,
    value: object,
    *,
    field: str = "path",
    must_exist: bool = False,
) -> Path:
    relative = _relative_path(value, field=field)
    root = workspace.resolve(strict=True)
    candidate = root.joinpath(*relative.parts)
    resolved = candidate.resolve(strict=must_exist)
    if not resolved.is_relative_to(root):
        raise CompanyScribeAdmissionError(f"{field} escapes the Company workspace")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise CompanyScribeAdmissionError(
                f"{field} contains a non-directory or symlink ancestor"
            )
    return candidate


def _read_regular_beneath(
    root: Path,
    relative: PurePosixPath,
    *,
    maximum: int,
) -> tuple[bytes, int]:
    """Read one regular file without following any path component.

    Sandboxed command output is hostile input.  Resolving a path and then
    calling ``read_bytes`` would let a command replace an ancestor with a
    symlink after the sandbox exits.  Walk from a pinned directory descriptor
    instead, keeping every component open until the final inode is read.
    """
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise CompanyScribeAdmissionError(
            "command publication path is not an exact relative file"
        )
    descriptors: list[int] = []
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    try:
        current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
        descriptors.append(current)
        root_info = os.fstat(current)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) & 0o022
        ):
            raise CompanyScribeAdmissionError(
                "quarantined command workspace root is unsafe"
            )
        for component in relative.parts[:-1]:
            current = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | nofollow | nonblock,
                dir_fd=current,
            )
            descriptors.append(current)
            info = os.fstat(current)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise CompanyScribeAdmissionError(
                    "command publication contains an unsafe directory"
                )
        descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | nofollow | nonblock,
            dir_fd=current,
        )
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        signature = (
            before.st_dev, before.st_ino, before.st_uid, before.st_gid,
            before.st_mode, before.st_nlink, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or mode & 0o7022
            or before.st_size > maximum
        ):
            raise CompanyScribeAdmissionError(
                "command publish target must be one safe regular file"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(data) > maximum or signature != (
            after.st_dev, after.st_ino, after.st_uid, after.st_gid,
            after.st_mode, after.st_nlink, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        ):
            raise CompanyScribeAdmissionError(
                "command publish target changed during sealed read"
            )
        return data, mode
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "command publish target is absent, linked, or unsafe"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _path_state_beneath(
    root: Path,
    relative: PurePosixPath,
) -> dict[str, object]:
    try:
        data, mode = _read_regular_beneath(
            root, relative, maximum=_MAX_TEXT_BYTES,
        )
    except CompanyScribeAdmissionError:
        descriptors: list[int] = []
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        try:
            current = os.open(
                root, os.O_RDONLY | os.O_DIRECTORY | nofollow,
            )
            descriptors.append(current)
            for component in relative.parts[:-1]:
                current = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | nofollow,
                    dir_fd=current,
                )
                descriptors.append(current)
                info = os.fstat(current)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o022
                ):
                    return {"kind": "unsafe"}
            try:
                info = os.stat(
                    relative.parts[-1],
                    dir_fd=current,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return {"kind": "absent"}
            mode = stat.S_IMODE(info.st_mode)
            if (
                info.st_uid != os.getuid()
                or mode & 0o7022
            ):
                return {"kind": "unsafe"}
            if stat.S_ISDIR(info.st_mode):
                return {"kind": "directory", "mode": mode}
            if info.st_nlink != 1:
                return {"kind": "unsafe"}
            return {"kind": "unsafe"}
        except OSError:
            return {"kind": "unsafe"}
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
    return {
        "kind": "file",
        "sha256": hashlib.sha256(data).hexdigest(),
        "mode": mode,
    }


def _path_absent_beneath(root: Path, relative: PurePosixPath) -> bool:
    """Confirm one absent leaf through safe, non-linked parent directories."""
    descriptors: list[int] = []
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
        descriptors.append(current)
        for component in relative.parts[:-1]:
            current = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | nofollow,
                dir_fd=current,
            )
            descriptors.append(current)
            info = os.fstat(current)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise CompanyScribeAdmissionError(
                    "workspace deletion contains an unsafe directory"
                )
        try:
            os.stat(relative.parts[-1], dir_fd=current, follow_symlinks=False)
        except FileNotFoundError:
            return True
        return False
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "workspace deletion path is linked or unsafe"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _workspace_write_plan(
    workspace: Path,
    arguments: Mapping[str, object],
    *,
    edit: bool,
) -> dict[str, object]:
    relative_path = _relative_path(arguments.get("path"), field="path")
    path = _workspace_path(workspace, relative_path.as_posix())
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise CompanyScribeAdmissionError(
            "workspace write parent must already be one real directory"
        )
    content_value = arguments.get("content")
    prestate: dict[str, object]
    original_data: bytes | None = None
    if edit:
        old = arguments.get("old_text")
        new = arguments.get("new_text")
        if not isinstance(old, str) or not old:
            raise CompanyScribeAdmissionError("old_text must be non-empty text")
        if not isinstance(new, str):
            raise CompanyScribeAdmissionError("new_text must be text")
        try:
            original_bytes, original_mode = _read_regular_beneath(
                workspace.resolve(strict=True),
                relative_path,
                maximum=_MAX_TEXT_BYTES,
            )
            original = original_bytes.decode("utf-8")
            original_data = original_bytes
        except (CompanyScribeAdmissionError, UnicodeDecodeError) as exc:
            raise CompanyScribeAdmissionError(
                "workspace edit target is not readable UTF-8"
            ) from exc
        prestate = {
            "kind": "file",
            "sha256": hashlib.sha256(original_bytes).hexdigest(),
            "mode": original_mode,
        }
        count = original.count(old)
        replace_all = arguments.get("replace_all", False)
        if not isinstance(replace_all, bool):
            raise CompanyScribeAdmissionError("replace_all must be boolean")
        if count == 0 or (count != 1 and not replace_all):
            raise CompanyScribeAdmissionError(
                "workspace edit old_text must match exactly once unless replace_all"
            )
        content_value = original.replace(old, new, -1 if replace_all else 1)
    else:
        prestate = _path_state_beneath(
            workspace.resolve(strict=True), relative_path,
        )
    if not isinstance(content_value, str):
        raise CompanyScribeAdmissionError("content must be UTF-8 text")
    executable = arguments.get("executable", False)
    if not edit and not isinstance(executable, bool):
        raise CompanyScribeAdmissionError("executable must be boolean")
    data = content_value.encode("utf-8")
    if len(data) > _MAX_TEXT_BYTES:
        raise CompanyScribeAdmissionError("workspace effect exceeds 8 MiB")
    if prestate["kind"] == "absent":
        mode = 0o755 if executable is True else 0o644
    elif prestate["kind"] == "file":
        mode = int(prestate["mode"])
    else:
        raise CompanyScribeAdmissionError(
            "workspace write target must be a regular file"
        )
    return {
        "path": path,
        "relative_path": relative_path.as_posix(),
        "data": data,
        "sha256": hashlib.sha256(data).hexdigest(),
        "mode": mode,
        "prestate": prestate,
        "original_data": original_data,
    }


def _workspace_recovery_plan(
    workspace: Path,
    request_arguments: Mapping[str, object],
    details: Mapping[str, object],
    *,
    edit: bool,
) -> dict[str, object]:
    if frozenset(details) != {
        "path", "prestate", "desired_sha256", "desired_mode",
        "original_base64", "request_arguments",
    }:
        raise CompanyScribeAdmissionError(
            "prepared workspace effect lost its recovery details"
        )
    relative = _relative_path(
        request_arguments.get("path"), field="prepared workspace path",
    )
    if details.get("path") != relative.as_posix():
        raise CompanyScribeAdmissionError(
            "prepared workspace effect path changed identity"
        )
    prestate = details.get("prestate")
    if not isinstance(prestate, dict):
        raise CompanyScribeAdmissionError(
            "prepared workspace effect prestate is malformed"
        )
    if prestate.get("kind") == "absent":
        if frozenset(prestate) != {"kind"}:
            raise CompanyScribeAdmissionError(
                "prepared workspace effect prestate is malformed"
            )
    elif prestate.get("kind") == "file":
        mode = prestate.get("mode")
        digest = prestate.get("sha256")
        if (
            frozenset(prestate) != {"kind", "sha256", "mode"}
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or not isinstance(mode, int)
            or mode < 0
            or mode > 0o777
            or mode & 0o022
        ):
            raise CompanyScribeAdmissionError(
                "prepared workspace effect prestate is malformed"
            )
    else:
        raise CompanyScribeAdmissionError(
            "prepared workspace effect prestate is malformed"
        )

    original_base64 = details.get("original_base64")
    if edit:
        if prestate.get("kind") != "file" or not isinstance(
            original_base64, str,
        ):
            raise CompanyScribeAdmissionError(
                "prepared edit lost its original content"
            )
        try:
            original_data = base64.b64decode(
                original_base64.encode("ascii"), validate=True,
            )
            original = original_data.decode("utf-8")
        except (ValueError, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise CompanyScribeAdmissionError(
                "prepared edit original content is malformed"
            ) from exc
        if (
            len(original_data) > _MAX_TEXT_BYTES
            or hashlib.sha256(original_data).hexdigest()
            != prestate.get("sha256")
        ):
            raise CompanyScribeAdmissionError(
                "prepared edit original content changed identity"
            )
        old = request_arguments.get("old_text")
        new = request_arguments.get("new_text")
        replace_all = request_arguments.get("replace_all", False)
        if (
            not isinstance(old, str)
            or not old
            or not isinstance(new, str)
            or not isinstance(replace_all, bool)
        ):
            raise CompanyScribeAdmissionError(
                "prepared edit request is malformed"
            )
        count = original.count(old)
        if count == 0 or (count != 1 and not replace_all):
            raise CompanyScribeAdmissionError(
                "prepared edit no longer proves its requested transform"
            )
        desired_content = original.replace(
            old, new, -1 if replace_all else 1,
        )
        expected_mode = int(prestate["mode"])
    else:
        if original_base64 is not None:
            raise CompanyScribeAdmissionError(
                "prepared write has unexpected original content"
            )
        desired_content = request_arguments.get("content")
        executable = request_arguments.get("executable", False)
        if (
            not isinstance(desired_content, str)
            or not isinstance(executable, bool)
        ):
            raise CompanyScribeAdmissionError(
                "prepared write request is malformed"
            )
        if prestate.get("kind") == "absent":
            expected_mode = 0o755 if executable else 0o644
        else:
            expected_mode = int(prestate["mode"])
    data = desired_content.encode("utf-8")
    desired_sha256 = hashlib.sha256(data).hexdigest()
    if (
        len(data) > _MAX_TEXT_BYTES
        or details.get("desired_sha256") != desired_sha256
        or details.get("desired_mode") != expected_mode
    ):
        raise CompanyScribeAdmissionError(
            "prepared workspace effect desired state changed identity"
        )
    path = _workspace_path(workspace, relative.as_posix())
    return {
        "path": path,
        "relative_path": relative.as_posix(),
        "data": data,
        "sha256": desired_sha256,
        "mode": expected_mode,
        "prestate": prestate,
    }


def _commit_workspace_write(
    authority: _Authority,
    workspace: Path,
    plan: Mapping[str, object],
) -> dict[str, object]:
    path = plan["path"]
    data = plan["data"]
    if not isinstance(path, Path) or not isinstance(data, bytes):
        raise CompanyScribeAdmissionError("workspace write plan is malformed")
    parent = path.parent
    authority.check()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    try:
        os.fchmod(descriptor, int(plan["mode"]))
        remaining = data
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("workspace write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        # The final authority check is immediately adjacent to the only
        # externally visible mutation.
        binding = authority.check()
        os.replace(temporary, path)
        temporary = ""
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return {
        "state": "committed",
        "path": str(plan["relative_path"]),
        "sha256": str(plan["sha256"]),
        "admission_id": str(binding.admission_id),
        "admission_epoch": binding.admission_epoch,
    }


def _atomic_workspace_write(
    authority: _Authority,
    workspace: Path,
    arguments: Mapping[str, object],
    *,
    edit: bool,
) -> dict[str, object]:
    return _commit_workspace_write(
        authority,
        workspace,
        _workspace_write_plan(workspace, arguments, edit=edit),
    )


def _journaled_workspace_write(
    authority: _Authority,
    workspace: Path,
    arguments: Mapping[str, object],
    *,
    edit: bool,
    journal: MutationJournal,
    request_sha256: str,
) -> dict[str, object]:
    effect_id = _validate_effect_id(arguments.get("effect_id"))
    operation = "workspace_edit" if edit else "workspace_write"
    request_arguments = _closed_request_arguments(
        arguments,
        allowed=(
            frozenset({
                "effect_id", "path", "old_text", "new_text", "replace_all",
            })
            if edit else
            frozenset({"effect_id", "path", "content", "executable"})
        ),
        required=(
            frozenset({"effect_id", "path", "old_text", "new_text"})
            if edit else
            frozenset({"effect_id", "path", "content"})
        ),
        operation=operation,
    )
    with journal.locked(
        effect_id=effect_id,
        request_sha256=request_sha256,
        operation=operation,
    ) as (record, write):
        if record is not None:
            replay = _replayed_result(record)
            if replay is not None:
                return replay
            details = record.get("details")
            if (
                not isinstance(details, dict)
                or details.get("request_arguments") != request_arguments
                or _effect_request_sha256(
                    "effect", operation,
                    {"effect_id": effect_id, **request_arguments},
                ) != request_sha256
            ):
                raise CompanyScribeAdmissionError(
                    "prepared workspace effect lost its recovery details"
                )
            plan = _workspace_recovery_plan(
                workspace,
                request_arguments,
                details,
                edit=edit,
            )
            current = _path_state_beneath(
                workspace.resolve(strict=True),
                PurePosixPath(str(plan["relative_path"])),
            )
            desired = {
                "kind": "file",
                "sha256": plan["sha256"],
                "mode": plan["mode"],
            }
            if current == desired:
                binding = authority.check()
                result = {
                    "state": "committed",
                    "path": str(plan["relative_path"]),
                    "sha256": str(plan["sha256"]),
                    "admission_id": str(binding.admission_id),
                    "admission_epoch": binding.admission_epoch,
                }
                write({
                    "state": "completed",
                    "details": details,
                    "result": result,
                })
                return result
            if current != details.get("prestate"):
                raise CompanyScribeAdmissionError(
                    "prepared workspace effect conflicts with current state"
                )
        else:
            plan = _workspace_write_plan(workspace, arguments, edit=edit)
            original_data = plan.get("original_data")
            details = {
                "path": str(plan["relative_path"]),
                "prestate": plan["prestate"],
                "desired_sha256": str(plan["sha256"]),
                "desired_mode": int(plan["mode"]),
                "original_base64": (
                    base64.b64encode(original_data).decode("ascii")
                    if isinstance(original_data, bytes) else None
                ),
                "request_arguments": request_arguments,
            }
            write({"state": "prepared", "details": details})
        result = _commit_workspace_write(authority, workspace, plan)
        write({"state": "completed", "details": details, "result": result})
        return result


def _make_directory(
    authority: _Authority,
    workspace: Path,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    relative = _relative_path(arguments.get("path"), field="path")
    path = _workspace_path(workspace, relative.as_posix())
    state = _path_state_beneath(workspace.resolve(strict=True), relative)
    if state.get("kind") == "directory":
        return {"state": "present", "path": relative.as_posix()}
    if state.get("kind") != "absent":
        raise CompanyScribeAdmissionError("workspace directory target conflicts")
    binding = authority.check()
    os.mkdir(path, 0o755)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return {
        "state": "created",
        "path": relative.as_posix(),
        "admission_id": str(binding.admission_id),
        "admission_epoch": binding.admission_epoch,
    }


def _journaled_make_directory(
    authority: _Authority,
    workspace: Path,
    arguments: Mapping[str, object],
    *,
    journal: MutationJournal,
    request_sha256: str,
) -> dict[str, object]:
    effect_id = _validate_effect_id(arguments.get("effect_id"))
    request_arguments = _closed_request_arguments(
        arguments,
        allowed=frozenset({"effect_id", "path"}),
        required=frozenset({"effect_id", "path"}),
        operation="workspace_mkdir",
    )
    relative_path = _relative_path(arguments.get("path"), field="path")
    path = _workspace_path(workspace, relative_path.as_posix())
    relative = relative_path.as_posix()
    with journal.locked(
        effect_id=effect_id,
        request_sha256=request_sha256,
        operation="workspace_mkdir",
    ) as (record, write):
        if record is not None:
            replay = _replayed_result(record)
            if replay is not None:
                return replay
            details = record.get("details")
            if (
                not isinstance(details, dict)
                or frozenset(details) != {
                    "path", "prestate", "request_arguments",
                }
                or details.get("path") != relative
                or details.get("request_arguments") != request_arguments
                or details.get("prestate") != {"kind": "absent"}
                or _effect_request_sha256(
                    "effect", "workspace_mkdir",
                    {"effect_id": effect_id, **request_arguments},
                ) != request_sha256
            ):
                raise CompanyScribeAdmissionError(
                    "prepared directory effect lost its recovery details"
                )
            current = _path_state_beneath(
                workspace.resolve(strict=True), relative_path,
            )
            if current == {"kind": "directory", "mode": 0o755}:
                binding = authority.check()
                result = {
                    "state": "created",
                    "path": relative,
                    "admission_id": str(binding.admission_id),
                    "admission_epoch": binding.admission_epoch,
                }
                write({"state": "completed", "details": details, "result": result})
                return result
            if current.get("kind") == "directory":
                raise CompanyScribeAdmissionError(
                    "prepared directory effect has the wrong mode"
                )
            if current.get("kind") != "absent":
                raise CompanyScribeAdmissionError(
                    "prepared directory effect conflicts with current state"
                )
        details = {
            "path": relative,
            "prestate": _path_state_beneath(
                workspace.resolve(strict=True), relative_path,
            ),
            "request_arguments": request_arguments,
        }
        if details["prestate"] != {"kind": "absent"}:
            raise CompanyScribeAdmissionError(
                "workspace directory target conflicts"
            )
        write({"state": "prepared", "details": details})
        result = _make_directory(authority, workspace, arguments)
        write({"state": "completed", "details": details, "result": result})
        return result


def _sandbox_quote(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def _sandbox_profile(staging_root: Path, executable: Path) -> str:
    """Deny by default; only the private snapshot is writable and network is absent."""
    stage_path = staging_root.resolve(strict=True)
    executable_path = executable.resolve(strict=True)
    read_roots = tuple(Path(value) for value in (
        "/System", "/usr", "/bin", "/sbin", "/Library/Apple",
        "/opt/homebrew/Cellar",
    ))
    # realpath(3) needs metadata access to each lexical ancestor, while file
    # contents remain limited to the OS/runtime closure and private staging.
    metadata_paths = {Path("/")}
    for allowed in (*read_roots, stage_path, executable_path):
        current = allowed if allowed.is_dir() else allowed.parent
        while True:
            metadata_paths.add(current)
            if current == Path("/"):
                break
            current = current.parent
    stage = _sandbox_quote(stage_path)
    executable_value = _sandbox_quote(executable_path)
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(deny process-fork)",
        '(allow file-read* (literal "/"))',
        '(allow file-read* (literal "/dev/null"))',
        '(allow file-write* (literal "/dev/null"))',
        f'(allow file-read* (literal "{executable_value}"))',
        f'(allow file-read* (subpath "{stage}"))',
        f'(allow file-write* (subpath "{stage}"))',
    ]
    rules.extend(
        f'(allow file-read-metadata (literal "{_sandbox_quote(path)}"))'
        for path in sorted(metadata_paths - {Path("/")}, key=str)
    )
    rules.extend(
        f'(allow file-read* (subpath "{_sandbox_quote(root)}"))'
        for root in read_roots
    )
    # No network rule is present. Under deny-default, inbound, outbound,
    # loopback, DNS, Unix sockets, Mach services, and inherited authority
    # sockets all fail. User files outside the snapshot are metadata-only.
    return "\n".join(rules)


def _command_environment(staging_root: Path, staged_workspace: Path) -> dict[str, str]:
    home = staging_root / "home"
    temporary = staging_root / "tmp"
    home.mkdir(mode=0o700, exist_ok=True)
    temporary.mkdir(mode=0o700, exist_ok=True)
    return {
        **identity_environment(),
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "SHELL": "/bin/sh",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "MISE_ROLE": "scribe",
        "AGENT_ROLE": "scribe",
        "CCSUITE_ROLE": "scribe",
        "CCSUITE_REPO_ROOT": str(staged_workspace),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }


def _copy_workspace_snapshot(
    workspace: Path,
    staged_workspace: Path,
    *,
    files: tuple[PurePosixPath, ...],
    directories: tuple[PurePosixPath, ...],
) -> None:
    """Copy only the closed tracked/broker-authorized file inventory."""
    staged_workspace.mkdir(mode=0o700)
    for relative in directories:
        target = staged_workspace.joinpath(*relative.parts)
        target.mkdir(parents=True, exist_ok=True, mode=0o755)
    total = 0
    for relative in files:
        try:
            data, mode = _read_regular_beneath(
                workspace, relative, maximum=64 * 1024 * 1024,
            )
        except CompanyScribeAdmissionError:
            try:
                if _path_absent_beneath(workspace, relative):
                    continue
            except CompanyScribeAdmissionError:
                pass
            try:
                info = workspace.joinpath(*relative.parts).lstat()
            except OSError:
                pass
            else:
                if stat.S_ISLNK(info.st_mode):
                    continue
            raise
        total += len(data)
        if total > 512 * 1024 * 1024:
            raise CompanyScribeAdmissionError(
                "workspace snapshot exceeds the closed 512 MiB bound"
            )
        target = staged_workspace.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o755 if mode & 0o111 else 0o644,
        )
        try:
            remaining = data
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise CompanyScribeAdmissionError(
                        "workspace snapshot copy made no progress"
                    )
                remaining = remaining[written:]
        finally:
            os.close(descriptor)


def _map_workspace_argument(value: str, workspace: Path, staged: Path) -> str:
    root = str(workspace.resolve(strict=True))
    if value == root:
        return str(staged)
    if value.startswith(root + os.sep):
        return str(staged) + value[len(root):]
    return value


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    # Darwin can return EPERM from killpg() after a new-session leader has
    # exited but before its parent reaps it. Signal errors are therefore only
    # advisory here. The caller must still wait/reap and prove the recorded
    # guardian epoch absent; a live process remains a fail-closed timeout.
    try:
        os.kill(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            if process.poll() is not None:
                return
            try:
                process.kill()
            except (ProcessLookupError, PermissionError):
                pass
            return
        time.sleep(0.01)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.kill(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _terminate_exact_command_child(
    *,
    pid: int,
    pgid: int,
    start_fingerprint: str,
) -> None:
    """Kill only the exact gated sandbox epoch and prove it is gone."""
    if pid <= 1 or pgid != pid or not start_fingerprint:
        raise CompanyScribeAdmissionError(
            "recorded command child identity is malformed"
        )
    try:
        observed_start = process_start_fingerprint(pid)
    except CompanyScribeAdmissionError:
        if _process_group_exists(pgid):
            raise CompanyScribeAdmissionError(
                "recorded command leader vanished while its group survives"
            )
        return
    if observed_start != start_fingerprint:
        if _process_group_exists(pgid):
            raise CompanyScribeAdmissionError(
                "recorded command PID was reused while its group is ambiguous"
            )
        return
    try:
        observed_pgid = os.getpgid(pid)
    except ProcessLookupError:
        if _process_group_exists(pgid):
            raise CompanyScribeAdmissionError(
                "recorded command leader vanished while its group survives"
            )
        return
    if observed_pgid != pgid:
        raise CompanyScribeAdmissionError(
            "recorded command child changed process-group identity"
        )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    leader_deadline = time.monotonic() + 1.0
    while time.monotonic() < leader_deadline:
        try:
            if process_start_fingerprint(pid) != start_fingerprint:
                break
        except CompanyScribeAdmissionError:
            break
        time.sleep(0.01)
    else:
        raise CompanyScribeAdmissionError(
            "recorded command child survived exact termination"
        )
    _verify_process_group_absent(pgid)


def _terminate_exact_command_guardian(
    *,
    pid: int,
    start_fingerprint: str,
    deadline_monotonic_ns: int,
) -> None:
    if (
        pid <= 1
        or not start_fingerprint
        or deadline_monotonic_ns <= 0
    ):
        raise CompanyScribeAdmissionError(
            "recorded command guardian identity is malformed"
        )
    natural_deadline = time.monotonic() + 0.5
    while time.monotonic() < natural_deadline:
        try:
            if process_start_fingerprint(pid) != start_fingerprint:
                return
        except CompanyScribeAdmissionError:
            return
        time.sleep(0.01)
    try:
        executable = process_executable_path(pid)
        arguments = _process_arguments(pid)
    except CompanyScribeAdmissionError:
        try:
            if process_start_fingerprint(pid) != start_fingerprint:
                return
        except CompanyScribeAdmissionError:
            return
        raise
    if (
        executable != Path(sys.executable).resolve(strict=True)
        or len(arguments) < 12
        or Path(arguments[1]).resolve(strict=True)
        != Path(__file__).resolve(strict=True)
        or arguments[2] != _COMMAND_GUARDIAN_MODE
        or arguments[9] != str(deadline_monotonic_ns)
        or arguments[10] != "--"
        or arguments[11] != "/usr/bin/sandbox-exec"
    ):
        raise CompanyScribeAdmissionError(
            "recorded command guardian no longer has the reviewed identity"
        )
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    kill_deadline = time.monotonic() + 1.0
    while time.monotonic() < kill_deadline:
        try:
            if process_start_fingerprint(pid) != start_fingerprint:
                return
        except CompanyScribeAdmissionError:
            return
        time.sleep(0.01)
    raise CompanyScribeAdmissionError(
        "recorded command guardian survived exact termination"
    )


def _guardian_parent_is_live(parent_pid: int, parent_start: str) -> bool:
    if os.getppid() != parent_pid:
        return False
    try:
        return process_start_fingerprint(parent_pid) == parent_start
    except CompanyScribeAdmissionError:
        return False


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = payload
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("pipe write made no progress")
        remaining = remaining[written:]


def _guardian_control_signal(descriptor: int) -> bytes | None:
    try:
        return os.read(descriptor, 65536)
    except BlockingIOError:
        return None


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_process_group_id(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _verify_process_group_absent(pgid: int) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _process_group_exists(pgid):
            return
        time.sleep(0.01)
    raise CompanyScribeAdmissionError(
        "recorded command process group survived termination"
    )


def _command_guardian(argv: list[str]) -> int:
    if len(argv) < 10 or argv[0] != _COMMAND_GUARDIAN_MODE:
        raise CompanyScribeAdmissionError(
            "command guardian invocation is malformed"
        )
    try:
        launch_gate_fd = int(argv[1])
        control_fd = int(argv[2])
        status_fd = int(argv[3])
        child_gate_fd = int(argv[4])
        parent_pid = int(argv[5])
        deadline_ns = int(argv[7])
    except ValueError as exc:
        raise CompanyScribeAdmissionError(
            "command guardian identity is malformed"
        ) from exc
    parent_start = argv[6]
    if (
        min(launch_gate_fd, control_fd, status_fd, child_gate_fd) < 3
        or len({launch_gate_fd, control_fd, status_fd, child_gate_fd}) != 4
        or parent_pid <= 1
        or not parent_start
        or deadline_ns <= time.monotonic_ns()
        or argv[8] != "--"
        or argv[9] != "/usr/bin/sandbox-exec"
    ):
        raise CompanyScribeAdmissionError(
            "command guardian boundary is malformed"
        )
    os.set_blocking(control_fd, False)
    try:
        token = os.read(launch_gate_fd, 1)
    finally:
        os.close(launch_gate_fd)
    if token != b"G" or not _guardian_parent_is_live(
        parent_pid, parent_start,
    ):
        return 78

    stop = threading.Event()

    def _stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    last_authorized: float | None = None
    child_pid: int | None = None
    child_pgid: int | None = None
    child_reaped = False
    identity_reported = False
    session_ready_read = -1
    session_ready_write = -1
    terminal: dict[str, object] | None = None
    try:
        initial_deadline = min(
            deadline_ns / 1_000_000_000,
            time.monotonic() + _AUTHORITY_SILENCE_SECONDS,
        )
        while time.monotonic() < initial_deadline:
            if stop.is_set() or not _guardian_parent_is_live(
                parent_pid, parent_start,
            ):
                terminal = {
                    "schema": "mise.company-scribe-command-terminal.v1",
                    "kind": "fenced",
                    "reason": "broker_identity_lost_before_child_spawn",
                }
                break
            signals = _guardian_control_signal(control_fd)
            if signals == b"" or (signals is not None and b"E" in signals):
                terminal = {
                    "schema": "mise.company-scribe-command-terminal.v1",
                    "kind": "fenced",
                    "reason": "authority_control_lost_before_child_spawn",
                }
                break
            if signals is not None and b"." in signals:
                last_authorized = time.monotonic()
                break
            time.sleep(0.005)
        if terminal is None and last_authorized is None:
            terminal = {
                "schema": "mise.company-scribe-command-terminal.v1",
                "kind": "fenced",
                "reason": "authority_heartbeat_absent_before_child_spawn",
            }

        if terminal is None:
            session_ready_read, session_ready_write = os.pipe()
            guardian_pid = os.getpid()
            child_pid = os.fork()
            if child_pid == 0:
                try:
                    os.close(session_ready_read)
                    os.close(control_fd)
                    os.close(status_fd)
                    signal.signal(signal.SIGTERM, signal.SIG_DFL)
                    signal.signal(signal.SIGINT, signal.SIG_DFL)
                    os.setsid()
                    if (
                        os.getpid() != os.getpgid(0)
                        or os.getpid() != os.getsid(0)
                    ):
                        os._exit(78)
                    _write_all(session_ready_write, b"R")
                    os.close(session_ready_write)
                    child_token = os.read(child_gate_fd, 1)
                    os.close(child_gate_fd)
                    if (
                        child_token != b"G"
                        or os.getppid() != guardian_pid
                        or time.monotonic_ns() >= deadline_ns
                    ):
                        os._exit(78)
                    os.execve(argv[9], argv[9:], dict(os.environ))
                except BaseException:
                    os._exit(78)
            os.close(session_ready_write)
            session_ready_write = -1
            os.close(child_gate_fd)
            child_gate_fd = -1
            os.set_blocking(session_ready_read, False)
            ready = False
            ready_deadline = min(
                deadline_ns / 1_000_000_000,
                time.monotonic() + _AUTHORITY_SILENCE_SECONDS,
            )
            while time.monotonic() < ready_deadline:
                try:
                    if os.read(session_ready_read, 1) == b"R":
                        ready = True
                        break
                except BlockingIOError:
                    pass
                if not _guardian_parent_is_live(parent_pid, parent_start):
                    break
                time.sleep(0.005)
            os.close(session_ready_read)
            session_ready_read = -1
            if not ready:
                terminal = {
                    "schema": "mise.company-scribe-command-terminal.v1",
                    "kind": "fenced",
                    "reason": "session_leader_child_not_ready",
                }
            else:
                child_pgid = os.getpgid(child_pid)
                child_start = process_start_fingerprint(child_pid)
                if child_pgid != child_pid or os.getsid(child_pid) != child_pid:
                    terminal = {
                        "schema": "mise.company-scribe-command-terminal.v1",
                        "kind": "fenced",
                        "reason": "child_session_identity_changed",
                    }
                else:
                    _write_all(status_fd, _canonical_bytes({
                        "schema": "mise.company-scribe-command-child.v1",
                        "pid": child_pid,
                        "pgid": child_pgid,
                        "start_fingerprint": child_start,
                    }) + b"\n")
                    identity_reported = True

        while terminal is None:
            assert child_pid is not None
            waited, status = os.waitpid(child_pid, os.WNOHANG)
            if waited == child_pid:
                child_reaped = True
                if time.monotonic_ns() >= deadline_ns:
                    terminal = {
                        "schema": "mise.company-scribe-command-terminal.v1",
                        "kind": "fenced",
                        "reason": "fixed_effect_deadline_reached",
                    }
                elif os.WIFEXITED(status):
                    returncode = min(os.WEXITSTATUS(status), 255)
                    terminal = {
                        "schema": "mise.company-scribe-command-terminal.v1",
                        "kind": "exited",
                        "returncode": returncode,
                    }
                elif os.WIFSIGNALED(status):
                    returncode = 128 + os.WTERMSIG(status)
                    terminal = {
                        "schema": "mise.company-scribe-command-terminal.v1",
                        "kind": "exited",
                        "returncode": returncode,
                    }
                else:
                    terminal = {
                        "schema": "mise.company-scribe-command-terminal.v1",
                        "kind": "exited",
                        "returncode": 78,
                    }
                break
            now = time.monotonic()
            if stop.is_set():
                reason = "guardian_stop_requested"
            elif time.monotonic_ns() >= deadline_ns:
                reason = "fixed_effect_deadline_reached"
            elif not _guardian_parent_is_live(parent_pid, parent_start):
                reason = "broker_identity_lost"
            else:
                signals = _guardian_control_signal(control_fd)
                if signals == b"":
                    reason = "broker_control_eof"
                elif signals is not None and b"E" in signals:
                    reason = "authority_revoked"
                else:
                    reason = ""
                    if signals is not None and b"." in signals:
                        last_authorized = now
                    if now - last_authorized > _AUTHORITY_SILENCE_SECONDS:
                        reason = "authority_heartbeat_expired"
            if reason:
                terminal = {
                    "schema": "mise.company-scribe-command-terminal.v1",
                    "kind": "fenced",
                    "reason": reason,
                }
                break
            time.sleep(0.005)
    except BaseException:
        terminal = {
            "schema": "mise.company-scribe-command-terminal.v1",
            "kind": "fenced",
            "reason": "guardian_internal_failure",
        }
    finally:
        for descriptor in (
            control_fd, child_gate_fd, session_ready_read,
            session_ready_write,
        ):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if child_pid is not None:
            if child_pgid is None:
                child_pgid = child_pid
            _kill_process_group_id(child_pgid)
            try:
                if not child_reaped:
                    os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if not child_reaped:
                try:
                    os.waitpid(child_pid, 0)
                except ChildProcessError:
                    pass
            try:
                _verify_process_group_absent(child_pgid)
            except CompanyScribeAdmissionError:
                terminal = {
                    "schema": "mise.company-scribe-command-terminal.v1",
                    "kind": "fenced",
                    "reason": "child_process_group_cleanup_uncertain",
                }
        if status_fd >= 0:
            if terminal is not None:
                try:
                    _write_all(status_fd, _canonical_bytes(terminal) + b"\n")
                except OSError:
                    pass
            try:
                os.close(status_fd)
            except OSError:
                pass
    if terminal is not None and terminal.get("kind") == "exited":
        return 0
    return 78


def _authority_watchdog(
    authority: _Authority,
    write_fds: tuple[int, ...],
    stop: threading.Event,
    first_check: threading.Event,
) -> None:
    try:
        while not stop.is_set():
            try:
                authority.check()
            except BaseException:
                for write_fd in write_fds:
                    try:
                        os.write(write_fd, b"E")
                    except OSError:
                        pass
                first_check.set()
                return
            for write_fd in write_fds:
                try:
                    os.write(write_fd, b".")
                except OSError:
                    first_check.set()
                    return
            first_check.set()
            stop.wait(_AUTHORITY_POLL_SECONDS)
    finally:
        first_check.set()
        for write_fd in write_fds:
            try:
                os.close(write_fd)
            except OSError:
                pass


def _read_nonblocking_pipe(descriptor: int) -> tuple[bytes, bool]:
    try:
        data = os.read(descriptor, 65536)
    except BlockingIOError:
        return b"", False
    return data, data == b""


def _await_initial_authority_heartbeat(
    descriptor: int,
    first_check: threading.Event,
) -> float:
    if not first_check.wait(_AUTHORITY_SILENCE_SECONDS):
        raise CompanyScribeAdmissionError(
            "command authority watchdog did not complete its first check"
        )
    deadline = time.monotonic() + _AUTHORITY_SILENCE_SECONDS
    while time.monotonic() < deadline:
        signals, eof = _read_nonblocking_pipe(descriptor)
        if eof or b"E" in signals:
            raise CompanyScribeAdmissionError(
                "command authority watchdog refused launch"
            )
        if b"." in signals:
            return time.monotonic()
        time.sleep(0.005)
    raise CompanyScribeAdmissionError(
        "command authority watchdog launch heartbeat is absent"
    )


def _read_guardian_child_identity(
    descriptor: int,
    guardian: subprocess.Popen[bytes],
    *,
    deadline: float,
) -> tuple[int, int, str]:
    os.set_blocking(descriptor, False)
    buffered = b""
    # Frame receipt gets one bounded heartbeat interval even when the effect
    # deadline arrives during startup. This never extends authority: the child
    # gate is still closed, and the caller rechecks authority before release.
    status_deadline = time.monotonic() + _AUTHORITY_SILENCE_SECONDS
    while time.monotonic() < status_deadline:
        try:
            # Read exactly one frame without consuming a terminal frame that
            # may already follow the child identity in the same pipe buffer.
            chunk = os.read(descriptor, 1)
        except BlockingIOError:
            chunk = None
        if chunk == b"":
            break
        if chunk:
            buffered += chunk
            if len(buffered) > 4096:
                break
            if chunk == b"\n":
                break
            continue
        # A reaped guardian can still have a final pipe frame pending. Wait
        # for the read end to return that frame or EOF rather than treating
        # process.poll() as proof that the frame is absent.
        time.sleep(0.005)
    if len(buffered) > 4096 or buffered.count(b"\n") != 1:
        raise CompanyScribeAdmissionError(
            "command guardian did not report one closed child identity"
        )
    raw, extra = buffered.split(b"\n", 1)
    if extra:
        raise CompanyScribeAdmissionError(
            "command guardian child identity has trailing bytes"
        )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "command guardian child identity is unreadable"
        ) from exc
    if (
        isinstance(value, dict)
        and value.get("schema")
        == "mise.company-scribe-command-terminal.v1"
    ):
        terminal_returncode = _parse_guardian_terminal(value)
        raise CompanyScribeAdmissionError(
            "command guardian reported an exit before child identity: "
            f"{terminal_returncode}"
        )
    if not isinstance(value, dict) or frozenset(value) != {
        "schema", "pid", "pgid", "start_fingerprint",
    }:
        raise CompanyScribeAdmissionError(
            "command guardian child identity is malformed"
        )
    pid = value.get("pid")
    pgid = value.get("pgid")
    start = value.get("start_fingerprint")
    if (
        value.get("schema") != "mise.company-scribe-command-child.v1"
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or not isinstance(pgid, int)
        or isinstance(pgid, bool)
        or pgid != pid
        or not isinstance(start, str)
        or not start
    ):
        raise CompanyScribeAdmissionError(
            "command guardian child identity is malformed"
        )
    try:
        if (
            process_start_fingerprint(pid) != start
            or os.getpgid(pid) != pgid
        ):
            raise CompanyScribeAdmissionError(
                "command guardian child identity changed before registration"
            )
    except ProcessLookupError as exc:
        raise CompanyScribeAdmissionError(
            "command guardian child vanished before registration"
        ) from exc
    return pid, pgid, start


def _parse_guardian_terminal(value: object) -> int:
    if not isinstance(value, dict) or value.get("schema") != (
        "mise.company-scribe-command-terminal.v1"
    ):
        raise CompanyScribeAdmissionError(
            "command guardian terminal status is malformed"
        )
    if frozenset(value) == {"schema", "kind", "returncode"}:
        returncode = value.get("returncode")
        if (
            value.get("kind") != "exited"
            or not isinstance(returncode, int)
            or isinstance(returncode, bool)
            or returncode < 0
            or returncode > 255
        ):
            raise CompanyScribeAdmissionError(
                "command guardian exit status is malformed"
            )
        return returncode
    if frozenset(value) == {"schema", "kind", "reason"}:
        reason = value.get("reason")
        if (
            value.get("kind") != "fenced"
            or not isinstance(reason, str)
            or not reason
            or len(reason) > 200
        ):
            raise CompanyScribeAdmissionError(
                "command guardian fence status is malformed"
            )
        raise _GuardianFenced(reason)
    raise CompanyScribeAdmissionError(
        "command guardian terminal status has an unsupported shape"
    )


def _read_guardian_terminal(
    descriptor: int,
) -> int:
    buffered = b""
    read_deadline = time.monotonic() + 1.0
    while time.monotonic() < read_deadline:
        try:
            chunk = os.read(descriptor, 4097 - len(buffered))
        except BlockingIOError:
            chunk = None
        if chunk == b"":
            break
        if chunk:
            buffered += chunk
            if len(buffered) > 4096 or b"\n" in buffered:
                break
        time.sleep(0.005)
    if len(buffered) > 4096 or buffered.count(b"\n") != 1:
        raise CompanyScribeAdmissionError(
            "command guardian ended without one terminal status"
        )
    raw, extra = buffered.split(b"\n", 1)
    if extra:
        raise CompanyScribeAdmissionError(
            "command guardian terminal status has trailing bytes"
        )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "command guardian terminal status is unreadable"
        ) from exc
    return _parse_guardian_terminal(value)


def _validate_command(
    workspace: Path,
    arguments: Mapping[str, object],
) -> tuple[list[str], float, Path, tuple[PurePosixPath, ...]]:
    argv_value = arguments.get("argv")
    if (
        not isinstance(argv_value, list)
        or not argv_value
        or len(argv_value) > _MAX_COMMAND_ARGV_ITEMS
        or any(not isinstance(item, str) or not item or "\x00" in item for item in argv_value)
    ):
        raise CompanyScribeAdmissionError("command argv must be a non-empty string array")
    argv = list(argv_value)
    executable_name = Path(argv[0]).name.lower()
    if executable_name in _FORBIDDEN_AGENT_NAMES:
        raise CompanyScribeAdmissionError("nested agent execution is forbidden")
    for item in argv:
        if Path(item).name.lower() in _FORBIDDEN_AGENT_NAMES:
            raise CompanyScribeAdmissionError("nested agent execution is forbidden")
    timeout_value = arguments.get("timeout_seconds", 60.0)
    if isinstance(timeout_value, bool) or not isinstance(timeout_value, (int, float)):
        raise CompanyScribeAdmissionError("timeout_seconds must be numeric")
    timeout_seconds = float(timeout_value)
    if (
        not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > _MAX_COMMAND_SECONDS
    ):
        raise CompanyScribeAdmissionError("command timeout must be in (0, 300]")
    cwd_value = arguments.get("cwd", ".")
    if cwd_value == ".":
        cwd = workspace.resolve(strict=True)
    else:
        cwd = _workspace_path(
            workspace, cwd_value, field="cwd", must_exist=True,
        ).resolve(strict=True)
    if not cwd.is_dir():
        raise CompanyScribeAdmissionError("command cwd must be a directory")
    publish_value = arguments.get("publish_paths", [])
    if not isinstance(publish_value, list):
        raise CompanyScribeAdmissionError("publish_paths must be an array")
    publish_paths = tuple(
        _relative_path(item, field="publish_paths") for item in publish_value
    )
    if len(publish_paths) > _MAX_PUBLISHED_COMMAND_CHANGES:
        raise CompanyScribeAdmissionError(
            "one command may publish at most one reviewed workspace file"
        )
    if len(set(publish_paths)) != len(publish_paths):
        raise CompanyScribeAdmissionError("publish_paths repeats a path")
    return argv, timeout_seconds, cwd, publish_paths


def _execute_sandboxed_command(
    authority: _Authority,
    workspace: Path,
    arguments: Mapping[str, object],
    *,
    job_root: Path,
    snapshot_files: tuple[PurePosixPath, ...],
    snapshot_directories: tuple[PurePosixPath, ...],
    on_guardian_started: (
        Callable[[int, str, int], None] | None
    ) = None,
    on_child_started: (
        Callable[[int, int, str], None] | None
    ) = None,
) -> tuple[dict[str, object], Path, tuple[PurePosixPath, ...]]:
    argv, timeout_seconds, cwd, publish_paths = _validate_command(
        workspace, arguments,
    )
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise CompanyScribeAdmissionError(
            "command effects require the reviewed macOS exec sandbox"
        )
    binding = authority.check()
    remaining_lease = (
        binding.lease_expires_at - datetime.now(timezone.utc)
    ).total_seconds()
    if remaining_lease <= 0:
        raise CompanyScribeAdmissionError("command effect lease expired")
    fixed_seconds = min(timeout_seconds, remaining_lease)
    deadline = time.monotonic() + fixed_seconds
    active = job_root / "active"
    staged_workspace = active / "workspace"
    active.mkdir(parents=True, mode=0o700)
    _copy_workspace_snapshot(
        workspace.resolve(strict=True),
        staged_workspace,
        files=snapshot_files,
        directories=snapshot_directories,
    )
    staged_cwd = staged_workspace / cwd.relative_to(workspace.resolve(strict=True))
    mapped_argv = [
        _map_workspace_argument(item, workspace, staged_workspace)
        for item in argv
    ]
    executable_value = shutil.which(mapped_argv[0], path="/usr/bin:/bin:/usr/sbin:/sbin")
    if executable_value is None:
        candidate = Path(mapped_argv[0])
        if not candidate.is_absolute() or not candidate.exists():
            raise CompanyScribeAdmissionError("command executable is unavailable")
        executable = candidate.resolve(strict=True)
    else:
        executable = Path(executable_value).resolve(strict=True)
    # Execute the exact resolved image authorized by the sandbox profile. A
    # venv/Homebrew symlink outside the permitted read roots must not become a
    # hidden dependency or make a denied launch look like a completed command.
    mapped_argv[0] = str(executable)
    sandboxed_argv = [
        "/usr/bin/sandbox-exec", "-p",
        _sandbox_profile(active, executable), *mapped_argv,
    ]
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        launch_read, launch_write = os.pipe()
        control_read, control_write = os.pipe()
        status_read, status_write = os.pipe()
        child_gate_read, child_gate_write = os.pipe()
        broker_watch_read, broker_watch_write = os.pipe()
        for descriptor in (
            control_read, control_write,
            broker_watch_read, broker_watch_write,
        ):
            os.set_blocking(descriptor, False)
        parent_pid = os.getpid()
        parent_start = process_start_fingerprint(parent_pid)
        deadline_ns = int(deadline * 1_000_000_000)
        guardian_python = str(Path(sys.executable).absolute())
        guardian_argv = [
            guardian_python,
            str(Path(__file__).resolve(strict=True)),
            _COMMAND_GUARDIAN_MODE,
            str(launch_read),
            str(control_read),
            str(status_write),
            str(child_gate_read),
            str(parent_pid),
            parent_start,
            str(deadline_ns),
            "--",
            *sandboxed_argv,
        ]
        process: subprocess.Popen[bytes] | None = None
        guardian_start: str | None = None
        child_pid: int | None = None
        child_pgid: int | None = None
        child_start: str | None = None
        command_returncode: int | None = None
        watchdog_stop = threading.Event()
        watchdog_first_check = threading.Event()
        watchdog: threading.Thread | None = None
        watchdog_started = False
        fence_reason: str | None = None
        try:
            try:
                process = subprocess.Popen(
                    guardian_argv,
                    cwd=staged_cwd,
                    env=_command_environment(active, staged_workspace),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                    pass_fds=(
                        launch_read, control_read,
                        status_write, child_gate_read,
                    ),
                )
            finally:
                for descriptor in (
                    launch_read, control_read,
                    status_write, child_gate_read,
                ):
                    os.close(descriptor)
            guardian_start = process_start_fingerprint(process.pid)
            if on_guardian_started is not None:
                on_guardian_started(
                    process.pid, guardian_start, deadline_ns,
                )
            watchdog = threading.Thread(
                target=_authority_watchdog,
                args=(
                    authority,
                    (control_write, broker_watch_write),
                    watchdog_stop,
                    watchdog_first_check,
                ),
                name=f"scribe-authority-watchdog-{process.pid}",
                daemon=True,
            )
            watchdog.start()
            watchdog_started = True
            last_authorized = _await_initial_authority_heartbeat(
                broker_watch_read, watchdog_first_check,
            )
            try:
                authority.check()
            except CompanyScribeAdmissionError as exc:
                raise CompanyScribeAdmissionError(
                    "command effect fenced: authority changed before "
                    "guardian release; enforcement bound "
                    f"{int(_AUTHORITY_SILENCE_SECONDS * 1000)}ms"
                ) from exc
            if time.monotonic() >= deadline:
                raise CompanyScribeAdmissionError(
                    "command effect fenced: fixed effect deadline reached "
                    "before guardian release; enforcement bound "
                    f"{int(_AUTHORITY_SILENCE_SECONDS * 1000)}ms"
                )
            if os.write(launch_write, b"G") != 1:
                raise CompanyScribeAdmissionError(
                    "command guardian release made no progress"
                )
            os.close(launch_write)
            launch_write = -1
            try:
                child_pid, child_pgid, child_start = (
                    _read_guardian_child_identity(
                        status_read, process, deadline=deadline,
                    )
                )
            except _GuardianFenced as exc:
                raise CompanyScribeAdmissionError(
                    f"command effect fenced: {exc}; enforcement bound "
                    f"{int(_AUTHORITY_SILENCE_SECONDS * 1000)}ms"
                ) from exc
            if time.monotonic() >= deadline:
                raise CompanyScribeAdmissionError(
                    "command effect fenced: fixed effect deadline reached "
                    "before child release; enforcement bound "
                    f"{int(_AUTHORITY_SILENCE_SECONDS * 1000)}ms"
                )
            if on_child_started is not None:
                on_child_started(child_pid, child_pgid, child_start)
            try:
                authority.check()
            except CompanyScribeAdmissionError as exc:
                raise CompanyScribeAdmissionError(
                    "command effect fenced: authority changed before child "
                    "release; enforcement bound "
                    f"{int(_AUTHORITY_SILENCE_SECONDS * 1000)}ms"
                ) from exc
            signals, eof = _read_nonblocking_pipe(broker_watch_read)
            if eof or b"E" in signals:
                raise CompanyScribeAdmissionError(
                    "command effect fenced: authority changed before child "
                    "release; enforcement bound "
                    f"{int(_AUTHORITY_SILENCE_SECONDS * 1000)}ms"
                )
            if b"." in signals:
                last_authorized = time.monotonic()
            if time.monotonic() - last_authorized > _AUTHORITY_SILENCE_SECONDS:
                raise CompanyScribeAdmissionError(
                    "command effect fenced: authority heartbeat expired "
                    "before child release; enforcement bound "
                    f"{int(_AUTHORITY_SILENCE_SECONDS * 1000)}ms"
                )
            if time.monotonic() >= deadline:
                raise CompanyScribeAdmissionError(
                    "command effect fenced: fixed effect deadline reached "
                    "before child release; enforcement bound "
                    f"{int(_AUTHORITY_SILENCE_SECONDS * 1000)}ms"
                )
            try:
                child_release_written = os.write(child_gate_write, b"G")
            except BrokenPipeError as exc:
                raise CompanyScribeAdmissionError(
                    "command effect fenced: child gate closed before release; "
                    "enforcement bound "
                    f"{int(_AUTHORITY_SILENCE_SECONDS * 1000)}ms"
                ) from exc
            if child_release_written != 1:
                raise CompanyScribeAdmissionError(
                    "command child release made no progress"
                )
            os.close(child_gate_write)
            child_gate_write = -1

            while process.poll() is None:
                now = time.monotonic()
                signals, eof = _read_nonblocking_pipe(broker_watch_read)
                if eof or b"E" in signals:
                    fence_reason = "database admission revoked"
                    break
                if b"." in signals:
                    last_authorized = now
                if now >= deadline:
                    fence_reason = "fixed effect deadline reached"
                    break
                if now - last_authorized > _AUTHORITY_SILENCE_SECONDS:
                    fence_reason = "authority watchdog became unavailable"
                    break
                time.sleep(0.005)
            if fence_reason is None:
                try:
                    command_returncode = _read_guardian_terminal(status_read)
                except CompanyScribeAdmissionError as exc:
                    fence_reason = str(exc)
        finally:
            cleanup_errors: list[str] = []
            child_cleanup_error: str | None = None
            for descriptor in (
                launch_write, status_read, child_gate_write,
                broker_watch_read,
            ):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            if (
                child_pid is not None
                and child_pgid is not None
                and child_start is not None
            ):
                try:
                    _terminate_exact_command_child(
                        pid=child_pid,
                        pgid=child_pgid,
                        start_fingerprint=child_start,
                    )
                except BaseException as exc:
                    child_cleanup_error = str(exc)
            if process is not None:
                try:
                    if process.poll() is None:
                        _kill_process_group(process)
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        _kill_process_group(process)
                        process.wait(timeout=1)
                    if guardian_start is not None:
                        try:
                            observed_guardian_start = (
                                process_start_fingerprint(process.pid)
                            )
                        except CompanyScribeAdmissionError:
                            observed_guardian_start = None
                        if observed_guardian_start == guardian_start:
                            raise CompanyScribeAdmissionError(
                                "command guardian survived termination"
                            )
                except BaseException as exc:
                    cleanup_errors.append(f"guardian: {exc}")
            if (
                child_cleanup_error is not None
                and child_pid is not None
                and child_pgid is not None
                and child_start is not None
            ):
                try:
                    _terminate_exact_command_child(
                        pid=child_pid,
                        pgid=child_pgid,
                        start_fingerprint=child_start,
                    )
                except BaseException as exc:
                    cleanup_errors.append(
                        "child: " + child_cleanup_error
                        + f"; retry after guardian cleanup: {exc}"
                    )
            watchdog_stop.set()
            if watchdog_started and watchdog is not None:
                watchdog.join(timeout=2.0)
                if watchdog.is_alive():
                    cleanup_errors.append("authority watchdog did not stop")
            else:
                for descriptor in (control_write, broker_watch_write):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            if cleanup_errors:
                raise _CommandCleanupUncertain(
                    "command cleanup could not prove every identity absent: "
                    + "; ".join(cleanup_errors)
                )
        if process is None:
            raise CompanyScribeAdmissionError(
                "command guardian did not start"
            )
        if fence_reason is not None:
            raise CompanyScribeAdmissionError(
                f"command effect fenced: {fence_reason}; enforcement bound "
                f"{int(_AUTHORITY_SILENCE_SECONDS * 1000)}ms"
            )
        if command_returncode is None:
            raise CompanyScribeAdmissionError(
                "command guardian produced no reviewed exit status"
            )
        final_binding = authority.check()
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(_MAX_OUTPUT_BYTES + 1)
        stderr = stderr_file.read(_MAX_OUTPUT_BYTES + 1)
    return ({
        "state": "completed",
        "returncode": command_returncode,
        "stdout": stdout[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
        "stderr": stderr[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
        "stdout_truncated": len(stdout) > _MAX_OUTPUT_BYTES,
        "stderr_truncated": len(stderr) > _MAX_OUTPUT_BYTES,
        "fixed_effect_deadline_seconds": fixed_seconds,
        "revocation_enforcement_bound_ms": int(_AUTHORITY_SILENCE_SECONDS * 1000),
        "admission_id": str(final_binding.admission_id),
        "admission_epoch": final_binding.admission_epoch,
    }, staged_workspace, publish_paths)


def _run_command(
    authority: _Authority,
    workspace: Path,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    """Compatibility probe: isolated execution with no canonical publication."""
    with tempfile.TemporaryDirectory(prefix="scribe-command-probe-") as value:
        root = Path(value)
        state = root / "state"
        state.mkdir(mode=0o700)
        journal = MutationJournal(state)
        files, directories = _workspace_snapshot_inventory(
            workspace.resolve(strict=True),
            journal=journal,
            state_dir=state,
        )
        result, _staged, _publish = _execute_sandboxed_command(
            authority, workspace, {**arguments, "publish_paths": []},
            job_root=root / "job",
            snapshot_files=files,
            snapshot_directories=directories,
        )
        return result


@dataclass
class _CommandJob:
    event: threading.Event
    request_sha256: str


class CommandSupervisor:
    """Daemon-owned command jobs survive MCP handler loss and replay exactly."""

    def __init__(
        self,
        *,
        authority: _Authority,
        workspace: Path,
        state_dir: Path,
        journal: MutationJournal,
    ) -> None:
        self.authority = authority
        self.workspace = workspace.resolve(strict=True)
        self.state_dir = state_dir
        self.journal = journal
        self.jobs_root = _ensure_private_state_directory(
            state_dir, "command-jobs",
        )
        self._jobs_root_identity = _directory_identity(self.jobs_root)
        self._jobs: dict[str, _CommandJob] = {}
        self._lock = threading.Lock()

    def _job_root(self, effect_id: str) -> Path:
        _require_directory_identity(
            self.jobs_root, self._jobs_root_identity,
        )
        return self.jobs_root / hashlib.sha256(
            effect_id.encode("utf-8")
        ).hexdigest()

    def _register_guardian(
        self,
        *,
        effect_id: str,
        request_sha256: str,
        request_arguments: Mapping[str, object],
        guardian_pid: int,
        guardian_start_fingerprint: str,
        deadline_monotonic_ns: int,
    ) -> None:
        with self.journal.locked(
            effect_id=effect_id,
            request_sha256=request_sha256,
            operation="workspace_command",
        ) as (record, write):
            if (
                record is None
                or record.get("state") != "running"
                or record.get("details") != {
                    "request_arguments": request_arguments,
                }
            ):
                raise CompanyScribeAdmissionError(
                    "command guardian registration state changed"
                )
            write({
                "state": "running",
                "details": {
                    "request_arguments": request_arguments,
                    "guardian_pid": guardian_pid,
                    "guardian_start_fingerprint": (
                        guardian_start_fingerprint
                    ),
                    "deadline_monotonic_ns": deadline_monotonic_ns,
                },
            })

    def _register_guardian_child(
        self,
        *,
        effect_id: str,
        request_sha256: str,
        request_arguments: Mapping[str, object],
        guardian_pid: int,
        guardian_start_fingerprint: str,
        deadline_monotonic_ns: int,
        child_pid: int,
        child_pgid: int,
        child_start_fingerprint: str,
    ) -> None:
        guardian_details = {
            "request_arguments": request_arguments,
            "guardian_pid": guardian_pid,
            "guardian_start_fingerprint": guardian_start_fingerprint,
            "deadline_monotonic_ns": deadline_monotonic_ns,
        }
        with self.journal.locked(
            effect_id=effect_id,
            request_sha256=request_sha256,
            operation="workspace_command",
        ) as (record, write):
            if (
                record is None
                or record.get("state") != "running"
                or record.get("details") != guardian_details
            ):
                raise CompanyScribeAdmissionError(
                    "command child registration state changed"
                )
            write({
                "state": "running",
                "details": {
                    **guardian_details,
                    "child_pid": child_pid,
                    "child_pgid": child_pgid,
                    "child_start_fingerprint": child_start_fingerprint,
                },
            })

    def _publish_recovery(
        self,
        record: Mapping[str, object],
        write: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        details = record.get("details")
        if not isinstance(details, dict):
            raise CompanyScribeAdmissionError(
                "command publication lost its durable recovery plan"
            )
        relative = details.get("path")
        sealed_value = details.get("sealed_path")
        effect_id = record.get("effect_id")
        desired_sha = details.get("desired_sha256")
        desired_mode = details.get("desired_mode")
        staged_mode = details.get("staged_mode")
        request_arguments = details.get("request_arguments")
        if (
            frozenset(details) != {
                "path", "prestate", "desired_sha256", "desired_mode",
                "staged_mode", "sealed_path", "request_arguments",
            }
            or not isinstance(relative, str)
            or not isinstance(sealed_value, str)
            or not isinstance(effect_id, str)
            or not isinstance(desired_sha, str)
            or len(desired_sha) != 64
            or any(char not in "0123456789abcdef" for char in desired_sha)
            or not isinstance(desired_mode, int)
            or desired_mode < 0
            or desired_mode > 0o777
            or desired_mode & 0o022
            or not isinstance(staged_mode, int)
            or staged_mode < 0
            or staged_mode > 0o777
            or staged_mode & 0o7022
            or not isinstance(request_arguments, dict)
            or _effect_request_sha256(
                "effect", "workspace_command",
                {"effect_id": effect_id, **request_arguments},
            ) != record.get("request_sha256")
        ):
            raise CompanyScribeAdmissionError(
                "command publication recovery plan is malformed"
            )
        closed_arguments = _closed_request_arguments(
            {"effect_id": effect_id, **request_arguments},
            allowed=frozenset({
                "effect_id", "argv", "cwd", "timeout_seconds",
                "publish_paths",
            }),
            required=frozenset({"effect_id", "argv"}),
            operation="workspace_command",
        )
        if closed_arguments != request_arguments:
            raise CompanyScribeAdmissionError(
                "command publication request changed identity"
            )
        _argv, _timeout, _cwd, publish_paths = _validate_command(
            self.workspace,
            {"effect_id": effect_id, **closed_arguments},
        )
        if (
            len(publish_paths) != 1
            or publish_paths[0].as_posix() != relative
        ):
            raise CompanyScribeAdmissionError(
                "command publication path changed from the exact request"
            )
        prestate = details.get("prestate")
        if prestate == {"kind": "absent"}:
            expected_mode = 0o755 if staged_mode & 0o111 else 0o644
        elif (
            isinstance(prestate, dict)
            and frozenset(prestate) == {"kind", "sha256", "mode"}
            and prestate.get("kind") == "file"
            and isinstance(prestate.get("sha256"), str)
            and len(str(prestate["sha256"])) == 64
            and all(
                char in "0123456789abcdef"
                for char in str(prestate["sha256"])
            )
            and isinstance(prestate.get("mode"), int)
            and 0 <= int(prestate["mode"]) <= 0o777
            and not int(prestate["mode"]) & 0o022
        ):
            expected_mode = int(prestate["mode"])
        else:
            raise CompanyScribeAdmissionError(
                "command publication prestate is malformed"
            )
        if desired_mode != expected_mode:
            raise CompanyScribeAdmissionError(
                "command publication mode changed identity"
            )
        relative_path = _relative_path(
            relative, field="command publication path",
        )
        target = _workspace_path(self.workspace, relative)
        current = _path_state_beneath(self.workspace, relative_path)
        desired = {
            "kind": "file",
            "sha256": details.get("desired_sha256"),
            "mode": details.get("desired_mode"),
        }
        result = record.get("command_result")
        if not isinstance(result, dict):
            raise CompanyScribeAdmissionError(
                "command publication lost its command result"
            )
        if current == desired:
            self.authority.check()
            completed = {
                **result,
                "published_paths": [relative],
                "publication_sha256": details.get("desired_sha256"),
            }
            write({
                "state": "completed",
                "details": details,
                "result": completed,
            })
            return completed
        if current != details.get("prestate"):
            raise CompanyScribeAdmissionError(
                "command publication conflicts with current workspace state"
            )
        job_root = self._job_root(_validate_effect_id(effect_id))
        sealed = job_root / "sealed" / "result"
        if sealed_value != str(sealed):
            raise CompanyScribeAdmissionError(
                "command publication sealed path changed identity"
            )
        data, sealed_mode = _read_regular_beneath(
            job_root,
            PurePosixPath("sealed/result"),
            maximum=_MAX_TEXT_BYTES,
        )
        if sealed_mode != 0o400:
            raise CompanyScribeAdmissionError(
                "command publication sealed file mode changed"
            )
        if hashlib.sha256(data).hexdigest() != desired_sha:
            raise CompanyScribeAdmissionError(
                "command publication sealed bytes changed"
            )
        publish_result = _commit_workspace_write(
            self.authority,
            self.workspace,
            {
                "path": target,
                "relative_path": relative,
                "data": data,
                "sha256": details["desired_sha256"],
                "mode": details["desired_mode"],
            },
        )
        completed = {
            **result,
            "published_paths": [relative],
            "publication_sha256": publish_result["sha256"],
        }
        write({
            "state": "completed",
            "details": details,
            "result": completed,
        })
        return completed

    def _worker(
        self,
        effect_id: str,
        request_sha256: str,
        arguments: Mapping[str, object],
        event: threading.Event,
        *,
        recover_publish: bool,
    ) -> None:
        try:
            if recover_publish:
                with self.journal.locked(
                    effect_id=effect_id,
                    request_sha256=request_sha256,
                    operation="workspace_command",
                ) as (record, write):
                    if record is None or record.get("state") != "publishing":
                        raise CompanyScribeAdmissionError(
                            "command publication recovery state changed"
                        )
                    self._publish_recovery(record, write)
                return
            request_arguments = _closed_request_arguments(
                arguments,
                allowed=frozenset({
                    "effect_id", "argv", "cwd", "timeout_seconds",
                    "publish_paths",
                }),
                required=frozenset({"effect_id", "argv"}),
                operation="workspace_command",
            )
            job_root = self._job_root(effect_id)
            if job_root.exists() or job_root.is_symlink():
                job_info = job_root.lstat()
                if (
                    job_root.is_symlink()
                    or not stat.S_ISDIR(job_info.st_mode)
                    or job_info.st_uid != os.getuid()
                    or stat.S_IMODE(job_info.st_mode) != 0o700
                ):
                    raise CompanyScribeAdmissionError(
                        "command job root is linked or unsafe"
                    )
                shutil.rmtree(job_root)
            job_root.mkdir(mode=0o700)
            _fsync_directory(self.jobs_root)
            _, _, _, publish_paths = _validate_command(self.workspace, arguments)
            prestate: dict[str, object] | None = None
            if publish_paths:
                target = _workspace_path(
                    self.workspace, publish_paths[0].as_posix(),
                )
                prestate = _path_state_beneath(
                    self.workspace, publish_paths[0],
                )
                if prestate.get("kind") not in {"absent", "file"}:
                    raise CompanyScribeAdmissionError(
                        "command publish target must be absent or a regular file"
                    )
            snapshot_files, snapshot_directories = (
                _workspace_snapshot_inventory(
                    self.workspace,
                    journal=self.journal,
                    state_dir=self.state_dir,
                )
            )
            guardian_identity: dict[str, object] = {}

            def register_guardian(
                pid: int,
                start: str,
                deadline: int,
            ) -> None:
                self._register_guardian(
                    effect_id=effect_id,
                    request_sha256=request_sha256,
                    request_arguments=request_arguments,
                    guardian_pid=pid,
                    guardian_start_fingerprint=start,
                    deadline_monotonic_ns=deadline,
                )
                guardian_identity.update({
                    "pid": pid,
                    "start": start,
                    "deadline": deadline,
                })

            def register_child(pid: int, pgid: int, start: str) -> None:
                guardian_pid = guardian_identity.get("pid")
                guardian_start = guardian_identity.get("start")
                guardian_deadline = guardian_identity.get("deadline")
                if (
                    not isinstance(guardian_pid, int)
                    or isinstance(guardian_pid, bool)
                    or not isinstance(guardian_start, str)
                    or not isinstance(guardian_deadline, int)
                    or isinstance(guardian_deadline, bool)
                ):
                    raise CompanyScribeAdmissionError(
                        "command guardian identity was not registered"
                    )
                self._register_guardian_child(
                    effect_id=effect_id,
                    request_sha256=request_sha256,
                    request_arguments=request_arguments,
                    guardian_pid=guardian_pid,
                    guardian_start_fingerprint=guardian_start,
                    deadline_monotonic_ns=guardian_deadline,
                    child_pid=pid,
                    child_pgid=pgid,
                    child_start_fingerprint=start,
                )

            result, staged_workspace, observed_publish = _execute_sandboxed_command(
                self.authority,
                self.workspace,
                arguments,
                job_root=job_root,
                snapshot_files=snapshot_files,
                snapshot_directories=snapshot_directories,
                on_guardian_started=register_guardian,
                on_child_started=register_child,
            )
            if observed_publish != publish_paths:
                raise CompanyScribeAdmissionError(
                    "command publication inventory changed during execution"
                )
            if publish_paths and result.get("returncode") != 0:
                raise CompanyScribeAdmissionError(
                    "failed sandbox command cannot publish canonical bytes"
                )
            if not publish_paths:
                with self.journal.locked(
                    effect_id=effect_id,
                    request_sha256=request_sha256,
                    operation="workspace_command",
                ) as (_record, write):
                    write({"state": "completed", "result": result})
                return
            relative_path = publish_paths[0]
            relative = relative_path.as_posix()
            # Quarantine the sandbox's complete writable tree before opening
            # any caller-selected output.  The original sandbox pathname then
            # no longer resolves, and every component below the quarantined
            # root is opened with O_NOFOLLOW.
            active = job_root / "active"
            quarantine = job_root / "quarantine"
            if quarantine.exists() or quarantine.is_symlink():
                raise CompanyScribeAdmissionError(
                    "command quarantine path is unexpectedly occupied"
                )
            os.replace(active, quarantine)
            data, staged_mode = _read_regular_beneath(
                quarantine / "workspace",
                relative_path,
                maximum=_MAX_TEXT_BYTES,
            )
            sealed_dir = job_root / "sealed"
            sealed_dir.mkdir(mode=0o700)
            sealed = sealed_dir / "result"
            descriptor = os.open(
                sealed,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o400,
            )
            try:
                remaining = data
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("sealed command write made no progress")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(sealed, 0o400)
            sealed_fd = os.open(
                sealed,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(sealed_fd)
            finally:
                os.close(sealed_fd)
            _fsync_directory(sealed_dir)
            _fsync_directory(job_root)
            desired_sha = hashlib.sha256(data).hexdigest()
            desired_mode = (
                int(prestate["mode"])
                if prestate is not None and prestate.get("kind") == "file"
                else 0o755 if staged_mode & 0o111 else 0o644
            )
            details = {
                "path": relative,
                "prestate": prestate,
                "desired_sha256": desired_sha,
                "desired_mode": desired_mode,
                "staged_mode": staged_mode,
                "sealed_path": str(sealed),
                "request_arguments": request_arguments,
            }
            with self.journal.locked(
                effect_id=effect_id,
                request_sha256=request_sha256,
                operation="workspace_command",
            ) as (_record, write):
                write({
                    "state": "publishing",
                    "details": details,
                    "command_result": result,
                })
                self._publish_recovery({
                    "effect_id": effect_id,
                    "request_sha256": request_sha256,
                    "state": "publishing",
                    "details": details,
                    "command_result": result,
                }, write)
        except _CommandCleanupUncertain:
            # The exact process identities remain in the running record so a
            # restart can retry recovery. Never terminalize uncertain cleanup.
            pass
        except BaseException as exc:
            try:
                with self.journal.locked(
                    effect_id=effect_id,
                    request_sha256=request_sha256,
                    operation="workspace_command",
                ) as (record, write):
                    if (
                        record is not None
                        and record.get("state") not in {"completed", "publishing"}
                    ):
                        write({
                            "state": "failed",
                            "details": record.get("details"),
                            "error": str(exc),
                        })
            except BaseException:
                pass
        finally:
            event.set()

    def submit(
        self,
        *,
        effect_id: str,
        request_sha256: str,
        arguments: Mapping[str, object],
    ) -> dict[str, object]:
        request_arguments = _closed_request_arguments(
            arguments,
            allowed=frozenset({
                "effect_id", "argv", "cwd", "timeout_seconds",
                "publish_paths",
            }),
            required=frozenset({"effect_id", "argv"}),
            operation="workspace_command",
        )
        effect_id = _validate_effect_id(effect_id)
        if _effect_request_sha256(
            "effect", "workspace_command",
            {"effect_id": effect_id, **request_arguments},
        ) != request_sha256:
            raise CompanyScribeAdmissionError(
                "workspace command request identity changed"
            )
        with self._lock:
            job = self._jobs.get(effect_id)
            if job is not None and job.request_sha256 != request_sha256:
                raise CompanyScribeAdmissionError(
                    "effect_id was already used for a different command"
                )
            if job is None:
                recover_publish = False
                with self.journal.locked(
                    effect_id=effect_id,
                    request_sha256=request_sha256,
                    operation="workspace_command",
                ) as (record, write):
                    if record is not None:
                        replay = _replayed_result(record)
                        if replay is not None:
                            return replay
                        if record.get("state") == "publishing":
                            recover_publish = True
                        elif record.get("state") == "running":
                            if record.get("details") != {
                                "request_arguments": request_arguments,
                            }:
                                raise CompanyScribeAdmissionError(
                                    "running command recovery request changed"
                                )
                            write({
                                "state": "failed",
                                "details": record.get("details"),
                                "error": (
                                    "prior broker ended while the sandboxed command "
                                    "was running; canonical publication was refused"
                                ),
                            })
                            raise CompanyScribeAdmissionError(
                                "prior command broker ended before a terminal result"
                            )
                        else:
                            raise CompanyScribeAdmissionError(
                                "command journal has an unsupported replay state"
                            )
                    else:
                        write({
                            "state": "running",
                            "details": {
                                "request_arguments": request_arguments,
                            },
                        })
                event = threading.Event()
                job = _CommandJob(event=event, request_sha256=request_sha256)
                self._jobs[effect_id] = job
                threading.Thread(
                    target=self._worker,
                    args=(effect_id, request_sha256, dict(arguments), event),
                    kwargs={"recover_publish": recover_publish},
                    name=f"scribe-command-{effect_id[:32]}",
                    daemon=True,
                ).start()
        job.event.wait()
        with self.journal.locked(
            effect_id=effect_id,
            request_sha256=request_sha256,
            operation="workspace_command",
        ) as (record, _write):
            if record is None:
                raise CompanyScribeAdmissionError(
                    "command supervisor lost its durable result"
                )
            replay = _replayed_result(record)
            if replay is None:
                raise CompanyScribeAdmissionError(
                    "command supervisor stopped without a terminal result"
                )
            return replay

    def wait_for_idle(self, timeout_seconds: float) -> bool:
        """Keep the daemon alive while workers fence and reap their commands."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                events = tuple(job.event for job in self._jobs.values())
            if all(event.is_set() for event in events):
                return True
            time.sleep(0.01)
        with self._lock:
            return all(job.event.is_set() for job in self._jobs.values())


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    if data and not data.endswith(b"\n"):
        raise CompanyScribeAdmissionError(
            f"channel journal has a torn final row: {path.name}"
        )
    rows: list[dict[str, object]] = []
    for index, raw in enumerate(data.splitlines(), start=1):
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanyScribeAdmissionError(
                f"channel journal row {index} is invalid: {path.name}"
            ) from exc
        if not isinstance(value, dict):
            raise CompanyScribeAdmissionError(
                f"channel journal row {index} is not an object: {path.name}"
            )
        rows.append(value)
    return rows


def _append_jsonl_once(
    authority: _Authority,
    path: Path,
    row: Mapping[str, object],
    *,
    identity: Callable[[Mapping[str, object]], object | None],
) -> bool:
    """Append exactly once under flock with a final adjacent authority check."""
    # Refusal must not even create an empty projection file or parent. The
    # runtime pre-creates the reviewed roots; authority is checked before the
    # first canonical open and again immediately beside the append.
    authority.check()
    parent_info = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or path.parent.is_symlink()
        or parent_info.st_uid != os.getuid()
        or stat.S_IMODE(parent_info.st_mode) & 0o022
    ):
        raise CompanyScribeAdmissionError("channel mutation parent is unsafe")
    expected_identity = identity(row)
    if expected_identity is None:
        raise CompanyScribeAdmissionError("channel mutation identity is absent")
    payload = _canonical_bytes(row)
    if b"\n" in payload or len(payload) > _MAX_TEXT_BYTES:
        raise CompanyScribeAdmissionError("channel mutation row is unsafe")
    flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent,
        )
        try:
            os.fchmod(descriptor, 0o600)
            expected = payload + b"\n"
            remaining = expected
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise CompanyScribeAdmissionError(
                        "channel mutation creation made no progress"
                    )
                remaining = remaining[written:]
            os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.read(descriptor, len(expected)) != expected:
                raise CompanyScribeAdmissionError(
                    "channel mutation creation lacks exact read-back receipt"
                )
            # No canonical path exists until this final adjacent authority
            # check succeeds. link(2) provides an exclusive atomic publish.
            authority.check()
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                pass
            else:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                os.close(descriptor)
                descriptor = -1
                os.unlink(temporary)
                temporary = ""
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                return True
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise CompanyScribeAdmissionError(
                "channel mutation target disappeared during exclusive create"
            ) from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise CompanyScribeAdmissionError("channel mutation target is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.lseek(descriptor, 0, os.SEEK_SET)
        existing = b""
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            existing += chunk
            if len(existing) > 64 * 1024 * 1024:
                raise CompanyScribeAdmissionError("channel journal exceeds 64 MiB")
        if existing and not existing.endswith(b"\n"):
            raise CompanyScribeAdmissionError("channel mutation target has a torn tail")
        for raw in existing.splitlines():
            if not raw:
                continue
            try:
                observed = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CompanyScribeAdmissionError(
                    "channel mutation target contains invalid JSON"
                ) from exc
            if not isinstance(observed, dict):
                raise CompanyScribeAdmissionError(
                    "channel mutation target contains a non-object row"
                )
            if identity(observed) == expected_identity:
                if _canonical_bytes(observed) != payload:
                    raise CompanyScribeAdmissionError(
                        "channel mutation identity conflicts with existing bytes"
                    )
                return False
        authority.check()
        offset = os.lseek(descriptor, 0, os.SEEK_END)
        remaining = payload + b"\n"
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise CompanyScribeAdmissionError(
                    "channel mutation append made no progress"
                )
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.lseek(descriptor, offset, os.SEEK_SET)
        if os.read(descriptor, len(payload) + 1) != payload + b"\n":
            raise CompanyScribeAdmissionError(
                "channel mutation append lacks exact read-back receipt"
            )
        return True
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _channel_send_rows(
    effect_id: str,
    arguments: Mapping[str, object],
) -> tuple[str, dict[str, object], dict[str, object]]:
    if not set(arguments).issubset({
        "effect_id", "target_role", "content", "event_type", "meta",
    }):
        raise CompanyScribeAdmissionError(
            "channel send contains an unknown argument"
        )
    target = arguments.get("target_role")
    content = arguments.get("content")
    event_type = arguments.get("event_type", "audit_request")
    meta = arguments.get("meta", {})
    if target not in _VALID_CHANNEL_ROLES:
        raise CompanyScribeAdmissionError("channel target role is not reviewed")
    if event_type not in _VALID_CHANNEL_EVENT_TYPES:
        raise CompanyScribeAdmissionError("channel event type is not reviewed")
    if not isinstance(content, str) or not content or len(content.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise CompanyScribeAdmissionError("channel content must be bounded non-empty text")
    if not isinstance(meta, dict):
        raise CompanyScribeAdmissionError("channel metadata must be an object")
    if _CHANNEL_SEND_META_RESERVED.intersection(meta):
        raise CompanyScribeAdmissionError(
            "channel metadata cannot set broker-reserved identity fields"
        )
    try:
        json.dumps(meta, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CompanyScribeAdmissionError("channel metadata is not JSON-safe") from exc
    send_id = "scribe-send-" + hashlib.sha256(
        effect_id.encode("utf-8")
    ).hexdigest()[:32]
    sent_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    event = {
        "event_type": event_type,
        "content": content,
        "meta": {
            **meta,
            "sender_role": "scribe",
            "target_role": target,
            "sent_at": sent_at,
            "send_id": send_id,
        },
    }
    audit = {
        "ts": sent_at,
        "send_id": send_id,
        "sender": "scribe",
        "target": target,
        "event_type": event_type,
        "content_length": len(content),
        "content_sha1": hashlib.sha1(
            content.encode("utf-8"), usedforsecurity=False,
        ).hexdigest()[:12],
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    return send_id, event, audit


def _valid_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        observed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return observed.tzinfo is not None and observed.utcoffset() == timezone.utc.utcoffset(observed)


def _reconstruct_channel_send_arguments(
    *,
    effect_id: str,
    target: str,
    content: str,
    event_type: str,
    user_meta: dict[str, object],
    request_sha256: str,
) -> dict[str, object]:
    event_type_options = (
        (False, True) if event_type == "audit_request" else (True,)
    )
    meta_options = (False, True) if not user_meta else (True,)
    matches: list[dict[str, object]] = []
    for include_event_type in event_type_options:
        for include_meta in meta_options:
            candidate: dict[str, object] = {
                "effect_id": effect_id,
                "target_role": target,
                "content": content,
            }
            if include_event_type:
                candidate["event_type"] = event_type
            if include_meta:
                candidate["meta"] = user_meta
            if (
                _effect_request_sha256(
                    "channel", "channel_send", candidate,
                )
                == request_sha256
            ):
                matches.append(candidate)
    if len(matches) != 1:
        raise CompanyScribeAdmissionError(
            "prepared channel send request identity is malformed"
        )
    return matches[0]


def _validate_prepared_channel_send(
    record: Mapping[str, object],
) -> tuple[str, str, dict[str, object], dict[str, object], dict[str, object]]:
    effect_id = _validate_effect_id(record.get("effect_id"))
    request_sha256 = record.get("request_sha256")
    details = record.get("details")
    if (
        not isinstance(request_sha256, str)
        or not isinstance(details, dict)
        or frozenset(details) != {
            "send_id", "target_role", "event", "audit",
        }
    ):
        raise CompanyScribeAdmissionError(
            "prepared channel send recovery details are malformed"
        )
    send_id = details.get("send_id")
    target = details.get("target_role")
    event = details.get("event")
    audit = details.get("audit")
    expected_send_id = "scribe-send-" + hashlib.sha256(
        effect_id.encode("utf-8")
    ).hexdigest()[:32]
    if (
        send_id != expected_send_id
        or target not in _VALID_CHANNEL_ROLES
        or not isinstance(event, dict)
        or frozenset(event) != {"event_type", "content", "meta"}
        or not isinstance(audit, dict)
        or frozenset(audit) != {
            "ts", "send_id", "sender", "target", "event_type",
            "content_length", "content_sha1", "content_sha256",
        }
    ):
        raise CompanyScribeAdmissionError(
            "prepared channel send recovery rows are malformed"
        )
    event_type = event.get("event_type")
    content = event.get("content")
    event_meta = event.get("meta")
    if (
        event_type not in _VALID_CHANNEL_EVENT_TYPES
        or not isinstance(content, str)
        or not content
        or len(content.encode("utf-8")) > _MAX_TEXT_BYTES
        or not isinstance(event_meta, dict)
        or not _CHANNEL_SEND_META_RESERVED.issubset(event_meta)
    ):
        raise CompanyScribeAdmissionError(
            "prepared channel send event is malformed"
        )
    user_meta = {
        key: value
        for key, value in event_meta.items()
        if key not in _CHANNEL_SEND_META_RESERVED
    }
    try:
        json.dumps(user_meta, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CompanyScribeAdmissionError(
            "prepared channel send metadata is malformed"
        ) from exc
    sent_at = event_meta.get("sent_at")
    content_bytes = content.encode("utf-8")
    if (
        event_meta.get("sender_role") != "scribe"
        or event_meta.get("target_role") != target
        or event_meta.get("send_id") != send_id
        or not _valid_utc_timestamp(sent_at)
        or audit.get("ts") != sent_at
        or audit.get("send_id") != send_id
        or audit.get("sender") != "scribe"
        or audit.get("target") != target
        or audit.get("event_type") != event_type
        or audit.get("content_length") != len(content)
        or audit.get("content_sha1") != hashlib.sha1(
            content_bytes, usedforsecurity=False,
        ).hexdigest()[:12]
        or audit.get("content_sha256") != hashlib.sha256(
            content_bytes,
        ).hexdigest()
    ):
        raise CompanyScribeAdmissionError(
            "prepared channel send rows changed identity"
        )
    reconstructed = _reconstruct_channel_send_arguments(
        effect_id=effect_id,
        target=str(target),
        content=content,
        event_type=str(event_type),
        user_meta=user_meta,
        request_sha256=request_sha256,
    )
    return (
        str(send_id), str(target), dict(event), dict(audit), reconstructed,
    )


def _journaled_channel_send(
    authority: _Authority,
    arguments: Mapping[str, object],
    *,
    journal: MutationJournal,
    request_sha256: str,
    channel_shared_dir: Path,
) -> dict[str, object]:
    effect_id = _validate_effect_id(arguments.get("effect_id"))
    with journal.locked(
        effect_id=effect_id,
        request_sha256=request_sha256,
        operation="channel_send",
    ) as (record, write):
        if record is not None:
            replay = _replayed_result(record)
            if replay is not None:
                return replay
            send_id, target, event, audit, _reconstructed = (
                _validate_prepared_channel_send(record)
            )
            details = record["details"]
            if not isinstance(details, dict):
                raise AssertionError("validated channel details changed type")
        else:
            send_id, event, audit = _channel_send_rows(effect_id, arguments)
            target = arguments.get("target_role")
            details = {
                "send_id": send_id,
                "target_role": target,
                "event": event,
                "audit": audit,
            }
            write({"state": "prepared", "details": details})
        queue_path = channel_shared_dir / f"queue_{target}.jsonl"
        audit_path = channel_shared_dir / "audit_sends.jsonl"
        _append_jsonl_once(
            authority,
            queue_path,
            event,
            identity=lambda row: (
                row.get("meta", {}).get("send_id")
                if isinstance(row.get("meta"), dict) else None
            ),
        )
        _append_jsonl_once(
            authority,
            audit_path,
            audit,
            identity=lambda row: row.get("send_id"),
        )
        result = {"state": "sent", "send_id": send_id, "target_role": target}
        write({"state": "completed", "details": details, "result": result})
        return result


def _delivery_snapshot(path: Path, event_id: str) -> dict[str, object] | None:
    """Resolve a journal event by event_id or coworker send_id."""
    snapshots: dict[str, dict[str, object]] = {}
    alias: dict[str, str] = {}
    for row in _read_jsonl(path):
        eid = row.get("event_id")
        if not isinstance(eid, str) or not eid:
            continue
        state = row.get("state")
        current = snapshots.get(eid)
        if state == "pending":
            snapshots[eid] = dict(row)
            event = row.get("event")
            meta = event.get("meta") if isinstance(event, dict) else None
            if isinstance(meta, dict):
                send_id = meta.get("send_id")
                if isinstance(send_id, str) and send_id:
                    alias[send_id] = eid
                stamped = meta.get("event_id")
                if isinstance(stamped, str) and stamped:
                    alias[stamped] = eid
            continue
        if current is None:
            raise CompanyScribeAdmissionError(
                "delivery journal transition lacks pending authority"
            )
        if row.get("event_sha256") != current.get("event_sha256"):
            raise CompanyScribeAdmissionError(
                "delivery journal event fingerprint changed"
            )
        snapshots[eid] = {**current, **row}
    resolved = event_id if event_id in snapshots else alias.get(event_id)
    if resolved is None:
        return None
    return snapshots.get(resolved)


def _journaled_channel_reply(
    authority: _Authority,
    arguments: Mapping[str, object],
    *,
    journal: MutationJournal,
    request_sha256: str,
    channel_state_dir: Path,
) -> dict[str, object]:
    effect_id = _validate_effect_id(arguments.get("effect_id"))
    if frozenset(arguments) != {"effect_id", "event_id", "text"}:
        raise CompanyScribeAdmissionError(
            "channel reply contains an unknown or missing argument"
        )
    event_id = arguments.get("event_id")
    verdict = arguments.get("text")
    if (
        not isinstance(event_id, str)
        or not event_id
        or len(event_id.encode("utf-8")) > 1024
        or not isinstance(verdict, str)
        or not verdict
        or len(verdict.encode("utf-8")) > _MAX_TEXT_BYTES
    ):
        raise CompanyScribeAdmissionError(
            "channel reply requires non-empty event_id and text"
        )
    delivery_path = channel_state_dir / "delivery_journal.jsonl"
    with journal.locked(
        effect_id=effect_id,
        request_sha256=request_sha256,
        operation="channel_reply",
    ) as (record, write):
        if record is not None:
            replay = _replayed_result(record)
            if replay is not None:
                return replay
            details = record.get("details")
            if (
                not isinstance(details, dict)
                or frozenset(details) != {"row"}
                or not isinstance(details.get("row"), dict)
            ):
                raise CompanyScribeAdmissionError(
                    "prepared channel reply lost its recovery row"
                )
            row = dict(details["row"])
        else:
            snapshot = _delivery_snapshot(delivery_path, event_id)
            if snapshot is None:
                raise CompanyScribeAdmissionError("unknown channel event_id")
            if snapshot.get("state") == "pending":
                raise CompanyScribeAdmissionError(
                    "channel event has not reached delivered state"
                )
            if snapshot.get("state") == "acknowledged":
                if snapshot.get("verdict") != verdict:
                    raise CompanyScribeAdmissionError(
                        "channel acknowledgment conflicts with prior verdict"
                    )
                result = {"state": "acknowledged", "kind": "duplicate", "event_id": event_id}
                write({"state": "completed", "result": result})
                return result
            row = {
                "schema": "mise.scribe-delivery-transition.v1",
                "event_id": event_id,
                "event_sha256": snapshot.get("event_sha256"),
                "state": "acknowledged",
                "transitioned_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "verdict": verdict,
            }
            details = {"row": row}
            write({"state": "prepared", "details": details})
        if (
            frozenset(row) != {
                "schema", "event_id", "event_sha256", "state",
                "transitioned_at", "verdict",
            }
            or row.get("schema") != "mise.scribe-delivery-transition.v1"
            or row.get("event_id") != event_id
            or row.get("state") != "acknowledged"
            or row.get("verdict") != verdict
            or not isinstance(row.get("event_sha256"), str)
            or len(str(row["event_sha256"])) != 64
            or any(
                char not in "0123456789abcdef"
                for char in str(row["event_sha256"])
            )
            or not _valid_utc_timestamp(row.get("transitioned_at"))
            or _effect_request_sha256(
                "channel",
                "channel_reply",
                {
                    "effect_id": effect_id,
                    "event_id": event_id,
                    "text": verdict,
                },
            ) != request_sha256
        ):
            raise CompanyScribeAdmissionError(
                "prepared channel reply recovery row is malformed"
            )
        current = _delivery_snapshot(delivery_path, event_id)
        if (
            current is None
            or current.get("event_id") != event_id
            or current.get("event_sha256") != row.get("event_sha256")
            or current.get("state") not in {"delivered", "acknowledged"}
            or (
                current.get("state") == "acknowledged"
                and current.get("verdict") != verdict
            )
        ):
            raise CompanyScribeAdmissionError(
                "prepared channel reply no longer matches delivery state"
            )
        if current.get("state") == "acknowledged":
            binding = authority.check()
            result = {
                "state": "acknowledged",
                "kind": "duplicate",
                "event_id": event_id,
                "admission_id": str(binding.admission_id),
                "admission_epoch": binding.admission_epoch,
            }
            write({"state": "completed", "details": details, "result": result})
            return result
        _append_jsonl_once(
            authority,
            delivery_path,
            row,
            identity=lambda value: (
                value.get("event_id"), value.get("state")
            ) if value.get("state") == "acknowledged" else None,
        )
        result = {"state": "acknowledged", "kind": "acknowledged", "event_id": event_id}
        write({"state": "completed", "details": details, "result": result})
        return result


def _recover_prepared_channel_effects(
    authority: _Authority,
    *,
    journal: MutationJournal,
    channel_state_dir: Path,
    channel_shared_dir: Path,
) -> None:
    """Finish durable channel effects before the daemon advertises readiness."""
    for record in journal.pending_records():
        operation = record["operation"]
        if operation not in {"channel_send", "channel_reply"}:
            continue
        effect_id = str(record["effect_id"])
        request_sha256 = str(record["request_sha256"])
        authority.check()
        if operation == "channel_send":
            _send_id, _target, _event, _audit, arguments = (
                _validate_prepared_channel_send(record)
            )
            _journaled_channel_send(
                authority,
                arguments,
                journal=journal,
                request_sha256=request_sha256,
                channel_shared_dir=channel_shared_dir,
            )
            continue
        details = record.get("details")
        row = details.get("row") if isinstance(details, dict) else None
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("event_id"), str)
            or not isinstance(row.get("verdict"), str)
        ):
            raise CompanyScribeAdmissionError(
                "prepared channel reply recovery row is malformed"
            )
        _journaled_channel_reply(
            authority,
            {
                "effect_id": effect_id,
                "event_id": row["event_id"],
                "text": row["verdict"],
            },
            journal=journal,
            request_sha256=request_sha256,
            channel_state_dir=channel_state_dir,
        )


def _assert_no_unresolved_effects(journal: MutationJournal) -> None:
    unresolved = tuple(
        record for record in journal.records()
        if record.get("state") in {"prepared", "running", "publishing"}
    )
    if not unresolved:
        return
    summary = ",".join(
        f"{record['operation']}:{record['effect_id']}:{record['state']}"
        for record in unresolved
    )
    raise CompanyScribeAdmissionError(
        "effect journal recovery required before broker readiness: " + summary
    )


def _assert_no_other_unresolved_nonchannel(
    journal: MutationJournal,
    *,
    effect_id: str,
) -> None:
    conflicting = tuple(
        record for record in journal.records()
        if record.get("state") in {"prepared", "running", "publishing"}
        and record.get("operation") not in {"channel_send", "channel_reply"}
        and record.get("effect_id") != effect_id
    )
    if conflicting:
        raise CompanyScribeAdmissionError(
            "another serialized workspace effect requires recovery"
        )


def _recover_nonchannel_effects(
    authority: _Authority,
    *,
    journal: MutationJournal,
    workspace: Path,
    state_dir: Path,
    command_supervisor: CommandSupervisor,
) -> None:
    unresolved = tuple(
        record for record in journal.records()
        if record.get("state") in {"prepared", "running", "publishing"}
        and record.get("operation") not in {"channel_send", "channel_reply"}
    )
    if len(unresolved) > 1:
        raise CompanyScribeAdmissionError(
            "serialized workspace journal contains multiple unresolved effects"
        )
    if not unresolved:
        return
    record = unresolved[0]
    effect_id = _validate_effect_id(record.get("effect_id"))
    request_sha256 = record.get("request_sha256")
    operation = record.get("operation")
    state = record.get("state")
    details = record.get("details")
    request_arguments = (
        details.get("request_arguments")
        if isinstance(details, dict) else None
    )
    if (
        not isinstance(request_sha256, str)
        or not isinstance(operation, str)
        or not isinstance(request_arguments, dict)
        or "effect_id" in request_arguments
    ):
        raise CompanyScribeAdmissionError(
            "unresolved workspace effect lost its recovery request"
        )
    arguments = {"effect_id": effect_id, **request_arguments}
    if _effect_request_sha256(
        "effect", operation, arguments,
    ) != request_sha256:
        raise CompanyScribeAdmissionError(
            "unresolved workspace effect request identity changed"
        )
    with _WORKSPACE_MUTATION_LOCK:
        if state == "prepared" and operation in {
            "workspace_write", "workspace_edit",
        }:
            _journaled_workspace_write(
                authority,
                workspace,
                arguments,
                edit=operation == "workspace_edit",
                journal=journal,
                request_sha256=request_sha256,
            )
            return
        if state == "prepared" and operation == "workspace_mkdir":
            _journaled_make_directory(
                authority,
                workspace,
                arguments,
                journal=journal,
                request_sha256=request_sha256,
            )
            return
        if state == "prepared" and operation == "workspace_commit":
            _journaled_workspace_commit(
                authority,
                workspace,
                arguments,
                journal=journal,
                request_sha256=request_sha256,
                state_dir=state_dir,
            )
            return
        if state == "running" and operation == "workspace_command":
            if not isinstance(details, dict):
                raise CompanyScribeAdmissionError(
                    "running command recovery details are malformed"
                )
            if frozenset(details) == {"request_arguments"}:
                pass
            elif frozenset(details) in ({
                "request_arguments", "guardian_pid",
                "guardian_start_fingerprint", "deadline_monotonic_ns",
            }, {
                "request_arguments", "guardian_pid",
                "guardian_start_fingerprint", "deadline_monotonic_ns",
                "child_pid", "child_pgid", "child_start_fingerprint",
            }):
                guardian_pid = details.get("guardian_pid")
                guardian_start = details.get(
                    "guardian_start_fingerprint",
                )
                guardian_deadline = details.get("deadline_monotonic_ns")
                if (
                    not isinstance(guardian_pid, int)
                    or isinstance(guardian_pid, bool)
                    or not isinstance(guardian_start, str)
                    or not isinstance(guardian_deadline, int)
                    or isinstance(guardian_deadline, bool)
                ):
                    raise CompanyScribeAdmissionError(
                        "running command guardian identity is malformed"
                    )
                cleanup_errors: list[str] = []
                child_cleanup_error: str | None = None
                child_identity: tuple[int, int, str] | None = None
                if "child_pid" in details:
                    child_pid = details.get("child_pid")
                    child_pgid = details.get("child_pgid")
                    child_start = details.get(
                        "child_start_fingerprint",
                    )
                    if (
                        not isinstance(child_pid, int)
                        or isinstance(child_pid, bool)
                        or not isinstance(child_pgid, int)
                        or isinstance(child_pgid, bool)
                        or child_pgid != child_pid
                        or not isinstance(child_start, str)
                        or not child_start
                    ):
                        raise CompanyScribeAdmissionError(
                            "running command child identity is malformed"
                        )
                    child_identity = (child_pid, child_pgid, child_start)
                    try:
                        _terminate_exact_command_child(
                            pid=child_pid,
                            pgid=child_pgid,
                            start_fingerprint=child_start,
                        )
                    except BaseException as exc:
                        child_cleanup_error = str(exc)
                try:
                    _terminate_exact_command_guardian(
                        pid=guardian_pid,
                        start_fingerprint=guardian_start,
                        deadline_monotonic_ns=guardian_deadline,
                    )
                except BaseException as exc:
                    cleanup_errors.append(f"guardian: {exc}")
                if child_cleanup_error is not None and child_identity is not None:
                    try:
                        _terminate_exact_command_child(
                            pid=child_identity[0],
                            pgid=child_identity[1],
                            start_fingerprint=child_identity[2],
                        )
                    except BaseException as exc:
                        cleanup_errors.append(
                            "child: " + child_cleanup_error
                            + f"; retry after guardian cleanup: {exc}"
                        )
                if cleanup_errors:
                    raise _CommandCleanupUncertain(
                        "running command recovery could not prove every "
                        "identity absent: " + "; ".join(cleanup_errors)
                    )
            else:
                raise CompanyScribeAdmissionError(
                    "running command recovery details are malformed"
                )
            authority.check()
            with journal.locked(
                effect_id=effect_id,
                request_sha256=request_sha256,
                operation=operation,
            ) as (current, write):
                if current != record:
                    raise CompanyScribeAdmissionError(
                        "running command recovery state changed"
                    )
                write({
                    "state": "failed",
                    "details": details,
                    "error": (
                        "prior broker ended while the sandboxed command was "
                        "running; the guardian fenced canonical publication"
                    ),
                })
            return
        if state == "publishing" and operation == "workspace_command":
            with journal.locked(
                effect_id=effect_id,
                request_sha256=request_sha256,
                operation=operation,
            ) as (current, write):
                if current is None or current.get("state") != "publishing":
                    raise CompanyScribeAdmissionError(
                        "command publication recovery state changed"
                    )
                command_supervisor._publish_recovery(current, write)
            return
    raise CompanyScribeAdmissionError(
        "effect journal contains an unsupported non-channel recovery state"
    )


def _git_environment(
    state_dir: Path,
    *,
    index: Path | None = None,
) -> dict[str, str]:
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(state_dir),
        "TMPDIR": str(state_dir),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "0",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_PAGER": "cat",
    }
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    return environment


def _bound_git_environment(
    environment: Mapping[str, str],
    *,
    workspace: Path,
    binding: Mapping[str, str],
    object_dir: Path | None = None,
) -> dict[str, str]:
    result = {
        **environment,
        "GIT_DIR": binding["git_dir"],
        "GIT_COMMON_DIR": binding["git_common_dir"],
        "GIT_WORK_TREE": str(workspace),
    }
    if object_dir is not None:
        result["GIT_OBJECT_DIRECTORY"] = str(object_dir)
        result["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(
            Path(binding["git_common_dir"]) / "objects"
        )
    return result


def _git_argv(repo: Path, args: list[str]) -> list[str]:
    return [
        "/usr/bin/git",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", "core.sparseCheckout=false",
        "-c", "core.sparseCheckoutCone=false",
        "-c", "index.sparse=false",
        "-c", "commit.gpgSign=false",
        "-c", "tag.gpgSign=false",
        "-c", "core.attributesFile=/dev/null",
        "-c", "core.excludesFile=/dev/null",
        "-c", "core.autocrlf=false",
        "-c", "core.bare=false",
        "-c", f"core.worktree={repo}",
        "-C", str(repo),
        *args,
    ]


def _git_command(
    repo: Path,
    args: list[str],
    *,
    env: Mapping[str, str],
    stdin: str | None = None,
) -> str:
    result = subprocess.run(
        _git_argv(repo, args),
        input=stdin,
        env=dict(env),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise CompanyScribeAdmissionError(
            f"typed workspace Git operation failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _git_binary_command(
    repo: Path,
    args: list[str],
    *,
    env: Mapping[str, str],
    stdin: bytes | None = None,
) -> bytes:
    result = subprocess.run(
        _git_argv(repo, args),
        input=stdin,
        env=dict(env),
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise CompanyScribeAdmissionError(
            "typed workspace Git operation failed: "
            + result.stderr.decode("utf-8", errors="replace").strip()
        )
    return result.stdout


def _safe_git_directory(path: Path, *, field: str) -> Path:
    candidate = Path(os.path.abspath(path))
    current = Path(candidate.anchor)
    try:
        for component in candidate.parts[1:]:
            current /= component
            if current.is_symlink():
                raise CompanyScribeAdmissionError(
                    f"{field} contains a linked ancestor"
                )
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            f"{field} ancestry is unreadable"
        ) from exc
    resolved = candidate.resolve(strict=True)
    info = resolved.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise CompanyScribeAdmissionError(f"{field} is linked or unsafe")
    return resolved


def _git_repository_binding(
    workspace: Path,
    *,
    env: Mapping[str, str],
) -> dict[str, str]:
    entry = workspace / ".git"
    try:
        entry_info = entry.lstat()
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "workspace Git control plane is absent"
        ) from exc
    if (
        entry.is_symlink()
        or entry_info.st_uid != os.getuid()
        or stat.S_IMODE(entry_info.st_mode) & 0o022
        or not (
            stat.S_ISREG(entry_info.st_mode)
            or stat.S_ISDIR(entry_info.st_mode)
        )
        or (
            stat.S_ISREG(entry_info.st_mode)
            and entry_info.st_nlink != 1
        )
    ):
        raise CompanyScribeAdmissionError(
            "workspace Git control plane is linked or unsafe"
        )
    git_dir = _safe_git_directory(
        Path(_git_command(
            workspace, ["rev-parse", "--absolute-git-dir"], env=env,
        )),
        field="workspace Git directory",
    )
    common_value = Path(_git_command(
        workspace, ["rev-parse", "--git-common-dir"], env=env,
    ))
    if not common_value.is_absolute():
        common_value = workspace / common_value
    common_dir = _safe_git_directory(
        common_value, field="workspace Git common directory",
    )
    if stat.S_ISDIR(entry_info.st_mode):
        if entry.resolve(strict=True) != git_dir or git_dir != common_dir:
            raise CompanyScribeAdmissionError(
                "workspace Git directory identity is ambiguous"
            )
    else:
        descriptor = os.open(
            entry, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            if entry_info.st_size > 4096:
                raise CompanyScribeAdmissionError(
                    "workspace Git pointer is oversized"
                )
            pointer = os.read(descriptor, 4097).decode("utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise CompanyScribeAdmissionError(
                "workspace Git pointer is unreadable"
            ) from exc
        finally:
            os.close(descriptor)
        if not pointer.startswith("gitdir: "):
            raise CompanyScribeAdmissionError(
                "workspace Git pointer is malformed"
            )
        pointer_value = Path(pointer[8:])
        if not pointer_value.is_absolute():
            pointer_value = workspace / pointer_value
        if (
            pointer_value.resolve(strict=True) != git_dir
            or git_dir.parent != common_dir / "worktrees"
        ):
            raise CompanyScribeAdmissionError(
                "workspace Git worktree indirection is not exact"
            )
    if _git_command(
        workspace, ["rev-parse", "--show-object-format"], env=env,
    ) != "sha1":
        raise CompanyScribeAdmissionError(
            "typed workspace commit requires the reviewed SHA-1 object format"
        )
    bound_ref = _git_command(
        workspace, ["symbolic-ref", "-q", "HEAD"], env=env,
    )
    if (
        not bound_ref.startswith("refs/heads/")
        or any(ord(char) < 32 or ord(char) == 127 for char in bound_ref)
    ):
        raise CompanyScribeAdmissionError(
            "typed workspace commit requires one exact local branch"
        )
    _git_command(
        workspace, ["check-ref-format", bound_ref], env=env,
    )
    return {
        "git_dir": str(git_dir),
        "git_common_dir": str(common_dir),
        "bound_ref": bound_ref,
    }


def _git_tree_entry(
    workspace: Path,
    commit: str,
    path: str,
    *,
    env: Mapping[str, str],
) -> tuple[str, str] | None:
    raw = _git_binary_command(
        workspace,
        ["ls-tree", "-z", commit, "--", path],
        env=env,
    )
    if not raw:
        return None
    rows = [row for row in raw.split(b"\0") if row]
    if len(rows) != 1:
        raise CompanyScribeAdmissionError(
            "typed workspace path has an ambiguous base-tree identity"
        )
    try:
        metadata, observed_path = rows[0].split(b"\t", 1)
        mode, kind, _object_id = metadata.decode("ascii").split(" ", 2)
        decoded_path = observed_path.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "typed workspace base-tree entry is malformed"
        ) from exc
    if decoded_path != path:
        raise CompanyScribeAdmissionError(
            "typed workspace base-tree path changed identity"
        )
    return mode, kind


def _verify_prepared_commit_identity(
    workspace: Path,
    details: Mapping[str, object],
    *,
    env: Mapping[str, str],
    effect_id: str,
    request_arguments: Mapping[str, object],
) -> None:
    commit = str(details["new_commit"])
    tree = _git_command(
        workspace, ["rev-parse", "--verify", f"{commit}^{{tree}}"], env=env,
    )
    parents = _git_command(
        workspace, ["rev-list", "--parents", "-n", "1", commit], env=env,
    ).split()
    try:
        raw_commit = _git_binary_command(
            workspace, ["cat-file", "commit", commit], env=env,
        ).decode("utf-8")
        headers, commit_message = raw_commit.split("\n\n", 1)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CompanyScribeAdmissionError(
            "prepared commit object is malformed"
        ) from exc
    header_lines = headers.splitlines()
    expected_message = (
        str(request_arguments.get("message")).rstrip()
        + "\n\nCompany-Scribe-Effect-ID: " + effect_id
    )
    if (
        tree != details["tree"]
        or parents != [commit, str(details["old_head"])]
        or not any(
            line.startswith("author Mise Scribe <scribe@localhost> ")
            for line in header_lines
        )
        or not any(
            line.startswith("committer Mise Scribe <scribe@localhost> ")
            for line in header_lines
        )
        or commit_message.rstrip("\n") != expected_message
    ):
        raise CompanyScribeAdmissionError(
            "prepared commit parent, tree, author, or message identity changed"
        )


def _git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _private_git_object_inventory(
    object_dir: Path,
) -> list[dict[str, object]]:
    root_info = object_dir.lstat()
    if (
        object_dir.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != os.getuid()
        or stat.S_IMODE(root_info.st_mode) & 0o022
    ):
        raise CompanyScribeAdmissionError(
            "typed workspace private object directory is unsafe"
        )
    inventory: list[dict[str, object]] = []
    for fanout in sorted(object_dir.iterdir()):
        if (
            len(fanout.name) != 2
            or any(char not in "0123456789abcdef" for char in fanout.name)
        ):
            raise CompanyScribeAdmissionError(
                "typed workspace private object fanout is not closed"
            )
        fanout_info = fanout.lstat()
        if (
            fanout.is_symlink()
            or not stat.S_ISDIR(fanout_info.st_mode)
            or fanout_info.st_uid != os.getuid()
            or stat.S_IMODE(fanout_info.st_mode) & 0o022
        ):
            raise CompanyScribeAdmissionError(
                "typed workspace private object fanout is unsafe"
            )
        for path in sorted(fanout.iterdir()):
            if (
                len(path.name) != 38
                or any(char not in "0123456789abcdef" for char in path.name)
            ):
                raise CompanyScribeAdmissionError(
                    "typed workspace private object name is not closed"
                )
            info = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o022
                or info.st_size > 16 * 1024 * 1024
            ):
                raise CompanyScribeAdmissionError(
                    "typed workspace private object is unsafe"
                )
            data, _ = _read_regular_beneath(
                object_dir,
                PurePosixPath(f"{fanout.name}/{path.name}"),
                maximum=16 * 1024 * 1024,
            )
            try:
                inflated = zlib.decompress(data)
                header, body = inflated.split(b"\0", 1)
                kind, size_value = header.split(b" ", 1)
                size = int(size_value)
            except (ValueError, zlib.error) as exc:
                raise CompanyScribeAdmissionError(
                    "typed workspace private object is malformed"
                ) from exc
            object_id = fanout.name + path.name
            if (
                kind not in {b"blob", b"tree", b"commit"}
                or size != len(body)
                or hashlib.sha1(
                    inflated, usedforsecurity=False,
                ).hexdigest() != object_id
            ):
                raise CompanyScribeAdmissionError(
                    "typed workspace private object identity changed"
                )
            inventory.append({
                "path": f"{fanout.name}/{path.name}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            })
    if not inventory:
        raise CompanyScribeAdmissionError(
            "typed workspace private object inventory is empty"
        )
    return inventory


def _fsync_private_git_objects(object_dir: Path) -> None:
    for fanout in sorted(object_dir.iterdir()):
        if fanout.is_symlink() or not fanout.is_dir():
            raise CompanyScribeAdmissionError(
                "typed workspace private object fanout is unsafe"
            )
        for path in sorted(fanout.iterdir()):
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        _fsync_directory(fanout)
    _fsync_directory(object_dir)
    _fsync_directory(object_dir.parent)


def _publish_private_git_objects(
    authority: _Authority,
    *,
    common_dir: Path,
    object_dir: Path,
    expected_inventory: object,
) -> None:
    observed_inventory = _private_git_object_inventory(object_dir)
    if observed_inventory != expected_inventory:
        raise CompanyScribeAdmissionError(
            "prepared private Git object inventory changed"
        )
    canonical_root = _safe_git_directory(
        common_dir / "objects", field="workspace Git object directory",
    )
    for record in observed_inventory:
        relative = PurePosixPath(str(record["path"]))
        data, _ = _read_regular_beneath(
            object_dir, relative, maximum=16 * 1024 * 1024,
        )
        fanout = canonical_root / relative.parts[0]
        try:
            fanout_info = fanout.lstat()
        except FileNotFoundError:
            authority.check()
            try:
                os.mkdir(fanout, 0o755)
            except FileExistsError:
                fanout_info = fanout.lstat()
            else:
                directory_fd = os.open(canonical_root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                fanout_info = fanout.lstat()
        if (
            fanout.is_symlink()
            or not stat.S_ISDIR(fanout_info.st_mode)
            or fanout_info.st_uid != os.getuid()
            or stat.S_IMODE(fanout_info.st_mode) & 0o022
        ):
            raise CompanyScribeAdmissionError(
                "workspace Git object fanout is unsafe"
            )
        target = fanout / relative.parts[1]
        try:
            descriptor = os.open(
                target,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            authority.check()
            try:
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o444,
                )
            except FileExistsError:
                descriptor = os.open(
                    target,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
            else:
                try:
                    remaining = data
                    while remaining:
                        written = os.write(descriptor, remaining)
                        if written <= 0:
                            raise CompanyScribeAdmissionError(
                                "workspace Git object publish made no progress"
                            )
                        remaining = remaining[written:]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                directory_fd = os.open(fanout, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                continue
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise CompanyScribeAdmissionError(
                    "workspace Git object target is unsafe"
                )
            existing = b""
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                existing += chunk
                if len(existing) > len(data):
                    break
            if existing != data:
                raise CompanyScribeAdmissionError(
                    "workspace Git object identity conflicts"
                )
        finally:
            os.close(descriptor)


def _journal_workspace_inventory(
    journal: MutationJournal,
) -> tuple[set[PurePosixPath], set[PurePosixPath]]:
    files: set[PurePosixPath] = set()
    directories: set[PurePosixPath] = set()
    for record in journal.records():
        if record.get("state") != "completed":
            continue
        operation = record.get("operation")
        result = record.get("result")
        if not isinstance(result, dict):
            continue
        raw_files: list[object] = []
        raw_directories: list[object] = []
        if operation in {"workspace_write", "workspace_edit"}:
            raw_files.append(result.get("path"))
        elif operation == "workspace_command":
            published = result.get("published_paths", [])
            if not isinstance(published, list):
                raise CompanyScribeAdmissionError(
                    "completed command publication inventory is malformed"
                )
            raw_files.extend(published)
        elif operation == "workspace_mkdir":
            raw_directories.append(result.get("path"))
        for value in raw_files:
            files.add(_relative_path(
                value, field="authorized workspace file",
            ))
        for value in raw_directories:
            directories.add(_relative_path(
                value, field="authorized workspace directory",
            ))
    return files, directories


def _safe_non_git_inventory(
    workspace: Path,
) -> tuple[set[PurePosixPath], set[PurePosixPath]]:
    """Test-only compatibility inventory for a workspace with no Git plane."""
    files: set[PurePosixPath] = set()
    directories: set[PurePosixPath] = set()
    pending: list[tuple[Path, PurePosixPath]] = [
        (workspace, PurePosixPath("."))
    ]
    while pending:
        directory, relative_dir = pending.pop()
        for entry in os.scandir(directory):
            relative = (
                PurePosixPath(entry.name)
                if relative_dir == PurePosixPath(".")
                else relative_dir / entry.name
            )
            if any(part.casefold() == ".git" for part in relative.parts):
                continue
            if _protected_channel_runtime_state(relative):
                continue
            info = entry.stat(follow_symlinks=False)
            if (
                info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise CompanyScribeAdmissionError(
                    "workspace snapshot contains an unsafe owner or mode"
                )
            if stat.S_ISDIR(info.st_mode):
                if entry.is_symlink():
                    raise CompanyScribeAdmissionError(
                        "workspace snapshot contains a linked directory"
                    )
                directories.add(relative)
                pending.append((Path(entry.path), relative))
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                files.add(relative)
            else:
                raise CompanyScribeAdmissionError(
                    "workspace snapshot contains a link or special file"
                )
    return files, directories


def _workspace_snapshot_inventory(
    workspace: Path,
    *,
    journal: MutationJournal,
    state_dir: Path,
) -> tuple[tuple[PurePosixPath, ...], tuple[PurePosixPath, ...]]:
    if not (workspace / ".git").exists():
        files, directories = _safe_non_git_inventory(workspace)
    else:
        discovery = _git_environment(state_dir)
        binding = _git_repository_binding(workspace, env=discovery)
        environment = _bound_git_environment(
            discovery, workspace=workspace, binding=binding,
        )
        raw = _git_binary_command(
            workspace, ["ls-files", "-z", "--cached"], env=environment,
        )
        files = set()
        directories: set[PurePosixPath] = set()
        for value in raw.split(b"\0"):
            if not value:
                continue
            try:
                text = value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CompanyScribeAdmissionError(
                    "tracked workspace path is not UTF-8"
                ) from exc
            candidate = PurePosixPath(text)
            if _protected_channel_runtime_state(candidate):
                continue
            relative = _relative_path(text, field="tracked workspace path")
            try:
                info = workspace.joinpath(*relative.parts).lstat()
            except FileNotFoundError:
                files.add(relative)
            else:
                # git ls-files lists symlinks. Sealed snapshot reads regular
                # files only; treating a link as a publish target made every
                # workspace_command, including /usr/bin/true, fail closed.
                if stat.S_ISLNK(info.st_mode):
                    continue
                files.add(relative)
            for parent in relative.parents:
                if parent != PurePosixPath("."):
                    directories.add(parent)
        journal_files, journal_directories = _journal_workspace_inventory(
            journal,
        )
        files.update(journal_files)
        directories.update(journal_directories)
        for relative in journal_files:
            for parent in relative.parents:
                if parent != PurePosixPath("."):
                    directories.add(parent)
    return (
        tuple(sorted(files, key=lambda item: item.as_posix())),
        tuple(sorted(directories, key=lambda item: item.as_posix())),
    )


def _workspace_read(
    authority: _Authority,
    workspace: Path,
    arguments: Mapping[str, object],
    *,
    journal: MutationJournal,
    state_dir: Path,
) -> dict[str, object]:
    relative = _relative_path(arguments.get("path"), field="read path")
    start_line = arguments.get("start_line", 1)
    line_count = arguments.get("line_count", 500)
    if (
        isinstance(start_line, bool)
        or not isinstance(start_line, int)
        or start_line < 1
        or isinstance(line_count, bool)
        or not isinstance(line_count, int)
        or line_count < 1
        or line_count > 2000
    ):
        raise CompanyScribeAdmissionError(
            "workspace read line window is invalid"
        )
    files, _directories = _workspace_snapshot_inventory(
        workspace,
        journal=journal,
        state_dir=state_dir,
    )
    if relative not in set(files):
        raise CompanyScribeAdmissionError(
            "workspace read path is not tracked or broker-authorized"
        )
    authority.check()
    data, _mode = _read_regular_beneath(
        workspace, relative, maximum=_MAX_TEXT_BYTES,
    )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompanyScribeAdmissionError(
            "workspace read target is not UTF-8 text"
        ) from exc
    lines = text.splitlines(keepends=True)
    start = start_line - 1
    selected = "".join(lines[start:start + line_count])
    authority.check()
    return {
        "state": "read",
        "path": relative.as_posix(),
        "start_line": start_line,
        "line_count": len(lines[start:start + line_count]),
        "total_lines": len(lines),
        "content": selected,
    }


def _selected_workspace_state_is_exact(
    workspace: Path,
    selected: object,
) -> bool:
    if not isinstance(selected, list) or not selected:
        return False
    seen: set[str] = set()
    for record in selected:
        if not isinstance(record, dict):
            return False
        path = record.get("path")
        kind = record.get("kind")
        try:
            relative = _relative_path(path, field="prepared commit path")
        except CompanyScribeAdmissionError:
            return False
        if relative.as_posix() in seen:
            return False
        seen.add(relative.as_posix())
        if kind == "deletion":
            if frozenset(record) != {"path", "kind"}:
                return False
            try:
                if not _path_absent_beneath(workspace, relative):
                    return False
            except CompanyScribeAdmissionError:
                return False
            continue
        if kind != "file" or frozenset(record) != {
            "path", "kind", "blob_sha", "mode",
        }:
            return False
        try:
            data, mode = _read_regular_beneath(
                workspace, relative, maximum=_MAX_TEXT_BYTES,
            )
        except CompanyScribeAdmissionError:
            return False
        observed_mode = "100755" if mode & 0o111 else "100644"
        if (
            record.get("mode") != observed_mode
            or record.get("blob_sha") != _git_blob_sha1(data)
        ):
            return False
    return True


def _journaled_workspace_commit(
    authority: _Authority,
    workspace: Path,
    arguments: Mapping[str, object],
    *,
    journal: MutationJournal,
    request_sha256: str,
    state_dir: Path,
) -> dict[str, object]:
    effect_id = _validate_effect_id(arguments.get("effect_id"))
    request_arguments = _closed_request_arguments(
        arguments,
        allowed=frozenset({"effect_id", "paths", "message"}),
        required=frozenset({"effect_id", "paths", "message"}),
        operation="workspace_commit",
    )
    message = arguments.get("message")
    paths_value = arguments.get("paths")
    if not isinstance(message, str) or not message.strip() or len(message.encode("utf-8")) > 16_384:
        raise CompanyScribeAdmissionError("commit message must be bounded non-empty text")
    if not isinstance(paths_value, list) or not paths_value or len(paths_value) > 128:
        raise CompanyScribeAdmissionError("commit paths must be a bounded non-empty array")
    relative_paths = tuple(
        _relative_path(value, field="paths") for value in paths_value
    )
    paths = tuple(path.as_posix() for path in relative_paths)
    if len(paths) != len(set(paths)):
        raise CompanyScribeAdmissionError("commit paths are repeated or unsafe")
    index_dir = _ensure_private_state_directory(
        state_dir, "git-indexes",
    )
    stem = hashlib.sha256(effect_id.encode("utf-8")).hexdigest()
    index = index_dir / f"{stem}.index"
    object_root = _ensure_private_state_directory(
        state_dir, "git-objects",
    )
    object_dir = object_root / stem
    discovery_env = _git_environment(state_dir)
    with journal.locked(
        effect_id=effect_id,
        request_sha256=request_sha256,
        operation="workspace_commit",
    ) as (record, write):
        if record is not None:
            replay = _replayed_result(record)
            if replay is not None:
                return replay
            details = record.get("details")
            if not isinstance(details, dict):
                raise CompanyScribeAdmissionError("prepared commit lost its recovery details")
        else:
            authority.check()
            binding = _git_repository_binding(workspace, env=discovery_env)
            real_git_env = _bound_git_environment(
                discovery_env,
                workspace=workspace,
                binding=binding,
            )
            old_head = _git_command(
                workspace,
                ["rev-parse", "--verify", f"{binding['bound_ref']}^{{commit}}"],
                env=real_git_env,
            )
            if _git_command(
                workspace, ["rev-parse", "--verify", "HEAD^{commit}"],
                env=real_git_env,
            ) != old_head:
                raise CompanyScribeAdmissionError(
                    "workspace HEAD is not the exact bound branch"
                )
            old_tree = _git_command(
                workspace, ["rev-parse", f"{old_head}^{{tree}}"],
                env=real_git_env,
            )
            if _git_command(
                workspace, ["write-tree"], env=real_git_env,
            ) != old_tree:
                raise CompanyScribeAdmissionError(
                    "typed workspace commit refuses a pre-existing staged "
                    "or split-index state"
                )
            try:
                index.unlink()
            except FileNotFoundError:
                pass
            if object_dir.exists() or object_dir.is_symlink():
                object_info = object_dir.lstat()
                if (
                    object_dir.is_symlink()
                    or not stat.S_ISDIR(object_info.st_mode)
                    or object_info.st_uid != os.getuid()
                    or stat.S_IMODE(object_info.st_mode) != 0o700
                ):
                    raise CompanyScribeAdmissionError(
                        "typed workspace private object directory is unsafe"
                    )
                shutil.rmtree(object_dir)
                _fsync_directory(object_root)
            object_dir = _ensure_private_state_directory(
                object_root, stem,
            )
            git_env = {
                **_bound_git_environment(
                    _git_environment(state_dir, index=index),
                    workspace=workspace,
                    binding=binding,
                    object_dir=object_dir,
                ),
                "GIT_AUTHOR_NAME": "Mise Scribe",
                "GIT_AUTHOR_EMAIL": "scribe@localhost",
                "GIT_COMMITTER_NAME": "Mise Scribe",
                "GIT_COMMITTER_EMAIL": "scribe@localhost",
            }
            authority.check()
            _git_command(workspace, ["read-tree", old_head], env=git_env)
            selected: list[dict[str, str]] = []
            for relative_path, path in zip(relative_paths, paths, strict=True):
                try:
                    data, mode = _read_regular_beneath(
                        workspace, relative_path, maximum=_MAX_TEXT_BYTES,
                    )
                except CompanyScribeAdmissionError:
                    if not _path_absent_beneath(workspace, relative_path):
                        raise
                    base = _git_tree_entry(
                        workspace, old_head, path, env=git_env,
                    )
                    if base is None or base not in {
                        ("100644", "blob"), ("100755", "blob"),
                    }:
                        raise CompanyScribeAdmissionError(
                            "typed workspace deletion must name one tracked "
                            "regular file"
                        )
                    _git_command(
                        workspace,
                        ["update-index", "--force-remove", "--", path],
                        env=git_env,
                    )
                    selected.append({"path": path, "kind": "deletion"})
                    continue
                authority.check()
                object_id = _git_binary_command(
                    workspace,
                    ["hash-object", "-w", "--stdin", "--no-filters"],
                    env=git_env,
                    stdin=data,
                ).decode("ascii").strip()
                if len(object_id) != 40 or any(
                    char not in "0123456789abcdef" for char in object_id
                ):
                    raise CompanyScribeAdmissionError(
                        "typed workspace blob identity is malformed"
                    )
                git_mode = "100755" if mode & 0o111 else "100644"
                _git_command(
                    workspace,
                    ["update-index", "--add", "--cacheinfo", git_mode, object_id, path],
                    env=git_env,
                )
                selected.append({
                    "path": path,
                    "kind": "file",
                    "blob_sha": object_id,
                    "mode": git_mode,
                })
            authority.check()
            tree = _git_command(workspace, ["write-tree"], env=git_env)
            if tree == old_tree:
                raise CompanyScribeAdmissionError("typed workspace commit has no selected changes")
            authority.check()
            new_commit = _git_command(
                workspace,
                ["commit-tree", tree, "-p", old_head],
                env=git_env,
                stdin=message.rstrip() + "\n\nCompany-Scribe-Effect-ID: " + effect_id + "\n",
            )
            private_objects = _private_git_object_inventory(object_dir)
            _fsync_private_git_objects(object_dir)
            if _private_git_object_inventory(object_dir) != private_objects:
                raise CompanyScribeAdmissionError(
                    "typed workspace private objects changed during durability"
                )
            details = {
                "old_head": old_head,
                "old_tree": old_tree,
                "new_commit": new_commit,
                "tree": tree,
                "paths": list(paths),
                "selected": selected,
                "object_dir": str(object_dir),
                "private_objects": private_objects,
                "request_arguments": request_arguments,
                **binding,
            }
            write({"state": "prepared", "details": details})
        required_detail_keys = {
            "old_head", "old_tree", "new_commit", "tree", "paths", "selected",
            "git_dir", "git_common_dir", "bound_ref", "object_dir",
            "private_objects", "request_arguments",
        }
        if frozenset(details) != required_detail_keys:
            raise CompanyScribeAdmissionError(
                "prepared commit recovery binding is incomplete"
            )
        for field in ("old_head", "old_tree", "new_commit", "tree"):
            value = details.get(field)
            if (
                not isinstance(value, str)
                or len(value) != 40
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise CompanyScribeAdmissionError(
                    "prepared commit object identity is malformed"
                )
        if (
            details.get("paths") != list(paths)
            or details.get("request_arguments") != request_arguments
            or _effect_request_sha256(
                "effect", "workspace_commit",
                {"effect_id": effect_id, **request_arguments},
            ) != request_sha256
            or details.get("object_dir") != str(object_dir)
            or not isinstance(details.get("git_dir"), str)
            or not isinstance(details.get("git_common_dir"), str)
            or not isinstance(details.get("bound_ref"), str)
        ):
            raise CompanyScribeAdmissionError(
                "prepared commit request or repository binding changed"
            )
        observed_binding = _git_repository_binding(
            workspace, env=discovery_env,
        )
        if any(
            observed_binding[key] != details.get(key)
            for key in ("git_dir", "git_common_dir", "bound_ref")
        ):
            raise CompanyScribeAdmissionError(
                "prepared commit Git control plane changed identity"
            )
        saved_binding = {
            key: str(details[key])
            for key in ("git_dir", "git_common_dir", "bound_ref")
        }
        real_git_env = _bound_git_environment(
            discovery_env,
            workspace=workspace,
            binding=saved_binding,
        )
        git_env = _bound_git_environment(
            _git_environment(state_dir, index=index),
            workspace=workspace,
            binding=saved_binding,
            object_dir=object_dir,
        )
        if _private_git_object_inventory(
            object_dir,
        ) != details.get("private_objects"):
            raise CompanyScribeAdmissionError(
                "prepared private Git objects changed before replay"
            )
        current = _git_command(
            workspace,
            ["rev-parse", "--verify", f"{details['bound_ref']}^{{commit}}"],
            env=real_git_env,
        )
        if _git_command(
            workspace, ["rev-parse", "--verify", "HEAD^{commit}"],
            env=real_git_env,
        ) != current:
            raise CompanyScribeAdmissionError(
                "prepared commit no longer owns workspace HEAD"
            )
        if current == details.get("new_commit"):
            _verify_prepared_commit_identity(
                workspace,
                details,
                env=real_git_env,
                effect_id=effect_id,
                request_arguments=request_arguments,
            )
            authority.check()
        elif current == details.get("old_head"):
            if (
                _git_command(workspace, ["write-tree"], env=real_git_env)
                != details["old_tree"]
                or not _selected_workspace_state_is_exact(
                    workspace, details["selected"],
                )
            ):
                raise CompanyScribeAdmissionError(
                    "prepared commit workspace or real index changed before CAS"
                )
            _publish_private_git_objects(
                authority,
                common_dir=Path(str(details["git_common_dir"])),
                object_dir=object_dir,
                expected_inventory=details["private_objects"],
            )
            _verify_prepared_commit_identity(
                workspace,
                details,
                env=real_git_env,
                effect_id=effect_id,
                request_arguments=request_arguments,
            )
            authority.check()
            if (
                _git_repository_binding(
                    workspace, env=discovery_env,
                ) != saved_binding
                or
                _git_command(
                    workspace,
                    [
                        "rev-parse", "--verify",
                        f"{details['bound_ref']}^{{commit}}",
                    ],
                    env=real_git_env,
                ) != details["old_head"]
                or _git_command(
                    workspace, ["rev-parse", "--verify", "HEAD^{commit}"],
                    env=real_git_env,
                ) != details["old_head"]
                or _git_command(
                    workspace, ["write-tree"], env=real_git_env,
                ) != details["old_tree"]
                or not _selected_workspace_state_is_exact(
                    workspace, details["selected"],
                )
            ):
                raise CompanyScribeAdmissionError(
                    "prepared commit changed during final authorization"
                )
            _git_command(
                workspace,
                [
                    "update-ref", "-m", f"company-scribe:{effect_id}",
                    str(details["bound_ref"]),
                    str(details["new_commit"]), str(details["old_head"]),
                ],
                env=real_git_env,
            )
        else:
            raise CompanyScribeAdmissionError(
                "prepared commit conflicts with the current workspace HEAD"
            )
        if (
            _git_command(
                workspace,
                ["rev-parse", "--verify", f"{details['bound_ref']}^{{commit}}"],
                env=real_git_env,
            ) != details["new_commit"]
            or _git_command(
                workspace, ["rev-parse", "--verify", "HEAD^{commit}"],
                env=real_git_env,
            ) != details["new_commit"]
        ):
            raise CompanyScribeAdmissionError(
                "typed workspace commit CAS did not bind the exact branch"
            )
        real_index_tree = _git_command(
            workspace, ["write-tree"], env=real_git_env,
        )
        if real_index_tree == details["old_tree"]:
            authority.check()
            _git_command(
                workspace,
                ["read-tree", "--reset", str(details["new_commit"])],
                env=real_git_env,
            )
        elif real_index_tree != details["tree"]:
            raise CompanyScribeAdmissionError(
                "typed workspace commit refuses to overwrite a changed real index"
            )
        if _git_command(
            workspace, ["write-tree"], env=real_git_env,
        ) != details["tree"]:
            raise CompanyScribeAdmissionError(
                "typed workspace commit did not reconcile the real index"
            )
        result = {
            "state": "committed",
            "commit_sha": details["new_commit"],
            "tree_sha": details["tree"],
            "paths": details["paths"],
        }
        write({"state": "completed", "details": details, "result": result})
        return result


_EFFECT_TOOLS: tuple[dict[str, object], ...] = (
    {
        "name": "authority_status",
        "description": "Recheck and report the exact live effect authority.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "workspace_read",
        "description": "Read one tracked or broker-authorized UTF-8 workspace file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "line_count": {
                    "type": "integer", "minimum": 1, "maximum": 2000,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_write",
        "description": "Atomically write one UTF-8 file after exact effect authorization.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "effect_id": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "executable": {"type": "boolean"},
            },
            "required": ["effect_id", "path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_edit",
        "description": "Atomically replace exact UTF-8 text after exact effect authorization.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "effect_id": {"type": "string"},
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["effect_id", "path", "old_text", "new_text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_mkdir",
        "description": "Create one workspace directory after exact effect authorization.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "effect_id": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["effect_id", "path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_command",
        "description": "Run one argv-only command under the fixed admission deadline and watchdog.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "effect_id": {"type": "string"},
                "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 300},
                "publish_paths": {
                    "type": "array", "items": {"type": "string"}, "maxItems": 1,
                },
            },
            "required": ["effect_id", "argv"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_commit",
        "description": "Create one typed Git commit and CAS-update the exact current ref.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "effect_id": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "message": {"type": "string"},
            },
            "required": ["effect_id", "paths", "message"],
            "additionalProperties": False,
        },
    },
)

_CHANNEL_TOOLS: tuple[dict[str, object], ...] = (
    {
        "name": "channel_send",
        "description": "Durably send one idempotent Scribe channel event.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "effect_id": {"type": "string"},
                "target_role": {"type": "string"},
                "content": {"type": "string"},
                "event_type": {"type": "string"},
                "meta": {"type": "object"},
            },
            "required": ["effect_id", "target_role", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "channel_reply",
        "description": "Durably acknowledge one delivered channel event.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "effect_id": {"type": "string"},
                "event_id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["effect_id", "event_id", "text"],
            "additionalProperties": False,
        },
    },
)


def _json_response(identifier: object, result: object) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": identifier, "result": result},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


_MCP_SKIP = object()
_BROKER_EVENT_SCHEMA = "mise.company-scribe-broker-event.v1"
_DAEMON_CALL_TIMEOUT_SECONDS = 5.0


def _record_broker_event(state_dir: Path, event: Mapping[str, object]) -> None:
    """Append one fail-closed auth/dispatch note. Never raise into the worker."""
    payload = {
        "schema": _BROKER_EVENT_SCHEMA,
        "recorded_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        **dict(event),
    }
    path = state_dir / "effect-broker-auth.jsonl"
    try:
        raw = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
    except OSError:
        return


def _read_mcp_message(
    stream: BinaryIO,
) -> tuple[dict[str, Any] | object | None, str]:
    """Read one MCP request: newline JSON or Content-Length framing.

    None is EOF. ``_MCP_SKIP`` is a blank or unparseable line — keep the
    socket open, same as the working channel bun server.
    """
    first = stream.readline(_MAX_MCP_LINE_BYTES + 1)
    if not first:
        return None, "jsonl"
    if first.lower().startswith(b"content-length:"):
        try:
            length = int(first.split(b":", 1)[1].strip())
        except ValueError:
            return _MCP_SKIP, "content-length"
        if length < 0 or length > _MAX_MCP_LINE_BYTES:
            return _MCP_SKIP, "content-length"
        while True:
            header = stream.readline(1024)
            if header in {b"", b"\n", b"\r\n"}:
                break
        body = stream.read(length)
        if len(body) != length:
            return _MCP_SKIP, "content-length"
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _MCP_SKIP, "content-length"
        if not isinstance(value, dict):
            return _MCP_SKIP, "content-length"
        return value, "content-length"
    if not first.strip():
        return _MCP_SKIP, "jsonl"
    if len(first) > _MAX_MCP_LINE_BYTES or not first.endswith(b"\n"):
        return _MCP_SKIP, "jsonl"
    try:
        value = json.loads(first)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _MCP_SKIP, "jsonl"
    if not isinstance(value, dict):
        return _MCP_SKIP, "jsonl"
    return value, "jsonl"


def _write_mcp_payload(
    stream: BinaryIO, payload: dict[str, Any], *, framing: str,
) -> None:
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    if framing == "content-length":
        stream.write(
            f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw
        )
    else:
        stream.write(raw + b"\n")


def _write_mcp_response(
    stream: BinaryIO, identifier: object, result: object, *, framing: str,
) -> None:
    _write_mcp_payload(
        stream,
        {"jsonrpc": "2.0", "id": identifier, "result": result},
        framing=framing,
    )


def _write_mcp_error(
    stream: BinaryIO,
    identifier: object,
    code: int,
    message: str,
    *,
    framing: str,
) -> None:
    _write_mcp_payload(
        stream,
        {
            "jsonrpc": "2.0",
            "id": identifier,
            "error": {"code": code, "message": message},
        },
        framing=framing,
    )


def _call_tool(
    authority: _Authority,
    workspace: Path,
    name: object,
    arguments: object,
    *,
    journal: MutationJournal,
    command_supervisor: CommandSupervisor,
    state_dir: Path,
) -> dict[str, object]:
    if not isinstance(arguments, dict):
        raise CompanyScribeAdmissionError("effect arguments must be an object")
    if name == "authority_status":
        binding = authority.check()
        value: dict[str, object] = {
            "state": "live",
            "admission_id": str(binding.admission_id),
            "admission_epoch": binding.admission_epoch,
            "lease_expires_at": binding.lease_expires_at.isoformat().replace("+00:00", "Z"),
            "workspace": str(workspace),
        }
    elif name == "workspace_read":
        value = _workspace_read(
            authority,
            workspace,
            arguments,
            journal=journal,
            state_dir=state_dir,
        )
    elif name in {
        "workspace_write", "workspace_edit", "workspace_mkdir",
        "workspace_command", "workspace_commit",
    }:
        with _WORKSPACE_MUTATION_LOCK:
            effect_id = _validate_effect_id(arguments.get("effect_id"))
            _assert_no_other_unresolved_nonchannel(
                journal, effect_id=effect_id,
            )
            request_sha256 = _effect_request_sha256(
                "effect", str(name), arguments,
            )
            if name == "workspace_write":
                value = _journaled_workspace_write(
                    authority, workspace, arguments, edit=False,
                    journal=journal, request_sha256=request_sha256,
                )
            elif name == "workspace_edit":
                value = _journaled_workspace_write(
                    authority, workspace, arguments, edit=True,
                    journal=journal, request_sha256=request_sha256,
                )
            elif name == "workspace_mkdir":
                value = _journaled_make_directory(
                    authority, workspace, arguments,
                    journal=journal, request_sha256=request_sha256,
                )
            elif name == "workspace_command":
                value = command_supervisor.submit(
                    effect_id=effect_id,
                    request_sha256=request_sha256,
                    arguments=arguments,
                )
            else:
                value = _journaled_workspace_commit(
                    authority, workspace, arguments,
                    journal=journal, request_sha256=request_sha256,
                    state_dir=state_dir,
                )
    else:
        raise CompanyScribeAdmissionError("unknown or unreviewed effect tool")
    return {
        "content": [{
            "type": "text",
            "text": json.dumps(value, sort_keys=True, separators=(",", ":")),
        }],
    }


def _call_channel_tool(
    authority: _Authority,
    name: object,
    arguments: object,
    *,
    journal: MutationJournal,
    channel_state_dir: Path,
    channel_shared_dir: Path,
) -> dict[str, object]:
    if not isinstance(arguments, dict):
        raise CompanyScribeAdmissionError("channel effect arguments must be an object")
    if name not in {"channel_send", "channel_reply"}:
        raise CompanyScribeAdmissionError("unknown or confused channel capability method")
    _validate_effect_id(arguments.get("effect_id"))
    request_sha256 = _effect_request_sha256("channel", str(name), arguments)
    if name == "channel_send":
        value = _journaled_channel_send(
            authority, arguments,
            journal=journal,
            request_sha256=request_sha256,
            channel_shared_dir=channel_shared_dir,
        )
    else:
        value = _journaled_channel_reply(
            authority, arguments,
            journal=journal,
            request_sha256=request_sha256,
            channel_state_dir=channel_state_dir,
        )
    return {
        "content": [{
            "type": "text",
            "text": json.dumps(value, sort_keys=True, separators=(",", ":")),
        }],
    }


def _serve_mcp(
    connection: socket.socket,
    *,
    capability: str,
    peer_gate: PeerGate,
    peer_identity: PeerIdentity,
    authority: _Authority,
    workspace: Path,
    journal: MutationJournal,
    command_supervisor: CommandSupervisor,
    state_dir: Path,
    channel_state_dir: Path,
    channel_shared_dir: Path,
    require_initialize: bool = True,
) -> None:
    stream = connection.makefile("rwb", buffering=0)
    handshake_ready = False
    # Unix-socket peers already passed PeerGate. Channel bun sends tools/call
    # with no MCP initialize on that socket. Stdio (Claude) still handshakes.
    session_initialized = not require_initialize
    while True:
        request, framing = _read_mcp_message(stream)
        if request is None:
            return
        if request is _MCP_SKIP:
            continue
        if not isinstance(request, dict):
            continue
        method = request.get("method")
        if method == "notifications/initialized":
            if handshake_ready:
                session_initialized = True
            continue
        if "id" not in request:
            continue
        peer_gate.reverify(peer_identity)
        if method == "initialize":
            params = request.get("params")
            requested = (
                params.get("protocolVersion")
                if isinstance(params, dict) else None
            )
            if not isinstance(requested, str) or not requested.startswith("2025-"):
                requested = "2025-11-25"
            result: object = {
                "protocolVersion": requested,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": f"scribe-{capability}-effects", "version": "3.0.0",
                },
            }
            handshake_ready = True
        elif not session_initialized and method != "ping":
            try:
                _write_mcp_error(
                    stream, request["id"], -32002, "Server not initialized",
                    framing=framing,
                )
                stream.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            continue
        elif method == "tools/list":
            result = {
                "tools": list(
                    _EFFECT_TOOLS if capability == "effect" else _CHANNEL_TOOLS
                ),
            }
        elif method == "tools/call":
            params = request.get("params")
            try:
                if not isinstance(params, dict):
                    raise CompanyScribeAdmissionError("effect call parameters are malformed")
                if capability == "effect":
                    result = _call_tool(
                        authority,
                        workspace,
                        params.get("name"),
                        params.get("arguments", {}),
                        journal=journal,
                        command_supervisor=command_supervisor,
                        state_dir=state_dir,
                    )
                else:
                    result = _call_channel_tool(
                        authority,
                        params.get("name"),
                        params.get("arguments", {}),
                        journal=journal,
                        channel_state_dir=channel_state_dir,
                        channel_shared_dir=channel_shared_dir,
                    )
            except (CompanyScribeAdmissionError, OSError, ValueError) as exc:
                result = {
                    "content": [{"type": "text", "text": f"effect refused: {exc}"}],
                    "isError": True,
                }
        elif method == "ping":
            result = {}
        else:
            result = {}
        try:
            _write_mcp_response(
                stream, request["id"], result, framing=framing,
            )
            stream.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The daemon-owned job and journal are independent of the client.
            # A retry receives the cached result from the stable effect_id.
            return


def _prepare_socket_path(path: Path, *, state_dir: Path) -> None:
    if not path.is_absolute() or ".." in path.parts or path.parent != state_dir:
        raise CompanyScribeAdmissionError(
            "broker sockets must be direct absolute children of the private state directory"
        )
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise CompanyScribeAdmissionError(
            f"broker socket path is occupied by an unsafe object: {path.name}"
        )
    path.unlink()


def _bind_private_socket(path: Path) -> socket.socket:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(16)
        server.setblocking(False)
        return server
    except BaseException:
        server.close()
        raise


def broker_readiness_hmac(secret: bytes, challenge: str) -> str:
    return hmac.new(
        secret,
        _canonical_bytes({
            "schema": BROKER_HEALTH_SCHEMA,
            "challenge": challenge,
            "purpose": "fresh-company-scribe-broker-readiness",
        }),
        hashlib.sha256,
    ).hexdigest()


def _serve_health(
    connection: socket.socket,
    *,
    secret: bytes,
    admitted_pid: int,
    admitted_start_fingerprint: str,
    effect_socket_path: Path,
    channel_socket_path: Path,
) -> None:
    connection.settimeout(1.0)
    if _peer_pid(connection) != admitted_pid:
        raise CompanyScribeAdmissionError(
            "broker readiness peer is not the admitted runtime"
        )
    if process_start_fingerprint(admitted_pid) != admitted_start_fingerprint:
        raise CompanyScribeAdmissionError(
            "broker readiness belongs to another admitted process epoch"
        )
    buffered = b""
    while b"\n" not in buffered and len(buffered) <= 4096:
        chunk = connection.recv(4096 - len(buffered) + 1)
        if not chunk:
            break
        buffered += chunk
    if len(buffered) > 4096 or b"\n" not in buffered:
        raise CompanyScribeAdmissionError("broker readiness request is malformed")
    raw, extra = buffered.split(b"\n", 1)
    if extra:
        raise CompanyScribeAdmissionError("broker readiness request has trailing bytes")
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "broker readiness request is not JSON"
        ) from exc
    challenge = request.get("challenge") if isinstance(request, dict) else None
    if (
        not isinstance(request, dict)
        or frozenset(request) != {"schema", "challenge"}
        or request.get("schema") != BROKER_HEALTH_SCHEMA
        or not isinstance(challenge, str)
        or len(challenge) != 64
        or any(character not in "0123456789abcdef" for character in challenge)
    ):
        raise CompanyScribeAdmissionError("broker readiness challenge is invalid")
    response = {
        "schema": BROKER_HEALTH_SCHEMA,
        "challenge": challenge,
        "response_hmac_sha256": broker_readiness_hmac(secret, challenge),
        "daemon_pid": os.getpid(),
        "effect_socket": str(effect_socket_path),
        "channel_socket": str(channel_socket_path),
        "journal_ready": True,
    }
    connection.sendall(_canonical_bytes(response) + b"\n")


def _connection_worker(
    connection: socket.socket,
    *,
    capability: str,
    peer_gate: PeerGate,
    authority: _Authority,
    workspace: Path,
    journal: MutationJournal,
    command_supervisor: CommandSupervisor,
    state_dir: Path,
    channel_state_dir: Path,
    channel_shared_dir: Path,
) -> None:
    peer_pid: int | None = None
    try:
        with connection:
            # Accepted sockets inherit the non-blocking listener on macOS.
            # A later readline() then returns empty and looks like EOF, so the
            # worker closes after one tools/call. Channel bun opens a new
            # socket per call; we do the same in proxy() and stay blocking here.
            connection.setblocking(True)
            try:
                peer_pid = _peer_pid(connection)
            except (CompanyScribeAdmissionError, OSError, ValueError):
                peer_pid = None
            try:
                identity = peer_gate.authenticate(connection)
            except (CompanyScribeAdmissionError, OSError, ValueError) as exc:
                _record_broker_event(state_dir, {
                    "event": "peer-refused",
                    "capability": capability,
                    "peer_pid": peer_pid,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                return
            _serve_mcp(
                connection,
                capability=capability,
                peer_gate=peer_gate,
                peer_identity=identity,
                authority=authority,
                workspace=workspace,
                journal=journal,
                command_supervisor=command_supervisor,
                state_dir=state_dir,
                channel_state_dir=channel_state_dir,
                channel_shared_dir=channel_shared_dir,
                require_initialize=False,
            )
    except (CompanyScribeAdmissionError, OSError, ValueError):
        return
    except Exception as exc:
        _record_broker_event(state_dir, {
            "event": "worker-crash",
            "capability": capability,
            "peer_pid": peer_pid,
            "error_type": type(exc).__name__,
            "error": "worker failed closed",
        })
        return


def serve_effect_daemon(
    effect_socket_path: Path,
    *,
    channel_socket_path: Path,
    health_socket_path: Path,
    readiness_secret: bytes,
    admitted_pid: int,
    admitted_start_fingerprint: str,
    effect_executable_path: Path,
    effect_executable_sha256: str,
    effect_expected_argv: tuple[str, ...],
    channel_executable_path: Path,
    channel_executable_sha256: str,
    channel_expected_argv: tuple[str, ...],
    binding_path: Path,
    release_package: Path,
    workspace: Path,
    database_url: str,
    channel_state_dir: Path,
    channel_shared_dir: Path,
) -> None:
    """Serve split, pinned capabilities from one persistent broker daemon."""
    if not isinstance(readiness_secret, bytes) or len(readiness_secret) != 32:
        raise CompanyScribeAdmissionError(
            "broker readiness secret must be one fresh 256-bit value"
        )
    state_dir = effect_socket_path.parent.resolve(strict=True)
    state_info = state_dir.lstat()
    if (
        not stat.S_ISDIR(state_info.st_mode)
        or state_info.st_uid != os.getuid()
        or stat.S_IMODE(state_info.st_mode) & 0o077
    ):
        raise CompanyScribeAdmissionError(
            "effect broker state directory is not private"
        )
    workspace = workspace.resolve(strict=True)
    channel_state_dir = channel_state_dir.resolve(strict=True)
    channel_shared_dir = channel_shared_dir.resolve(strict=True)
    authority = EffectAuthority(
        admitted_pid=admitted_pid,
        admitted_start_fingerprint=admitted_start_fingerprint,
        binding_path=binding_path,
        release_package=release_package,
        database_url=database_url,
    )
    try:
        journal = MutationJournal(state_dir)
        _recover_prepared_channel_effects(
            authority,
            journal=journal,
            channel_state_dir=channel_state_dir,
            channel_shared_dir=channel_shared_dir,
        )
        command_supervisor = CommandSupervisor(
            authority=authority,
            workspace=workspace,
            state_dir=state_dir,
            journal=journal,
        )
        _recover_nonchannel_effects(
            authority,
            journal=journal,
            workspace=workspace,
            state_dir=state_dir,
            command_supervisor=command_supervisor,
        )
        _assert_no_unresolved_effects(journal)
    except BaseException:
        authority.close()
        raise
    effect_gate = PeerGate(
        capability="effect",
        admitted_pid=admitted_pid,
        admitted_start_fingerprint=admitted_start_fingerprint,
        executable_path=effect_executable_path,
        executable_digest=effect_executable_sha256,
        expected_argv=effect_expected_argv,
    )
    channel_gate = PeerGate(
        capability="channel",
        admitted_pid=admitted_pid,
        admitted_start_fingerprint=admitted_start_fingerprint,
        executable_path=channel_executable_path,
        executable_digest=channel_executable_sha256,
        expected_argv=channel_expected_argv,
    )
    paths = (effect_socket_path, channel_socket_path, health_socket_path)
    for path in paths:
        _prepare_socket_path(path, state_dir=state_dir)
    servers: list[socket.socket] = []
    selector = selectors.DefaultSelector()
    try:
        for capability, path in (
            ("effect", effect_socket_path),
            ("channel", channel_socket_path),
            ("health", health_socket_path),
        ):
            server = _bind_private_socket(path)
            servers.append(server)
            selector.register(server, selectors.EVENT_READ, capability)
        while True:
            try:
                if process_start_fingerprint(admitted_pid) != admitted_start_fingerprint:
                    command_supervisor.wait_for_idle(2.0)
                    return
            except CompanyScribeAdmissionError:
                command_supervisor.wait_for_idle(2.0)
                return
            for key, _ in selector.select(timeout=0.25):
                server = key.fileobj
                if not isinstance(server, socket.socket):
                    continue
                try:
                    connection, _ = server.accept()
                except (BlockingIOError, OSError):
                    continue
                capability = str(key.data)
                if capability == "health":
                    try:
                        with connection:
                            _serve_health(
                                connection,
                                secret=readiness_secret,
                                admitted_pid=admitted_pid,
                                admitted_start_fingerprint=admitted_start_fingerprint,
                                effect_socket_path=effect_socket_path,
                                channel_socket_path=channel_socket_path,
                            )
                    except (CompanyScribeAdmissionError, OSError, ValueError):
                        pass
                    continue
                gate = effect_gate if capability == "effect" else channel_gate
                threading.Thread(
                    target=_connection_worker,
                    kwargs={
                        "connection": connection,
                        "capability": capability,
                        "peer_gate": gate,
                        "authority": authority,
                        "workspace": workspace,
                        "journal": journal,
                        "command_supervisor": command_supervisor,
                        "state_dir": state_dir,
                        "channel_state_dir": channel_state_dir,
                        "channel_shared_dir": channel_shared_dir,
                    },
                    name=f"scribe-{capability}-connection",
                    daemon=True,
                ).start()
    finally:
        command_supervisor.wait_for_idle(2.0)
        selector.close()
        for server in servers:
            server.close()
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        authority.close()


def _copy(source: BinaryIO, destination: BinaryIO) -> None:
    while True:
        data = source.read(65536)
        if not data:
            try:
                destination.flush()
            except OSError:
                pass
            return
        destination.write(data)
        destination.flush()


def _open_daemon_mcp(socket_path: Path) -> tuple[socket.socket, BinaryIO]:
    """Open the forked daemon. PeerGate is the auth; no second MCP handshake.

    Channel bun writes tools/call on this socket with no initialize. The
    previous proxy initialize here turned a PeerGate refuse into a silent
    stdio death: Claude saw CONNECTION_CLOSED instead of the refuse.
    """
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(_DAEMON_CALL_TIMEOUT_SECONDS)
    connection.connect(str(socket_path))
    return connection, connection.makefile("rwb", buffering=0)


def proxy(socket_path: Path) -> int:
    """Stdio MCP for Claude. Handshake is local; only tools/call uses the daemon.

    A byte-pump tied Claude's stdio lifetime to the daemon socket. Claude
    closes and respawns the child after initialize; that looked like
    CONNECTION_CLOSED and tools never listed. Channel never had this
    problem because bun speaks MCP on stdio itself. tools/call failures
    stay on this stdio session so Claude sees the refuse, not a dead child.
    """
    info = socket_path.parent.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise CompanyScribeAdmissionError(
            "effect broker state directory is not private"
        )
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    handshake_ready = False
    session_initialized = False
    daemon_conn: socket.socket | None = None
    daemon_stream: BinaryIO | None = None
    try:
        while True:
            request, framing = _read_mcp_message(stdin)
            if request is None:
                return 0
            if request is _MCP_SKIP or not isinstance(request, dict):
                continue
            method = request.get("method")
            if method == "notifications/initialized":
                if handshake_ready:
                    session_initialized = True
                continue
            if "id" not in request:
                continue
            if method == "initialize":
                params = request.get("params")
                requested = (
                    params.get("protocolVersion")
                    if isinstance(params, dict) else None
                )
                if (
                    not isinstance(requested, str)
                    or not requested.startswith("2025-")
                ):
                    requested = "2025-11-25"
                _write_mcp_response(
                    stdout,
                    request["id"],
                    {
                        "protocolVersion": requested,
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "scribe-effect-effects",
                            "version": "3.0.0",
                        },
                    },
                    framing=framing,
                )
                stdout.flush()
                handshake_ready = True
                continue
            if not session_initialized and method != "ping":
                _write_mcp_error(
                    stdout, request["id"], -32002, "Server not initialized",
                    framing=framing,
                )
                stdout.flush()
                continue
            if method == "tools/list":
                _write_mcp_response(
                    stdout,
                    request["id"],
                    {"tools": list(_EFFECT_TOOLS)},
                    framing=framing,
                )
                stdout.flush()
                continue
            if method == "ping":
                _write_mcp_response(
                    stdout, request["id"], {}, framing=framing,
                )
                stdout.flush()
                continue
            if method == "tools/call":
                try:
                    if daemon_stream is None:
                        daemon_conn, daemon_stream = _open_daemon_mcp(
                            socket_path,
                        )
                    _write_mcp_payload(
                        daemon_stream, request, framing="jsonl",
                    )
                    daemon_stream.flush()
                    reply, _ = _read_mcp_message(daemon_stream)
                    if not isinstance(reply, dict):
                        raise CompanyScribeAdmissionError(
                            "effect daemon closed during tools/call"
                        )
                    if "result" in reply:
                        _write_mcp_response(
                            stdout, request["id"], reply["result"],
                            framing=framing,
                        )
                    else:
                        error = reply.get("error")
                        if not isinstance(error, dict):
                            error = {
                                "code": -32000, "message": "daemon error",
                            }
                        _write_mcp_error(
                            stdout,
                            request["id"],
                            int(error.get("code", -32000)),
                            str(error.get("message", "daemon error")),
                            framing=framing,
                        )
                    stdout.flush()
                    if daemon_conn is not None:
                        try:
                            daemon_conn.close()
                        except OSError:
                            pass
                    daemon_conn = None
                    daemon_stream = None
                except (CompanyScribeAdmissionError, OSError) as exc:
                    if daemon_conn is not None:
                        try:
                            daemon_conn.close()
                        except OSError:
                            pass
                    daemon_conn = None
                    daemon_stream = None
                    _write_mcp_error(
                        stdout, request["id"], -32000, str(exc),
                        framing=framing,
                    )
                    stdout.flush()
                    try:
                        _record_broker_event(socket_path.parent, {
                            "event": "proxy-tools-call-failed",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        })
                    except OSError:
                        pass
                continue
            _write_mcp_response(
                stdout, request["id"], {}, framing=framing,
            )
            stdout.flush()
    finally:
        if daemon_conn is not None:
            daemon_conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == _COMMAND_GUARDIAN_MODE:
        return _command_guardian(values)
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy", action="store_true", required=True)
    parser.add_argument("--socket", type=Path, required=True)
    args = parser.parse_args(values)
    if not args.socket.is_absolute() or ".." in args.socket.parts:
        raise CompanyScribeAdmissionError(
            "effect broker socket must be one absolute path"
        )
    return proxy(args.socket)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CompanyScribeAdmissionError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(78) from exc
