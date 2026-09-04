"""Fail-closed process wrapper for the one writable Company Fleet Scribe."""
from __future__ import annotations
from fleet_kernel.configuration import company_id, scribe_deployment_id, authority_issuer, deployment_binding, identity_environment

import errno
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from fleet_kernel.company_scribe_admission import (
    CompanyScribeAdmissionError,
    _canonical_json,
    _git,
    _read_private_file,
    _secure_directory,
    _sha256,
    _write_pointer,
    bind_transaction,
    boot_identity_sha256,
    executable_sha256,
    host_identity_sha256,
    load_binding,
    process_start_fingerprint,
    runtime_lock,
    validate_database_route,
    verify_complete_release_package,
    verify_local_binding,
)
from fleet_kernel.company_scribe_supervisor import _load_restart_request
from scripts.company_scribe_effect_broker import (
    BROKER_HEALTH_SCHEMA,
    _git_environment,
    _git_repository_binding,
    broker_readiness_hmac,
    serve_effect_daemon,
)

_WORKSPACE_BINDING_SCHEMA = "mise.company-scribe-workspace-binding.v2"
_CURSOR_MIGRATION_SCHEMA = "mise.company-scribe-cursor-migration.v1"
_LEGACY_CURSOR_NAME = re.compile(r"^cursor_scribe_(\d+)\.json$")
_CURSOR_ANCHOR = re.compile(r"^[0-9a-f]{16}$")
_MAX_QUEUE_BYTES = 64 * 1024 * 1024
_MAX_CURSOR_BYTES = 8192
_ALLOWED_SCRIBE_TOOLS = (
    "mcp__scribe-effects__authority_status",
    "mcp__scribe-effects__workspace_read",
    "mcp__scribe-effects__workspace_write",
    "mcp__scribe-effects__workspace_edit",
    "mcp__scribe-effects__workspace_mkdir",
    "mcp__scribe-effects__workspace_command",
    "mcp__scribe-effects__workspace_commit",
    "mcp__scribe-channel-push__reply",
    "mcp__scribe-channel-push__send",
)
from fleet_kernel.identity import company_role_spec
_DEFAULT_SCRIBE_MODEL = company_role_spec("scribe").default_model
_DEFAULT_SCRIBE_EFFORT = "high"
_CLAUDE_EXECUTABLE_CANDIDATES = ()

_CHILD_ENV_EXACT = frozenset({
    "CC_SUITE_COMPANY_ID", "CC_SUITE_SCRIBE_DEPLOYMENT_ID",
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TERM",
    "COLORTERM", "LANG", "__CFBundleIdentifier",
    "XPC_FLAGS", "XPC_SERVICE_NAME",
})
_CHILD_LOCALE_ENV = frozenset({
    "LC_ALL", "LC_COLLATE", "LC_CTYPE", "LC_MESSAGES", "LC_MONETARY",
    "LC_NUMERIC", "LC_TIME", "LC_PAPER", "LC_NAME", "LC_ADDRESS",
    "LC_TELEPHONE", "LC_MEASUREMENT", "LC_IDENTIFICATION",
})
_FORBIDDEN_LIBPQ_ENV = frozenset({
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER",
    "PGPASSWORD", "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE",
    "PGOPTIONS", "PGAPPNAME", "PGSSLMODE", "PGSSLROOTCERT",
    "PGSSLCERT", "PGSSLKEY",
})


def _runtime_child_environment(
    source: dict[str, str],
) -> dict[str, str]:
    forbidden = sorted(
        key for key in source
        if (
            key == "COMPANY_SCRIBE_PROVISIONER_DATABASE_URL"
            or key in _FORBIDDEN_LIBPQ_ENV
            or (
                (key == "DATABASE_URL" or key.endswith("_DATABASE_URL"))
                and key != "COMPANY_SCRIBE_DATABASE_URL"
            )
        )
    )
    if forbidden:
        raise CompanyScribeAdmissionError(
            "writable Scribe runtime inherited a forbidden database route: "
            + ",".join(forbidden)
        )
    child = {
        key: value
        for key, value in source.items()
        if key in _CHILD_ENV_EXACT or key in _CHILD_LOCALE_ENV
    }
    child["MISE_ROLE"] = "scribe"
    child["AGENT_ROLE"] = "scribe"
    child["CCSUITE_ROLE"] = "scribe"
    child["MISE_SCRIBE_ADMITTED"] = "1"
    return child


def _env_path(name: str) -> Path:
    value = os.environ.get(name, "")
    path = Path(value)
    if not value or not path.is_absolute() or ".." in path.parts:
        raise CompanyScribeAdmissionError(
            f"{name} must be one absolute path without '..'"
        )
    return path


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CompanyScribeAdmissionError(f"{name} is required")
    return value


def _safe_external_channel_directory(*, repo: Path, path: Path) -> Path:
    """Bind the admitted channel to one safe bus outside its mutable workspace."""
    _secure_directory(path)
    resolved = path.resolve(strict=True)
    if resolved == repo or repo in resolved.parents:
        raise CompanyScribeAdmissionError(
            "writable Scribe channel bus must be outside its workspace"
        )
    return resolved


def _file_signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_stable_shared_file(
    path: Path,
    *,
    maximum_bytes: int,
) -> tuple[bytes, os.stat_result]:
    """Read one owner-controlled, non-linked shared-bus file without races."""
    _secure_directory(path.parent)
    for _attempt in range(3):
        try:
            descriptor = os.open(
                path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            raise CompanyScribeAdmissionError(
                f"legacy Scribe cursor source is unavailable: {path}"
            ) from exc
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) & 0o022
                or before.st_size > maximum_bytes
            ):
                raise CompanyScribeAdmissionError(
                    f"legacy Scribe cursor source is unsafe: {path}"
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum_bytes:
                    raise CompanyScribeAdmissionError(
                        f"legacy Scribe cursor source is oversized: {path}"
                    )
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        try:
            named = path.lstat()
        except OSError:
            continue
        if (
            _file_signature(before) == _file_signature(after)
            and _file_signature(after) == _file_signature(named)
            and total == after.st_size
        ):
            return b"".join(chunks), after
    raise CompanyScribeAdmissionError(
        f"legacy Scribe cursor source changed during migration: {path}"
    )


def _decode_shared_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw, _info = _read_stable_shared_file(
        path, maximum_bytes=_MAX_CURSOR_BYTES,
    )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            f"legacy Scribe cursor source is malformed: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise CompanyScribeAdmissionError(
            f"legacy Scribe cursor source is malformed: {path}"
        )
    return value, raw


def _complete_queue_lines(raw: bytes) -> tuple[str, ...]:
    if raw and not raw.endswith(b"\n"):
        raise CompanyScribeAdmissionError(
            "legacy Scribe queue has a torn final record"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompanyScribeAdmissionError(
            "legacy Scribe queue is not UTF-8"
        ) from exc
    return tuple(line for line in text.split("\n") if line.strip())


def _cursor_anchor(line: str) -> str:
    return hashlib.sha1(line.encode("utf-8")).hexdigest()[:16]


def _pid_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise CompanyScribeAdmissionError(
            "legacy Scribe predecessor process state is unreadable"
        ) from exc
    return True


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CompanyScribeAdmissionError(
            f"legacy Scribe {field} is malformed"
        )
    return value


def _legacy_cursor_records(
    channel_shared_dir: Path,
    *,
    queue_lines: tuple[str, ...],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    try:
        entries = sorted(channel_shared_dir.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "legacy Scribe cursor directory is unreadable"
        ) from exc
    for path in entries:
        match = _LEGACY_CURSOR_NAME.fullmatch(path.name)
        if match is None:
            continue
        value, raw = _decode_shared_json(path)
        if frozenset(value) != {"line", "pid", "role", "anchor"}:
            raise CompanyScribeAdmissionError(
                f"legacy Scribe process cursor is malformed: {path}"
            )
        pid = _nonnegative_int(value.get("pid"), field="cursor pid")
        line = _nonnegative_int(value.get("line"), field="cursor line")
        anchor = value.get("anchor")
        if (
            pid <= 1
            or pid != int(match.group(1))
            or value.get("role") != "scribe"
            or not isinstance(anchor, str)
            or line > len(queue_lines)
            or (line == 0 and anchor != "")
            or (
                line > 0
                and (
                    _CURSOR_ANCHOR.fullmatch(anchor) is None
                    or anchor != _cursor_anchor(queue_lines[line - 1])
                )
            )
        ):
            raise CompanyScribeAdmissionError(
                f"legacy Scribe process cursor is not queue-anchored: {path}"
            )
        if _pid_is_live(pid):
            raise CompanyScribeAdmissionError(
                f"legacy writable Scribe predecessor is still live: pid {pid}"
            )
        records.append({
            "path": str(path),
            "pid": pid,
            "line": line,
            "anchor": anchor,
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    return records


def _verify_cursor_migration(
    *,
    state_dir: Path,
    channel_shared_dir: Path,
) -> dict[str, object]:
    receipt_path = state_dir / "channel" / "cursor-migration.json"
    target_path = state_dir / "channel" / "cursor_scribe-shared_role.json"
    try:
        receipt = json.loads(_read_private_file(receipt_path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe cursor migration receipt is unreadable"
        ) from exc
    required = {
        "schema", "channel_shared_dir", "source_queue_path",
        "source_queue_device", "source_queue_inode", "source_queue_size",
        "source_queue_sha256", "source_complete_lines",
        "source_role_checkpoint_path", "source_role_checkpoint_sha256",
        "migrated_line", "migrated_anchor", "selected_cursor_path",
        "selected_cursor_pid", "selected_cursor_sha256",
        "target_role_checkpoint_path", "cursor_migration_sha256",
    }
    if not isinstance(receipt, dict) or frozenset(receipt) != required:
        raise CompanyScribeAdmissionError(
            "Scribe cursor migration receipt is malformed"
        )
    basis = {
        key: value for key, value in receipt.items()
        if key != "cursor_migration_sha256"
    }
    migrated_line = _nonnegative_int(
        receipt.get("migrated_line"), field="migration line",
    )
    source_size = _nonnegative_int(
        receipt.get("source_queue_size"), field="migration queue size",
    )
    source_lines = _nonnegative_int(
        receipt.get("source_complete_lines"), field="migration queue lines",
    )
    source_device = _nonnegative_int(
        receipt.get("source_queue_device"), field="migration queue device",
    )
    source_inode = _nonnegative_int(
        receipt.get("source_queue_inode"), field="migration queue inode",
    )
    sha_fields = (
        "source_queue_sha256", "source_role_checkpoint_sha256",
        "cursor_migration_sha256",
    )
    if (
        receipt.get("schema") != _CURSOR_MIGRATION_SCHEMA
        or receipt.get("channel_shared_dir") != str(channel_shared_dir)
        or receipt.get("source_queue_path")
        != str(channel_shared_dir / "queue_scribe.jsonl")
        or receipt.get("source_role_checkpoint_path")
        != str(channel_shared_dir / "cursor_scribe_role.json")
        or receipt.get("target_role_checkpoint_path") != str(target_path)
        or receipt.get("cursor_migration_sha256")
        != _sha256(_canonical_json(basis))
        or any(
            not isinstance(receipt.get(field), str)
            or re.fullmatch(r"[0-9a-f]{64}", str(receipt[field])) is None
            for field in sha_fields
        )
        or source_lines < migrated_line
    ):
        raise CompanyScribeAdmissionError(
            "Scribe cursor migration receipt changed identity"
        )
    target, _raw = _decode_shared_json(target_path)
    if frozenset(target) != {"line"}:
        raise CompanyScribeAdmissionError(
            "Scribe migrated role checkpoint is malformed"
        )
    target_line = _nonnegative_int(
        target.get("line"), field="migrated target line",
    )
    queue_raw, queue_info = _read_stable_shared_file(
        channel_shared_dir / "queue_scribe.jsonl",
        maximum_bytes=_MAX_QUEUE_BYTES,
    )
    queue_lines = _complete_queue_lines(queue_raw)
    role, role_raw = _decode_shared_json(
        channel_shared_dir / "cursor_scribe_role.json"
    )
    if frozenset(role) != {"line"}:
        raise CompanyScribeAdmissionError(
            "legacy Scribe role checkpoint is malformed"
        )
    current_legacy_line = _nonnegative_int(
        role.get("line"), field="role checkpoint line",
    )
    records = _legacy_cursor_records(
        channel_shared_dir, queue_lines=queue_lines,
    )
    selected_path = receipt.get("selected_cursor_path")
    selected_pid = receipt.get("selected_cursor_pid")
    selected_sha = receipt.get("selected_cursor_sha256")
    if selected_path is None or selected_pid is None or selected_sha is None:
        selected_valid = (
            selected_path is None
            and selected_pid is None
            and selected_sha is None
            and migrated_line == 0
        )
    else:
        selected_valid = (
            isinstance(selected_path, str)
            and isinstance(selected_pid, int)
            and not isinstance(selected_pid, bool)
            and isinstance(selected_sha, str)
            and re.fullmatch(r"[0-9a-f]{64}", selected_sha) is not None
            and selected_path
            == str(channel_shared_dir / f"cursor_scribe_{selected_pid}.json")
            and any(
                record["path"] == selected_path
                and record["pid"] == selected_pid
                and record["line"] == migrated_line
                and record["sha256"] == selected_sha
                for record in records
            )
        )
    migrated_anchor = receipt.get("migrated_anchor")
    if (
        not selected_valid
        or queue_info.st_dev != source_device
        or queue_info.st_ino != source_inode
        or len(queue_raw) < source_size
        or hashlib.sha256(queue_raw[:source_size]).hexdigest()
        != receipt["source_queue_sha256"]
        or hashlib.sha256(role_raw).hexdigest()
        != receipt["source_role_checkpoint_sha256"]
        or current_legacy_line != migrated_line
        or len(_complete_queue_lines(queue_raw[:source_size])) != source_lines
        or target_line < migrated_line
        or target_line > len(queue_lines)
        or migrated_line > len(queue_lines)
        or not isinstance(migrated_anchor, str)
        or (migrated_line == 0 and migrated_anchor != "")
        or (
            migrated_line > 0
            and migrated_anchor != _cursor_anchor(queue_lines[migrated_line - 1])
        )
    ):
        raise CompanyScribeAdmissionError(
            "Scribe migrated role checkpoint is no longer queue-anchored"
        )
    return receipt


def _migrate_legacy_scribe_cursor(
    *,
    state_dir: Path,
    channel_shared_dir: Path,
    activation_mode: str,
) -> dict[str, object]:
    """Copy the proven legacy high-water mark without mutating its queue."""
    if activation_mode == "recovery":
        return _verify_cursor_migration(
            state_dir=state_dir,
            channel_shared_dir=channel_shared_dir,
        )
    if activation_mode != "initial":
        raise CompanyScribeAdmissionError("unknown Scribe activation mode")
    source_queue = channel_shared_dir / "queue_scribe.jsonl"
    source_role = channel_shared_dir / "cursor_scribe_role.json"
    target = state_dir / "channel" / "cursor_scribe-shared_role.json"
    receipt_path = state_dir / "channel" / "cursor-migration.json"
    if target.exists() or receipt_path.exists():
        raise CompanyScribeAdmissionError(
            "initial Scribe cursor migration target is not empty"
        )
    queue_raw, queue_info = _read_stable_shared_file(
        source_queue, maximum_bytes=_MAX_QUEUE_BYTES,
    )
    queue_lines = _complete_queue_lines(queue_raw)
    role, role_raw = _decode_shared_json(source_role)
    if frozenset(role) != {"line"}:
        raise CompanyScribeAdmissionError(
            "legacy Scribe role checkpoint is malformed"
        )
    migrated_line = _nonnegative_int(
        role.get("line"), field="role checkpoint line",
    )
    if migrated_line > len(queue_lines):
        raise CompanyScribeAdmissionError(
            "legacy Scribe role checkpoint is beyond its queue"
        )
    records = _legacy_cursor_records(
        channel_shared_dir, queue_lines=queue_lines,
    )
    matching = [
        record for record in records if record["line"] == migrated_line
    ]
    if migrated_line > 0 and not matching:
        raise CompanyScribeAdmissionError(
            "legacy Scribe role checkpoint lacks a dead anchored process cursor"
        )
    selected = matching[0] if matching else None
    queue_after, queue_after_info = _read_stable_shared_file(
        source_queue, maximum_bytes=_MAX_QUEUE_BYTES,
    )
    role_after, role_after_raw = _decode_shared_json(source_role)
    records_after = _legacy_cursor_records(
        channel_shared_dir,
        queue_lines=_complete_queue_lines(queue_after),
    )
    if (
        queue_after_info.st_dev != queue_info.st_dev
        or queue_after_info.st_ino != queue_info.st_ino
        or not queue_after.startswith(queue_raw)
        or role_after != role
        or role_after_raw != role_raw
        or records_after != records
    ):
        raise CompanyScribeAdmissionError(
            "legacy Scribe cursor sources changed during migration"
        )
    migrated_anchor = (
        "" if migrated_line == 0
        else _cursor_anchor(queue_lines[migrated_line - 1])
    )
    basis: dict[str, object] = {
        "schema": _CURSOR_MIGRATION_SCHEMA,
        "channel_shared_dir": str(channel_shared_dir),
        "source_queue_path": str(source_queue),
        "source_queue_device": queue_info.st_dev,
        "source_queue_inode": queue_info.st_ino,
        "source_queue_size": len(queue_raw),
        "source_queue_sha256": hashlib.sha256(queue_raw).hexdigest(),
        "source_complete_lines": len(queue_lines),
        "source_role_checkpoint_path": str(source_role),
        "source_role_checkpoint_sha256": hashlib.sha256(role_raw).hexdigest(),
        "migrated_line": migrated_line,
        "migrated_anchor": migrated_anchor,
        "selected_cursor_path": None if selected is None else selected["path"],
        "selected_cursor_pid": None if selected is None else selected["pid"],
        "selected_cursor_sha256": None if selected is None else selected["sha256"],
        "target_role_checkpoint_path": str(target),
    }
    receipt = {
        **basis,
        "cursor_migration_sha256": _sha256(_canonical_json(basis)),
    }
    _write_pointer(target, {"line": migrated_line})
    _write_pointer(receipt_path, receipt)
    return receipt


def _provision_claude_oauth(
    claude_config: Path,
) -> Path:
    """Copy only the reviewed Claude OAuth record from the login Keychain."""
    try:
        result = subprocess.run(
            [
                "/usr/bin/security", "find-generic-password",
                "-s", "Claude Code-credentials", "-w",
            ],
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": os.environ["HOME"],
                "LANG": "C",
                "LC_ALL": "C",
            },
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (KeyError, OSError, subprocess.TimeoutExpired) as exc:
        raise CompanyScribeAdmissionError(
            "reviewed Claude OAuth Keychain item is unavailable"
        ) from exc
    if result.returncode or not result.stdout or len(result.stdout) > 64 * 1024:
        raise CompanyScribeAdmissionError(
            "reviewed Claude OAuth Keychain item is unavailable"
        )
    try:
        credential = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "reviewed Claude OAuth Keychain item is malformed"
        ) from exc
    oauth = (
        credential.get("claudeAiOauth")
        if isinstance(credential, dict) else None
    )
    required = {
        "accessToken", "expiresAt", "refreshToken", "refreshTokenExpiresAt",
        "scopes", "subscriptionType", "rateLimitTier",
    }
    if (
        not isinstance(credential, dict)
        or frozenset(credential) != {"claudeAiOauth"}
        or not isinstance(oauth, dict)
        or frozenset(oauth) != required
        or any(
            not isinstance(oauth.get(field), str)
            or not oauth[field]
            or len(oauth[field]) > 4096
            for field in (
                "accessToken", "refreshToken",
                "subscriptionType", "rateLimitTier",
            )
        )
        or any(
            isinstance(oauth.get(field), bool)
            or not isinstance(oauth.get(field), int)
            or oauth[field] <= 0
            for field in ("expiresAt", "refreshTokenExpiresAt")
        )
        or not isinstance(oauth.get("scopes"), list)
        or not oauth["scopes"]
        or any(
            not isinstance(scope, str) or not scope or len(scope) > 256
            for scope in oauth["scopes"]
        )
    ):
        raise CompanyScribeAdmissionError(
            "reviewed Claude OAuth Keychain item has an unexpected schema"
        )
    credential_path = claude_config / ".credentials.json"
    _write_pointer(credential_path, credential)
    return credential_path


def _verify_isolated_claude_auth(
    executable: Path,
    *,
    environment: Mapping[str, str],
    payload_root: Path,
) -> None:
    try:
        result = subprocess.run(
            [str(executable), "auth", "status", "--json"],
            cwd=payload_root,
            env=dict(environment),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = json.loads(result.stdout)
    except (
        OSError, subprocess.TimeoutExpired, UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise CompanyScribeAdmissionError(
            "isolated Claude authentication preflight failed"
        ) from exc
    if (
        result.returncode
        or not isinstance(status, dict)
        or status.get("loggedIn") is not True
        or status.get("authMethod") != "claude.ai"
        or status.get("apiProvider") != "firstParty"
    ):
        raise CompanyScribeAdmissionError(
            "isolated Claude authentication preflight failed"
        )


def _claude_executable() -> Path:
    candidates: list[Path] = []
    which = shutil.which("claude")
    if which:
        candidates.append(Path(which))
    candidates.extend(_CLAUDE_EXECUTABLE_CANDIDATES)
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    raise CompanyScribeAdmissionError("Claude executable is unavailable")


def _bootstrap_request(
    *,
    release_manifest: Mapping[str, Any],
    executable: Path,
    runtime_lock_path: Path,
    task_id: str,
    session_id: str,
) -> dict[str, object]:
    return {
        "schema": "mise.company-scribe-bootstrap-request.v1",
        "company_id": company_id(),
        "deployment_id": scribe_deployment_id(),
        "runtime_kind": "claude-code",
        "task_id": task_id,
        "claude_session_id": session_id,
        "process_id": os.getpid(),
        "process_start_fingerprint_sha256": process_start_fingerprint(os.getpid()),
        "repo_commit_sha": release_manifest["source_commit_sha"],
        "repo_tree_sha": release_manifest["source_tree_sha"],
        "host_identity_sha256": host_identity_sha256(),
        "host_boot_id_sha256": boot_identity_sha256(),
        "runtime_executable_path": str(executable),
        "runtime_executable_sha256": executable_sha256(executable),
        "runtime_lock_path": str(runtime_lock_path),
    }


def _runtime_python_image(launcher: Path) -> Path:
    """Return the interpreter image the kernel will report after launch.

    CPython's macOS framework layout ships ``bin/python3.14`` as a stub that
    execs ``Resources/Python.app/Contents/MacOS/Python``. PeerGate pins the
    live image (``proc_pidpath`` + kernel argv[0]). Launch that image
    directly so command, argv[0], digest, and the observed process agree.
    """
    launcher = launcher.resolve(strict=True)
    framework = (
        launcher.parents[1]
        / "Resources"
        / "Python.app"
        / "Contents"
        / "MacOS"
        / "Python"
    )
    if framework.is_file() and not framework.is_symlink():
        return framework.resolve(strict=True)
    return launcher


def _write_exact_mcp_config(
    *,
    payload_root: Path,
    state_dir: Path,
    channel_shared_dir: Path,
    admitted_pid: int,
) -> Path:
    python = _runtime_python_image(
        payload_root / "runtime" / "python" / "bin" / "python3.14"
    )
    bun = (payload_root / "runtime" / "bun").resolve(strict=True)
    webhook = (
        payload_root / "runtime" / "scribe-channel.js"
    ).resolve(strict=True)
    proxy = (
        payload_root / "scripts" / "company_scribe_effect_broker.py"
    ).resolve(strict=True)
    effect_socket = state_dir / "effect-broker.sock"
    channel_socket = state_dir / "channel-broker.sock"
    channel_state = state_dir / "channel"
    channel_state.mkdir(mode=0o700, exist_ok=True)
    mcp_home = state_dir / "mcp-home"
    mcp_config = state_dir / "mcp-config"
    mcp_home.mkdir(mode=0o700, exist_ok=True)
    mcp_config.mkdir(mode=0o700, exist_ok=True)
    isolated_mcp_env = {
        **identity_environment(),
        "HOME": str(mcp_home),
        "CLAUDE_CONFIG_DIR": str(mcp_config),
        "SSH_AUTH_SOCK": "",
    }
    config = {
        "mcpServers": {
            "scribe-channel-push": {
                "type": "stdio",
                "command": str(bun),
                "args": ["--no-install", str(webhook)],
                "env": {
                    **isolated_mcp_env,
                    "MISE_SCRIBE_ADMITTED": "1",
                    "SCRIBE_EXPECTED_PARENT_PID": str(admitted_pid),
                    "SCRIBE_CHANNEL_SHARED_DIR": str(channel_shared_dir),
                    "SCRIBE_CHANNEL_STATE_DIR": str(channel_state),
                    "SCRIBE_DELIVERY_STATE_DIR": str(channel_state),
                    "SCRIBE_CHANNEL_EFFECT_SOCKET": str(channel_socket),
                },
            },
            "scribe-effects": {
                "type": "stdio",
                "command": str(python),
                "args": [
                    "-I", str(proxy), "--proxy", "--socket",
                    str(effect_socket),
                ],
                "env": isolated_mcp_env,
            },
        },
    }
    path = state_dir / "runtime-mcp.json"
    _write_pointer(path, config)
    return path


def _prepare_stream_input(state_dir: Path) -> int:
    """Create one private persistent SDK input stream with the init message."""
    path = state_dir / "claude-input.fifo"
    try:
        os.mkfifo(path, mode=0o600)
    except FileExistsError:
        info = path.lstat()
        if (
            not stat.S_ISFIFO(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise CompanyScribeAdmissionError(
                "Scribe streaming input path is unsafe"
            )
    descriptor = os.open(
        path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
    )
    message = _canonical_json({
        "type": "user",
        "message": {"role": "user", "content": "/init-scribe"},
        "parent_tool_use_id": None,
    }) + b"\n"
    if os.write(descriptor, message) != len(message):
        os.close(descriptor)
        raise CompanyScribeAdmissionError(
            "Scribe streaming init message was not written exactly"
        )
    return descriptor


def _verify_broker_readiness_response(
    response: object,
    *,
    readiness_secret: bytes,
    challenge: str,
    daemon_pid: int,
    effect_socket_path: Path,
    channel_socket_path: Path,
) -> None:
    required = {
        "schema", "challenge", "response_hmac_sha256", "daemon_pid",
        "effect_socket", "channel_socket", "journal_ready",
    }
    if (
        not isinstance(response, dict)
        or frozenset(response) != required
        or response.get("schema") != BROKER_HEALTH_SCHEMA
        or response.get("challenge") != challenge
        or response.get("daemon_pid") != daemon_pid
        or response.get("effect_socket") != str(effect_socket_path)
        or response.get("channel_socket") != str(channel_socket_path)
        or response.get("journal_ready") is not True
        or not isinstance(response.get("response_hmac_sha256"), str)
        or not hmac.compare_digest(
            response["response_hmac_sha256"],
            broker_readiness_hmac(readiness_secret, challenge),
        )
    ):
        raise CompanyScribeAdmissionError(
            "broker readiness authentication failed"
        )


def _start_effect_daemon(
    *,
    effect_socket_path: Path,
    channel_socket_path: Path,
    health_socket_path: Path,
    admitted_pid: int,
    admitted_start_fingerprint: str,
    runtime_lock_descriptor: int,
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
) -> int:
    state_dir = effect_socket_path.parent
    readiness_secret = os.urandom(32)
    daemon_pid = os.fork()
    if daemon_pid == 0:
        try:
            os.close(runtime_lock_descriptor)
            serve_effect_daemon(
                effect_socket_path,
                channel_socket_path=channel_socket_path,
                health_socket_path=health_socket_path,
                readiness_secret=readiness_secret,
                admitted_pid=admitted_pid,
                admitted_start_fingerprint=admitted_start_fingerprint,
                effect_executable_path=effect_executable_path,
                effect_executable_sha256=effect_executable_sha256,
                effect_expected_argv=effect_expected_argv,
                channel_executable_path=channel_executable_path,
                channel_executable_sha256=channel_executable_sha256,
                channel_expected_argv=channel_expected_argv,
                binding_path=binding_path,
                release_package=release_package,
                workspace=workspace,
                database_url=database_url,
                channel_state_dir=channel_state_dir,
                channel_shared_dir=channel_shared_dir,
            )
        except BaseException as exc:
            _write_pointer(
                state_dir / "effect-daemon-startup.json",
                {
                    "schema": "mise.company-scribe-effect-daemon-startup.v1",
                    "status": "failed-closed",
                    "error_type": type(exc).__name__,
                    "error": (
                        str(exc)
                        if isinstance(exc, CompanyScribeAdmissionError)
                        else "startup dependency failed closed"
                    ),
                },
            )
            os._exit(78)
        os._exit(0)
    deadline = time.monotonic() + 5
    challenge = os.urandom(32).hex()
    request = (
        json.dumps(
            {"schema": BROKER_HEALTH_SCHEMA, "challenge": challenge},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    )
    while time.monotonic() < deadline:
        try:
            waited, status = os.waitpid(daemon_pid, os.WNOHANG)
        except ChildProcessError:
            waited, status = daemon_pid, 78 << 8
        if waited == daemon_pid:
            raise CompanyScribeAdmissionError(
                "Company Scribe effect daemon failed before readiness"
            )
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(0.25)
                client.connect(str(health_socket_path))
                client.sendall(request)
                buffered = b""
                while b"\n" not in buffered and len(buffered) <= 8192:
                    chunk = client.recv(8192 - len(buffered) + 1)
                    if not chunk:
                        break
                    buffered += chunk
            if len(buffered) > 8192 or b"\n" not in buffered:
                raise ValueError("incomplete broker readiness response")
            raw, extra = buffered.split(b"\n", 1)
            if extra:
                raise ValueError("trailing broker readiness response")
            response = json.loads(raw)
            _verify_broker_readiness_response(
                response,
                readiness_secret=readiness_secret,
                challenge=challenge,
                daemon_pid=daemon_pid,
                effect_socket_path=effect_socket_path,
                channel_socket_path=channel_socket_path,
            )
            return daemon_pid
        except (
            FileNotFoundError, ConnectionRefusedError, ConnectionResetError,
            TimeoutError, socket.timeout, OSError, ValueError,
            UnicodeDecodeError, json.JSONDecodeError,
            CompanyScribeAdmissionError,
        ):
            pass
        time.sleep(0.02)
    try:
        os.kill(daemon_pid, 9)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(daemon_pid, 0)
    except ChildProcessError:
        pass
    raise CompanyScribeAdmissionError(
        "Company Scribe effect daemon did not authenticate readiness"
    )


def _workspace_binding(
    *,
    repo: Path,
    release_manifest: Mapping[str, Any],
    state_dir: Path,
    channel_shared_dir: Path,
    workspace_ref: str,
) -> dict[str, str]:
    git_binding = _git_repository_binding(
        repo, env=_git_environment(state_dir),
    )
    if git_binding["bound_ref"] != workspace_ref:
        raise CompanyScribeAdmissionError(
            "writable Scribe workspace is not on its exact symbolic ref"
        )
    basis = {
        "schema": _WORKSPACE_BINDING_SCHEMA,
        "workspace_path": str(repo),
        "workspace_ref": workspace_ref,
        "git_dir": git_binding["git_dir"],
        "git_common_dir": git_binding["git_common_dir"],
        "channel_shared_dir": str(channel_shared_dir),
        "source_commit_sha": str(release_manifest["source_commit_sha"]),
        "source_tree_sha": str(release_manifest["source_tree_sha"]),
        "package_sha256": str(release_manifest["package_sha256"]),
    }
    return {
        **basis,
        "workspace_binding_sha256": _sha256(_canonical_json(basis)),
    }


def _verify_or_create_workspace_binding(
    *,
    repo: Path,
    release_manifest: Mapping[str, Any],
    state_dir: Path,
    channel_shared_dir: Path,
    workspace_ref: str,
    restart: Mapping[str, Any] | None,
) -> str:
    """Freeze the initial checkout, then preserve its identity across recovery."""
    expected = _workspace_binding(
        repo=repo,
        release_manifest=release_manifest,
        state_dir=state_dir,
        channel_shared_dir=channel_shared_dir,
        workspace_ref=workspace_ref,
    )
    path = state_dir / "workspace-binding.json"
    if restart is None:
        if path.exists():
            raise CompanyScribeAdmissionError(
                "initial writable Scribe workspace binding already exists"
            )
        if (
            _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
            or _git(repo, "rev-parse", "HEAD^{commit}")
            != release_manifest["source_commit_sha"]
            or _git(repo, "rev-parse", "HEAD^{tree}")
            != release_manifest["source_tree_sha"]
        ):
            raise CompanyScribeAdmissionError(
                "initial writable Scribe activation requires the clean "
                "exact release checkout"
            )
        _write_pointer(path, expected)
        return "initial"
    if restart["state"] != "awaiting-external-launch":
        raise CompanyScribeAdmissionError(
            "Scribe restart lineage is not awaiting a recovery launch"
        )
    try:
        observed = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe workspace binding is unreadable"
        ) from exc
    if observed != expected:
        raise CompanyScribeAdmissionError(
            "Scribe recovery workspace or release binding changed"
        )
    if _git(repo, "rev-parse", "--is-inside-work-tree") != "true":
        raise CompanyScribeAdmissionError(
            "Scribe recovery workspace is not a Git worktree"
        )
    # The mutable workspace may contain the workflow being recovered.  Its
    # runtime and authority remain pinned to the immutable installed package.
    _git(
        repo, "cat-file", "-e",
        f"{release_manifest['source_commit_sha']}^{{commit}}",
    )
    return "recovery"


def _assert_initial_state_empty(
    state_dir: Path,
    *,
    restart: Mapping[str, Any] | None,
) -> None:
    if restart is not None:
        return
    try:
        has_entries = any(state_dir.iterdir())
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "Scribe private state directory is unreadable"
        ) from exc
    if has_entries:
        raise CompanyScribeAdmissionError(
            "initial writable Scribe activation requires an empty state directory"
        )


def _wait_for_binding(
    path: Path,
    timeout_seconds: int,
    *,
    task_id: str,
    session_id: str,
    executable: Path,
):
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            binding = load_binding(path)
        except CompanyScribeAdmissionError as exc:
            last_error = exc
            time.sleep(0.2)
            continue
        if (
            binding.task_id == task_id
            and binding.claude_session_id == session_id
            and binding.runtime_executable_path == str(executable)
        ):
            return binding
        last_error = CompanyScribeAdmissionError(
            "available Scribe binding belongs to a prior launch epoch"
        )
        time.sleep(0.2)
    raise CompanyScribeAdmissionError(
        "timed out waiting for the exact Scribe admission binding"
    ) from last_error


def main() -> int:
    repo = _env_path("CCSUITE_REPO_ROOT").resolve(strict=True)
    release_package = _env_path("MISE_SCRIBE_RELEASE_PACKAGE")
    release_manifest = verify_complete_release_package(release_package)
    payload_root = (release_package / "payload").resolve(strict=True)
    binding_path = _env_path("MISE_SCRIBE_ADMISSION_BINDING")
    request_path = _env_path("MISE_SCRIBE_BOOTSTRAP_REQUEST")
    lock_path = _env_path("MISE_SCRIBE_RUNTIME_LOCK")
    state_dir = request_path.parent.resolve(strict=True)
    if any(
        item.parent.resolve(strict=True) != state_dir
        for item in (binding_path, lock_path)
    ):
        raise CompanyScribeAdmissionError(
            "Scribe runtime authority files must share one private state directory"
        )
    restart = _load_restart_request(
        state_dir / "restart-request.json", state_dir=state_dir,
    )
    _assert_initial_state_empty(state_dir, restart=restart)
    workspace_ref = _required_env("MISE_SCRIBE_WORKSPACE_REF")
    channel_shared_dir = _safe_external_channel_directory(
        repo=repo,
        path=_env_path("MISE_SCRIBE_CHANNEL_SHARED_DIR"),
    )
    database_url = validate_database_route(
        _required_env("COMPANY_SCRIBE_DATABASE_URL")
    )
    task_id = _required_env("MISE_SCRIBE_TASK_ID")
    session_id = _required_env("MISE_SCRIBE_CLAUDE_SESSION_ID")
    try:
        wait_seconds = int(os.environ.get("MISE_SCRIBE_ADMISSION_WAIT_SECONDS", "300"))
    except ValueError as exc:
        raise CompanyScribeAdmissionError(
            "MISE_SCRIBE_ADMISSION_WAIT_SECONDS must be an integer"
        ) from exc
    if wait_seconds < 1 or wait_seconds > 900:
        raise CompanyScribeAdmissionError(
            "Scribe admission wait must be between 1 and 900 seconds"
        )
    model = os.environ.get("MISE_SCRIBE_MODEL", "").strip() or _DEFAULT_SCRIBE_MODEL
    effort = os.environ.get("MISE_SCRIBE_EFFORT", "").strip() or _DEFAULT_SCRIBE_EFFORT
    executable = _claude_executable()
    activation_mode = _verify_or_create_workspace_binding(
        repo=repo,
        release_manifest=release_manifest,
        state_dir=state_dir,
        channel_shared_dir=channel_shared_dir,
        workspace_ref=workspace_ref,
        restart=restart,
    )
    _migrate_legacy_scribe_cursor(
        state_dir=state_dir,
        channel_shared_dir=channel_shared_dir,
        activation_mode=activation_mode,
    )
    child_source = dict(os.environ)
    child_source["MISE_SCRIBE_RELEASE_PACKAGE"] = str(release_package)
    child_source["MISE_SCRIBE_RELEASE_ROOT"] = str(payload_root)
    runtime_manifest = release_manifest["runtime"]
    packaged_python = _runtime_python_image(
        payload_root / runtime_manifest["python_executable"]
    )
    packaged_bun = (
        payload_root / runtime_manifest["bun_executable"]
    ).resolve(strict=True)
    packaged_channel = (
        payload_root / runtime_manifest["scribe_channel_bundle"]
    ).resolve(strict=True)
    packaged_proxy = (
        payload_root / "scripts" / "company_scribe_effect_broker.py"
    ).resolve(strict=True)
    effect_socket = state_dir / "effect-broker.sock"
    channel_socket = state_dir / "channel-broker.sock"
    health_socket = state_dir / "broker-health.sock"
    channel_state_dir = state_dir / "channel"
    channel_state_dir.mkdir(mode=0o700, exist_ok=True)
    child_source["MISE_SCRIBE_PYTHON_EXECUTABLE"] = str(packaged_python)
    claude_config = state_dir / "claude-config"
    claude_config.mkdir(mode=0o700, exist_ok=True)
    claude_home = state_dir / "claude-home"
    claude_home.mkdir(mode=0o700, exist_ok=True)
    _provision_claude_oauth(claude_config)
    child_source["HOME"] = str(claude_home)
    child_source["CLAUDE_CONFIG_DIR"] = str(claude_config)
    child_source.pop("COMPANY_SCRIBE_DATABASE_URL", None)
    child_source.pop("MISE_SCRIBE_ADMISSION_BINDING", None)
    child_environment = _runtime_child_environment(child_source)
    child_environment.update({
        "CCSUITE_REPO_ROOT": str(repo),
        "MISE_SCRIBE_RELEASE_PACKAGE": str(release_package),
        "MISE_SCRIBE_RELEASE_ROOT": str(payload_root),
        "MISE_SCRIBE_PYTHON_EXECUTABLE": str(packaged_python),
        "CLAUDE_CONFIG_DIR": str(claude_config),
        "MISE_SCRIBE_MODEL": model,
        "MISE_SCRIBE_EFFORT": effort,
        "CCSUITE_ROLE_ID": scribe_deployment_id(),
    })
    _verify_isolated_claude_auth(
        executable,
        environment=child_environment,
        payload_root=payload_root,
    )
    with runtime_lock(lock_path) as descriptor:
        request = _bootstrap_request(
            release_manifest=release_manifest,
            executable=executable, runtime_lock_path=lock_path,
            task_id=task_id, session_id=session_id,
        )
        _write_pointer(request_path, request)
        binding = _wait_for_binding(
            binding_path,
            wait_seconds,
            task_id=task_id,
            session_id=session_id,
            executable=executable,
        )
        verify_local_binding(
            binding,
            release_package=release_package,
            expected_pid=os.getpid(),
        )
        if binding.runtime_lock_path != str(lock_path):
            raise CompanyScribeAdmissionError(
                "Scribe admission names another runtime lock"
            )
        with psycopg.connect(database_url) as conn, conn.transaction():
            bind_transaction(conn, binding)
        settings = payload_root / ".claude" / "settings.json"
        init_command = (
            payload_root / ".claude" / "commands" / "init-scribe.md"
        )
        for required in (settings, init_command):
            if not required.is_file():
                raise CompanyScribeAdmissionError(
                    f"required Scribe runtime configuration is absent: {required}"
                )
        _start_effect_daemon(
            effect_socket_path=effect_socket,
            channel_socket_path=channel_socket,
            health_socket_path=health_socket,
            admitted_pid=binding.process_id,
            admitted_start_fingerprint=(
                binding.process_start_fingerprint_sha256
            ),
            runtime_lock_descriptor=descriptor,
            effect_executable_path=packaged_python,
            effect_executable_sha256=executable_sha256(packaged_python),
            effect_expected_argv=(
                str(packaged_python), "-I", str(packaged_proxy), "--proxy",
                "--socket", str(effect_socket),
            ),
            channel_executable_path=packaged_bun,
            channel_executable_sha256=(
                runtime_manifest["bun_executable_sha256"]
            ),
            channel_expected_argv=(
                str(packaged_bun), "--no-install", str(packaged_channel),
            ),
            binding_path=binding_path,
            release_package=release_package,
            workspace=repo,
            database_url=database_url,
            channel_state_dir=channel_state_dir,
            channel_shared_dir=channel_shared_dir,
        )
        mcp_config = _write_exact_mcp_config(
            payload_root=payload_root,
            state_dir=state_dir,
            channel_shared_dir=channel_shared_dir,
            admitted_pid=binding.process_id,
        )
        stream_descriptor = _prepare_stream_input(state_dir)
        argv = [
            str(executable),
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--model", model,
            "--effort", effort,
            "--permission-mode", "dontAsk",
            "--tools", "",
            "--allowedTools", ",".join(_ALLOWED_SCRIBE_TOOLS),
            "--disallowedTools",
            "Bash,Edit,Write,Read,Glob,Grep,WebSearch,WebFetch,Task,Agent,NotebookEdit",
            "--session-id", session_id,
            "--add-dir", str(repo),
            "--settings", str(settings),
            "--setting-sources", "project",
            "--strict-mcp-config",
            "--mcp-config", str(mcp_config),
            "--dangerously-load-development-channels",
            "server:scribe-channel-push",
        ]
        os.set_inheritable(descriptor, True)
        os.dup2(stream_descriptor, 0)
        if stream_descriptor != 0:
            os.close(stream_descriptor)
        os.chdir(payload_root)
        os.execve(executable, argv, child_environment)
    raise AssertionError("Scribe runtime exec unexpectedly returned")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CompanyScribeAdmissionError, psycopg.Error, OSError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(78) from exc
