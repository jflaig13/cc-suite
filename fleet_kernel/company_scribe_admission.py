"""Exact MacBook Company Scribe package and single-writer admission.

This module prepares the activation path but never activates anything at
import time.  Writable authority is the intersection of:

* one installed, byte-verified configured Scribe package;
* one active Company ``scribe`` worker session and its lease;
* one exact founder authority artifact;
* one live database admission; and
* one process-lifetime local lock held by the Claude Code runtime.

Codex and headless cognition intentionally have no writable path in v1.
"""
from __future__ import annotations
from fleet_kernel.configuration import company_id, scribe_deployment_id, authority_issuer, deployment_binding

import fcntl
import ctypes
import hashlib
import json
import os
import platform
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from fleet_kernel.founder_authority import (
    ActivationStep,
    ActivationStepSubject,
    FounderActivationMandate,
    FounderAuthorityError,
    Phase4FounderActivationMandate,
    UtilityFounderActivationMandate,
)
from fleet_kernel.identity import (
    COMPANY_ROLE_TYPES,
    IdentityPlaneError,
    assert_company_role_runtime_admitted,
    company_role_model_family,
    company_role_spec,
)
COMPANY_ID = company_id()
DEPLOYMENT_ID = scribe_deployment_id()
RUNTIME_KIND = "claude-code"
COMPANY_ROLE_HOST_REFUSAL_SCHEMA = "mise.company-role-host-refusal.v1"
COMPANY_ROLE_PROFILE_SCHEMA = "mise.company-role-admission-profile.v1"
COMPANY_ROLE_PACKAGE_SCHEMA = "mise.company-role-package.v1"
COMPANY_ROLE_HELD_POINTER_SCHEMA = "mise.company-role-held-pointer.v1"
PACKAGE_SCHEMA = "mise.company-scribe-package.v1"
COMPLETE_PACKAGE_SCHEMA = "mise.company-scribe-package.v2"
FROZEN_CHANNEL_BUNDLE_SCHEMA = "mise.company-scribe-frozen-channel-bundle.v1"
HELD_POINTER_SCHEMA = "mise.company-scribe-held-pointer.v1"
ROLLBACK_RECEIPT_SCHEMA = "mise.company-scribe-rollback-receipt.v1"
REVOKED_BINDING_POINTER_SCHEMA = "mise.company-scribe-revoked-binding-pointer.v1"
CURRENT_LEASE_POINTER_SCHEMA = "mise.company-scribe-current-lease.v1"
COMPANY_STATE_SNAPSHOT_SCHEMA = "mise.company-workspace-state-snapshot.v1"
RESTART_INTENT_SCHEMA = "mise.company-scribe-restart-intent.v1"
RESTART_EPOCH_RECEIPT_SCHEMA = "mise.company-scribe-restart-epoch-receipt.v1"
INITIAL_AUTHORITY_MODE = "initial-local-activation"
RECOVERY_AUTHORITY_MODE = "standing-mandate-recovery"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_PINNED_BUN_VERSION = "1.3.12"
_PINNED_BUN_SHA256 = (
    "39e644cea4e6db24a3af36013695655d6f789b4b98f1f13bacb882ac6e5c3c18"
)
_FROZEN_CHANNEL_INPUTS = (
    "channels/scribe/bun.lock",
    "channels/scribe/closed_mcp.ts",
    "channels/scribe/durable_delivery.ts",
    "channels/scribe/package.json",
    "channels/scribe/tsconfig.json",
    "channels/scribe/webhook.ts",
    "channels/shared/durable_inbox.ts",
    "channels/shared/send_receipt.ts",
)
_FROZEN_CHANNEL_BUNDLE = "channels/scribe/webhook.bundle.js"
_FROZEN_CHANNEL_MANIFEST = "channels/scribe/frozen-bundle.json"


class CompanyScribeAdmissionError(RuntimeError):
    """An exact Scribe admission or package check failed closed."""


def assert_company_role_host_admitted(
    role_type: str, runtime_kind: str,
) -> None:
    """Refuse a Company seat start on a host outside its official family.

    Chokepoint placement (founder 2026-08-15, Claude-door retirement): this
    fires at lineage-creating and process-starting actions only.  Profile
    validation and supervision of an already-live binding stay permissive so
    live windows run to their natural end; the door changes at the next start.
    """
    try:
        assert_company_role_runtime_admitted(role_type, runtime_kind)
    except IdentityPlaneError as exc:
        raise CompanyScribeAdmissionError(str(exc)) from exc


def record_company_role_host_refusal(
    state_dir: Path | None,
    *,
    role_type: str,
    attempted_runtime_kind: str,
    surface: str,
) -> None:
    """Append one durable host-refusal record; never mask the refusal."""
    if state_dir is None:
        return
    try:
        spec = company_role_spec(role_type)
        official_model = spec.default_model
        official_family = company_role_model_family(role_type).value
    except IdentityPlaneError:
        official_model = "unknown"
        official_family = "unknown"
    row = {
        "schema": COMPANY_ROLE_HOST_REFUSAL_SCHEMA,
        "refused_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "role_type": role_type,
        "official_model": official_model,
        "official_family": official_family,
        "attempted_runtime_kind": attempted_runtime_kind,
        "surface": surface,
        "pid": os.getpid(),
    }
    try:
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (state_dir / "host-refusals.jsonl").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


@dataclass(frozen=True, slots=True)
class CompanyRoleAdmissionProfile:
    """Closed, founder-bound identity for one Company Fleet admission lane.

    Role-scoped schemas, advisory-lock namespaces, and state names are derived
    from the canonical role.  Callers cannot supply those values independently.
    """

    company_id: str
    role_type: str
    deployment_id: str
    worker_id: uuid.UUID
    host_kind: str
    runtime_kind: str
    mandate_id: str
    mandate_artifact_sha256: str

    def __post_init__(self) -> None:
        if self.company_id != COMPANY_ID:
            raise CompanyScribeAdmissionError(
                "Company role profile names an ineligible company"
            )
        if self.role_type not in COMPANY_ROLE_TYPES:
            raise CompanyScribeAdmissionError(
                "Company role profile names a non-canonical role"
            )
        _required(self.deployment_id, "deployment_id")
        if not isinstance(self.worker_id, uuid.UUID):
            try:
                object.__setattr__(self, "worker_id", uuid.UUID(str(self.worker_id)))
            except ValueError as exc:
                raise CompanyScribeAdmissionError(
                    "Company role profile worker_id is malformed"
                ) from exc
        if self.host_kind != "macbook":
            raise CompanyScribeAdmissionError(
                "Company role profile host kind must be macbook"
            )
        if self.runtime_kind != RUNTIME_KIND:
            raise CompanyScribeAdmissionError(
                "Company role profile runtime kind is unsupported"
            )
        if re.fullmatch(r"activation-mandate-[A-Za-z0-9._-]+", self.mandate_id) is None:
            raise CompanyScribeAdmissionError(
                "Company role profile mandate_id is malformed"
            )
        _exact_sha(
            self.mandate_artifact_sha256,
            "mandate_artifact_sha256",
            _SHA256,
        )

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any],
    ) -> "CompanyRoleAdmissionProfile":
        keys = frozenset({
            "company_id", "role_type", "deployment_id", "worker_id",
            "host_kind", "runtime_kind", "mandate_id",
            "mandate_artifact_sha256",
        })
        if not isinstance(value, Mapping) or frozenset(value) != keys:
            raise CompanyScribeAdmissionError(
                "Company role admission profile is not closed"
            )
        try:
            return cls(
                company_id=_required(value.get("company_id"), "company_id"),
                role_type=_required(value.get("role_type"), "role_type"),
                deployment_id=_required(
                    value.get("deployment_id"), "deployment_id",
                ),
                worker_id=uuid.UUID(_required(value.get("worker_id"), "worker_id")),
                host_kind=_required(value.get("host_kind"), "host_kind"),
                runtime_kind=_required(value.get("runtime_kind"), "runtime_kind"),
                mandate_id=_required(value.get("mandate_id"), "mandate_id"),
                mandate_artifact_sha256=_exact_sha(
                    value.get("mandate_artifact_sha256"),
                    "mandate_artifact_sha256", _SHA256,
                ),
            )
        except (TypeError, ValueError) as exc:
            raise CompanyScribeAdmissionError(
                "Company role admission profile is malformed"
            ) from exc

    @property
    def package_schema(self) -> str:
        return "mise.company-role-package.v1"

    @property
    def bootstrap_schema(self) -> str:
        return "mise.company-role-bootstrap-request.v1"

    @property
    def candidate_schema(self) -> str:
        return "mise.company-role-admission-candidate.v1"

    @property
    def heartbeat_schema(self) -> str:
        return "mise.company-role-supervisor-heartbeat.v1"

    @property
    def restart_request_schema(self) -> str:
        return "mise.company-role-restart-request.v1"

    @property
    def admission_lock_key(self) -> str:
        return f"{self.company_id}:{self.role_type}:admission"

    @property
    def supervisor_lock_key(self) -> str:
        return f"{self.company_id}:{self.role_type}:supervisor"

    @property
    def state_namespace(self) -> str:
        return f"company-role/{self.role_type}"

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema": COMPANY_ROLE_PROFILE_SCHEMA,
            "company_id": self.company_id,
            "role_type": self.role_type,
            "deployment_id": self.deployment_id,
            "worker_id": str(self.worker_id),
            "host_kind": self.host_kind,
            "runtime_kind": self.runtime_kind,
            "mandate_id": self.mandate_id,
            "mandate_artifact_sha256": self.mandate_artifact_sha256,
            "package_schema": self.package_schema,
            "bootstrap_schema": self.bootstrap_schema,
            "candidate_schema": self.candidate_schema,
            "heartbeat_schema": self.heartbeat_schema,
            "restart_request_schema": self.restart_request_schema,
            "admission_lock_key": self.admission_lock_key,
            "supervisor_lock_key": self.supervisor_lock_key,
            "state_namespace": self.state_namespace,
        }

    @property
    def sha256(self) -> str:
        return _sha256(_canonical_json(self.canonical_payload))


def scribe_admission_profile(
    *,
    worker_id: uuid.UUID,
    mandate_id: str,
    mandate_artifact_sha256: str,
) -> CompanyRoleAdmissionProfile:
    """Build the only compatibility profile admitted by the Scribe adapter."""
    return CompanyRoleAdmissionProfile(
        company_id=COMPANY_ID,
        role_type="scribe",
        deployment_id=DEPLOYMENT_ID,
        worker_id=worker_id,
        host_kind="macbook",
        runtime_kind=RUNTIME_KIND,
        mandate_id=mandate_id,
        mandate_artifact_sha256=mandate_artifact_sha256,
    )


def _crash_checkpoint(_name: str) -> None:
    """Deterministic test seam for replaying every recovery crash boundary."""


def _require_idle_connection(conn: psycopg.Connection) -> None:
    info = getattr(conn, "info", None)
    status = getattr(info, "transaction_status", None)
    if status is not None and status != TransactionStatus.IDLE:
        raise CompanyScribeAdmissionError(
            "Scribe rollback requires an idle provisioner connection"
        )


def validate_database_route(value: str) -> str:
    """Accept a libpq route but never an inline plaintext password."""
    route = _required(value, "Company Scribe database route")
    try:
        parameters = psycopg.conninfo.conninfo_to_dict(route)
    except psycopg.ProgrammingError as exc:
        raise CompanyScribeAdmissionError(
            "Company Scribe database route is malformed"
        ) from exc
    if parameters.get("password"):
        raise CompanyScribeAdmissionError(
            "Company Scribe database route must use an approved secret store"
        )
    return route


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _required(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text or any(ord(char) < 32 for char in text):
        raise CompanyScribeAdmissionError(f"{field} is missing or invalid")
    return text


def _exact_sha(value: object, field: str, pattern: re.Pattern[str]) -> str:
    text = _required(value, field)
    if pattern.fullmatch(text) is None:
        raise CompanyScribeAdmissionError(f"{field} is not an exact digest")
    return text


def _utc(value: object, field: str) -> datetime:
    text = _required(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CompanyScribeAdmissionError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise CompanyScribeAdmissionError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False, capture_output=True, text=True,
    )
    if result.returncode:
        raise CompanyScribeAdmissionError(
            f"git {' '.join(args)} failed for the Scribe repository"
        )
    return result.stdout.strip()


def process_start_fingerprint(pid: int) -> str:
    """Bind a PID to a kernel birth instant with sub-second precision."""
    if pid <= 1:
        raise CompanyScribeAdmissionError("Scribe process id is invalid")
    if platform.system() == "Darwin":
        class ProcBsdInfo(ctypes.Structure):
            _fields_ = [
                ("pbi_flags", ctypes.c_uint32),
                ("pbi_status", ctypes.c_uint32),
                ("pbi_xstatus", ctypes.c_uint32),
                ("pbi_pid", ctypes.c_uint32),
                ("pbi_ppid", ctypes.c_uint32),
                ("pbi_uid", ctypes.c_uint32),
                ("pbi_gid", ctypes.c_uint32),
                ("pbi_ruid", ctypes.c_uint32),
                ("pbi_rgid", ctypes.c_uint32),
                ("pbi_svuid", ctypes.c_uint32),
                ("pbi_svgid", ctypes.c_uint32),
                ("rfu_1", ctypes.c_uint32),
                ("pbi_comm", ctypes.c_char * 16),
                ("pbi_name", ctypes.c_char * 32),
                ("pbi_nfiles", ctypes.c_uint32),
                ("pbi_pgid", ctypes.c_uint32),
                ("pbi_pjobc", ctypes.c_uint32),
                ("e_tdev", ctypes.c_uint32),
                ("e_tpgid", ctypes.c_uint32),
                ("pbi_nice", ctypes.c_int32),
                ("pbi_start_tvsec", ctypes.c_uint64),
                ("pbi_start_tvusec", ctypes.c_uint64),
            ]
        info = ProcBsdInfo()
        libproc = ctypes.CDLL(
            "/usr/lib/libproc.dylib", use_errno=True,
        )
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
            ctypes.c_void_p, ctypes.c_int,
        ]
        proc_pidinfo.restype = ctypes.c_int
        size = proc_pidinfo(
            pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info),
        )
        if (
            size != ctypes.sizeof(info)
            or info.pbi_pid != pid
            or info.pbi_start_tvsec <= 0
            or info.pbi_start_tvusec >= 1_000_000
        ):
            raise CompanyScribeAdmissionError(
                "Scribe process kernel birth identity is unavailable"
            )
        identity = (
            f"darwin-libproc:{info.pbi_start_tvsec}:"
            f"{info.pbi_start_tvusec}"
        )
    elif platform.system() == "Linux":
        try:
            fields = Path(f"/proc/{pid}/stat").read_text(
                encoding="ascii",
            ).rsplit(")", 1)[1].split()
            # proc(5): field 22 is starttime; after removing pid/comm it is
            # zero-based index 19 in the remaining fields.
            start_ticks = int(fields[19])
        except (FileNotFoundError, IndexError, OSError, ValueError) as exc:
            raise CompanyScribeAdmissionError(
                "Scribe process kernel birth identity is unavailable"
            ) from exc
        identity = f"linux-proc-startticks:{start_ticks}"
    else:
        raise CompanyScribeAdmissionError(
            "Scribe process birth identity has no exact platform adapter"
        )
    return _sha256(
        f"company-scribe-process-v2|{pid}|{identity}".encode("utf-8")
    )


def process_executable_path(pid: int) -> Path:
    """Resolve the executable the live PID is currently running.

    Reading the admitted path from its binding is insufficient: ``execve``
    preserves both PID and kernel start time.  The authority monitor therefore
    asks the kernel for the current image and hashes those bytes on every
    authorization check.
    """
    if pid <= 1:
        raise CompanyScribeAdmissionError("Scribe process id is invalid")
    if platform.system() == "Darwin":
        buffer = ctypes.create_string_buffer(4096)
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidpath = libproc.proc_pidpath
        proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        proc_pidpath.restype = ctypes.c_int
        length = proc_pidpath(pid, buffer, ctypes.sizeof(buffer))
        if length <= 0:
            raise CompanyScribeAdmissionError(
                "Scribe process executable identity is unavailable"
            )
        raw = buffer.raw[:length].split(b"\0", 1)[0]
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CompanyScribeAdmissionError(
                "Scribe process executable identity is unreadable"
            ) from exc
        path = Path(value)
    elif platform.system() == "Linux":
        try:
            path = Path(os.readlink(f"/proc/{pid}/exe"))
        except OSError as exc:
            raise CompanyScribeAdmissionError(
                "Scribe process executable identity is unavailable"
            ) from exc
    else:
        raise CompanyScribeAdmissionError(
            "Scribe process executable has no exact platform adapter"
        )
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "Scribe process executable identity is unavailable"
        ) from exc


def host_identity_sha256() -> str:
    """Stable Mac host identity without persisting the raw hardware UUID."""
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            check=False, capture_output=True, text=True,
        )
        match = re.search(
            r'"IOPlatformUUID"\s*=\s*"([^"]+)"',
            result.stdout if result.returncode == 0 else "",
        )
        if match is None:
            raise CompanyScribeAdmissionError(
                "Mac host hardware identity is unavailable"
            )
        stable_identity = f"ioplatformuuid:{match.group(1).lower()}"
    elif platform.system() == "Linux":
        machine_id = Path("/etc/machine-id")
        try:
            value = machine_id.read_text(encoding="ascii").strip().lower()
        except OSError as exc:
            raise CompanyScribeAdmissionError(
                "Linux host hardware identity is unavailable"
            ) from exc
        if re.fullmatch(r"[0-9a-f]{32}", value) is None:
            raise CompanyScribeAdmissionError(
                "Linux host hardware identity is malformed"
            )
        stable_identity = f"machine-id:{value}"
    else:
        raise CompanyScribeAdmissionError(
            "Company Scribe host identity has no exact platform adapter"
        )
    return _sha256(
        f"company-scribe-host-v1|{stable_identity}".encode("utf-8")
    )


def boot_identity_sha256() -> str:
    """Bind authority to the current boot so restart requires a fresh epoch."""
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["sysctl", "-n", "kern.bootsessionuuid"],
            check=False, capture_output=True, text=True,
        )
        value = " ".join(result.stdout.split()) if result.returncode == 0 else ""
    elif platform.system() == "Linux":
        boot_id = Path("/proc/sys/kernel/random/boot_id")
        value = boot_id.read_text(encoding="utf-8").strip() if boot_id.exists() else ""
    else:
        value = ""
    if not value:
        raise CompanyScribeAdmissionError("host boot identity is unavailable")
    return _sha256(f"company-scribe-boot-v2|{value}".encode("utf-8"))


def executable_sha256(path: Path) -> str:
    resolved = path.resolve(strict=True)
    info = resolved.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise CompanyScribeAdmissionError("Scribe executable is not a regular file")
    return _sha256(resolved.read_bytes())


def verify_runtime_lock_held(path: Path, *, expected_pid: int) -> None:
    """Prove the exact admitted PID still holds this runtime-lock inode."""
    if expected_pid <= 1:
        raise CompanyScribeAdmissionError("Scribe runtime lock PID is invalid")
    _secure_directory(path.parent)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "Scribe runtime lock is unavailable"
        ) from exc
    acquired = False
    inode: tuple[int, int] | None = None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise CompanyScribeAdmissionError(
                "Scribe runtime lock owner/type/mode is unsafe"
            )
        inode = (info.st_dev, info.st_ino)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, 64) != f"{expected_pid}\n".encode("ascii"):
            raise CompanyScribeAdmissionError(
                "Scribe runtime lock names another process"
            )
        blocked = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            blocked = True
        if not blocked:
            acquired = True
            raise CompanyScribeAdmissionError(
                "Scribe runtime lock is not held by a live launcher"
            )
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    # The non-acquirable flock proves a live holder. The descriptor census then
    # proves the exact admitted PID remains one of the holders. Child processes
    # may inherit the same locked open-file description from that runtime.
    lsof = shutil.which("lsof")
    if not lsof:
        raise CompanyScribeAdmissionError(
            "exact Scribe runtime lock ownership probe is unavailable"
        )
    census = subprocess.run(
        [lsof, "-n", "-F", "p", "--", str(path)],
        check=False, capture_output=True, text=True,
    )
    openers = {
        int(line[1:])
        for line in census.stdout.splitlines()
        if line.startswith("p") and line[1:].isdigit()
    }
    if census.returncode != 0 or expected_pid not in openers:
        raise CompanyScribeAdmissionError(
            "Scribe runtime lock is not held by the expected process"
        )
    recheck = os.open(path, flags)
    reacquired = False
    try:
        info = os.fstat(recheck)
        if inode != (info.st_dev, info.st_ino):
            raise CompanyScribeAdmissionError(
                "Scribe runtime lock inode changed during ownership proof"
            )
        try:
            fcntl.flock(recheck, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        reacquired = True
        raise CompanyScribeAdmissionError(
            "Scribe runtime lock holder vanished during ownership proof"
        )
    finally:
        if reacquired:
            fcntl.flock(recheck, fcntl.LOCK_UN)
        os.close(recheck)


@dataclass(frozen=True, slots=True)
class CompanyRoleAdmissionBinding:
    """Immutable process/admission tuple for one exact Company role profile."""

    admission_id: uuid.UUID
    worker_id: uuid.UUID
    worker_session_id: uuid.UUID
    package_id: uuid.UUID
    agent_release_sha256: str
    kernel_release_sha256: str
    package_sha256: str
    task_id: str
    claude_session_id: str
    process_id: int
    process_start_fingerprint_sha256: str
    repo_commit_sha: str
    repo_tree_sha: str
    lease_token: str
    admission_epoch: int
    lease_expires_at: datetime
    mandate_id: str
    mandate_artifact_sha256: str
    authority_approval_id: uuid.UUID
    authority_subject_ref: str
    authority_subject_sha256: str
    mini_activation_receipt_sha256: str
    host_identity_sha256: str
    host_boot_id_sha256: str
    runtime_executable_path: str
    runtime_executable_sha256: str
    runtime_lock_path: str
    authority_mode: str = INITIAL_AUTHORITY_MODE
    authority_root_admission_id: uuid.UUID | None = None
    prior_admission_id: uuid.UUID | None = None
    recovery_intent_id: uuid.UUID | None = None
    prior_rollback_receipt_sha256: str | None = None
    restart_intent_sha256: str | None = None
    deployment_id: str = DEPLOYMENT_ID
    company_id: str = COMPANY_ID
    role_type: str = "scribe"
    runtime_kind: str = RUNTIME_KIND

    def __post_init__(self) -> None:
        if (
            self.authority_mode == INITIAL_AUTHORITY_MODE
            and self.authority_root_admission_id is None
        ):
            object.__setattr__(
                self, "authority_root_admission_id", self.admission_id,
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        profile: CompanyRoleAdmissionProfile,
    ) -> "CompanyRoleAdmissionBinding":
        try:
            binding = cls(
                admission_id=uuid.UUID(_required(value.get("admission_id"), "admission_id")),
                worker_id=uuid.UUID(_required(value.get("worker_id"), "worker_id")),
                worker_session_id=uuid.UUID(
                    _required(value.get("worker_session_id"), "worker_session_id")
                ),
                package_id=uuid.UUID(_required(value.get("package_id"), "package_id")),
                agent_release_sha256=_exact_sha(
                    value.get("agent_release_sha256"), "agent_release_sha256", _SHA256,
                ),
                kernel_release_sha256=_exact_sha(
                    value.get("kernel_release_sha256"), "kernel_release_sha256", _SHA256,
                ),
                package_sha256=_exact_sha(
                    value.get("package_sha256"), "package_sha256", _SHA256,
                ),
                task_id=_required(value.get("task_id"), "task_id"),
                claude_session_id=_required(
                    value.get("claude_session_id"), "claude_session_id",
                ),
                process_id=int(value.get("process_id", 0)),
                process_start_fingerprint_sha256=_exact_sha(
                    value.get("process_start_fingerprint_sha256"),
                    "process_start_fingerprint_sha256", _SHA256,
                ),
                repo_commit_sha=_exact_sha(
                    value.get("repo_commit_sha"), "repo_commit_sha", _GIT_SHA,
                ),
                repo_tree_sha=_exact_sha(
                    value.get("repo_tree_sha"), "repo_tree_sha", _GIT_SHA,
                ),
                lease_token=_required(value.get("lease_token"), "lease_token"),
                admission_epoch=int(value.get("admission_epoch", 0)),
                lease_expires_at=_utc(value.get("lease_expires_at"), "lease_expires_at"),
                mandate_id=_required(value.get("mandate_id"), "mandate_id"),
                mandate_artifact_sha256=_exact_sha(
                    value.get("mandate_artifact_sha256"),
                    "mandate_artifact_sha256", _SHA256,
                ),
                authority_approval_id=uuid.UUID(_required(
                    value.get("authority_approval_id"), "authority_approval_id",
                )),
                authority_subject_ref=_required(
                    value.get("authority_subject_ref"), "authority_subject_ref",
                ),
                authority_subject_sha256=_exact_sha(
                    value.get("authority_subject_sha256"),
                    "authority_subject_sha256", _SHA256,
                ),
                mini_activation_receipt_sha256=_exact_sha(
                    value.get("mini_activation_receipt_sha256"),
                    "mini_activation_receipt_sha256", _SHA256,
                ),
                host_identity_sha256=_exact_sha(
                    value.get("host_identity_sha256"), "host_identity_sha256", _SHA256,
                ),
                host_boot_id_sha256=_exact_sha(
                    value.get("host_boot_id_sha256"), "host_boot_id_sha256", _SHA256,
                ),
                runtime_executable_path=_required(
                    value.get("runtime_executable_path"), "runtime_executable_path",
                ),
                runtime_executable_sha256=_exact_sha(
                    value.get("runtime_executable_sha256"),
                    "runtime_executable_sha256", _SHA256,
                ),
                runtime_lock_path=_required(
                    value.get("runtime_lock_path"), "runtime_lock_path",
                ),
                authority_mode=_required(
                    value.get("authority_mode", INITIAL_AUTHORITY_MODE),
                    "authority_mode",
                ),
                authority_root_admission_id=(
                    None
                    if value.get("authority_root_admission_id") is None
                    else uuid.UUID(_required(
                        value.get("authority_root_admission_id"),
                        "authority_root_admission_id",
                    ))
                ),
                prior_admission_id=(
                    None
                    if value.get("prior_admission_id") is None
                    else uuid.UUID(_required(
                        value.get("prior_admission_id"), "prior_admission_id",
                    ))
                ),
                recovery_intent_id=(
                    None
                    if value.get("recovery_intent_id") is None
                    else uuid.UUID(_required(
                        value.get("recovery_intent_id"), "recovery_intent_id",
                    ))
                ),
                prior_rollback_receipt_sha256=(
                    None
                    if value.get("prior_rollback_receipt_sha256") is None
                    else _exact_sha(
                        value.get("prior_rollback_receipt_sha256"),
                        "prior_rollback_receipt_sha256", _SHA256,
                    )
                ),
                restart_intent_sha256=(
                    None
                    if value.get("restart_intent_sha256") is None
                    else _exact_sha(
                        value.get("restart_intent_sha256"),
                        "restart_intent_sha256", _SHA256,
                    )
                ),
                deployment_id=_required(
                    value.get("deployment_id"), "deployment_id",
                ),
                company_id=_required(value.get("company_id"), "company_id"),
                role_type=_required(value.get("role_type"), "role_type"),
                runtime_kind=_required(
                    value.get("runtime_kind"), "runtime_kind",
                ),
            )
        except (TypeError, ValueError) as exc:
            raise CompanyScribeAdmissionError(
                "Company role admission binding is malformed"
            ) from exc
        if binding.process_id <= 1 or binding.admission_epoch <= 0:
            raise CompanyScribeAdmissionError(
                "Company role PID and admission epoch must be positive"
            )
        if (
            binding.company_id != profile.company_id
            or binding.role_type != profile.role_type
            or binding.deployment_id != profile.deployment_id
            or binding.worker_id != profile.worker_id
            or binding.runtime_kind != profile.runtime_kind
            or binding.mandate_id != profile.mandate_id
            or binding.mandate_artifact_sha256
            != profile.mandate_artifact_sha256
        ):
            raise CompanyScribeAdmissionError(
                "Company role admission differs from its exact profile"
            )
        executable = Path(binding.runtime_executable_path)
        runtime_lock = Path(binding.runtime_lock_path)
        if (
            not executable.is_absolute()
            or ".." in executable.parts
            or not runtime_lock.is_absolute()
            or ".." in runtime_lock.parts
        ):
            raise CompanyScribeAdmissionError(
                "Company role executable or runtime-lock path is unsafe"
            )
        recovery_values = (
            binding.prior_admission_id,
            binding.recovery_intent_id,
            binding.prior_rollback_receipt_sha256,
            binding.restart_intent_sha256,
        )
        if binding.authority_mode == INITIAL_AUTHORITY_MODE:
            if (
                binding.authority_root_admission_id != binding.admission_id
                or any(item is not None for item in recovery_values)
            ):
                raise CompanyScribeAdmissionError(
                    "initial Company role admission contains recovery authority"
                )
        elif binding.authority_mode == RECOVERY_AUTHORITY_MODE:
            if (
                binding.authority_root_admission_id in {
                    None, binding.admission_id,
                }
                or any(item is None for item in recovery_values)
            ):
                raise CompanyScribeAdmissionError(
                    "recovered Company role admission lacks its exact recovery chain"
                )
        else:
            raise CompanyScribeAdmissionError(
                "Company role admission authority mode is invalid"
            )
        return binding

    def to_mapping(self) -> dict[str, Any]:
        result = asdict(self)
        for field in (
            "admission_id", "worker_id", "worker_session_id", "package_id",
            "authority_approval_id", "authority_root_admission_id",
            "prior_admission_id", "recovery_intent_id",
        ):
            current = result[field]
            result[field] = None if current is None else str(current)
        result["lease_expires_at"] = (
            self.lease_expires_at.astimezone(timezone.utc)
            .isoformat().replace("+00:00", "Z")
        )
        return result


def validate_admission_identity(
    *,
    profile: CompanyRoleAdmissionProfile,
    binding: CompanyRoleAdmissionBinding,
    worker: Mapping[str, Any],
    package: Mapping[str, Any],
    observed_host_kind: str,
    observed_host_identity_sha256: str,
) -> None:
    """Validate the cross-role, cross-package, and cross-host refusal axes.

    This is deliberately side-effect free. Database triggers independently
    enforce the same equality dimensions when migration 031 is applied in a
    later, separately authorized activation lane.
    """
    if (
        binding.company_id != profile.company_id
        or binding.role_type != profile.role_type
        or binding.deployment_id != profile.deployment_id
        or binding.worker_id != profile.worker_id
        or binding.runtime_kind != profile.runtime_kind
        or binding.mandate_id != profile.mandate_id
        or binding.mandate_artifact_sha256 != profile.mandate_artifact_sha256
    ):
        raise CompanyScribeAdmissionError(
            "Company role binding does not match its profile"
        )
    worker_exact = (
        str(worker.get("id")) == str(profile.worker_id)
        and worker.get("tenant_id") == profile.company_id
        and worker.get("fleet_type") == "company"
        and worker.get("role_type") == profile.role_type
        and worker.get("status") == "active"
        and worker.get("manifest_status") == "active"
        and worker.get("agent_release_sha256")
        == binding.agent_release_sha256
    )
    if not worker_exact:
        raise CompanyScribeAdmissionError(
            "Company role worker role, release, or active lineage is not exact"
        )
    package_exact = (
        str(package.get("id")) == str(binding.package_id)
        and package.get("company_id") == profile.company_id
        and package.get("role_type") == profile.role_type
        and package.get("deployment_id") == profile.deployment_id
        and package.get("host_kind") == profile.host_kind
        and package.get("state") == "installed"
        and package.get("kernel_release_sha256")
        == binding.kernel_release_sha256
        and package.get("package_sha256") == binding.package_sha256
        and package.get("source_commit_sha") == binding.repo_commit_sha
        and package.get("source_tree_sha") == binding.repo_tree_sha
    )
    if not package_exact:
        raise CompanyScribeAdmissionError(
            "Company role package role, deployment, release, or digest is not exact"
        )
    if (
        observed_host_kind != profile.host_kind
        or observed_host_identity_sha256 != binding.host_identity_sha256
    ):
        raise CompanyScribeAdmissionError(
            "Company role admission belongs to another host"
        )


def company_role_activation_subject_payload(
    *,
    profile: CompanyRoleAdmissionProfile,
    binding: CompanyRoleAdmissionBinding,
) -> dict[str, Any]:
    """Canonical Phase-4 authority subject for the complete admission tuple."""
    if (
        binding.company_id != profile.company_id
        or binding.role_type != profile.role_type
        or binding.deployment_id != profile.deployment_id
        or binding.worker_id != profile.worker_id
        or binding.mandate_id != profile.mandate_id
        or binding.mandate_artifact_sha256
        != profile.mandate_artifact_sha256
    ):
        raise CompanyScribeAdmissionError(
            "Company role authority subject differs from its profile"
        )
    return {
        "schema": "mise.company-role-activation-subject.v1",
        "mandate_id": binding.mandate_id,
        "mandate_artifact_sha256": binding.mandate_artifact_sha256,
        "company_id": binding.company_id,
        "role_type": binding.role_type,
        "deployment_id": binding.deployment_id,
        "worker_id": str(binding.worker_id),
        "worker_session_id": str(binding.worker_session_id),
        "package_id": str(binding.package_id),
        "package_sha256": binding.package_sha256,
        "agent_release_sha256": binding.agent_release_sha256,
        "kernel_release_sha256": binding.kernel_release_sha256,
        "source_commit_sha": binding.repo_commit_sha,
        "source_tree_sha": binding.repo_tree_sha,
        "host_kind": profile.host_kind,
        "host_identity_sha256": binding.host_identity_sha256,
        "host_boot_id_sha256": binding.host_boot_id_sha256,
        "runtime_kind": binding.runtime_kind,
        "runtime_executable_path": binding.runtime_executable_path,
        "runtime_executable_sha256": binding.runtime_executable_sha256,
        "runtime_lock_path": binding.runtime_lock_path,
        "process_id": binding.process_id,
        "process_start_fingerprint_sha256": (
            binding.process_start_fingerprint_sha256
        ),
        "task_id": binding.task_id,
        "claude_session_id": binding.claude_session_id,
        "lease_token_sha256": _sha256(binding.lease_token.encode("utf-8")),
        "lease_expires_at": (
            binding.lease_expires_at.astimezone(timezone.utc)
            .isoformat().replace("+00:00", "Z")
        ),
        "admission_id": str(binding.admission_id),
        "admission_epoch": binding.admission_epoch,
        "predecessor_fence_receipt_sha256": (
            binding.mini_activation_receipt_sha256
        ),
        "authority_mode": binding.authority_mode,
        "authority_root_admission_id": str(
            binding.authority_root_admission_id
        ),
        "prior_admission_id": (
            None
            if binding.prior_admission_id is None
            else str(binding.prior_admission_id)
        ),
        "recovery_intent_id": (
            None
            if binding.recovery_intent_id is None
            else str(binding.recovery_intent_id)
        ),
        "prior_rollback_receipt_sha256": (
            binding.prior_rollback_receipt_sha256
        ),
        "restart_intent_sha256": binding.restart_intent_sha256,
        "attempt_key": (
            f"{binding.role_type}-admission:{binding.admission_id}:"
            f"epoch:{binding.admission_epoch}"
        ),
        "before_authority_state": {
            "role_type": binding.role_type,
            "writer": "fenced",
        },
        "after_authority_state": {
            "role_type": binding.role_type,
            "writer": "kernel-admitted",
        },
        "approval_id": str(binding.authority_approval_id),
    }


def validate_company_role_authority_subject(
    *,
    profile: CompanyRoleAdmissionProfile,
    binding: CompanyRoleAdmissionBinding,
) -> dict[str, Any]:
    payload = company_role_activation_subject_payload(
        profile=profile, binding=binding,
    )
    digest = _sha256(_canonical_json(payload))
    subject_ref = (
        f"founder://company-role-activation/{binding.mandate_id}/"
        f"{binding.role_type}/{digest}"
    )
    if (
        binding.authority_subject_sha256 != digest
        or binding.authority_subject_ref != subject_ref
    ):
        raise CompanyScribeAdmissionError(
            "Company role authority subject does not match the exact binding"
        )
    return payload


def create_company_role_live_admission(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
    binding: CompanyRoleAdmissionBinding,
    mandate: Phase4FounderActivationMandate | UtilityFounderActivationMandate,
) -> None:
    """Create one generic live row only after the Phase-4 pins are recordable."""
    mandate.require_recordable()
    if (
        mandate.mandate_id != profile.mandate_id
        or mandate.artifact_sha256 != profile.mandate_artifact_sha256
    ):
        raise CompanyScribeAdmissionError(
            "Company role mandate differs from the exact profile"
        )
    validate_company_role_authority_subject(profile=profile, binding=binding)
    with conn.transaction():
        conn.execute(
            "INSERT INTO kernel_company_scribe_admissions ("
            "id,company_id,role_type,deployment_id,worker_id,worker_session_id,"
            "package_id,agent_release_sha256,kernel_release_sha256,package_sha256,"
            "runtime_kind,task_id,claude_session_id,process_id,"
            "process_start_fingerprint_sha256,repo_commit_sha,repo_tree_sha,"
            "lease_token_sha256,admission_epoch,mandate_id,"
            "mandate_artifact_sha256,authority_approval_id,authority_subject_ref,"
            "authority_subject_sha256,mini_activation_receipt_sha256,"
            "host_identity_sha256,host_boot_id_sha256,runtime_executable_path,"
            "runtime_executable_sha256,runtime_lock_path,authority_mode,"
            "authority_root_admission_id,prior_admission_id,recovery_intent_id,"
            "prior_rollback_receipt_sha256,restart_intent_sha256,state,"
            "lease_expires_at,authority_lease_expires_at) VALUES ("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'live',%s,%s)",
            (
                binding.admission_id, profile.company_id, profile.role_type,
                profile.deployment_id, profile.worker_id,
                binding.worker_session_id, binding.package_id,
                binding.agent_release_sha256, binding.kernel_release_sha256,
                binding.package_sha256, profile.runtime_kind, binding.task_id,
                binding.claude_session_id, binding.process_id,
                binding.process_start_fingerprint_sha256,
                binding.repo_commit_sha, binding.repo_tree_sha,
                _sha256(binding.lease_token.encode("utf-8")),
                binding.admission_epoch, profile.mandate_id,
                profile.mandate_artifact_sha256,
                binding.authority_approval_id, binding.authority_subject_ref,
                binding.authority_subject_sha256,
                binding.mini_activation_receipt_sha256,
                binding.host_identity_sha256, binding.host_boot_id_sha256,
                binding.runtime_executable_path,
                binding.runtime_executable_sha256, binding.runtime_lock_path,
                binding.authority_mode, binding.authority_root_admission_id,
                binding.prior_admission_id, binding.recovery_intent_id,
                binding.prior_rollback_receipt_sha256,
                binding.restart_intent_sha256, binding.lease_expires_at,
                binding.lease_expires_at,
            ),
        )


def register_company_role_activation_subject(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
    binding: CompanyRoleAdmissionBinding,
    mandate: Phase4FounderActivationMandate | UtilityFounderActivationMandate,
) -> dict[str, Any]:
    """Record the exact mandate-derived subject consumed by one admission.

    This function does not invent an approval: it accepts only a subject whose
    complete tuple hashes to the binding and whose role is explicitly present
    in the pinned founder mandate's ordered role list.
    """
    mandate.require_recordable()
    payload = validate_company_role_authority_subject(
        profile=profile, binding=binding,
    )
    ordered = {
        (
            str(item["role_type"]),
            str(item["deployment_id"]),
            str(item["worker_id"]),
        )
        for item in mandate.artifact["ordered_roles"]
    }
    if (
        mandate.mandate_id != profile.mandate_id
        or mandate.artifact_sha256 != profile.mandate_artifact_sha256
        or (
            profile.role_type,
            profile.deployment_id,
            str(profile.worker_id),
        ) not in ordered
    ):
        raise CompanyScribeAdmissionError(
            "Company role activation subject is outside the pinned mandate"
        )
    with conn.transaction():
        observed_mandate = conn.execute(
            "SELECT status,artifact_sha256,artifact_bytes,artifact,issued_at,"
            "authority_verification_mac FROM kernel_founder_activation_mandates "
            "WHERE mandate_id=%s FOR SHARE",
            (profile.mandate_id,),
        ).fetchone()
        if observed_mandate is None:
            raise CompanyScribeAdmissionError(
                "Company role activation mandate is not registered"
            )
        mandate_keys = (
            "status", "artifact_sha256", "artifact_bytes", "artifact",
            "issued_at", "authority_verification_mac",
        )
        mandate_row = (
            dict(observed_mandate)
            if isinstance(observed_mandate, Mapping)
            else dict(zip(mandate_keys, observed_mandate, strict=True))
        )
        expected_mac = conn.execute(
            "SELECT kernel_founder_mandate_expected_mac(%s,%s,%s,%s,%s)",
            (
                mandate.mandate_id, mandate.artifact_sha256,
                mandate.artifact_bytes, Jsonb(dict(mandate.artifact)),
                mandate.issued_at,
            ),
        ).fetchone()[0]
        if (
            mandate_row["status"] != "active"
            or mandate_row["artifact_sha256"] != mandate.artifact_sha256
            or bytes(mandate_row["artifact_bytes"]) != mandate.artifact_bytes
            or mandate_row["artifact"] != mandate.artifact
            or mandate_row["issued_at"] != mandate.issued_at
            or mandate_row["authority_verification_mac"] != expected_mac
        ):
            raise CompanyScribeAdmissionError(
                "Company role activation mandate registration changed"
            )
        conn.execute(
            "INSERT INTO kernel_company_role_activation_subjects ("
            "approval_id,company_id,role_type,deployment_id,worker_id,"
            "mandate_id,mandate_artifact_sha256,subject_ref,subject_sha256,"
            "subject_payload,state,approved_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'approved',now()) "
            "ON CONFLICT (approval_id) DO NOTHING",
            (
                binding.authority_approval_id, profile.company_id,
                profile.role_type, profile.deployment_id, profile.worker_id,
                profile.mandate_id, profile.mandate_artifact_sha256,
                binding.authority_subject_ref,
                binding.authority_subject_sha256, Jsonb(payload),
            ),
        )
        row = conn.execute(
            "SELECT approval_id,company_id,role_type,deployment_id,worker_id,"
            "mandate_id,mandate_artifact_sha256,subject_ref,subject_sha256,"
            "subject_payload,state FROM kernel_company_role_activation_subjects "
            "WHERE approval_id=%s FOR UPDATE",
            (binding.authority_approval_id,),
        ).fetchone()
        keys = (
            "approval_id", "company_id", "role_type", "deployment_id",
            "worker_id", "mandate_id", "mandate_artifact_sha256",
            "subject_ref", "subject_sha256", "subject_payload", "state",
        )
        value = dict(row) if isinstance(row, Mapping) else dict(
            zip(keys, row, strict=True)
        )
        expected = {
            "approval_id": binding.authority_approval_id,
            "company_id": profile.company_id,
            "role_type": profile.role_type,
            "deployment_id": profile.deployment_id,
            "worker_id": profile.worker_id,
            "mandate_id": profile.mandate_id,
            "mandate_artifact_sha256": profile.mandate_artifact_sha256,
            "subject_ref": binding.authority_subject_ref,
            "subject_sha256": binding.authority_subject_sha256,
            "subject_payload": payload,
            "state": "approved",
        }
        if value != expected:
            raise CompanyScribeAdmissionError(
                "Company role activation subject id belongs to another tuple"
            )
    return value


def bind_company_role_transaction(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
    binding: CompanyRoleAdmissionBinding,
) -> None:
    """Bind the current transaction with the exact role-scoped DB proof."""
    validate_company_role_authority_subject(profile=profile, binding=binding)
    payload = {
        "company_id": binding.company_id,
        "role_type": binding.role_type,
        "deployment_id": binding.deployment_id,
        "worker_id": str(binding.worker_id),
        "worker_session_id": str(binding.worker_session_id),
        "package_id": str(binding.package_id),
        "agent_release_sha256": binding.agent_release_sha256,
        "kernel_release_sha256": binding.kernel_release_sha256,
        "package_sha256": binding.package_sha256,
        "task_id": binding.task_id,
        "claude_session_id": binding.claude_session_id,
        "process_id": binding.process_id,
        "process_start_fingerprint_sha256": binding.process_start_fingerprint_sha256,
        "repo_commit_sha": binding.repo_commit_sha,
        "repo_tree_sha": binding.repo_tree_sha,
        "admission_epoch": binding.admission_epoch,
        "mandate_id": binding.mandate_id,
        "mandate_artifact_sha256": binding.mandate_artifact_sha256,
        "authority_approval_id": str(binding.authority_approval_id),
        "authority_subject_ref": binding.authority_subject_ref,
        "authority_subject_sha256": binding.authority_subject_sha256,
        "host_identity_sha256": binding.host_identity_sha256,
        "host_boot_id_sha256": binding.host_boot_id_sha256,
        "runtime_executable_path": binding.runtime_executable_path,
        "runtime_executable_sha256": binding.runtime_executable_sha256,
        "runtime_lock_path": binding.runtime_lock_path,
        "authority_mode": binding.authority_mode,
        "authority_root_admission_id": str(binding.authority_root_admission_id),
        "prior_admission_id": (
            None if binding.prior_admission_id is None
            else str(binding.prior_admission_id)
        ),
        "recovery_intent_id": (
            None if binding.recovery_intent_id is None
            else str(binding.recovery_intent_id)
        ),
        "prior_rollback_receipt_sha256": binding.prior_rollback_receipt_sha256,
        "restart_intent_sha256": binding.restart_intent_sha256,
    }
    conn.execute(
        "SELECT kernel_bind_company_role_admission(%s,%s,%s,%s)",
        (profile.role_type, binding.admission_id, binding.lease_token, Jsonb(payload)),
    )


@dataclass(frozen=True, slots=True)
class ScribeAdmissionBinding:
    admission_id: uuid.UUID
    worker_id: uuid.UUID
    worker_session_id: uuid.UUID
    package_id: uuid.UUID
    agent_release_sha256: str
    kernel_release_sha256: str
    package_sha256: str
    task_id: str
    claude_session_id: str
    process_id: int
    process_start_fingerprint_sha256: str
    repo_commit_sha: str
    repo_tree_sha: str
    lease_token: str
    admission_epoch: int
    lease_expires_at: datetime
    mandate_id: str
    mandate_artifact_sha256: str
    authority_approval_id: uuid.UUID
    authority_subject_ref: str
    authority_subject_sha256: str
    mini_activation_receipt_sha256: str
    host_identity_sha256: str
    host_boot_id_sha256: str
    runtime_executable_path: str
    runtime_executable_sha256: str
    runtime_lock_path: str
    authority_mode: str = INITIAL_AUTHORITY_MODE
    authority_root_admission_id: uuid.UUID | None = None
    prior_admission_id: uuid.UUID | None = None
    recovery_intent_id: uuid.UUID | None = None
    prior_rollback_receipt_sha256: str | None = None
    restart_intent_sha256: str | None = None
    deployment_id: str = DEPLOYMENT_ID
    company_id: str = COMPANY_ID
    runtime_kind: str = RUNTIME_KIND

    @property
    def role_type(self) -> str:
        """Strict compatibility role; callers cannot select another role."""
        return "scribe"

    def __post_init__(self) -> None:
        if (
            self.authority_mode == INITIAL_AUTHORITY_MODE
            and self.authority_root_admission_id is None
        ):
            object.__setattr__(
                self, "authority_root_admission_id", self.admission_id,
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScribeAdmissionBinding":
        if value.get("role_type", "scribe") != "scribe":
            raise CompanyScribeAdmissionError(
                "Scribe compatibility binding refuses a non-scribe role"
            )
        try:
            binding = cls(
                admission_id=uuid.UUID(_required(value.get("admission_id"), "admission_id")),
                worker_id=uuid.UUID(_required(value.get("worker_id"), "worker_id")),
                worker_session_id=uuid.UUID(
                    _required(value.get("worker_session_id"), "worker_session_id")
                ),
                package_id=uuid.UUID(_required(value.get("package_id"), "package_id")),
                agent_release_sha256=_exact_sha(
                    value.get("agent_release_sha256"), "agent_release_sha256", _SHA256,
                ),
                kernel_release_sha256=_exact_sha(
                    value.get("kernel_release_sha256"), "kernel_release_sha256", _SHA256,
                ),
                package_sha256=_exact_sha(
                    value.get("package_sha256"), "package_sha256", _SHA256,
                ),
                task_id=_required(value.get("task_id"), "task_id"),
                claude_session_id=_required(
                    value.get("claude_session_id"), "claude_session_id",
                ),
                process_id=int(value.get("process_id", 0)),
                process_start_fingerprint_sha256=_exact_sha(
                    value.get("process_start_fingerprint_sha256"),
                    "process_start_fingerprint_sha256", _SHA256,
                ),
                repo_commit_sha=_exact_sha(
                    value.get("repo_commit_sha"), "repo_commit_sha", _GIT_SHA,
                ),
                repo_tree_sha=_exact_sha(
                    value.get("repo_tree_sha"), "repo_tree_sha", _GIT_SHA,
                ),
                lease_token=_required(value.get("lease_token"), "lease_token"),
                admission_epoch=int(value.get("admission_epoch", 0)),
                lease_expires_at=_utc(value.get("lease_expires_at"), "lease_expires_at"),
                mandate_id=_required(value.get("mandate_id"), "mandate_id"),
                mandate_artifact_sha256=_exact_sha(
                    value.get("mandate_artifact_sha256"),
                    "mandate_artifact_sha256", _SHA256,
                ),
                authority_approval_id=uuid.UUID(_required(
                    value.get("authority_approval_id"), "authority_approval_id",
                )),
                authority_subject_ref=_required(
                    value.get("authority_subject_ref"), "authority_subject_ref",
                ),
                authority_subject_sha256=_exact_sha(
                    value.get("authority_subject_sha256"),
                    "authority_subject_sha256", _SHA256,
                ),
                mini_activation_receipt_sha256=_exact_sha(
                    value.get("mini_activation_receipt_sha256"),
                    "mini_activation_receipt_sha256", _SHA256,
                ),
                host_identity_sha256=_exact_sha(
                    value.get("host_identity_sha256"), "host_identity_sha256", _SHA256,
                ),
                host_boot_id_sha256=_exact_sha(
                    value.get("host_boot_id_sha256"), "host_boot_id_sha256", _SHA256,
                ),
                runtime_executable_path=_required(
                    value.get("runtime_executable_path"), "runtime_executable_path",
                ),
                runtime_executable_sha256=_exact_sha(
                    value.get("runtime_executable_sha256"),
                    "runtime_executable_sha256", _SHA256,
                ),
                runtime_lock_path=_required(
                    value.get("runtime_lock_path"), "runtime_lock_path",
                ),
                authority_mode=_required(
                    value.get("authority_mode", INITIAL_AUTHORITY_MODE),
                    "authority_mode",
                ),
                authority_root_admission_id=(
                    None
                    if value.get("authority_root_admission_id") is None
                    else uuid.UUID(_required(
                        value.get("authority_root_admission_id"),
                        "authority_root_admission_id",
                    ))
                ),
                prior_admission_id=(
                    None
                    if value.get("prior_admission_id") is None
                    else uuid.UUID(_required(
                        value.get("prior_admission_id"),
                        "prior_admission_id",
                    ))
                ),
                recovery_intent_id=(
                    None
                    if value.get("recovery_intent_id") is None
                    else uuid.UUID(_required(
                        value.get("recovery_intent_id"),
                        "recovery_intent_id",
                    ))
                ),
                prior_rollback_receipt_sha256=(
                    None
                    if value.get("prior_rollback_receipt_sha256") is None
                    else _exact_sha(
                        value.get("prior_rollback_receipt_sha256"),
                        "prior_rollback_receipt_sha256", _SHA256,
                    )
                ),
                restart_intent_sha256=(
                    None
                    if value.get("restart_intent_sha256") is None
                    else _exact_sha(
                        value.get("restart_intent_sha256"),
                        "restart_intent_sha256", _SHA256,
                    )
                ),
                deployment_id=_required(
                    value.get("deployment_id", DEPLOYMENT_ID), "deployment_id",
                ),
                company_id=_required(value.get("company_id", COMPANY_ID), "company_id"),
                runtime_kind=_required(
                    value.get("runtime_kind", RUNTIME_KIND), "runtime_kind",
                ),
            )
        except (TypeError, ValueError) as exc:
            raise CompanyScribeAdmissionError("Scribe admission binding is malformed") from exc
        if binding.process_id <= 1 or binding.admission_epoch <= 0:
            raise CompanyScribeAdmissionError("Scribe PID and admission epoch must be positive")
        if (
            binding.company_id != COMPANY_ID
            or binding.deployment_id != DEPLOYMENT_ID
            or binding.runtime_kind != RUNTIME_KIND
        ):
            raise CompanyScribeAdmissionError(
                "Scribe admission names an ineligible company, deployment, or runtime"
            )
        if binding.authority_root_admission_id is None:
            if binding.authority_mode != INITIAL_AUTHORITY_MODE:
                raise CompanyScribeAdmissionError(
                    "recovered Scribe admission lacks its authority root"
                )
            object.__setattr__(
                binding, "authority_root_admission_id", binding.admission_id,
            )
        executable = Path(binding.runtime_executable_path)
        runtime_lock = Path(binding.runtime_lock_path)
        if (
            not executable.is_absolute()
            or ".." in executable.parts
            or not runtime_lock.is_absolute()
            or ".." in runtime_lock.parts
        ):
            raise CompanyScribeAdmissionError(
                "Scribe executable or runtime-lock path is unsafe"
            )
        recovery_values = (
            binding.prior_admission_id,
            binding.recovery_intent_id,
            binding.prior_rollback_receipt_sha256,
            binding.restart_intent_sha256,
        )
        if binding.authority_mode == INITIAL_AUTHORITY_MODE:
            if (
                binding.authority_root_admission_id != binding.admission_id
                or any(value is not None for value in recovery_values)
            ):
                raise CompanyScribeAdmissionError(
                    "initial Scribe admission contains recovery authority"
                )
        elif binding.authority_mode == RECOVERY_AUTHORITY_MODE:
            if (
                binding.authority_root_admission_id == binding.admission_id
                or any(value is None for value in recovery_values)
            ):
                raise CompanyScribeAdmissionError(
                    "recovered Scribe admission lacks its exact recovery chain"
                )
        else:
            raise CompanyScribeAdmissionError(
                "Scribe admission authority mode is invalid"
            )
        try:
            subject = scribe_activation_subject(binding)
        except FounderAuthorityError as exc:
            raise CompanyScribeAdmissionError(
                "Scribe admission activation subject is malformed"
            ) from exc
        if (
            binding.authority_subject_sha256 != subject.sha256
            or binding.authority_subject_ref != subject.subject_ref
            or binding.authority_approval_id != subject.approval_id
        ):
            raise CompanyScribeAdmissionError(
                "Scribe admission authority subject does not match its exact binding"
            )
        return binding

    def to_mapping(self) -> dict[str, Any]:
        result = asdict(self)
        result["role_type"] = self.role_type
        result["admission_id"] = str(self.admission_id)
        result["worker_id"] = str(self.worker_id)
        result["worker_session_id"] = str(self.worker_session_id)
        result["package_id"] = str(self.package_id)
        result["authority_approval_id"] = str(self.authority_approval_id)
        result["authority_root_admission_id"] = (
            None
            if self.authority_root_admission_id is None
            else str(self.authority_root_admission_id)
        )
        result["prior_admission_id"] = (
            None if self.prior_admission_id is None else str(self.prior_admission_id)
        )
        result["recovery_intent_id"] = (
            None if self.recovery_intent_id is None else str(self.recovery_intent_id)
        )
        result["lease_expires_at"] = self.lease_expires_at.isoformat().replace("+00:00", "Z")
        return result


@dataclass(frozen=True, slots=True)
class ScribeVerificationEvidence:
    approval_id: uuid.UUID
    admission_receipt_sha256: str
    singleton_probe_sha256: str
    persistence_probe_sha256: str
    restart_probe_sha256: str
    receipts_manifest_sha256: str

    def __post_init__(self) -> None:
        for field in (
            "admission_receipt_sha256", "singleton_probe_sha256",
            "persistence_probe_sha256", "restart_probe_sha256",
            "receipts_manifest_sha256",
        ):
            _exact_sha(getattr(self, field), field, _SHA256)


def scribe_activation_subject(
    binding: ScribeAdmissionBinding,
) -> ActivationStepSubject:
    return ActivationStepSubject.exact(
        mandate_id=binding.mandate_id,
        step=ActivationStep.SCRIBE_ADMISSION,
        tenant_id=binding.company_id,
        release_commit_sha=binding.repo_commit_sha,
        release_tree_sha=binding.repo_tree_sha,
        release_digest_sha256=binding.kernel_release_sha256,
        package_id=binding.package_id,
        package_sha256=binding.package_sha256,
        attempt_key=(
            f"scribe-admission:{binding.admission_id}:"
            f"epoch:{binding.admission_epoch}"
        ),
        activation_binding={
            "approval_id": str(binding.authority_approval_id),
            "runtime_host_identity_sha256": binding.host_identity_sha256,
            "runtime_boot_id_sha256": binding.host_boot_id_sha256,
            "runtime_executable_sha256": binding.runtime_executable_sha256,
            "admission_id": str(binding.admission_id),
            "deployment_id": binding.deployment_id,
            "worker_id": str(binding.worker_id),
            "worker_session_id": str(binding.worker_session_id),
            "agent_release_sha256": binding.agent_release_sha256,
            "task_id": binding.task_id,
            "claude_session_id": binding.claude_session_id,
            "process_id": binding.process_id,
            "process_start_fingerprint_sha256": (
                binding.process_start_fingerprint_sha256
            ),
            "admission_epoch": binding.admission_epoch,
            "lease_expires_at": (
                binding.lease_expires_at.astimezone(timezone.utc)
                .isoformat().replace("+00:00", "Z")
            ),
            "runtime_executable_path": binding.runtime_executable_path,
            "runtime_lock_path": binding.runtime_lock_path,
            "authority_mode": binding.authority_mode,
            "authority_root_admission_id": str(
                binding.authority_root_admission_id
            ),
            "prior_admission_id": (
                None
                if binding.prior_admission_id is None
                else str(binding.prior_admission_id)
            ),
            "recovery_intent_id": (
                None
                if binding.recovery_intent_id is None
                else str(binding.recovery_intent_id)
            ),
            "prior_rollback_receipt_sha256": (
                binding.prior_rollback_receipt_sha256
            ),
            "restart_intent_sha256": binding.restart_intent_sha256,
        },
    )


def scribe_verification_subject(
    binding: ScribeAdmissionBinding,
    evidence: ScribeVerificationEvidence,
) -> ActivationStepSubject:
    """Exact final step proving the admitted logical Scribe survives restart."""
    return ActivationStepSubject.exact(
        mandate_id=binding.mandate_id,
        step=ActivationStep.SCRIBE_VERIFICATION,
        tenant_id=binding.company_id,
        release_commit_sha=binding.repo_commit_sha,
        release_tree_sha=binding.repo_tree_sha,
        release_digest_sha256=binding.kernel_release_sha256,
        package_id=binding.package_id,
        package_sha256=binding.package_sha256,
        attempt_key=(
            f"scribe-verification:{binding.admission_id}:"
            f"epoch:{binding.admission_epoch}"
        ),
        activation_binding={
            "approval_id": str(evidence.approval_id),
            "runtime_host_identity_sha256": binding.host_identity_sha256,
            "runtime_boot_id_sha256": binding.host_boot_id_sha256,
            "runtime_executable_sha256": binding.runtime_executable_sha256,
            "admission_id": str(binding.admission_id),
            "deployment_id": binding.deployment_id,
            "worker_id": str(binding.worker_id),
            "admission_epoch": binding.admission_epoch,
            "admission_receipt_sha256": evidence.admission_receipt_sha256,
            "singleton_probe_sha256": evidence.singleton_probe_sha256,
            "persistence_probe_sha256": evidence.persistence_probe_sha256,
            "restart_probe_sha256": evidence.restart_probe_sha256,
            "receipts_manifest_sha256": evidence.receipts_manifest_sha256,
        },
    )


def create_live_admission(
    conn: psycopg.Connection,
    *,
    binding: ScribeAdmissionBinding,
    mandate: FounderActivationMandate,
) -> None:
    """Persist one live epoch under local admission or exact restart lineage."""
    subject = scribe_activation_subject(binding)
    if (
        mandate.mandate_id != binding.mandate_id
        or mandate.artifact_sha256 != binding.mandate_artifact_sha256
        or binding.authority_subject_sha256 != subject.sha256
    ):
        raise CompanyScribeAdmissionError("activation authority subject is not exact")
    with conn.transaction():
        if binding.authority_mode not in {
            INITIAL_AUTHORITY_MODE, RECOVERY_AUTHORITY_MODE,
        }:
            raise CompanyScribeAdmissionError(
                "Scribe admission authority mode is unsupported"
            )
        conn.execute(
            "INSERT INTO kernel_company_scribe_admissions "
            "(id,company_id,role_type,deployment_id,worker_id,worker_session_id,package_id,"
            "agent_release_sha256,kernel_release_sha256,package_sha256,runtime_kind,"
            "task_id,claude_session_id,process_id,process_start_fingerprint_sha256,"
            "repo_commit_sha,repo_tree_sha,lease_token_sha256,admission_epoch,"
            "mandate_id,mandate_artifact_sha256,authority_approval_id,"
            "authority_subject_ref,authority_subject_sha256,"
            "mini_activation_receipt_sha256,host_identity_sha256,"
            "host_boot_id_sha256,runtime_executable_path,"
            "runtime_executable_sha256,runtime_lock_path,authority_mode,"
            "authority_root_admission_id,prior_admission_id,recovery_intent_id,"
            "prior_rollback_receipt_sha256,restart_intent_sha256,"
            "state,lease_expires_at,authority_lease_expires_at) "
            "SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "'live',%s,%s "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM kernel_company_scribe_admissions WHERE id=%s"
            ") "
            "ON CONFLICT (id) DO NOTHING",
            (
                binding.admission_id, binding.company_id, "scribe",
                binding.deployment_id,
                binding.worker_id, binding.worker_session_id, binding.package_id,
                binding.agent_release_sha256, binding.kernel_release_sha256,
                binding.package_sha256, binding.runtime_kind, binding.task_id,
                binding.claude_session_id, binding.process_id,
                binding.process_start_fingerprint_sha256, binding.repo_commit_sha,
                binding.repo_tree_sha, _sha256(binding.lease_token.encode("utf-8")),
                binding.admission_epoch, binding.mandate_id,
                binding.mandate_artifact_sha256, binding.authority_approval_id,
                binding.authority_subject_ref, binding.authority_subject_sha256,
                binding.mini_activation_receipt_sha256,
                binding.host_identity_sha256, binding.host_boot_id_sha256,
                binding.runtime_executable_path,
                binding.runtime_executable_sha256, binding.runtime_lock_path,
                binding.authority_mode, binding.authority_root_admission_id,
                binding.prior_admission_id, binding.recovery_intent_id,
                binding.prior_rollback_receipt_sha256,
                binding.restart_intent_sha256, binding.lease_expires_at,
                binding.lease_expires_at,
                binding.admission_id,
            ),
        )
        observed = conn.execute(
            "SELECT jsonb_build_object("
            "'state',state,'company_id',company_id,"
            "'deployment_id',deployment_id,'worker_id',worker_id,"
            "'worker_session_id',worker_session_id,'package_id',package_id,"
            "'admission_epoch',admission_epoch,'process_id',process_id,"
            "'process_start_fingerprint_sha256',"
            "process_start_fingerprint_sha256,"
            "'authority_subject_sha256',authority_subject_sha256,"
            "'lease_token_sha256',lease_token_sha256,"
            "'runtime_lock_path',runtime_lock_path,"
            "'authority_mode',authority_mode,"
            "'authority_root_admission_id',authority_root_admission_id,"
            "'prior_admission_id',prior_admission_id,"
            "'recovery_intent_id',recovery_intent_id,"
            "'prior_rollback_receipt_sha256',prior_rollback_receipt_sha256,"
            "'restart_intent_sha256',restart_intent_sha256"
            ") AS admission FROM kernel_company_scribe_admissions "
            "WHERE id=%s",
            (binding.admission_id,),
        ).fetchone()
        value = dict(
            observed["admission"]
            if isinstance(observed, Mapping)
            else observed[0]
        )
        expected = {
            "state": "live",
            "company_id": binding.company_id,
            "deployment_id": binding.deployment_id,
            "worker_id": str(binding.worker_id),
            "worker_session_id": str(binding.worker_session_id),
            "package_id": str(binding.package_id),
            "admission_epoch": binding.admission_epoch,
            "process_id": binding.process_id,
            "process_start_fingerprint_sha256": (
                binding.process_start_fingerprint_sha256
            ),
            "authority_subject_sha256": binding.authority_subject_sha256,
            "lease_token_sha256": _sha256(
                binding.lease_token.encode("utf-8")
            ),
            "runtime_lock_path": binding.runtime_lock_path,
            "authority_mode": binding.authority_mode,
            "authority_root_admission_id": str(
                binding.authority_root_admission_id
            ),
            "prior_admission_id": (
                None
                if binding.prior_admission_id is None
                else str(binding.prior_admission_id)
            ),
            "recovery_intent_id": (
                None
                if binding.recovery_intent_id is None
                else str(binding.recovery_intent_id)
            ),
            "prior_rollback_receipt_sha256": (
                binding.prior_rollback_receipt_sha256
            ),
            "restart_intent_sha256": binding.restart_intent_sha256,
        }
        if value != expected:
            raise CompanyScribeAdmissionError(
                "existing Scribe admission id belongs to another binding"
            )


def close_live_admission(
    conn: psycopg.Connection,
    *,
    binding: ScribeAdmissionBinding,
    state: str,
) -> dict[str, Any]:
    """Close only the exact bound admission and free the singleton for restart."""
    if state not in {"revoked", "expired"}:
        raise CompanyScribeAdmissionError(
            "Scribe admission closure must be revoked or expired"
        )
    with conn.transaction():
        row = conn.execute(
            "UPDATE kernel_company_scribe_admissions SET state=%s "
            "WHERE id=%s AND company_id=%s AND deployment_id=%s "
            "AND worker_id=%s AND worker_session_id=%s AND package_id=%s "
            "AND agent_release_sha256=%s AND kernel_release_sha256=%s "
            "AND package_sha256=%s AND task_id=%s AND claude_session_id=%s "
            "AND process_id=%s AND process_start_fingerprint_sha256=%s "
            "AND repo_commit_sha=%s AND repo_tree_sha=%s "
            "AND lease_token_sha256=%s AND admission_epoch=%s "
            "AND mandate_id=%s AND mandate_artifact_sha256=%s "
            "AND authority_approval_id=%s AND authority_subject_ref=%s "
            "AND authority_subject_sha256=%s "
            "AND mini_activation_receipt_sha256=%s "
            "AND host_identity_sha256=%s AND host_boot_id_sha256=%s "
            "AND runtime_executable_path=%s "
            "AND runtime_executable_sha256=%s AND runtime_lock_path=%s "
            "AND authority_mode=%s AND authority_root_admission_id=%s "
            "AND prior_admission_id IS NOT DISTINCT FROM %s "
            "AND recovery_intent_id IS NOT DISTINCT FROM %s "
            "AND prior_rollback_receipt_sha256 IS NOT DISTINCT FROM %s "
            "AND restart_intent_sha256 IS NOT DISTINCT FROM %s "
            "AND state='live' "
            "RETURNING jsonb_build_object("
            "'state',state,'closed_at',closed_at,"
            "'admission_epoch',admission_epoch,"
            "'process_id',process_id) AS closure",
            (
                state, binding.admission_id, binding.company_id,
                binding.deployment_id, binding.worker_id,
                binding.worker_session_id, binding.package_id,
                binding.agent_release_sha256, binding.kernel_release_sha256,
                binding.package_sha256, binding.task_id,
                binding.claude_session_id, binding.process_id,
                binding.process_start_fingerprint_sha256,
                binding.repo_commit_sha, binding.repo_tree_sha,
                _sha256(binding.lease_token.encode("utf-8")),
                binding.admission_epoch, binding.mandate_id,
                binding.mandate_artifact_sha256,
                binding.authority_approval_id,
                binding.authority_subject_ref,
                binding.authority_subject_sha256,
                binding.mini_activation_receipt_sha256,
                binding.host_identity_sha256,
                binding.host_boot_id_sha256,
                binding.runtime_executable_path,
                binding.runtime_executable_sha256,
                binding.runtime_lock_path, binding.authority_mode,
                binding.authority_root_admission_id,
                binding.prior_admission_id, binding.recovery_intent_id,
                binding.prior_rollback_receipt_sha256,
                binding.restart_intent_sha256,
            ),
        ).fetchone()
        if row is not None:
            return dict(row["closure"] if isinstance(row, Mapping) else row[0])
        existing = conn.execute(
            "SELECT jsonb_build_object("
            "'state',state,'closed_at',closed_at,"
            "'admission_epoch',admission_epoch,"
            "'process_id',process_id,"
            "'authority_subject_sha256',authority_subject_sha256"
            ") AS closure "
            "FROM kernel_company_scribe_admissions "
            "WHERE id=%s AND company_id=%s",
            (binding.admission_id, binding.company_id),
        ).fetchone()
        if existing is None:
            raise CompanyScribeAdmissionError(
                "Scribe admission closure target does not exist"
            )
        closure = dict(
            existing["closure"] if isinstance(existing, Mapping) else existing[0]
        )
        if (
            closure.get("state") != state
            or int(closure.get("admission_epoch", 0)) != binding.admission_epoch
            or int(closure.get("process_id", 0)) != binding.process_id
            or closure.get("authority_subject_sha256")
            != binding.authority_subject_sha256
        ):
            raise CompanyScribeAdmissionError(
                "Scribe admission closure target is not the exact binding"
            )
        return closure


def end_bound_worker_session(
    conn: psycopg.Connection,
    *,
    binding: ScribeAdmissionBinding,
) -> dict[str, Any]:
    """Terminally end only the admission-bound worker session."""
    lease_sha256 = _sha256(binding.lease_token.encode("utf-8"))
    with conn.transaction():
        row = conn.execute(
            "UPDATE kernel_worker_sessions SET state='ended' "
            "WHERE id=%s AND tenant_id=%s AND worker_id=%s "
            "AND role_type='scribe' AND agent_release_sha256=%s "
            "AND lease_token=%s AND state='active' "
            "RETURNING jsonb_build_object("
            "'state',state,'ended_at',ended_at) AS closure",
            (
                binding.worker_session_id, binding.company_id,
                binding.worker_id, binding.agent_release_sha256,
                binding.lease_token,
            ),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT jsonb_build_object("
                "'state',state,'ended_at',ended_at,"
                "'worker_id',worker_id,'lease_token_sha256',"
                "encode(digest(convert_to(lease_token,'UTF8'),'sha256'),'hex')"
                ") AS closure FROM kernel_worker_sessions "
                "WHERE id=%s AND tenant_id=%s",
                (binding.worker_session_id, binding.company_id),
            ).fetchone()
            if row is None:
                raise CompanyScribeAdmissionError(
                    "bound Scribe worker session disappeared"
                )
            closure = dict(
                row["closure"] if isinstance(row, Mapping) else row[0]
            )
            if (
                closure.get("state") != "ended"
                or closure.get("worker_id") != str(binding.worker_id)
                or closure.get("lease_token_sha256") != lease_sha256
            ):
                raise CompanyScribeAdmissionError(
                    "Scribe worker-session closure target is not exact"
                )
        else:
            closure = dict(
                row["closure"] if isinstance(row, Mapping) else row[0]
            )
        conn.execute(
            "UPDATE kernel_runtime_workers "
            "SET lease_token=NULL,lease_expires_at=NULL "
            "WHERE id=%s AND tenant_id=%s AND role_type='scribe' "
            "AND lease_token=%s",
            (
                binding.worker_id, binding.company_id,
                binding.lease_token,
            ),
        )
        return closure


def renew_live_admission(
    conn: psycopg.Connection,
    *,
    binding: ScribeAdmissionBinding,
    lease_seconds: int = 120,
) -> datetime:
    """Provisioner-side bounded renewal of the session and admission together."""
    if lease_seconds < 30 or lease_seconds > 300:
        raise CompanyScribeAdmissionError(
            "Scribe lease renewal must be between 30 and 300 seconds"
        )
    with conn.transaction():
        clock = conn.execute(
            "SELECT clock_timestamp() AS now,"
            "clock_timestamp()+(%s * interval '1 second') AS expires",
            (lease_seconds,),
        ).fetchone()
        now_value = clock["now"] if isinstance(clock, Mapping) else clock[0]
        expires = clock["expires"] if isinstance(clock, Mapping) else clock[1]
        session = conn.execute(
            "UPDATE kernel_worker_sessions "
            "SET heartbeat_at=%s,lease_expires_at=%s "
            "WHERE id=%s AND tenant_id=%s AND worker_id=%s "
            "AND role_type='scribe' AND agent_release_sha256=%s "
            "AND lease_token=%s AND state='active' "
            "RETURNING id",
            (
                now_value, expires, binding.worker_session_id,
                binding.company_id, binding.worker_id,
                binding.agent_release_sha256, binding.lease_token,
            ),
        ).fetchone()
        if session is None:
            raise CompanyScribeAdmissionError(
                "Scribe worker-session renewal is stale or fenced"
            )
        worker = conn.execute(
            "UPDATE kernel_runtime_workers SET lease_expires_at=%s "
            "WHERE id=%s AND tenant_id=%s AND role_type='scribe' "
            "AND status='active' AND lease_token=%s RETURNING id",
            (
                expires, binding.worker_id, binding.company_id,
                binding.lease_token,
            ),
        ).fetchone()
        if worker is None:
            raise CompanyScribeAdmissionError(
                "Scribe worker renewal is stale or fenced"
            )
        admission = conn.execute(
            "UPDATE kernel_company_scribe_admissions "
            "SET heartbeat_at=%s,lease_expires_at=%s "
            "WHERE id=%s AND company_id=%s AND deployment_id=%s "
            "AND worker_id=%s AND worker_session_id=%s "
            "AND process_id=%s AND process_start_fingerprint_sha256=%s "
            "AND admission_epoch=%s AND authority_subject_sha256=%s "
            "AND lease_token_sha256=%s AND state='live' "
            "RETURNING id",
            (
                now_value, expires, binding.admission_id,
                binding.company_id, binding.deployment_id,
                binding.worker_id, binding.worker_session_id,
                binding.process_id,
                binding.process_start_fingerprint_sha256,
                binding.admission_epoch,
                binding.authority_subject_sha256,
                _sha256(binding.lease_token.encode("utf-8")),
            ),
        ).fetchone()
        if admission is None:
            raise CompanyScribeAdmissionError(
                "Scribe admission renewal is stale or fenced"
            )
    return expires.astimezone(timezone.utc)


_COMPANY_STATE_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "kernel_company_objectives",
        "SELECT to_jsonb(row_value) AS payload "
        "FROM kernel_company_objectives AS row_value "
        "WHERE company_id=%s ORDER BY id",
    ),
    (
        "kernel_company_objective_events",
        "SELECT to_jsonb(row_value) AS payload "
        "FROM kernel_company_objective_events AS row_value "
        "WHERE company_id=%s ORDER BY objective_id,event_seq,id",
    ),
    (
        "kernel_company_review_receipts",
        "SELECT to_jsonb(row_value) AS payload "
        "FROM kernel_company_review_receipts AS row_value "
        "WHERE company_id=%s ORDER BY objective_id,issued_at,id",
    ),
)
_COMPANY_STATE_SCHEMA_TABLES = frozenset({
    "kernel_company_principals",
    "kernel_company_reviewer_principals",
    "kernel_company_review_attestor_principals",
    "kernel_company_review_mac_signer_principals",
    "kernel_company_review_hmac_keys",
    "kernel_company_worker_principals",
    "kernel_company_objectives",
    "kernel_company_objective_events",
    "kernel_company_review_receipts",
    "kernel_company_scribe_admission_keys",
    "kernel_company_scribe_packages",
    "kernel_company_scribe_admissions",
    "kernel_company_scribe_recovery_intents",
    "kernel_company_role_admission_targets",
    "kernel_company_role_activation_subjects",
    "kernel_company_role_provisioner_principals",
    # Company message plane (061 + 062) — schema coverage only; digests stay
    # on objectives/events/review receipts unless query list is extended.
    "kernel_company_messages",
    "kernel_company_message_recipient_sequences",
    "kernel_company_message_send_receipts",
    "kernel_company_message_delivery_attempts",
    "kernel_company_message_consumption_receipts",
    "kernel_company_message_cutover_registry",
    "kernel_company_message_migration_claims",
})


def company_state_snapshot(conn: psycopg.Connection) -> dict[str, Any]:
    """Digest the canonical Company Workspace state without exposing its rows."""
    schema_rows = conn.execute(
        "SELECT tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname='public' AND tablename LIKE 'kernel_company_%' "
        "ORDER BY tablename"
    ).fetchall()
    observed_schema = frozenset(
        (
            row["tablename"] if isinstance(row, Mapping) else row[0]
        )
        for row in schema_rows
    )
    if observed_schema != _COMPANY_STATE_SCHEMA_TABLES:
        unexpected = sorted(observed_schema - _COMPANY_STATE_SCHEMA_TABLES)
        missing = sorted(_COMPANY_STATE_SCHEMA_TABLES - observed_schema)
        raise CompanyScribeAdmissionError(
            "Company Workspace snapshot coverage is stale for the live schema "
            f"(unexpected={unexpected}, missing={missing})"
        )
    tables: list[dict[str, Any]] = []
    for table_name, query in _COMPANY_STATE_QUERIES:
        rows = conn.execute(query, (COMPANY_ID,)).fetchall()
        payloads = [
            row["payload"] if isinstance(row, Mapping) else row[0]
            for row in rows
        ]
        tables.append({
            "name": table_name,
            "row_count": len(payloads),
            "rows_sha256": _sha256(_canonical_json({"rows": payloads})),
        })
    basis = {
        "schema": COMPANY_STATE_SNAPSHOT_SCHEMA,
        "company_id": COMPANY_ID,
        "tables": tables,
    }
    return {**basis, "snapshot_sha256": _sha256(_canonical_json(basis))}


def _process_state(binding: ScribeAdmissionBinding) -> str:
    if host_identity_sha256() != binding.host_identity_sha256:
        raise CompanyScribeAdmissionError(
            "Scribe rollback is running on a different host"
        )
    if boot_identity_sha256() != binding.host_boot_id_sha256:
        return "previous_boot"
    try:
        os.kill(binding.process_id, 0)
    except ProcessLookupError:
        return "absent"
    except PermissionError as exc:
        raise CompanyScribeAdmissionError(
            "Scribe rollback cannot inspect the bound process"
        ) from exc
    status = subprocess.run(
        ["ps", "-p", str(binding.process_id), "-o", "stat="],
        check=False, capture_output=True, text=True,
    )
    if status.returncode or not status.stdout.strip():
        return "absent"
    if status.stdout.strip().startswith("Z"):
        return "zombie"
    try:
        observed = process_start_fingerprint(binding.process_id)
    except CompanyScribeAdmissionError as exc:
        raise CompanyScribeAdmissionError(
            "Scribe rollback could not prove the live process identity"
        ) from exc
    if observed != binding.process_start_fingerprint_sha256:
        return "pid_reused"
    return "exact_live"


def quiesce_bound_process(
    binding: ScribeAdmissionBinding,
    *,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Stop only the PID/start/host/boot-bound process after DB revocation."""
    if timeout_seconds < 0 or timeout_seconds > 60:
        raise CompanyScribeAdmissionError(
            "Scribe quiesce timeout must be between 0 and 60 seconds"
        )
    if binding.process_id == os.getpid():
        raise CompanyScribeAdmissionError(
            "Scribe rollback cannot terminate its own controller process"
        )
    initial = _process_state(binding)
    result: dict[str, Any] = {
        "initial_process_state": initial,
        "termination_signal": "none",
        "forced": False,
    }
    if initial != "exact_live":
        result["final_process_state"] = initial
        return result

    try:
        os.kill(binding.process_id, signal.SIGTERM)
    except ProcessLookupError:
        result["final_process_state"] = "absent"
        return result
    result["termination_signal"] = "SIGTERM"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        observed = _process_state(binding)
        if observed != "exact_live":
            result["final_process_state"] = observed
            return result
        time.sleep(0.05)

    # Recheck the immutable PID/start fingerprint immediately before force.
    if _process_state(binding) == "exact_live":
        try:
            os.kill(binding.process_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        else:
            result["termination_signal"] = "SIGKILL"
            result["forced"] = True
    kill_deadline = time.monotonic() + 5.0
    while time.monotonic() < kill_deadline:
        observed = _process_state(binding)
        if observed != "exact_live":
            result["final_process_state"] = observed
            return result
        time.sleep(0.05)
    raise CompanyScribeAdmissionError(
        "Scribe process did not quiesce after exact-bound termination"
    )


def bind_transaction(
    conn: psycopg.Connection, binding: ScribeAdmissionBinding,
) -> None:
    """Bind the exact live admission to the current database transaction."""
    conn.execute(
        "SELECT kernel_bind_company_scribe_admission("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            binding.admission_id, binding.lease_token, binding.deployment_id,
            binding.agent_release_sha256, binding.kernel_release_sha256,
            binding.package_sha256, binding.task_id, binding.claude_session_id,
            binding.process_id, binding.process_start_fingerprint_sha256,
            binding.repo_commit_sha, binding.repo_tree_sha,
            binding.admission_epoch, binding.mandate_id,
            binding.mandate_artifact_sha256, binding.authority_approval_id,
            binding.authority_subject_ref, binding.authority_subject_sha256,
            binding.mini_activation_receipt_sha256,
            binding.host_identity_sha256, binding.host_boot_id_sha256,
            binding.runtime_executable_path, binding.runtime_executable_sha256,
            binding.runtime_lock_path, binding.authority_mode,
            binding.authority_root_admission_id, binding.prior_admission_id,
            binding.recovery_intent_id,
            binding.prior_rollback_receipt_sha256,
            binding.restart_intent_sha256,
        ),
    )


def _secure_directory(path: Path, *, create: bool = False) -> Path:
    """Reject symlinked or group/world-writable ancestry before local authority I/O."""
    if not path.is_absolute():
        raise CompanyScribeAdmissionError("Scribe authority paths must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                raise CompanyScribeAdmissionError(
                    f"Scribe authority directory is missing: {current}"
                ) from None
            try:
                os.mkdir(current, 0o700)
            except FileExistsError:
                pass
            info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise CompanyScribeAdmissionError(
                f"Scribe authority directory ancestry is unsafe: {current}"
            )
        if info.st_uid not in {0, os.getuid()} or stat.S_IMODE(info.st_mode) & 0o022:
            raise CompanyScribeAdmissionError(
                f"Scribe authority directory owner/mode is unsafe: {current}"
            )
    return path


def _read_private_file(path: Path) -> bytes:
    _secure_directory(path.parent)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "Scribe admission binding is unavailable"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise CompanyScribeAdmissionError(
                "Scribe admission binding owner/type is unsafe"
            )
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise CompanyScribeAdmissionError(
                "Scribe admission binding must be mode 0600 or stricter"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def load_binding(path: Path) -> ScribeAdmissionBinding:
    try:
        payload = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError("Scribe admission binding is unreadable") from exc
    if not isinstance(payload, dict):
        raise CompanyScribeAdmissionError("Scribe admission binding is not an object")
    return ScribeAdmissionBinding.from_mapping(payload)


def verify_local_binding(
    binding: ScribeAdmissionBinding,
    *,
    release_package: Path,
    expected_pid: int | None = None,
    now: datetime | None = None,
    require_unexpired_lease: bool = True,
) -> None:
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if require_unexpired_lease and binding.lease_expires_at <= current_time:
        raise CompanyScribeAdmissionError("Scribe admission lease is expired")
    pid = expected_pid if expected_pid is not None else os.getpid()
    if binding.process_id != pid:
        raise CompanyScribeAdmissionError("Scribe admission belongs to another process")
    if process_start_fingerprint(pid) != binding.process_start_fingerprint_sha256:
        raise CompanyScribeAdmissionError("Scribe PID start fingerprint changed")
    manifest = verify_complete_release_package(release_package)
    if (
        manifest["source_commit_sha"] != binding.repo_commit_sha
        or manifest["source_tree_sha"] != binding.repo_tree_sha
        or manifest["kernel_release_sha256"]
        != binding.kernel_release_sha256
        or manifest["package_sha256"] != binding.package_sha256
        or uuid.UUID(str(manifest["package_id"])) != binding.package_id
    ):
        raise CompanyScribeAdmissionError(
            "Scribe installed release package changed"
        )
    if host_identity_sha256() != binding.host_identity_sha256:
        raise CompanyScribeAdmissionError("Scribe host identity changed")
    if boot_identity_sha256() != binding.host_boot_id_sha256:
        raise CompanyScribeAdmissionError("Scribe boot identity changed")
    executable = Path(binding.runtime_executable_path)
    if str(executable.resolve(strict=True)) != binding.runtime_executable_path:
        raise CompanyScribeAdmissionError(
            "Scribe runtime executable path is not canonical"
        )
    if executable_sha256(executable) != binding.runtime_executable_sha256:
        raise CompanyScribeAdmissionError("Scribe runtime executable changed")
    verify_runtime_lock_held(
        Path(binding.runtime_lock_path), expected_pid=binding.process_id,
    )
    subject = scribe_activation_subject(binding)
    if (
        subject.sha256 != binding.authority_subject_sha256
        or subject.subject_ref != binding.authority_subject_ref
        or subject.approval_id != binding.authority_approval_id
    ):
        raise CompanyScribeAdmissionError("Scribe authority subject changed")


@contextmanager
def runtime_lock(path: Path) -> Iterator[int]:
    """Hold the local single-writer lock for the entire exec'd runtime."""
    _secure_directory(path.parent, create=True)
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise CompanyScribeAdmissionError(
                "Scribe runtime lock owner/type/mode is unsafe"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CompanyScribeAdmissionError(
                "another writable Company Scribe runtime already holds the lock"
            ) from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield descriptor
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _safe_relative(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise CompanyScribeAdmissionError("package file path escapes the repository")
    return candidate


def _git_blob(repo: Path, commit: str, relative: str) -> tuple[bytes, int, str]:
    listing = _git(repo, "ls-tree", commit, "--", relative)
    lines = listing.splitlines()
    if len(lines) != 1 or "\t" not in lines[0]:
        raise CompanyScribeAdmissionError(
            f"Scribe package path is not one exact tracked file: {relative}"
        )
    header, listed_path = lines[0].split("\t", 1)
    parts = header.split()
    if listed_path != relative or len(parts) != 3 or parts[1] != "blob":
        raise CompanyScribeAdmissionError(
            f"Scribe package path is not a commit blob: {relative}"
        )
    git_mode, _, object_sha = parts
    if git_mode not in {"100644", "100755"} or _GIT_SHA.fullmatch(object_sha) is None:
        raise CompanyScribeAdmissionError(
            f"Scribe package blob mode/object is unsafe: {relative}"
        )
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", f"{commit}:{relative}"],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise CompanyScribeAdmissionError(
            f"Scribe package blob is unavailable from the exact commit: {relative}"
        )
    return result.stdout, (0o755 if git_mode == "100755" else 0o644), object_sha


def build_held_package(
    *,
    repo: Path,
    output_dir: Path,
    kernel_release_sha256: str,
    files: Sequence[str],
) -> Path:
    """Build an immutable, exact-byte package without installing or activating."""
    release = _exact_sha(kernel_release_sha256, "kernel release", _SHA256)
    commit = _exact_sha(_git(repo, "rev-parse", "HEAD^{commit}"), "commit", _GIT_SHA)
    tree = _exact_sha(_git(repo, "rev-parse", "HEAD^{tree}"), "tree", _GIT_SHA)
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise CompanyScribeAdmissionError(
            "Scribe package requires a clean exact release worktree"
        )
    records: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    normalized = sorted({_safe_relative(item).as_posix() for item in files})
    if not normalized:
        raise CompanyScribeAdmissionError("Scribe package file allowlist is empty")
    for relative_text in normalized:
        source = repo / relative_text
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or source.is_symlink():
            raise CompanyScribeAdmissionError(
                f"Scribe package source is not a regular file: {relative_text}"
            )
        blob, committed_mode, object_sha = _git_blob(repo, commit, relative_text)
        if source.read_bytes() != blob:
            raise CompanyScribeAdmissionError(
                f"Scribe package source differs from its commit: {relative_text}"
            )
        if bool(info.st_mode & stat.S_IXUSR) != bool(committed_mode & stat.S_IXUSR):
            raise CompanyScribeAdmissionError(
                f"Scribe package source mode differs from its commit: {relative_text}"
            )
        blobs[relative_text] = blob
        records.append({
            "path": relative_text,
            "sha256": _sha256(blob),
            "mode": committed_mode,
            "git_object_sha": object_sha,
        })
    basis = {
        "schema": PACKAGE_SCHEMA,
        "company_id": COMPANY_ID,
        "deployment_id": DEPLOYMENT_ID,
        "host_kind": "macbook",
        "runtime_kind": RUNTIME_KIND,
        "kernel_release_sha256": release,
        "source_commit_sha": commit,
        "source_tree_sha": tree,
        "files": records,
    }
    package_sha = _sha256(_canonical_json(basis))
    package_id = uuid.uuid5(uuid.NAMESPACE_URL, f"mise:{DEPLOYMENT_ID}:{package_sha}")
    manifest = {
        **basis, "package_id": str(package_id), "package_sha256": package_sha,
        "state": "held",
    }
    target = output_dir / package_sha
    try:
        target_info = target.lstat()
    except FileNotFoundError:
        target_info = None
    if target_info is not None:
        verify_package(target)
        return target
    _secure_directory(output_dir, create=True)
    staging = Path(tempfile.mkdtemp(prefix=".scribe-package-", dir=output_dir))
    try:
        payload = staging / "payload"
        payload.mkdir(mode=0o700)
        for record in records:
            destination = payload / record["path"]
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes(blobs[record["path"]])
            os.chmod(destination, int(record["mode"]))
        (staging / "manifest.json").write_bytes(_canonical_json(manifest) + b"\n")
        os.chmod(staging / "manifest.json", 0o600)
        verify_package(staging)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target


def _copy_runtime_tree(source: Path, destination: Path) -> None:
    def ignored(_directory: str, names: list[str]) -> set[str]:
        return {
            name for name in names
            if name == "site-packages"
            or name == "__pycache__"
            or name.endswith((".pyc", ".pyo"))
        }

    shutil.copytree(
        source, destination, symlinks=False, ignore=ignored,
    )


def _patch_copied_python(executable: Path, copied_python: Path) -> None:
    listing = subprocess.run(
        ["/usr/bin/otool", "-L", str(executable)],
        check=False, capture_output=True, text=True,
    )
    old = next(
        (
            line.strip().split(" ", 1)[0]
            for line in listing.stdout.splitlines()[1:]
            if line.strip().split(" ", 1)[0].endswith("/Python")
            and not line.strip().startswith("/System/")
        ),
        None,
    )
    if listing.returncode or old is None:
        raise CompanyScribeAdmissionError(
            "packaged Python framework dependency is not exact"
        )
    relative = os.path.relpath(copied_python, executable.parent)
    replacement = f"@executable_path/{relative}"
    changed = subprocess.run(
        ["/usr/bin/install_name_tool", "-change", old, replacement, str(executable)],
        check=False, capture_output=True, text=True,
    )
    if changed.returncode:
        raise CompanyScribeAdmissionError(
            "packaged Python executable could not be made relocatable"
        )
    signed = subprocess.run(
        [
            "/usr/bin/codesign", "--force", "--sign", "-",
            "--timestamp=none", str(executable),
        ],
        check=False, capture_output=True, text=True,
    )
    if signed.returncode:
        raise CompanyScribeAdmissionError(
            "packaged Python executable could not be sealed"
        )


def _reviewed_bun_executable() -> Path:
    value = shutil.which("bun")
    if not value:
        raise CompanyScribeAdmissionError(
            "reviewed Bun executable is unavailable"
        )
    path = Path(value).resolve(strict=True)
    info = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o022
        or _sha256(path.read_bytes()) != _PINNED_BUN_SHA256
    ):
        raise CompanyScribeAdmissionError(
            "reviewed Bun executable bytes or ownership changed"
        )
    probe = subprocess.run(
        [str(path), "--version"],
        env={"PATH": "/usr/bin:/bin", "HOME": "/var/empty"},
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe.returncode or probe.stdout.strip() != _PINNED_BUN_VERSION:
        raise CompanyScribeAdmissionError(
            "reviewed Bun executable version changed"
        )
    return path


def _verify_frozen_channel_bundle(root: Path) -> tuple[Path, str]:
    """Verify the committed source+lock+bundle linkage from exact bytes."""
    manifest_path = root / _FROZEN_CHANNEL_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "committed Scribe channel bundle linkage is unreadable"
        ) from exc
    required = {
        "schema", "bundler", "build_command", "entrypoint", "lockfile",
        "inputs", "bundle", "bundle_sha256", "link_sha256",
    }
    if (
        not isinstance(manifest, dict)
        or frozenset(manifest) != required
        or manifest.get("schema") != FROZEN_CHANNEL_BUNDLE_SCHEMA
        or manifest.get("bundler") != "bun-1.3.12"
        or manifest.get("build_command") != [
            "bun", "--no-install", "build", "webhook.ts", "--target=bun",
            "--outfile=webhook.bundle.js",
        ]
        or manifest.get("entrypoint") != "channels/scribe/webhook.ts"
        or manifest.get("lockfile") != "channels/scribe/bun.lock"
        or manifest.get("bundle") != _FROZEN_CHANNEL_BUNDLE
    ):
        raise CompanyScribeAdmissionError(
            "committed Scribe channel bundle linkage is not closed"
        )
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list) or len(inputs) != len(_FROZEN_CHANNEL_INPUTS):
        raise CompanyScribeAdmissionError(
            "committed Scribe channel bundle input inventory is not closed"
        )
    expected_records: list[dict[str, str]] = []
    for relative in _FROZEN_CHANNEL_INPUTS:
        path = root / relative
        try:
            info = path.lstat()
            data = path.read_bytes()
        except OSError as exc:
            raise CompanyScribeAdmissionError(
                f"committed Scribe channel input is absent: {relative}"
            ) from exc
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise CompanyScribeAdmissionError(
                f"committed Scribe channel input is unsafe: {relative}"
            )
        expected_records.append({"path": relative, "sha256": _sha256(data)})
    if inputs != expected_records:
        raise CompanyScribeAdmissionError(
            "committed Scribe channel source or lock bytes changed"
        )
    bundle = root / _FROZEN_CHANNEL_BUNDLE
    try:
        bundle_info = bundle.lstat()
        bundle_sha = _sha256(bundle.read_bytes())
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "committed Scribe channel bundle is absent"
        ) from exc
    if bundle.is_symlink() or not stat.S_ISREG(bundle_info.st_mode):
        raise CompanyScribeAdmissionError(
            "committed Scribe channel bundle is unsafe"
        )
    if bundle_sha != _exact_sha(
        manifest.get("bundle_sha256"), "frozen channel bundle", _SHA256,
    ):
        raise CompanyScribeAdmissionError(
            "committed Scribe channel bundle bytes changed"
        )
    basis = {key: manifest[key] for key in sorted(required - {"link_sha256"})}
    if _sha256(_canonical_json(basis)) != _exact_sha(
        manifest.get("link_sha256"), "frozen channel linkage", _SHA256,
    ):
        raise CompanyScribeAdmissionError(
            "committed Scribe source-lock-bundle linkage changed"
        )
    bun = _reviewed_bun_executable()
    with tempfile.TemporaryDirectory(
        prefix="scribe-channel-rebuild-",
    ) as temporary_value:
        temporary = Path(temporary_value)
        for relative in _FROZEN_CHANNEL_INPUTS:
            source = root / relative
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        home = temporary / "home"
        cache = temporary / "cache"
        home.mkdir(mode=0o700)
        cache.mkdir(mode=0o700)
        rebuilt = subprocess.run(
            [
                str(bun), "--no-install", "build", "webhook.ts",
                "--target=bun", "--outfile=webhook.bundle.js",
            ],
            cwd=temporary / "channels" / "scribe",
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "XDG_CACHE_HOME": str(cache),
                "BUN_INSTALL_CACHE_DIR": str(cache / "bun"),
            },
            check=False,
            capture_output=True,
            timeout=60,
        )
        rebuilt_path = (
            temporary / "channels" / "scribe" / "webhook.bundle.js"
        )
        if (
            rebuilt.returncode
            or not rebuilt_path.is_file()
            or rebuilt_path.read_bytes() != bundle.read_bytes()
        ):
            raise CompanyScribeAdmissionError(
                "committed Scribe channel bundle is not derivable from the "
                "closed tracked inputs with reviewed Bun"
            )
    return bundle, bundle_sha


def _build_offline_runtime(*, payload: Path) -> dict[str, str]:
    """Copy the cold runtime and consume only the frozen channel bundle."""
    if sys.version_info[:2] != (3, 14):
        raise CompanyScribeAdmissionError(
            "Company Scribe package build requires the reviewed Python 3.14 runtime"
        )
    base = Path(sys.base_prefix).resolve(strict=True)
    version = "python3.14"
    source_python = base / "bin" / version
    source_framework = base / "Python"
    source_stdlib = base / "lib" / version
    source_resources = base / "Resources"
    for required in (
        source_python, source_framework, source_stdlib, source_resources,
    ):
        if not required.exists():
            raise CompanyScribeAdmissionError(
                f"reviewed Python runtime dependency is absent: {required}"
            )
    runtime = payload / "runtime"
    python_root = runtime / "python"
    (python_root / "bin").mkdir(parents=True, mode=0o700)
    (python_root / "lib").mkdir(mode=0o700)
    shutil.copy2(source_python, python_root / "bin" / version)
    shutil.copy2(source_framework, python_root / "Python")
    _copy_runtime_tree(source_stdlib, python_root / "lib" / version)
    _copy_runtime_tree(source_resources, python_root / "Resources")
    site = python_root / "lib" / version / "site-packages"
    site.mkdir(mode=0o700)
    active_site = Path(psycopg.__file__).resolve().parents[1]
    dependency_names = (
        "psycopg", "psycopg_binary", "typing_extensions.py",
    )
    copied = 0
    for name in dependency_names:
        source = active_site / name
        if source.is_dir():
            _copy_runtime_tree(source, site / name)
        elif source.is_file():
            shutil.copy2(source, site / name)
        else:
            raise CompanyScribeAdmissionError(
                f"reviewed Python dependency is absent: {name}"
            )
        copied += 1
    for pattern in (
        "psycopg-*.dist-info", "psycopg_binary-*.dist-info",
        "typing_extensions-*.dist-info",
    ):
        matches = sorted(active_site.glob(pattern))
        if len(matches) != 1:
            raise CompanyScribeAdmissionError(
                f"reviewed Python dependency metadata is ambiguous: {pattern}"
            )
        _copy_runtime_tree(matches[0], site / matches[0].name)
    python_executable = python_root / "bin" / version
    app_executable = (
        python_root / "Resources" / "Python.app" / "Contents"
        / "MacOS" / "Python"
    )
    _patch_copied_python(python_executable, python_root / "Python")
    _patch_copied_python(app_executable, python_root / "Python")

    bun_source = _reviewed_bun_executable()
    bun_executable = runtime / "bun"
    shutil.copy2(bun_source, bun_executable)
    frozen_bundle, _ = _verify_frozen_channel_bundle(payload)
    bundle = runtime / "scribe-channel.js"
    shutil.copy2(frozen_bundle, bundle)
    return {
        "python_executable": python_executable.relative_to(payload).as_posix(),
        "bun_executable": bun_executable.relative_to(payload).as_posix(),
        "scribe_channel_bundle": bundle.relative_to(payload).as_posix(),
    }


def build_complete_held_package(
    *,
    repo: Path,
    output_dir: Path,
    kernel_release_sha256: str,
    files: Sequence[str],
) -> Path:
    """Build the exact source plus a relocatable, cold/offline runtime closure."""
    _secure_directory(output_dir, create=True)
    assembly = Path(tempfile.mkdtemp(prefix=".scribe-complete-", dir=output_dir))
    try:
        core_root = assembly / "core"
        core = build_held_package(
            repo=repo,
            output_dir=core_root,
            kernel_release_sha256=kernel_release_sha256,
            files=files,
        )
        core_manifest = verify_package(core)
        staging = assembly / "complete"
        payload = staging / "payload"
        shutil.copytree(core / "payload", payload)
        runtime = _build_offline_runtime(payload=payload)
        records: list[dict[str, Any]] = []
        git_records = {
            str(record["path"]): record
            for record in core_manifest["files"]
        }
        for item in sorted(payload.rglob("*")):
            if item.is_symlink():
                raise CompanyScribeAdmissionError(
                    "complete Scribe package contains a runtime symlink"
                )
            if not item.is_file():
                continue
            relative = item.relative_to(payload).as_posix()
            executable = bool(item.stat().st_mode & stat.S_IXUSR)
            mode = 0o555 if executable else 0o444
            os.chmod(item, mode)
            data_sha = _sha256(item.read_bytes())
            if relative in git_records:
                records.append({
                    "path": relative,
                    "sha256": data_sha,
                    "mode": mode,
                    "source_kind": "git",
                    "source_sha": git_records[relative]["git_object_sha"],
                })
            else:
                records.append({
                    "path": relative,
                    "sha256": data_sha,
                    "mode": mode,
                    "source_kind": "runtime",
                    "source_sha": data_sha,
                })
        runtime_with_hashes = {
            **runtime,
            "python_executable_sha256": _sha256(
                (payload / runtime["python_executable"]).read_bytes()
            ),
            "bun_executable_sha256": _sha256(
                (payload / runtime["bun_executable"]).read_bytes()
            ),
            "scribe_channel_bundle_sha256": _sha256(
                (payload / runtime["scribe_channel_bundle"]).read_bytes()
            ),
        }
        basis = {
            "schema": COMPLETE_PACKAGE_SCHEMA,
            "company_id": COMPANY_ID,
            "deployment_id": DEPLOYMENT_ID,
            "host_kind": "macbook",
            "runtime_kind": RUNTIME_KIND,
            "kernel_release_sha256": core_manifest["kernel_release_sha256"],
            "source_commit_sha": core_manifest["source_commit_sha"],
            "source_tree_sha": core_manifest["source_tree_sha"],
            "runtime": runtime_with_hashes,
            "files": records,
        }
        package_sha = _sha256(_canonical_json(basis))
        manifest = {
            **basis,
            "package_id": str(uuid.uuid5(
                uuid.NAMESPACE_URL, f"mise:{DEPLOYMENT_ID}:{package_sha}",
            )),
            "package_sha256": package_sha,
            "state": "held",
        }
        (staging / "manifest.json").write_bytes(
            _canonical_json(manifest) + b"\n"
        )
        os.chmod(staging / "manifest.json", 0o400)
        for directory in sorted(
            (item for item in payload.rglob("*") if item.is_dir()),
            reverse=True,
        ):
            os.chmod(directory, 0o555)
        os.chmod(payload, 0o555)
        verify_complete_release_package(staging)
        target = output_dir / package_sha
        try:
            target.lstat()
        except FileNotFoundError:
            os.replace(staging, target)
        else:
            verify_complete_release_package(target)
        return target
    finally:
        if assembly.exists():
            for directory in (
                assembly,
                *(
                    item for item in assembly.rglob("*")
                    if item.is_dir() and not item.is_symlink()
                ),
            ):
                try:
                    os.chmod(directory, 0o700)
                except FileNotFoundError:
                    pass
            shutil.rmtree(assembly)


def verify_package(package_dir: Path) -> dict[str, Any]:
    _secure_directory(package_dir)
    try:
        manifest = json.loads(_read_private_file(package_dir / "manifest.json"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError("Scribe package manifest is unreadable") from exc
    schema = manifest.get("schema")
    expected_manifest_keys = {
            "schema", "company_id", "deployment_id", "host_kind",
            "runtime_kind", "kernel_release_sha256", "source_commit_sha",
            "source_tree_sha", "files", "package_id", "package_sha256",
            "state",
        }
    if schema == COMPLETE_PACKAGE_SCHEMA:
        expected_manifest_keys.add("runtime")
    if (
        frozenset(manifest) != expected_manifest_keys
        or schema not in {PACKAGE_SCHEMA, COMPLETE_PACKAGE_SCHEMA}
        or manifest.get("company_id") != COMPANY_ID
        or manifest.get("deployment_id") != DEPLOYMENT_ID
        or manifest.get("host_kind") != "macbook"
        or manifest.get("runtime_kind") != RUNTIME_KIND
        or manifest.get("state") != "held"
    ):
        raise CompanyScribeAdmissionError("Scribe package identity is invalid")
    _exact_sha(
        manifest.get("kernel_release_sha256"), "package kernel release", _SHA256,
    )
    _exact_sha(manifest.get("source_commit_sha"), "package source commit", _GIT_SHA)
    _exact_sha(manifest.get("source_tree_sha"), "package source tree", _GIT_SHA)
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise CompanyScribeAdmissionError("Scribe package has no exact file manifest")
    basis = {
        key: manifest[key] for key in (
            "schema", "company_id", "deployment_id", "host_kind", "runtime_kind",
            "kernel_release_sha256", "source_commit_sha", "source_tree_sha", "files",
        )
    }
    if schema == COMPLETE_PACKAGE_SCHEMA:
        basis["runtime"] = manifest["runtime"]
    expected_package_sha = _sha256(_canonical_json(basis))
    if (
        manifest.get("package_sha256") != expected_package_sha
        or manifest.get("package_id")
        != str(uuid.uuid5(
            uuid.NAMESPACE_URL, f"mise:{DEPLOYMENT_ID}:{expected_package_sha}",
        ))
    ):
        raise CompanyScribeAdmissionError("Scribe package digest or identity is forged")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise CompanyScribeAdmissionError("Scribe package file record is malformed")
        if schema == PACKAGE_SCHEMA:
            if frozenset(record) != {
                "path", "sha256", "mode", "git_object_sha",
            }:
                raise CompanyScribeAdmissionError(
                    "Scribe package file record is not closed"
                )
        elif frozenset(record) != {
            "path", "sha256", "mode", "source_kind", "source_sha",
        }:
            raise CompanyScribeAdmissionError(
                "complete Scribe package file record is not closed"
            )
        relative = _safe_relative(_required(record.get("path"), "package path"))
        relative_text = relative.as_posix()
        if relative_text in seen:
            raise CompanyScribeAdmissionError("Scribe package repeats a file")
        seen.add(relative_text)
        target = package_dir / "payload" / relative
        info = target.lstat()
        if target.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise CompanyScribeAdmissionError("Scribe package payload type is unsafe")
        if _sha256(target.read_bytes()) != _exact_sha(
            record.get("sha256"), "package file digest", _SHA256,
        ):
            raise CompanyScribeAdmissionError("Scribe package payload bytes changed")
        if stat.S_IMODE(info.st_mode) != int(record.get("mode", -1)):
            raise CompanyScribeAdmissionError("Scribe package payload mode changed")
        if schema == PACKAGE_SCHEMA:
            _exact_sha(record.get("git_object_sha"), "Git blob object", _GIT_SHA)
        elif record.get("source_kind") == "git":
            _exact_sha(record.get("source_sha"), "Git blob object", _GIT_SHA)
        elif record.get("source_kind") == "runtime":
            _exact_sha(record.get("source_sha"), "runtime source", _SHA256)
        else:
            raise CompanyScribeAdmissionError(
                "complete Scribe package source kind is invalid"
            )
    actual_files: set[str] = set()
    for item in (package_dir / "payload").rglob("*"):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise CompanyScribeAdmissionError("Scribe package contains a symlink")
        if stat.S_ISREG(info.st_mode):
            actual_files.add(
                item.relative_to(package_dir / "payload").as_posix()
            )
        elif not stat.S_ISDIR(info.st_mode):
            raise CompanyScribeAdmissionError(
                "Scribe package contains a special filesystem entry"
            )
    if actual_files != seen:
        raise CompanyScribeAdmissionError("Scribe package contains unmanifested files")
    root_entries = {item.name for item in package_dir.iterdir()}
    if root_entries != {"manifest.json", "payload"}:
        raise CompanyScribeAdmissionError("Scribe package root contains extra entries")
    if schema == COMPLETE_PACKAGE_SCHEMA:
        runtime = manifest.get("runtime")
        required_runtime = {
            "python_executable", "python_executable_sha256",
            "bun_executable", "bun_executable_sha256",
            "scribe_channel_bundle", "scribe_channel_bundle_sha256",
        }
        if not isinstance(runtime, dict) or frozenset(runtime) != required_runtime:
            raise CompanyScribeAdmissionError(
                "complete Scribe runtime manifest is not closed"
            )
        for path_key, sha_key in (
            ("python_executable", "python_executable_sha256"),
            ("bun_executable", "bun_executable_sha256"),
            ("scribe_channel_bundle", "scribe_channel_bundle_sha256"),
        ):
            relative = _safe_relative(_required(runtime.get(path_key), path_key))
            target = package_dir / "payload" / relative
            if not target.is_file() or (
                path_key != "scribe_channel_bundle"
                and not os.access(target, os.X_OK)
            ):
                raise CompanyScribeAdmissionError(
                    f"complete Scribe runtime executable is absent: {path_key}"
                )
            if _sha256(target.read_bytes()) != _exact_sha(
                runtime.get(sha_key), sha_key, _SHA256,
            ):
                raise CompanyScribeAdmissionError(
                    f"complete Scribe runtime digest changed: {path_key}"
                )
    return manifest


# Public exact-byte closure is defined by fleet_kernel/release_files.json.
# Private payroll, init macros and deployment assets are not payload defaults.

def scribe_release_files() -> tuple[str, ...]:
    """Exact public release inventory, separated from private domain releases."""
    from fleet_kernel.release import runtime_release_files
    return runtime_release_files()


def verify_complete_release_package(package_dir: Path) -> dict[str, Any]:
    """Require the complete current reviewed Kernel + Scribe release package."""
    manifest = verify_package(package_dir)
    # Import here to keep package primitives reusable in isolated tests while
    # binding every operational install/registration path to the closed release.
    from fleet_kernel.release import (  # noqa: PLC0415
        runtime_release_manifest_bytes,
    )

    if manifest.get("schema") != COMPLETE_PACKAGE_SCHEMA:
        raise CompanyScribeAdmissionError(
            "installed Scribe package is not the complete exact reviewed "
            "release with a closed offline runtime"
        )
    expected_files = sorted(set(scribe_release_files()))
    observed_files = [
        record["path"] for record in manifest["files"]
        if record["source_kind"] == "git"
    ]
    expected_kernel_release = _sha256(runtime_release_manifest_bytes())
    if (
        observed_files != expected_files
        or manifest["kernel_release_sha256"] != expected_kernel_release
    ):
        raise CompanyScribeAdmissionError(
            "installed Scribe package is not the complete exact reviewed release"
        )
    return manifest


def verify_offline_package_runtime(package_dir: Path) -> dict[str, Any]:
    """Execute cold probes with no cache, network installer, or workspace venv."""
    manifest = verify_complete_release_package(package_dir)
    payload = package_dir / "payload"
    runtime = manifest["runtime"]
    python = payload / runtime["python_executable"]
    python_root = python.parent.parent
    base_environment = {
        "PATH": "/usr/bin:/bin",
        "PYTHONHOME": str(python_root),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    python_probe = subprocess.run(
        [
            str(python), "-I", "-c",
            "import psycopg,ssl,sqlite3;print('offline-python-ok')",
        ],
        cwd=payload,
        env=base_environment,
        check=False, capture_output=True, text=True,
        timeout=30,
    )
    if python_probe.returncode or python_probe.stdout.strip() != "offline-python-ok":
        raise CompanyScribeAdmissionError(
            "packaged Python cold/offline probe failed: "
            + python_probe.stderr.strip()
        )
    bun = payload / runtime["bun_executable"]
    bundle = payload / runtime["scribe_channel_bundle"]
    with tempfile.TemporaryDirectory(prefix="scribe-bun-cold-probe-") as home:
        bun_probe = subprocess.run(
            [str(bun), "--no-install", str(bundle)],
            cwd=payload,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": home,
                "XDG_CACHE_HOME": str(Path(home) / "cache"),
                "BUN_INSTALL_CACHE_DIR": str(Path(home) / "bun-cache"),
                "SCRIBE_OFFLINE_PROBE": "1",
            },
            check=False, capture_output=True, text=True,
            timeout=30,
        )
    if bun_probe.returncode or bun_probe.stdout.strip() != "offline-bun-ok":
        raise CompanyScribeAdmissionError(
            "packaged Bun cold/offline probe failed: "
            + bun_probe.stderr.strip()
        )
    observed = verify_complete_release_package(package_dir)
    if observed != manifest:
        raise CompanyScribeAdmissionError(
            "Scribe cold/offline probe changed its immutable package"
        )
    return observed


def _write_pointer(path: Path, payload: Mapping[str, Any]) -> None:
    _secure_directory(path.parent, create=True)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None and (
        stat.S_ISLNK(existing.st_mode)
        or not stat.S_ISREG(existing.st_mode)
        or existing.st_uid != os.getuid()
        or stat.S_IMODE(existing.st_mode) & 0o077
    ):
        raise CompanyScribeAdmissionError("existing Scribe pointer is unsafe")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        data = _canonical_json(payload) + b"\n"
        os.write(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_current_lease_pointer(
    path: Path,
    *,
    binding: ScribeAdmissionBinding,
    lease_expires_at: datetime,
) -> None:
    expires = lease_expires_at.astimezone(timezone.utc)
    _write_pointer(path, {
        "schema": CURRENT_LEASE_POINTER_SCHEMA,
        "company_id": binding.company_id,
        "deployment_id": binding.deployment_id,
        "admission_id": str(binding.admission_id),
        "admission_epoch": binding.admission_epoch,
        "worker_session_id": str(binding.worker_session_id),
        "authority_subject_sha256": binding.authority_subject_sha256,
        "lease_expires_at": expires.isoformat().replace("+00:00", "Z"),
    })


def load_current_lease_pointer(
    path: Path,
    *,
    binding: ScribeAdmissionBinding,
) -> datetime:
    try:
        path.lstat()
    except FileNotFoundError:
        return binding.lease_expires_at
    try:
        value = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company Scribe current lease pointer is unreadable"
        ) from exc
    expected = {
        "schema", "company_id", "deployment_id", "admission_id",
        "admission_epoch", "worker_session_id", "authority_subject_sha256",
        "lease_expires_at",
    }
    if (
        not isinstance(value, dict)
        or frozenset(value) != expected
        or value.get("schema") != CURRENT_LEASE_POINTER_SCHEMA
        or value.get("company_id") != binding.company_id
        or value.get("deployment_id") != binding.deployment_id
        or value.get("admission_id") != str(binding.admission_id)
        or value.get("admission_epoch") != binding.admission_epoch
        or value.get("worker_session_id") != str(binding.worker_session_id)
        or value.get("authority_subject_sha256")
        != binding.authority_subject_sha256
    ):
        raise CompanyScribeAdmissionError(
            "Company Scribe current lease pointer names another epoch"
        )
    return _utc(value.get("lease_expires_at"), "lease_expires_at")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_once_private(path: Path, data: bytes, *, mode: int) -> None:
    """Create one immutable-by-convention receipt/archive file, idempotently."""
    _secure_directory(path.parent, create=True)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, mode)
    except FileExistsError:
        if _read_private_file(path) != data:
            raise CompanyScribeAdmissionError(
                "existing Scribe rollback artifact conflicts with exact bytes"
            ) from None
        return
    try:
        os.fchmod(descriptor, mode)
        remaining = data
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("Scribe rollback artifact write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def archive_binding_for_restart(
    path: Path,
    *,
    binding: ScribeAdmissionBinding,
    archive_dir: Path,
) -> tuple[Path, str]:
    """Move the revoked binding to a private audit archive without deleting it."""
    if not path.is_absolute() or not archive_dir.is_absolute():
        raise CompanyScribeAdmissionError(
            "Scribe binding and archive paths must be absolute"
        )
    archive = archive_dir / (
        f"binding-{binding.admission_id}-epoch-{binding.admission_epoch}.json"
    )
    _secure_directory(archive_dir, create=True)
    expected = _canonical_json(binding.to_mapping()) + b"\n"
    try:
        path.lstat()
    except FileNotFoundError:
        try:
            archived = _read_private_file(archive)
        except CompanyScribeAdmissionError as exc:
            raise CompanyScribeAdmissionError(
                "neither the live nor archived exact Scribe binding is available"
            ) from exc
        if archived != expected:
            raise CompanyScribeAdmissionError(
                "archived Scribe binding differs from the exact rollback target"
            )
        archived_sha256 = _sha256(archived)
        _write_pointer(path, {
            "schema": REVOKED_BINDING_POINTER_SCHEMA,
            "state": "revoked",
            "admission_id": str(binding.admission_id),
            "admission_epoch": binding.admission_epoch,
            "archive_path": str(archive),
            "archive_sha256": archived_sha256,
        })
        _crash_checkpoint("active-binding-replaced")
        return archive, archived_sha256
    source = _read_private_file(path)
    try:
        source_payload = json.loads(source)
    except (UnicodeDecodeError, json.JSONDecodeError):
        source_payload = None
    if (
        isinstance(source_payload, dict)
        and source_payload.get("schema") == REVOKED_BINDING_POINTER_SCHEMA
    ):
        if (
            frozenset(source_payload) != {
                "schema", "state", "admission_id", "admission_epoch",
                "archive_path", "archive_sha256",
            }
            or source_payload.get("state") != "revoked"
            or source_payload.get("admission_id") != str(binding.admission_id)
            or source_payload.get("admission_epoch") != binding.admission_epoch
            or source_payload.get("archive_path") != str(archive)
        ):
            raise CompanyScribeAdmissionError(
                "revoked Scribe binding pointer is malformed"
            )
        archived = _read_private_file(archive)
        if (
            archived != expected
            or source_payload.get("archive_sha256") != _sha256(archived)
        ):
            raise CompanyScribeAdmissionError(
                "revoked Scribe binding pointer lost its exact archive"
            )
        return archive, _sha256(archived)
    if source != expected:
        # A binding written by the previous release may lack later defaulted
        # fields (from_mapping already tolerates that — e.g. role_type), so
        # its bytes cannot equal the current canonical serialization.  The
        # fence must be able to fence its predecessor's binding: accept the
        # source iff it denotes this exact binding after normalization.
        legacy_binding = None
        if isinstance(source_payload, dict):
            try:
                legacy_binding = ScribeAdmissionBinding.from_mapping(
                    source_payload
                )
            except CompanyScribeAdmissionError:
                legacy_binding = None
        if (
            legacy_binding is None
            or _canonical_json(legacy_binding.to_mapping()) + b"\n" != expected
        ):
            raise CompanyScribeAdmissionError(
                "live Scribe binding differs from the exact rollback target"
            )
    _write_once_private(archive, source, mode=0o400)
    _crash_checkpoint("binding-archive-created")
    archived = _read_private_file(archive)
    if archived != source:
        raise CompanyScribeAdmissionError(
            "archived Scribe binding bytes changed during rollback"
        )
    archived_sha256 = _sha256(archived)
    _write_pointer(path, {
        "schema": REVOKED_BINDING_POINTER_SCHEMA,
        "state": "revoked",
        "admission_id": str(binding.admission_id),
        "admission_epoch": binding.admission_epoch,
        "archive_path": str(archive),
        "archive_sha256": archived_sha256,
    })
    _crash_checkpoint("active-binding-replaced")
    return archive, archived_sha256


COMPANY_ROLE_REVOKED_BINDING_POINTER_SCHEMA = (
    "mise.company-role-revoked-binding-pointer.v1"
)
COMPANY_ROLE_ROLLBACK_RECEIPT_SCHEMA = "mise.company-role-rollback-receipt.v1"
COMPANY_ROLE_RESTART_INTENT_SCHEMA = "mise.company-role-restart-intent.v1"


def _load_company_role_rollback_binding(
    path: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
) -> CompanyRoleAdmissionBinding:
    """Load one live binding or its exact role-scoped revoked pointer."""
    try:
        payload = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role rollback binding is unreadable"
        ) from exc
    if not isinstance(payload, dict):
        raise CompanyScribeAdmissionError(
            "Company role rollback binding is not an object"
        )
    if payload.get("schema") != COMPANY_ROLE_REVOKED_BINDING_POINTER_SCHEMA:
        return CompanyRoleAdmissionBinding.from_mapping(payload, profile=profile)
    required = {
        "schema", "profile_sha256", "role_type", "deployment_id", "state",
        "admission_id", "admission_epoch", "archive_path", "archive_sha256",
    }
    if (
        frozenset(payload) != required
        or payload.get("profile_sha256") != profile.sha256
        or payload.get("role_type") != profile.role_type
        or payload.get("deployment_id") != profile.deployment_id
        or payload.get("state") != "revoked"
    ):
        raise CompanyScribeAdmissionError(
            "Company role revoked binding pointer differs from its profile"
        )
    archive = Path(str(payload.get("archive_path", "")))
    if not archive.is_absolute() or ".." in archive.parts:
        raise CompanyScribeAdmissionError(
            "Company role revoked binding archive path is unsafe"
        )
    archived = _read_private_file(archive)
    if _sha256(archived) != _exact_sha(
        payload.get("archive_sha256"), "archive_sha256", _SHA256,
    ):
        raise CompanyScribeAdmissionError(
            "Company role revoked binding archive digest changed"
        )
    try:
        value = json.loads(archived)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role archived binding is unreadable"
        ) from exc
    binding = CompanyRoleAdmissionBinding.from_mapping(value, profile=profile)
    if (
        payload["admission_id"] != str(binding.admission_id)
        or payload["admission_epoch"] != binding.admission_epoch
    ):
        raise CompanyScribeAdmissionError(
            "Company role revoked binding pointer names another epoch"
        )
    return binding


def archive_company_role_binding_for_restart(
    path: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
    binding: CompanyRoleAdmissionBinding,
    archive_dir: Path,
) -> tuple[Path, str]:
    """Archive one exact role binding and replace it with a revoked pointer."""
    if not path.is_absolute() or not archive_dir.is_absolute():
        raise CompanyScribeAdmissionError(
            "Company role binding and archive paths must be absolute"
        )
    expected = _canonical_json(binding.to_mapping()) + b"\n"
    archive = archive_dir / (
        f"binding-{binding.admission_id}-epoch-{binding.admission_epoch}.json"
    )
    _secure_directory(archive_dir, create=True)
    if archive.exists():
        archived = _read_private_file(archive)
        if archived != expected:
            raise CompanyScribeAdmissionError(
                "archived Company role binding differs from rollback target"
            )
    else:
        observed = _read_private_file(path)
        if observed != expected:
            try:
                pointer = json.loads(observed)
            except (UnicodeDecodeError, json.JSONDecodeError):
                pointer = None
            if not (
                isinstance(pointer, dict)
                and pointer.get("schema")
                == COMPANY_ROLE_REVOKED_BINDING_POINTER_SCHEMA
                and pointer.get("admission_id") == str(binding.admission_id)
            ):
                raise CompanyScribeAdmissionError(
                    "live Company role binding differs from rollback target"
                )
        else:
            _write_once_private(archive, observed, mode=0o400)
    archived = _read_private_file(archive)
    archived_sha256 = _sha256(archived)
    _write_pointer(path, {
        "schema": COMPANY_ROLE_REVOKED_BINDING_POINTER_SCHEMA,
        "profile_sha256": profile.sha256,
        "role_type": profile.role_type,
        "deployment_id": profile.deployment_id,
        "state": "revoked",
        "admission_id": str(binding.admission_id),
        "admission_epoch": binding.admission_epoch,
        "archive_path": str(archive),
        "archive_sha256": archived_sha256,
    })
    return archive, archived_sha256


def _load_company_role_rollback_receipt(
    path: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
) -> dict[str, Any]:
    try:
        receipt = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role rollback receipt is unreadable"
        ) from exc
    if not isinstance(receipt, dict):
        raise CompanyScribeAdmissionError(
            "Company role rollback receipt is not an object"
        )
    basis = dict(receipt)
    digest = basis.pop("receipt_sha256", None)
    if (
        receipt.get("schema") != COMPANY_ROLE_ROLLBACK_RECEIPT_SCHEMA
        or receipt.get("profile_sha256") != profile.sha256
        or receipt.get("role_type") != profile.role_type
        or receipt.get("deployment_id") != profile.deployment_id
        or receipt.get("rollback_state") != "inactive"
        or receipt.get("live_admission_count_after") != 0
        or receipt.get("session_state_after") != "ended"
        or receipt.get("worker_lease_cleared") is not True
        or digest != _sha256(_canonical_json(basis))
    ):
        raise CompanyScribeAdmissionError(
            "Company role rollback receipt is not exact"
        )
    return receipt


def rollback_live_company_role(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
    binding_path: Path,
    archive_dir: Path,
    receipt_path: Path,
    reason: str,
    allow_exact_live_process_if_authority_lost: bool = False,
) -> dict[str, Any]:
    """Revoke one exact non-Scribe role without reading another role lineage."""
    if profile.role_type == "scribe":
        raise CompanyScribeAdmissionError(
            "generic Company role rollback cannot target Scribe"
        )
    rollback_reason = _required(reason, "Company role rollback reason")
    if len(rollback_reason) > 512:
        raise CompanyScribeAdmissionError(
            "Company role rollback reason exceeds 512 characters"
        )
    binding = _load_company_role_rollback_binding(
        binding_path, profile=profile,
    )
    exact_process_holds_lock = False
    if _process_state(binding) == "exact_live":
        try:
            verify_runtime_lock_held(
                Path(binding.runtime_lock_path),
                expected_pid=binding.process_id,
            )
        except CompanyScribeAdmissionError as exc:
            if "not held by a live launcher" not in str(exc):
                raise
        else:
            exact_process_holds_lock = True
    if exact_process_holds_lock and not (
        allow_exact_live_process_if_authority_lost
    ):
        raise CompanyScribeAdmissionError(
            "Company role rollback requires the exact process to be fenced first"
        )
    binding_sha256 = _sha256(_canonical_json(binding.to_mapping()))
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (profile.admission_lock_key,),
        )
        if exact_process_holds_lock:
            authority = cursor.execute(
                "SELECT admission.state,admission.lease_expires_at,"
                "session.state AS session_state,"
                "session.lease_expires_at AS session_lease_expires_at,"
                "worker.status AS worker_status,"
                "worker.lease_expires_at AS worker_lease_expires_at,"
                "clock_timestamp() AS now "
                "FROM kernel_company_scribe_admissions admission "
                "JOIN kernel_worker_sessions session "
                "ON session.id=admission.worker_session_id "
                "JOIN kernel_runtime_workers worker "
                "ON worker.id=admission.worker_id "
                "AND worker.tenant_id=admission.company_id "
                "WHERE admission.id=%s AND admission.company_id=%s "
                "AND admission.role_type=%s AND admission.deployment_id=%s "
                "AND admission.worker_id=%s "
                "AND admission.worker_session_id=%s "
                "AND admission.package_id=%s "
                "AND admission.admission_epoch=%s "
                "AND admission.authority_root_admission_id=%s "
                "AND admission.authority_subject_sha256=%s "
                "FOR UPDATE OF admission,session,worker",
                (
                    binding.admission_id, profile.company_id,
                    profile.role_type, profile.deployment_id,
                    profile.worker_id, binding.worker_session_id,
                    binding.package_id, binding.admission_epoch,
                    binding.authority_root_admission_id,
                    binding.authority_subject_sha256,
                ),
            ).fetchone()
            if (
                authority is not None
                and authority["state"] == "live"
                and authority["lease_expires_at"] > authority["now"]
                and authority["session_state"] == "active"
                and authority["session_lease_expires_at"] > authority["now"]
                and authority["worker_status"] == "active"
                and authority["worker_lease_expires_at"] is not None
                and authority["worker_lease_expires_at"] > authority["now"]
            ):
                raise CompanyScribeAdmissionError(
                    "Company role rollback cannot fence an exact live process "
                    "while its database authority remains live"
                )
        row = cursor.execute(
            "UPDATE kernel_company_scribe_admissions SET state='revoked' "
            "WHERE id=%s AND company_id=%s AND role_type=%s "
            "AND deployment_id=%s AND worker_id=%s AND worker_session_id=%s "
            "AND package_id=%s AND admission_epoch=%s AND mandate_id=%s "
            "AND mandate_artifact_sha256=%s AND authority_root_admission_id=%s "
            "AND authority_subject_sha256=%s AND state='live' RETURNING state",
            (
                binding.admission_id, profile.company_id, profile.role_type,
                profile.deployment_id, profile.worker_id,
                binding.worker_session_id, binding.package_id,
                binding.admission_epoch, profile.mandate_id,
                profile.mandate_artifact_sha256,
                binding.authority_root_admission_id,
                binding.authority_subject_sha256,
            ),
        ).fetchone()
        if row is None:
            row = cursor.execute(
                "SELECT state FROM kernel_company_scribe_admissions "
                "WHERE id=%s AND company_id=%s AND role_type=%s "
                "AND deployment_id=%s AND worker_id=%s AND worker_session_id=%s "
                "AND package_id=%s AND admission_epoch=%s "
                "AND authority_subject_sha256=%s",
                (
                    binding.admission_id, profile.company_id,
                    profile.role_type, profile.deployment_id,
                    profile.worker_id, binding.worker_session_id,
                    binding.package_id, binding.admission_epoch,
                    binding.authority_subject_sha256,
                ),
            ).fetchone()
            if row is None or row["state"] != "revoked":
                raise CompanyScribeAdmissionError(
                    "Company role rollback target is not the exact binding"
                )
        session = cursor.execute(
            "UPDATE kernel_worker_sessions SET state='ended',"
            "ended_at=COALESCE(ended_at,now()) WHERE id=%s AND tenant_id=%s "
            "AND worker_id=%s AND role_type=%s AND state='active' "
            "AND lease_token=%s RETURNING state",
            (
                binding.worker_session_id, profile.company_id,
                profile.worker_id, profile.role_type, binding.lease_token,
            ),
        ).fetchone()
        if session is None:
            session = cursor.execute(
                "SELECT state FROM kernel_worker_sessions WHERE id=%s "
                "AND tenant_id=%s AND worker_id=%s AND role_type=%s",
                (
                    binding.worker_session_id, profile.company_id,
                    profile.worker_id, profile.role_type,
                ),
            ).fetchone()
        if session is None or session["state"] != "ended":
            raise CompanyScribeAdmissionError(
                "Company role rollback did not end the exact worker session"
            )
        cursor.execute(
            "UPDATE kernel_runtime_workers SET lease_token=NULL,"
            "lease_expires_at=NULL WHERE id=%s AND tenant_id=%s "
            "AND role_type=%s AND lease_token=%s",
            (
                profile.worker_id, profile.company_id, profile.role_type,
                binding.lease_token,
            ),
        )
        worker = cursor.execute(
            "SELECT lease_token,lease_expires_at FROM kernel_runtime_workers "
            "WHERE id=%s AND tenant_id=%s AND role_type=%s",
            (profile.worker_id, profile.company_id, profile.role_type),
        ).fetchone()
        live_count = cursor.execute(
            "SELECT count(*) AS count FROM kernel_company_scribe_admissions "
            "WHERE company_id=%s AND role_type=%s AND state='live'",
            (profile.company_id, profile.role_type),
        ).fetchone()["count"]
        if (
            worker is None
            or worker["lease_token"] is not None
            or worker["lease_expires_at"] is not None
            or live_count != 0
        ):
            raise CompanyScribeAdmissionError(
                "Company role rollback left role authority or worker lease live"
            )
    archive, archive_sha256 = archive_company_role_binding_for_restart(
        binding_path,
        profile=profile,
        binding=binding,
        archive_dir=archive_dir,
    )
    basis = {
        "schema": COMPANY_ROLE_ROLLBACK_RECEIPT_SCHEMA,
        "profile_sha256": profile.sha256,
        "company_id": profile.company_id,
        "role_type": profile.role_type,
        "deployment_id": profile.deployment_id,
        "worker_id": str(profile.worker_id),
        "admission_id": str(binding.admission_id),
        "admission_epoch": binding.admission_epoch,
        "authority_root_admission_id": str(
            binding.authority_root_admission_id
        ),
        "worker_session_id": str(binding.worker_session_id),
        "package_id": str(binding.package_id),
        "package_sha256": binding.package_sha256,
        "agent_release_sha256": binding.agent_release_sha256,
        "kernel_release_sha256": binding.kernel_release_sha256,
        "repo_commit_sha": binding.repo_commit_sha,
        "repo_tree_sha": binding.repo_tree_sha,
        "host_identity_sha256": binding.host_identity_sha256,
        "runtime_executable_path": binding.runtime_executable_path,
        "runtime_executable_sha256": binding.runtime_executable_sha256,
        "runtime_lock_path": binding.runtime_lock_path,
        "mandate_id": binding.mandate_id,
        "mandate_artifact_sha256": binding.mandate_artifact_sha256,
        "predecessor_fence_receipt_sha256": (
            binding.mini_activation_receipt_sha256
        ),
        "binding_sha256": binding_sha256,
        "archive_path": str(archive),
        "archive_sha256": archive_sha256,
        "reason": rollback_reason,
        "rollback_state": "inactive",
        "live_admission_count_after": 0,
        "session_state_after": "ended",
        "worker_lease_cleared": True,
    }
    receipt = {**basis, "receipt_sha256": _sha256(_canonical_json(basis))}
    if receipt_path.exists():
        if _load_company_role_rollback_receipt(
            receipt_path, profile=profile,
        ) != receipt:
            raise CompanyScribeAdmissionError(
                "existing Company role rollback receipt differs"
            )
    else:
        _write_once_private(
            receipt_path, _canonical_json(receipt) + b"\n", mode=0o400,
        )
    return receipt


def create_role_restart_intent(
    *,
    profile: CompanyRoleAdmissionProfile,
    prior_binding: CompanyRoleAdmissionBinding,
    rollback_receipt_path: Path,
    intent_path: Path,
) -> dict[str, Any]:
    """Create one immutable, same-root restart intent for a Company role."""
    receipt = _load_company_role_rollback_receipt(
        rollback_receipt_path, profile=profile,
    )
    exact = {
        "company_id": prior_binding.company_id,
        "role_type": prior_binding.role_type,
        "deployment_id": prior_binding.deployment_id,
        "worker_id": str(prior_binding.worker_id),
        "admission_id": str(prior_binding.admission_id),
        "admission_epoch": prior_binding.admission_epoch,
        "authority_root_admission_id": str(
            prior_binding.authority_root_admission_id
        ),
        "worker_session_id": str(prior_binding.worker_session_id),
        "package_id": str(prior_binding.package_id),
        "package_sha256": prior_binding.package_sha256,
        "agent_release_sha256": prior_binding.agent_release_sha256,
        "kernel_release_sha256": prior_binding.kernel_release_sha256,
        "repo_commit_sha": prior_binding.repo_commit_sha,
        "repo_tree_sha": prior_binding.repo_tree_sha,
        "host_identity_sha256": prior_binding.host_identity_sha256,
        "runtime_executable_path": prior_binding.runtime_executable_path,
        "runtime_executable_sha256": prior_binding.runtime_executable_sha256,
        "runtime_lock_path": prior_binding.runtime_lock_path,
        "mandate_id": prior_binding.mandate_id,
        "mandate_artifact_sha256": prior_binding.mandate_artifact_sha256,
        "predecessor_fence_receipt_sha256": (
            prior_binding.mini_activation_receipt_sha256
        ),
    }
    if any(receipt.get(key) != value for key, value in exact.items()):
        raise CompanyScribeAdmissionError(
            "Company role restart intent changed the revoked binding lineage"
        )
    rollback_sha256 = str(receipt["receipt_sha256"])
    intent_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"mise:{profile.company_id}:{profile.role_type}:recovery:{rollback_sha256}",
    )
    basis = {
        "schema": COMPANY_ROLE_RESTART_INTENT_SCHEMA,
        "profile_sha256": profile.sha256,
        "state": "prepared",
        "intent_id": str(intent_id),
        "company_id": profile.company_id,
        "role_type": profile.role_type,
        "deployment_id": profile.deployment_id,
        "mandate_id": profile.mandate_id,
        "mandate_artifact_sha256": profile.mandate_artifact_sha256,
        "authority_root_admission_id": str(
            prior_binding.authority_root_admission_id
        ),
        # Recovery approval IDs are epoch-local.  The restart lineage must
        # keep naming the initial root approval or a second recovery compares
        # the root row against the first recovery's approval and dead-ends.
        "authority_approval_id": str(
            prior_binding.authority_root_admission_id
        ),
        "prior_admission_id": str(prior_binding.admission_id),
        "prior_admission_epoch": prior_binding.admission_epoch,
        "next_admission_epoch": prior_binding.admission_epoch + 1,
        "worker_id": str(prior_binding.worker_id),
        "agent_release_sha256": prior_binding.agent_release_sha256,
        "package_id": str(prior_binding.package_id),
        "package_sha256": prior_binding.package_sha256,
        "kernel_release_sha256": prior_binding.kernel_release_sha256,
        "repo_commit_sha": prior_binding.repo_commit_sha,
        "repo_tree_sha": prior_binding.repo_tree_sha,
        "host_identity_sha256": prior_binding.host_identity_sha256,
        "predecessor_fence_receipt_sha256": (
            prior_binding.mini_activation_receipt_sha256
        ),
        "runtime_executable_path": prior_binding.runtime_executable_path,
        "runtime_executable_sha256": prior_binding.runtime_executable_sha256,
        "runtime_lock_path": prior_binding.runtime_lock_path,
        "rollback_receipt_path": str(rollback_receipt_path),
        "prior_rollback_receipt_sha256": rollback_sha256,
        "self_approval_permitted": False,
    }
    intent = {
        **basis,
        "restart_intent_sha256": _sha256(_canonical_json(basis)),
    }
    encoded = _canonical_json(intent) + b"\n"
    if intent_path.exists():
        if _read_private_file(intent_path) != encoded:
            raise CompanyScribeAdmissionError(
                "existing Company role restart intent differs"
            )
    else:
        _write_once_private(intent_path, encoded, mode=0o400)
    if load_role_restart_intent(intent_path, profile=profile) != intent:
        raise CompanyScribeAdmissionError(
            "durable Company role restart intent changed on readback"
        )
    return intent


def load_role_restart_intent(
    path: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
) -> dict[str, Any]:
    try:
        intent = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role restart intent is unreadable"
        ) from exc
    if not isinstance(intent, dict):
        raise CompanyScribeAdmissionError(
            "Company role restart intent is not an object"
        )
    basis = dict(intent)
    digest = basis.pop("restart_intent_sha256", None)
    required = {
        "schema", "profile_sha256", "state", "intent_id", "company_id",
        "role_type", "deployment_id", "mandate_id",
        "mandate_artifact_sha256", "authority_root_admission_id",
        "authority_approval_id", "prior_admission_id",
        "prior_admission_epoch", "next_admission_epoch", "worker_id",
        "agent_release_sha256", "package_id", "package_sha256",
        "kernel_release_sha256", "repo_commit_sha", "repo_tree_sha",
        "host_identity_sha256", "predecessor_fence_receipt_sha256",
        "runtime_executable_path", "runtime_executable_sha256",
        "runtime_lock_path", "rollback_receipt_path",
        "prior_rollback_receipt_sha256", "self_approval_permitted",
    }
    try:
        for field in (
            "intent_id", "authority_root_admission_id",
            "authority_approval_id", "prior_admission_id", "worker_id",
            "package_id",
        ):
            uuid.UUID(str(intent.get(field)))
        prior_epoch = int(intent.get("prior_admission_epoch", 0))
        next_epoch = int(intent.get("next_admission_epoch", 0))
    except (TypeError, ValueError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role restart intent identities are malformed"
        ) from exc
    if (
        frozenset(basis) != required
        or intent.get("schema") != COMPANY_ROLE_RESTART_INTENT_SCHEMA
        or intent.get("profile_sha256") != profile.sha256
        or intent.get("state") != "prepared"
        or intent.get("company_id") != profile.company_id
        or intent.get("role_type") != profile.role_type
        or intent.get("deployment_id") != profile.deployment_id
        or intent.get("worker_id") != str(profile.worker_id)
        or intent.get("mandate_id") != profile.mandate_id
        or intent.get("mandate_artifact_sha256")
        != profile.mandate_artifact_sha256
        or intent.get("self_approval_permitted") is not False
        or next_epoch != prior_epoch + 1
        or digest != _sha256(_canonical_json(basis))
    ):
        raise CompanyScribeAdmissionError(
            "Company role restart intent is not exact"
        )
    rollback_path = Path(str(intent["rollback_receipt_path"]))
    if not rollback_path.is_absolute() or ".." in rollback_path.parts:
        raise CompanyScribeAdmissionError(
            "Company role restart intent rollback path is unsafe"
        )
    receipt = _load_company_role_rollback_receipt(
        rollback_path, profile=profile,
    )
    if (
        receipt["receipt_sha256"]
        != intent["prior_rollback_receipt_sha256"]
        or receipt["authority_root_admission_id"]
        != intent["authority_root_admission_id"]
        or receipt["admission_id"] != intent["prior_admission_id"]
        or receipt["admission_epoch"] != intent["prior_admission_epoch"]
        or receipt["package_id"] != intent["package_id"]
        or receipt["package_sha256"] != intent["package_sha256"]
        or receipt["host_identity_sha256"]
        != intent["host_identity_sha256"]
    ):
        raise CompanyScribeAdmissionError(
            "Company role restart intent rollback lineage changed"
        )
    return intent


def _load_rollback_binding(path: Path) -> ScribeAdmissionBinding:
    try:
        payload = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe rollback binding pointer is unreadable"
        ) from exc
    if not isinstance(payload, dict):
        raise CompanyScribeAdmissionError(
            "Scribe rollback binding pointer is not an object"
        )
    if payload.get("schema") != REVOKED_BINDING_POINTER_SCHEMA:
        return ScribeAdmissionBinding.from_mapping(payload)
    if (
        frozenset(payload) != {
            "schema", "state", "admission_id", "admission_epoch",
            "archive_path", "archive_sha256",
        }
        or payload.get("state") != "revoked"
    ):
        raise CompanyScribeAdmissionError(
            "revoked Scribe binding pointer is malformed"
        )
    archive = Path(_required(payload.get("archive_path"), "archive_path"))
    if not archive.is_absolute():
        raise CompanyScribeAdmissionError(
            "revoked Scribe binding archive path is not absolute"
        )
    archived = _read_private_file(archive)
    if _sha256(archived) != _exact_sha(
        payload.get("archive_sha256"), "archive_sha256", _SHA256,
    ):
        raise CompanyScribeAdmissionError(
            "revoked Scribe binding archive digest changed"
        )
    try:
        archived_payload = json.loads(archived)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "archived Scribe binding is unreadable"
        ) from exc
    if not isinstance(archived_payload, dict):
        raise CompanyScribeAdmissionError(
            "archived Scribe binding is not an object"
        )
    binding = ScribeAdmissionBinding.from_mapping(archived_payload)
    if (
        payload.get("admission_id") != str(binding.admission_id)
        or payload.get("admission_epoch") != binding.admission_epoch
    ):
        raise CompanyScribeAdmissionError(
            "revoked Scribe binding pointer names another epoch"
        )
    return binding


def _load_rollback_receipt(path: Path) -> dict[str, Any]:
    try:
        receipt = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe rollback receipt is unreadable"
        ) from exc
    if not isinstance(receipt, dict):
        raise CompanyScribeAdmissionError(
            "Scribe rollback receipt is not an object"
        )
    basis = dict(receipt)
    digest = basis.pop("receipt_sha256", None)
    if (
        receipt.get("schema") != ROLLBACK_RECEIPT_SCHEMA
        or receipt.get("rollback_state") != "inactive"
        or receipt.get("canonical_state_preserved") is not True
        or receipt.get("live_admission_count_after") != 0
        or digest != _sha256(_canonical_json(basis))
    ):
        raise CompanyScribeAdmissionError(
            "Scribe rollback receipt is not exact"
        )
    return receipt


def create_restart_intent(
    *,
    prior_binding: ScribeAdmissionBinding,
    rollback_receipt_path: Path,
    intent_path: Path,
) -> dict[str, Any]:
    """Create an immutable non-secret intent for one next recovery epoch."""
    prior = _load_rollback_receipt(rollback_receipt_path)
    root_id = prior_binding.authority_root_admission_id
    if (
        root_id is None
        or prior.get("company_id") != prior_binding.company_id
        or prior.get("deployment_id") != prior_binding.deployment_id
        or prior.get("admission_id") != str(prior_binding.admission_id)
        or prior.get("admission_epoch") != prior_binding.admission_epoch
        or prior.get("worker_id") != str(prior_binding.worker_id)
        or prior.get("worker_session_id") != str(
            prior_binding.worker_session_id
        )
        or prior.get("package_id") != str(prior_binding.package_id)
        or prior.get("package_sha256") != prior_binding.package_sha256
        or prior.get("kernel_release_sha256")
        != prior_binding.kernel_release_sha256
        or prior.get("repo_commit_sha") != prior_binding.repo_commit_sha
        or prior.get("repo_tree_sha") != prior_binding.repo_tree_sha
        or prior.get("host_identity_sha256")
        != prior_binding.host_identity_sha256
        or prior.get("mandate_id") != prior_binding.mandate_id
        or prior.get("mandate_artifact_sha256")
        != prior_binding.mandate_artifact_sha256
        or prior.get("authority_root_admission_id") != str(root_id)
        or prior.get("authority_approval_id")
        != str(prior_binding.authority_approval_id)
        or prior.get("mini_activation_receipt_sha256")
        != prior_binding.mini_activation_receipt_sha256
    ):
        raise CompanyScribeAdmissionError(
            "Scribe restart intent does not continue the exact revoked binding"
        )
    rollback_digest = _exact_sha(
        prior.get("receipt_sha256"), "rollback receipt SHA-256", _SHA256,
    )
    intent_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        (
            f"mise:{prior_binding.company_id}:{prior_binding.deployment_id}:"
            f"scribe-recovery:{rollback_digest}"
        ),
    )
    basis = {
        "schema": RESTART_INTENT_SCHEMA,
        "state": "prepared",
        "intent_id": str(intent_id),
        "company_id": prior_binding.company_id,
        "deployment_id": prior_binding.deployment_id,
        "mandate_id": prior_binding.mandate_id,
        "mandate_artifact_sha256": prior_binding.mandate_artifact_sha256,
        "authority_root_admission_id": str(root_id),
        "authority_approval_id": str(prior_binding.authority_approval_id),
        "prior_admission_id": str(prior_binding.admission_id),
        "prior_admission_epoch": prior_binding.admission_epoch,
        "next_admission_epoch": prior_binding.admission_epoch + 1,
        "worker_id": str(prior_binding.worker_id),
        "agent_release_sha256": prior_binding.agent_release_sha256,
        "package_id": str(prior_binding.package_id),
        "package_sha256": prior_binding.package_sha256,
        "kernel_release_sha256": prior_binding.kernel_release_sha256,
        "repo_commit_sha": prior_binding.repo_commit_sha,
        "repo_tree_sha": prior_binding.repo_tree_sha,
        "host_identity_sha256": prior_binding.host_identity_sha256,
        "mini_activation_receipt_sha256": (
            prior_binding.mini_activation_receipt_sha256
        ),
        "runtime_executable_path": prior_binding.runtime_executable_path,
        "runtime_executable_sha256": (
            prior_binding.runtime_executable_sha256
        ),
        "runtime_lock_path": prior_binding.runtime_lock_path,
        "rollback_receipt_path": str(rollback_receipt_path),
        "prior_rollback_receipt_sha256": rollback_digest,
        "self_approval_permitted": False,
    }
    intent = {
        **basis,
        "restart_intent_sha256": _sha256(_canonical_json(basis)),
    }
    _write_once_private(
        intent_path, _canonical_json(intent) + b"\n", mode=0o400,
    )
    if load_restart_intent(intent_path) != intent:
        raise CompanyScribeAdmissionError(
            "durable Scribe restart intent readback changed"
        )
    return intent


def load_restart_intent(path: Path) -> dict[str, Any]:
    try:
        intent = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe restart intent is unreadable"
        ) from exc
    if not isinstance(intent, dict):
        raise CompanyScribeAdmissionError(
            "Scribe restart intent is not an object"
        )
    basis = dict(intent)
    digest = basis.pop("restart_intent_sha256", None)
    required = {
        "schema", "state", "intent_id", "company_id", "deployment_id",
        "mandate_id", "mandate_artifact_sha256",
        "authority_root_admission_id", "authority_approval_id",
        "prior_admission_id", "prior_admission_epoch",
        "next_admission_epoch", "worker_id", "agent_release_sha256",
        "package_id", "package_sha256", "kernel_release_sha256",
        "repo_commit_sha", "repo_tree_sha", "host_identity_sha256",
        "mini_activation_receipt_sha256", "runtime_executable_path",
        "runtime_executable_sha256", "runtime_lock_path",
        "rollback_receipt_path", "prior_rollback_receipt_sha256",
        "self_approval_permitted",
    }
    try:
        uuid.UUID(str(intent.get("intent_id")))
        uuid.UUID(str(intent.get("authority_root_admission_id")))
        uuid.UUID(str(intent.get("authority_approval_id")))
        uuid.UUID(str(intent.get("prior_admission_id")))
        uuid.UUID(str(intent.get("worker_id")))
        uuid.UUID(str(intent.get("package_id")))
        prior_epoch = int(intent.get("prior_admission_epoch", 0))
        next_epoch = int(intent.get("next_admission_epoch", 0))
    except (TypeError, ValueError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe restart intent identities are malformed"
        ) from exc
    if (
        frozenset(basis) != required
        or intent.get("schema") != RESTART_INTENT_SCHEMA
        or intent.get("state") != "prepared"
        or intent.get("company_id") != COMPANY_ID
        or intent.get("deployment_id") != DEPLOYMENT_ID
        or intent.get("self_approval_permitted") is not False
        or next_epoch != prior_epoch + 1
        or digest != _sha256(_canonical_json(basis))
    ):
        raise CompanyScribeAdmissionError(
            "Scribe restart intent is not exact"
        )
    for field in (
        "mandate_artifact_sha256", "agent_release_sha256",
        "package_sha256", "kernel_release_sha256", "host_identity_sha256",
        "mini_activation_receipt_sha256", "runtime_executable_sha256",
        "prior_rollback_receipt_sha256",
    ):
        _exact_sha(intent.get(field), field, _SHA256)
    for field in ("repo_commit_sha", "repo_tree_sha"):
        _exact_sha(intent.get(field), field, _GIT_SHA)
    for field in (
        "runtime_executable_path", "runtime_lock_path",
        "rollback_receipt_path",
    ):
        candidate = Path(_required(intent.get(field), field))
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise CompanyScribeAdmissionError(
                f"Scribe restart intent {field} is unsafe"
            )
    rollback = _load_rollback_receipt(
        Path(str(intent["rollback_receipt_path"]))
    )
    if (
        rollback.get("receipt_sha256")
        != intent.get("prior_rollback_receipt_sha256")
        or rollback.get("company_id") != intent.get("company_id")
        or rollback.get("deployment_id") != intent.get("deployment_id")
        or rollback.get("mandate_id") != intent.get("mandate_id")
        or rollback.get("mandate_artifact_sha256")
        != intent.get("mandate_artifact_sha256")
        or rollback.get("authority_root_admission_id")
        != intent.get("authority_root_admission_id")
        or rollback.get("authority_approval_id")
        != intent.get("authority_approval_id")
        or rollback.get("admission_id")
        != intent.get("prior_admission_id")
        or rollback.get("admission_epoch")
        != intent.get("prior_admission_epoch")
        or rollback.get("worker_id") != intent.get("worker_id")
        or rollback.get("package_id") != intent.get("package_id")
        or rollback.get("agent_release_sha256")
        != intent.get("agent_release_sha256")
        or rollback.get("package_sha256") != intent.get("package_sha256")
        or rollback.get("kernel_release_sha256")
        != intent.get("kernel_release_sha256")
        or rollback.get("repo_commit_sha") != intent.get("repo_commit_sha")
        or rollback.get("repo_tree_sha") != intent.get("repo_tree_sha")
        or rollback.get("host_identity_sha256")
        != intent.get("host_identity_sha256")
        or rollback.get("mini_activation_receipt_sha256")
        != intent.get("mini_activation_receipt_sha256")
        or rollback.get("runtime_executable_path")
        != intent.get("runtime_executable_path")
        or rollback.get("runtime_executable_sha256")
        != intent.get("runtime_executable_sha256")
        or rollback.get("runtime_lock_path")
        != intent.get("runtime_lock_path")
    ):
        raise CompanyScribeAdmissionError(
            "Scribe restart intent rollback lineage changed"
        )
    return intent


def rollback_live_scribe(
    conn: psycopg.Connection,
    *,
    binding_path: Path,
    archive_dir: Path,
    receipt_path: Path,
    reason: str,
    quiesce_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Revoke, quiesce, archive, and receipt one exact writable Scribe.

    The canonical Company Workspace digest is compared before and after the
    admission update in one repeatable-read transaction.  No package pointer is
    changed and no legacy authority is reactivated.  A later launch must obtain
    a fresh admission with the next epoch.
    """
    rollback_reason = _required(reason, "Scribe rollback reason")
    if len(rollback_reason) > 512:
        raise CompanyScribeAdmissionError(
            "Scribe rollback reason exceeds 512 characters"
        )
    _require_idle_connection(conn)
    binding = _load_rollback_binding(binding_path)
    binding_sha256 = _sha256(_canonical_json(binding.to_mapping()))
    try:
        receipt_path.lstat()
    except FileNotFoundError:
        pass
    else:
        try:
            existing_receipt = json.loads(_read_private_file(receipt_path))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanyScribeAdmissionError(
                "existing Scribe rollback receipt is unreadable"
            ) from exc
        if not isinstance(existing_receipt, dict):
            raise CompanyScribeAdmissionError(
                "existing Scribe rollback receipt is not an object"
            )
        existing_basis = dict(existing_receipt)
        existing_digest = existing_basis.pop("receipt_sha256", None)
        if (
            existing_receipt.get("schema") != ROLLBACK_RECEIPT_SCHEMA
            or existing_receipt.get("admission_id") != str(binding.admission_id)
            or existing_receipt.get("admission_epoch") != binding.admission_epoch
            or existing_receipt.get("binding_sha256") != binding_sha256
            or existing_receipt.get("reason") != rollback_reason
            or existing_receipt.get("rollback_state") != "inactive"
            or existing_receipt.get("canonical_state_preserved") is not True
            or existing_digest != _sha256(_canonical_json(existing_basis))
        ):
            raise CompanyScribeAdmissionError(
                "existing Scribe rollback receipt is not exact"
            )
        archive_binding_for_restart(
            binding_path, binding=binding, archive_dir=archive_dir,
        )
        with conn.transaction():
            state_probe = conn.execute(
                "SELECT state FROM kernel_company_scribe_admissions "
                "WHERE id=%s AND company_id=%s",
                (binding.admission_id, binding.company_id),
            ).fetchone()
            live_probe = conn.execute(
                "SELECT count(*) FROM kernel_company_scribe_admissions "
                "WHERE company_id=%s AND role_type='scribe' AND state='live'",
                (binding.company_id,),
            ).fetchone()
            session_probe = conn.execute(
                "SELECT state FROM kernel_worker_sessions "
                "WHERE id=%s AND tenant_id=%s",
                (binding.worker_session_id, binding.company_id),
            ).fetchone()
        state_value = (
            state_probe["state"]
            if isinstance(state_probe, Mapping)
            else state_probe[0] if state_probe is not None else None
        )
        live_value = int(
            live_probe["count"] if isinstance(live_probe, Mapping) else live_probe[0]
        )
        session_value = (
            session_probe["state"]
            if isinstance(session_probe, Mapping)
            else session_probe[0] if session_probe is not None else None
        )
        if (
            state_value != "revoked"
            or live_value != 0
            or session_value != "ended"
        ):
            raise CompanyScribeAdmissionError(
                "existing Scribe rollback receipt no longer matches database state"
            )
        if _process_state(binding) == "exact_live":
            raise CompanyScribeAdmissionError(
                "existing Scribe rollback receipt conflicts with a live bound process"
            )
        return existing_receipt
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        before = company_state_snapshot(conn)
        closure = close_live_admission(
            conn, binding=binding, state="revoked",
        )
        session_closure = end_bound_worker_session(
            conn, binding=binding,
        )
        after = company_state_snapshot(conn)
        if before != after:
            raise CompanyScribeAdmissionError(
                "Scribe rollback changed canonical Company Workspace state"
            )
    _require_idle_connection(conn)
    _crash_checkpoint("database-revocation-committed")
    quiescence = quiesce_bound_process(
        binding, timeout_seconds=quiesce_timeout_seconds,
    )
    _crash_checkpoint("bound-process-quiesced")
    archive_path, archived_binding_sha256 = archive_binding_for_restart(
        binding_path, binding=binding, archive_dir=archive_dir,
    )
    with conn.transaction():
        probe = conn.execute(
            "SELECT jsonb_build_object("
            "'state',state,'closed_at',closed_at,"
            "'admission_epoch',admission_epoch,"
            "'process_id',process_id"
            ") AS closure "
            "FROM kernel_company_scribe_admissions "
            "WHERE id=%s AND company_id=%s",
            (binding.admission_id, binding.company_id),
        ).fetchone()
        live = conn.execute(
            "SELECT count(*) FROM kernel_company_scribe_admissions "
            "WHERE company_id=%s AND role_type='scribe' AND state='live'",
            (binding.company_id,),
        ).fetchone()
    if probe is None:
        raise CompanyScribeAdmissionError(
            "revoked Scribe admission disappeared during rollback"
        )
    observed_closure = dict(
        probe["closure"] if isinstance(probe, Mapping) else probe[0]
    )
    live_count = int(
        live["count"] if isinstance(live, Mapping) else live[0]
    )
    if (
        observed_closure.get("state") != "revoked"
        or int(observed_closure.get("admission_epoch", 0))
        != binding.admission_epoch
        or live_count != 0
    ):
        raise CompanyScribeAdmissionError(
            "Scribe rollback did not leave exactly zero live admissions"
        )
    basis = {
        "schema": ROLLBACK_RECEIPT_SCHEMA,
        "company_id": binding.company_id,
        "deployment_id": binding.deployment_id,
        "rollback_state": "inactive",
        "reason": rollback_reason,
        "admission_id": str(binding.admission_id),
        "admission_epoch": binding.admission_epoch,
        "worker_id": str(binding.worker_id),
        "worker_session_id": str(binding.worker_session_id),
        "agent_release_sha256": binding.agent_release_sha256,
        "task_id": binding.task_id,
        "claude_session_id": binding.claude_session_id,
        "process_id": binding.process_id,
        "process_start_fingerprint_sha256": (
            binding.process_start_fingerprint_sha256
        ),
        "package_id": str(binding.package_id),
        "package_sha256": binding.package_sha256,
        "kernel_release_sha256": binding.kernel_release_sha256,
        "repo_commit_sha": binding.repo_commit_sha,
        "repo_tree_sha": binding.repo_tree_sha,
        "mandate_id": binding.mandate_id,
        "mandate_artifact_sha256": binding.mandate_artifact_sha256,
        "authority_root_admission_id": str(
            binding.authority_root_admission_id
        ),
        "authority_approval_id": str(binding.authority_approval_id),
        "mini_activation_receipt_sha256": (
            binding.mini_activation_receipt_sha256
        ),
        "host_identity_sha256": binding.host_identity_sha256,
        "host_boot_id_sha256": binding.host_boot_id_sha256,
        "runtime_executable_path": binding.runtime_executable_path,
        "runtime_executable_sha256": binding.runtime_executable_sha256,
        "runtime_lock_path": binding.runtime_lock_path,
        "binding_sha256": binding_sha256,
        "archived_binding_path": str(archive_path),
        "archived_binding_sha256": archived_binding_sha256,
        "admission_state_after": observed_closure["state"],
        "admission_closed_at": observed_closure["closed_at"],
        "worker_session_state_after": session_closure["state"],
        "worker_session_ended_at": session_closure["ended_at"],
        "live_admission_count_after": live_count,
        "canonical_state_before": before,
        "canonical_state_after": after,
        "canonical_state_preserved": before == after,
        "process_quiescence": quiescence,
        "restart_contract": {
            "authority": "standing-mandate-recovery",
            "new_founder_decision_required": False,
            "next_admission_epoch": binding.admission_epoch + 1,
            "stale_binding_reusable": False,
        },
        "legacy_runner_recovery": "outside-normal-activation",
    }
    receipt = {
        **basis,
        "receipt_sha256": _sha256(_canonical_json(basis)),
    }
    _write_once_private(
        receipt_path, _canonical_json(receipt) + b"\n", mode=0o400,
    )
    observed_receipt = json.loads(_read_private_file(receipt_path))
    if observed_receipt != receipt:
        raise CompanyScribeAdmissionError(
            "durable Scribe rollback receipt readback changed"
        )
    _crash_checkpoint("rollback-receipt-written")
    return receipt


def restart_epoch_root_authority_exact(
    root: Mapping[str, Any],
    *,
    restarted_binding: ScribeAdmissionBinding,
) -> bool:
    """The recovery root is the exact Scribe-first local-activation root.

    This mirrors `kernel_validate_company_scribe_admission`'s `root_exact` in
    `fleet_kernel/ddl/025_company_scribe_admission.sql`, restricted to the
    fields this call site loads.  It replaces two stale predicates that could
    never pass on a Scribe-first lineage:

    * the root query filtered on the retired `initial-m5-decision` authority
      mode and joined `m5_approval_decisions`.  DDL 027 dropped that mode from
      the CHECK, so no row can carry it and the join returned nothing, firing
      both assertions on every supervisor tick; and
    * the Mini predicate compared `mini_activation_receipt_sha256` to a
      `kernel_founder_activation_step_receipts` row for step
      `mini-verification`.  That field is the all-zeros sentinel that Founder
      Ruling 3 ruled correct and DDL 025 requires, so a real Mini receipt
      digest could never equal it — unsatisfiable forever, not merely today.

    DDL 025 compares the recovery row's Mini sentinel to **the root's**, which
    is what this predicate does, and no DDL object requires a
    `mini-verification` receipt for Scribe recovery.
    """
    return (
        isinstance(root, Mapping)
        and root.get("id") == restarted_binding.authority_root_admission_id
        and root.get("authority_mode") == INITIAL_AUTHORITY_MODE
        and root.get("authority_root_admission_id") == root.get("id")
        and root.get("authority_approval_id")
        == restarted_binding.authority_approval_id
        and root.get("mini_activation_receipt_sha256")
        == restarted_binding.mini_activation_receipt_sha256
    )


def record_restart_epoch_receipt(
    conn: psycopg.Connection,
    *,
    prior_rollback_receipt_path: Path,
    restarted_binding: ScribeAdmissionBinding,
    receipt_path: Path,
) -> dict[str, Any]:
    """Receipt a fresh approved epoch; never create or approve the admission."""
    prior = _load_rollback_receipt(prior_rollback_receipt_path)
    prior_digest = prior["receipt_sha256"]
    if (
        restarted_binding.authority_mode != RECOVERY_AUTHORITY_MODE
        or prior.get("company_id") != restarted_binding.company_id
        or prior.get("deployment_id") != restarted_binding.deployment_id
        or prior.get("worker_id") != str(restarted_binding.worker_id)
        or prior.get("package_id") != str(restarted_binding.package_id)
        or prior.get("package_sha256") != restarted_binding.package_sha256
        or prior.get("kernel_release_sha256")
        != restarted_binding.kernel_release_sha256
        or prior.get("repo_commit_sha") != restarted_binding.repo_commit_sha
        or prior.get("repo_tree_sha") != restarted_binding.repo_tree_sha
        or prior.get("host_identity_sha256")
        != restarted_binding.host_identity_sha256
        or prior.get("mandate_id") != restarted_binding.mandate_id
        or prior.get("mandate_artifact_sha256")
        != restarted_binding.mandate_artifact_sha256
        or prior.get("authority_root_admission_id")
        != str(restarted_binding.authority_root_admission_id)
        or prior.get("authority_approval_id")
        != str(restarted_binding.authority_approval_id)
        or prior.get("mini_activation_receipt_sha256")
        != restarted_binding.mini_activation_receipt_sha256
        or prior.get("admission_id")
        != str(restarted_binding.prior_admission_id)
        or prior_digest != restarted_binding.prior_rollback_receipt_sha256
        or int(prior.get("admission_epoch", 0)) + 1
        != restarted_binding.admission_epoch
    ):
        raise CompanyScribeAdmissionError(
            "Scribe restart does not continue the exact revoked epoch"
        )
    if _process_state(restarted_binding) != "exact_live":
        raise CompanyScribeAdmissionError(
            "restarted Scribe process is not the exact live binding"
        )
    verify_runtime_lock_held(
        Path(restarted_binding.runtime_lock_path),
        expected_pid=restarted_binding.process_id,
    )
    with conn.transaction():
        old = conn.execute(
            "SELECT admission.state,session.state AS session_state "
            "FROM kernel_company_scribe_admissions admission "
            "JOIN kernel_worker_sessions session "
            "ON session.id=admission.worker_session_id "
            "WHERE admission.id=%s AND admission.company_id=%s",
            (uuid.UUID(str(prior["admission_id"])), restarted_binding.company_id),
        ).fetchone()
        new = conn.execute(
            "SELECT jsonb_build_object("
            "'state',state,'admission_epoch',admission_epoch,"
            "'worker_session_id',worker_session_id,"
            "'process_id',process_id,"
            "'process_start_fingerprint_sha256',"
            "process_start_fingerprint_sha256,"
            "'authority_subject_sha256',authority_subject_sha256,"
            "'authority_mode',authority_mode,"
            "'authority_root_admission_id',authority_root_admission_id,"
            "'prior_admission_id',prior_admission_id,"
            "'recovery_intent_id',recovery_intent_id,"
            "'prior_rollback_receipt_sha256',prior_rollback_receipt_sha256,"
            "'restart_intent_sha256',restart_intent_sha256,"
            "'lease_token_sha256',lease_token_sha256,"
            "'lease_live',lease_expires_at>now()"
            ") AS admission FROM kernel_company_scribe_admissions "
            "WHERE id=%s AND company_id=%s",
            (restarted_binding.admission_id, restarted_binding.company_id),
        ).fetchone()
        live = conn.execute(
            "SELECT count(*) FROM kernel_company_scribe_admissions "
            "WHERE company_id=%s AND role_type='scribe' AND state='live'",
            (restarted_binding.company_id,),
        ).fetchone()
        session = conn.execute(
            "SELECT state,lease_expires_at,"
            "encode(digest(convert_to(lease_token,'UTF8'),'sha256'),'hex') "
            "AS lease_token_sha256 FROM kernel_worker_sessions "
            "WHERE id=%s AND tenant_id=%s AND worker_id=%s",
            (
                restarted_binding.worker_session_id,
                restarted_binding.company_id,
                restarted_binding.worker_id,
            ),
        ).fetchone()
        worker = conn.execute(
            "SELECT status,lease_expires_at,"
            "encode(digest(convert_to(lease_token,'UTF8'),'sha256'),'hex') "
            "AS lease_token_sha256 FROM kernel_runtime_workers "
            "WHERE id=%s AND tenant_id=%s AND role_type='scribe'",
            (restarted_binding.worker_id, restarted_binding.company_id),
        ).fetchone()
        intent = conn.execute(
            "SELECT state,restarted_admission_id,"
            "authority_root_admission_id,prior_admission_id,"
            "next_admission_epoch,restart_intent_sha256,"
            "prior_rollback_receipt_sha256 "
            "FROM kernel_company_scribe_recovery_intents "
            "WHERE id=%s AND company_id=%s",
            (
                restarted_binding.recovery_intent_id,
                restarted_binding.company_id,
            ),
        ).fetchone()
        root = conn.execute(
            "SELECT root.id,root.authority_mode,"
            "root.authority_root_admission_id,root.authority_approval_id,"
            "root.mini_activation_receipt_sha256,root.authority_subject_ref,"
            "root.authority_subject_sha256 "
            "FROM kernel_company_scribe_admissions root "
            "WHERE root.id=%s AND root.company_id=%s",
            (
                restarted_binding.authority_root_admission_id,
                restarted_binding.company_id,
            ),
        ).fetchone()
        mandate = conn.execute(
            "SELECT status FROM kernel_founder_activation_mandates "
            "WHERE mandate_id=%s AND artifact_sha256=%s",
            (
                restarted_binding.mandate_id,
                restarted_binding.mandate_artifact_sha256,
            ),
        ).fetchone()
        final_receipt = conn.execute(
            "SELECT receipt_payload "
            "FROM kernel_founder_activation_step_receipts "
            "WHERE mandate_id=%s AND activation_step='scribe-verification'",
            (restarted_binding.mandate_id,),
        ).fetchone()
        snapshot = company_state_snapshot(conn)
    old_value = (
        dict(old) if isinstance(old, Mapping)
        else (
            {"state": old[0], "session_state": old[1]}
            if old else {}
        )
    )
    new_value = dict(new["admission"] if isinstance(new, Mapping) else new[0])
    live_count = int(
        live["count"] if isinstance(live, Mapping) else live[0]
    )
    session_value = dict(session) if isinstance(session, Mapping) else {}
    worker_value = dict(worker) if isinstance(worker, Mapping) else {}
    intent_value = dict(intent) if isinstance(intent, Mapping) else {}
    root_value = dict(root) if isinstance(root, Mapping) else {}
    mandate_status = (
        mandate["status"] if isinstance(mandate, Mapping) else None
    )
    final_payload = (
        final_receipt["receipt_payload"]
        if isinstance(final_receipt, Mapping)
        else None
    )
    now = datetime.now(timezone.utc)
    lease_sha256 = _sha256(
        restarted_binding.lease_token.encode("utf-8")
    )
    if (
        old_value != {"state": "revoked", "session_state": "ended"}
        or new_value != {
            "state": "live",
            "admission_epoch": restarted_binding.admission_epoch,
            "worker_session_id": str(restarted_binding.worker_session_id),
            "process_id": restarted_binding.process_id,
            "process_start_fingerprint_sha256": (
                restarted_binding.process_start_fingerprint_sha256
            ),
            "authority_subject_sha256": (
                restarted_binding.authority_subject_sha256
            ),
            "authority_mode": RECOVERY_AUTHORITY_MODE,
            "authority_root_admission_id": str(
                restarted_binding.authority_root_admission_id
            ),
            "prior_admission_id": str(
                restarted_binding.prior_admission_id
            ),
            "recovery_intent_id": str(
                restarted_binding.recovery_intent_id
            ),
            "prior_rollback_receipt_sha256": prior_digest,
            "restart_intent_sha256": (
                restarted_binding.restart_intent_sha256
            ),
            "lease_token_sha256": lease_sha256,
            "lease_live": True,
        }
        or live_count != 1
        or session_value.get("state") != "active"
        or session_value.get("lease_expires_at") <= now
        or session_value.get("lease_token_sha256") != lease_sha256
        or worker_value.get("status") != "active"
        or worker_value.get("lease_expires_at") is None
        or worker_value.get("lease_expires_at") <= now
        or worker_value.get("lease_token_sha256") != lease_sha256
        or intent_value != {
            "state": "consumed",
            "restarted_admission_id": restarted_binding.admission_id,
            "authority_root_admission_id": (
                restarted_binding.authority_root_admission_id
            ),
            "prior_admission_id": restarted_binding.prior_admission_id,
            "next_admission_epoch": restarted_binding.admission_epoch,
            "restart_intent_sha256": (
                restarted_binding.restart_intent_sha256
            ),
            "prior_rollback_receipt_sha256": prior_digest,
        }
        or not restart_epoch_root_authority_exact(
            root_value, restarted_binding=restarted_binding,
        )
        or mandate_status not in {"active", "completed"}
        or (
            mandate_status == "completed"
            and (
                not isinstance(final_payload, Mapping)
                or final_payload.get("status") != "completed"
                or final_payload.get("singleton") != "verified"
                or final_payload.get("persistence") != "verified"
                or final_payload.get("restart") != "passed"
                or final_payload.get("receipts") != "verified"
            )
        )
        or snapshot != prior.get("canonical_state_after")
    ):
        raise CompanyScribeAdmissionError(
            "Scribe restart epoch failed singleton or persistence verification"
        )
    basis = {
        "schema": RESTART_EPOCH_RECEIPT_SCHEMA,
        "company_id": restarted_binding.company_id,
        "deployment_id": restarted_binding.deployment_id,
        "prior_rollback_receipt_sha256": prior_digest,
        "prior_admission_id": prior["admission_id"],
        "prior_admission_epoch": prior["admission_epoch"],
        "prior_worker_session_id": prior["worker_session_id"],
        "prior_process_id": prior["process_id"],
        "restarted_admission_id": str(restarted_binding.admission_id),
        "restarted_admission_epoch": restarted_binding.admission_epoch,
        "restarted_worker_session_id": str(restarted_binding.worker_session_id),
        "restarted_process_id": restarted_binding.process_id,
        "restarted_process_start_fingerprint_sha256": (
            restarted_binding.process_start_fingerprint_sha256
        ),
        "authority_subject_sha256": (
            restarted_binding.authority_subject_sha256
        ),
        "authority_mode": restarted_binding.authority_mode,
        "authority_root_admission_id": str(
            restarted_binding.authority_root_admission_id
        ),
        "recovery_intent_id": str(restarted_binding.recovery_intent_id),
        "restart_intent_sha256": restarted_binding.restart_intent_sha256,
        "host_identity_sha256": restarted_binding.host_identity_sha256,
        "host_boot_id_sha256": restarted_binding.host_boot_id_sha256,
        "mini_activation_receipt_sha256": (
            restarted_binding.mini_activation_receipt_sha256
        ),
        "singleton_live_admission_count": live_count,
        "canonical_state": snapshot,
        "restart_state": "live-standing-mandate-recovery-epoch",
        "legacy_runner_recovery": "not-invoked",
    }
    receipt = {
        **basis,
        "receipt_sha256": _sha256(_canonical_json(basis)),
    }
    _write_once_private(
        receipt_path, _canonical_json(receipt) + b"\n", mode=0o400,
    )
    observed = load_restart_epoch_receipt(receipt_path)
    if observed != receipt:
        raise CompanyScribeAdmissionError(
            "durable Scribe restart receipt readback changed"
        )
    return receipt


def load_restart_epoch_receipt(path: Path) -> dict[str, Any]:
    """Load one immutable restart receipt only after full self-verification."""
    try:
        receipt = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe restart receipt is unreadable"
        ) from exc
    if not isinstance(receipt, dict):
        raise CompanyScribeAdmissionError(
            "Scribe restart receipt is not an object"
        )
    basis = dict(receipt)
    digest = basis.pop("receipt_sha256", None)
    required = {
        "schema", "company_id", "deployment_id",
        "prior_rollback_receipt_sha256", "prior_admission_id",
        "prior_admission_epoch", "prior_worker_session_id",
        "prior_process_id", "restarted_admission_id",
        "restarted_admission_epoch", "restarted_worker_session_id",
        "restarted_process_id",
        "restarted_process_start_fingerprint_sha256",
        "authority_subject_sha256", "authority_mode",
        "authority_root_admission_id", "recovery_intent_id",
        "restart_intent_sha256", "host_identity_sha256",
        "host_boot_id_sha256", "mini_activation_receipt_sha256",
        "singleton_live_admission_count", "canonical_state",
        "restart_state", "legacy_runner_recovery",
    }
    try:
        for field in (
            "prior_admission_id", "prior_worker_session_id",
            "restarted_admission_id", "restarted_worker_session_id",
            "authority_root_admission_id", "recovery_intent_id",
        ):
            uuid.UUID(str(receipt.get(field)))
        prior_epoch = int(receipt.get("prior_admission_epoch", 0))
        restarted_epoch = int(
            receipt.get("restarted_admission_epoch", 0)
        )
        prior_pid = int(receipt.get("prior_process_id", 0))
        restarted_pid = int(receipt.get("restarted_process_id", 0))
    except (TypeError, ValueError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe restart receipt identities are malformed"
        ) from exc
    if (
        frozenset(basis) != required
        or receipt.get("schema") != RESTART_EPOCH_RECEIPT_SCHEMA
        or receipt.get("company_id") != COMPANY_ID
        or receipt.get("deployment_id") != DEPLOYMENT_ID
        or receipt.get("authority_mode") != RECOVERY_AUTHORITY_MODE
        or receipt.get("restart_state")
        != "live-standing-mandate-recovery-epoch"
        or receipt.get("legacy_runner_recovery") != "not-invoked"
        or receipt.get("singleton_live_admission_count") != 1
        or not isinstance(receipt.get("canonical_state"), dict)
        or restarted_epoch != prior_epoch + 1
        or prior_pid <= 1
        or restarted_pid <= 1
        or digest != _sha256(_canonical_json(basis))
    ):
        raise CompanyScribeAdmissionError(
            "Scribe restart receipt is not exact"
        )
    for field in (
        "prior_rollback_receipt_sha256",
        "restarted_process_start_fingerprint_sha256",
        "authority_subject_sha256", "restart_intent_sha256",
        "host_identity_sha256", "host_boot_id_sha256",
        "mini_activation_receipt_sha256",
    ):
        _exact_sha(receipt.get(field), field, _SHA256)
    return receipt


def install_held_package(package_dir: Path, *, install_root: Path) -> dict[str, Any]:
    """Install exact bytes and update only the HELD pointer, never authority."""
    manifest = verify_package(package_dir)
    packages_root = install_root / "packages"
    destination = packages_root / manifest["package_sha256"]
    _secure_directory(packages_root, create=True)
    try:
        destination_info = destination.lstat()
    except FileNotFoundError:
        destination_info = None
    if destination_info is None:
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=packages_root))
        try:
            shutil.copytree(package_dir, staging / "package")
            verify_package(staging / "package")
            os.replace(staging / "package", destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    installed = verify_package(destination)
    pointer = install_root / "HELD.json"
    previous: dict[str, Any] | None = None
    try:
        pointer.lstat()
    except FileNotFoundError:
        pass
    else:
        try:
            previous = json.loads(_read_private_file(pointer))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanyScribeAdmissionError(
                "existing Scribe HELD pointer is unreadable"
            ) from exc
        if previous.get("schema") != HELD_POINTER_SCHEMA:
            raise CompanyScribeAdmissionError("existing Scribe HELD pointer is invalid")
        _write_pointer(install_root / "ROLLBACK.json", previous)
    _write_pointer(pointer, {
        "schema": HELD_POINTER_SCHEMA,
        "state": "held",
        "package_id": installed["package_id"],
        "package_sha256": installed["package_sha256"],
        "kernel_release_sha256": installed["kernel_release_sha256"],
        "source_commit_sha": installed["source_commit_sha"],
        "source_tree_sha": installed["source_tree_sha"],
    })
    return installed


def company_role_release_files(profile: CompanyRoleAdmissionProfile) -> tuple[str, ...]:
    """Exact public release inventory, separated from private domain releases."""
    from fleet_kernel.release import runtime_release_files
    return runtime_release_files()


def build_company_role_package(
    *,
    profile: CompanyRoleAdmissionProfile,
    repo: Path,
    output_dir: Path,
    kernel_release_sha256: str,
    files: Sequence[str] | None = None,
) -> Path:
    """Build one exact, source-complete held package for a non-Scribe role."""
    if profile.role_type == "scribe":
        raise CompanyScribeAdmissionError(
            "generic Company role package cannot replace Scribe compatibility"
        )
    release = _exact_sha(
        kernel_release_sha256, "Company role kernel release", _SHA256,
    )
    commit = _exact_sha(
        _git(repo, "rev-parse", "HEAD^{commit}"), "commit", _GIT_SHA,
    )
    tree = _exact_sha(
        _git(repo, "rev-parse", "HEAD^{tree}"), "tree", _GIT_SHA,
    )
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise CompanyScribeAdmissionError(
            "Company role package requires a clean exact release worktree"
        )
    selected = tuple(files or company_role_release_files(profile))
    normalized = sorted({_safe_relative(item).as_posix() for item in selected})
    required = set(company_role_release_files(profile))
    if not required.issubset(normalized):
        raise CompanyScribeAdmissionError(
            "Company role package omits a required runtime surface"
        )
    records: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    for relative_text in normalized:
        source = repo / relative_text
        try:
            info = source.lstat()
        except FileNotFoundError as exc:
            raise CompanyScribeAdmissionError(
                f"Company role package source is absent: {relative_text}"
            ) from exc
        if not stat.S_ISREG(info.st_mode) or source.is_symlink():
            raise CompanyScribeAdmissionError(
                f"Company role package source is unsafe: {relative_text}"
            )
        blob, committed_mode, object_sha = _git_blob(
            repo, commit, relative_text,
        )
        if source.read_bytes() != blob:
            raise CompanyScribeAdmissionError(
                f"Company role package source differs from its commit: {relative_text}"
            )
        blobs[relative_text] = blob
        records.append({
            "path": relative_text,
            "sha256": _sha256(blob),
            "mode": committed_mode,
            "git_object_sha": object_sha,
        })
    basis = {
        "schema": COMPANY_ROLE_PACKAGE_SCHEMA,
        "profile": profile.canonical_payload,
        "profile_sha256": profile.sha256,
        "company_id": profile.company_id,
        "role_type": profile.role_type,
        "deployment_id": profile.deployment_id,
        "worker_id": str(profile.worker_id),
        "host_kind": profile.host_kind,
        "runtime_kind": profile.runtime_kind,
        "mandate_id": profile.mandate_id,
        "mandate_artifact_sha256": profile.mandate_artifact_sha256,
        "kernel_release_sha256": release,
        "source_commit_sha": commit,
        "source_tree_sha": tree,
        "runtime_entrypoint": "fleet_kernel/company_role_runtime.py",
        "supervisor_entrypoint": "scripts/company_scribe_supervisor.py",
        "files": records,
    }
    package_sha = _sha256(_canonical_json(basis))
    package_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"mise:{profile.role_type}:{profile.deployment_id}:{package_sha}",
    )
    manifest = {
        **basis,
        "package_id": str(package_id),
        "package_sha256": package_sha,
        "state": "held",
    }
    _secure_directory(output_dir, create=True)
    target = output_dir / package_sha
    if target.exists():
        verify_company_role_package(target, profile=profile)
        return target
    staging = Path(tempfile.mkdtemp(prefix=".company-role-", dir=output_dir))
    try:
        payload = staging / "payload"
        payload.mkdir(mode=0o700)
        for record in records:
            destination = payload / record["path"]
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes(blobs[record["path"]])
            os.chmod(destination, int(record["mode"]))
        (staging / "profile.json").write_bytes(
            _canonical_json(profile.canonical_payload)
        )
        os.chmod(staging / "profile.json", 0o600)
        (staging / "manifest.json").write_bytes(
            _canonical_json(manifest) + b"\n"
        )
        os.chmod(staging / "manifest.json", 0o600)
        verify_company_role_package(staging, profile=profile)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target


def verify_company_role_package(
    package_dir: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
) -> dict[str, Any]:
    """Verify a role package without accepting identity from its manifest."""
    _secure_directory(package_dir)
    try:
        manifest = json.loads(_read_private_file(package_dir / "manifest.json"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role package manifest is unreadable"
        ) from exc
    keys = frozenset({
        "schema", "profile", "profile_sha256", "company_id", "role_type",
        "deployment_id", "worker_id", "host_kind", "runtime_kind",
        "mandate_id", "mandate_artifact_sha256", "kernel_release_sha256",
        "source_commit_sha", "source_tree_sha", "runtime_entrypoint",
        "supervisor_entrypoint", "files", "package_id", "package_sha256",
        "state",
    })
    if not isinstance(manifest, dict) or frozenset(manifest) != keys:
        raise CompanyScribeAdmissionError(
            "Company role package manifest is not closed"
        )
    exact_identity = (
        manifest["schema"] == COMPANY_ROLE_PACKAGE_SCHEMA
        and manifest["profile"] == profile.canonical_payload
        and manifest["profile_sha256"] == profile.sha256
        and manifest["company_id"] == profile.company_id
        and manifest["role_type"] == profile.role_type
        and manifest["deployment_id"] == profile.deployment_id
        and str(manifest["worker_id"]) == str(profile.worker_id)
        and manifest["host_kind"] == profile.host_kind
        and manifest["runtime_kind"] == profile.runtime_kind
        and manifest["mandate_id"] == profile.mandate_id
        and manifest["mandate_artifact_sha256"]
        == profile.mandate_artifact_sha256
        and manifest["runtime_entrypoint"]
        == "fleet_kernel/company_role_runtime.py"
        and manifest["supervisor_entrypoint"]
        == "scripts/company_scribe_supervisor.py"
        and manifest["state"] == "held"
    )
    if not exact_identity:
        raise CompanyScribeAdmissionError(
            "Company role package belongs to another role or profile"
        )
    if _read_private_file(package_dir / "profile.json") != _canonical_json(
        profile.canonical_payload
    ):
        raise CompanyScribeAdmissionError(
            "Company role package profile artifact changed"
        )
    _exact_sha(manifest["kernel_release_sha256"], "kernel release", _SHA256)
    _exact_sha(manifest["source_commit_sha"], "source commit", _GIT_SHA)
    _exact_sha(manifest["source_tree_sha"], "source tree", _GIT_SHA)
    records = manifest["files"]
    if not isinstance(records, list) or not records:
        raise CompanyScribeAdmissionError("Company role package is empty")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or frozenset(record) != {
            "path", "sha256", "mode", "git_object_sha",
        }:
            raise CompanyScribeAdmissionError(
                "Company role package file record is not closed"
            )
        relative = _safe_relative(_required(record["path"], "package path"))
        relative_text = relative.as_posix()
        if relative_text in seen:
            raise CompanyScribeAdmissionError(
                "Company role package repeats a file"
            )
        seen.add(relative_text)
        target = package_dir / "payload" / relative
        try:
            info = target.lstat()
        except FileNotFoundError as exc:
            raise CompanyScribeAdmissionError(
                "Company role package payload is incomplete"
            ) from exc
        if target.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise CompanyScribeAdmissionError(
                "Company role package payload type is unsafe"
            )
        if (
            _sha256(target.read_bytes()) != _exact_sha(
                record["sha256"], "file digest", _SHA256,
            )
            or stat.S_IMODE(info.st_mode) != int(record["mode"])
        ):
            raise CompanyScribeAdmissionError(
                "Company role package payload bytes or mode changed"
            )
        _exact_sha(record["git_object_sha"], "Git blob object", _GIT_SHA)
    if not set(company_role_release_files(profile)).issubset(seen):
        raise CompanyScribeAdmissionError(
            "Company role package omits a required runtime surface"
        )
    actual = {
        item.relative_to(package_dir / "payload").as_posix()
        for item in (package_dir / "payload").rglob("*")
        if item.is_file()
    }
    if actual != seen or {item.name for item in package_dir.iterdir()} != {
        "manifest.json", "profile.json", "payload",
    }:
        raise CompanyScribeAdmissionError(
            "Company role package contains unmanifested files"
        )
    basis = {
        key: manifest[key]
        for key in manifest
        if key not in {"package_id", "package_sha256", "state"}
    }
    package_sha = _sha256(_canonical_json(basis))
    package_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"mise:{profile.role_type}:{profile.deployment_id}:{package_sha}",
    ))
    if (
        manifest["package_sha256"] != package_sha
        or manifest["package_id"] != package_id
    ):
        raise CompanyScribeAdmissionError(
            "Company role package digest or identity is forged"
        )
    return manifest


def install_company_role_package(
    package_dir: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
    install_root: Path,
) -> dict[str, Any]:
    """Install exact role bytes under only that role's private root."""
    manifest = verify_company_role_package(package_dir, profile=profile)
    packages_root = install_root / "packages"
    _secure_directory(packages_root, create=True)
    destination = packages_root / manifest["package_sha256"]
    if not destination.exists():
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=packages_root))
        try:
            shutil.copytree(package_dir, staging / "package")
            verify_company_role_package(staging / "package", profile=profile)
            os.replace(staging / "package", destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    installed = verify_company_role_package(destination, profile=profile)
    _write_pointer(install_root / "HELD.json", {
        "schema": COMPANY_ROLE_HELD_POINTER_SCHEMA,
        "state": "held",
        "profile_sha256": profile.sha256,
        "role_type": profile.role_type,
        "deployment_id": profile.deployment_id,
        "package_id": installed["package_id"],
        "package_sha256": installed["package_sha256"],
        "kernel_release_sha256": installed["kernel_release_sha256"],
        "source_commit_sha": installed["source_commit_sha"],
        "source_tree_sha": installed["source_tree_sha"],
    })
    return installed


def register_company_role_package(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
    package_dir: Path,
) -> dict[str, Any]:
    """Register and install exactly one package under its role target."""
    manifest = verify_company_role_package(package_dir, profile=profile)
    installed_root = str(package_dir.resolve(strict=True))
    package_id = uuid.UUID(str(manifest["package_id"]))
    with conn.transaction():
        conn.execute(
            "INSERT INTO kernel_company_scribe_packages ("
            "id,company_id,role_type,deployment_id,kernel_release_sha256,"
            "package_sha256,source_commit_sha,source_tree_sha,host_kind,"
            "installed_root,package_manifest) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO NOTHING",
            (
                package_id, profile.company_id, profile.role_type,
                profile.deployment_id, manifest["kernel_release_sha256"],
                manifest["package_sha256"], manifest["source_commit_sha"],
                manifest["source_tree_sha"], profile.host_kind,
                installed_root, Jsonb(manifest),
            ),
        )
        observed = conn.execute(
            "SELECT id,company_id,role_type,deployment_id,"
            "kernel_release_sha256,package_sha256,source_commit_sha,"
            "source_tree_sha,host_kind,installed_root,package_manifest,state "
            "FROM kernel_company_scribe_packages WHERE id=%s FOR UPDATE",
            (package_id,),
        ).fetchone()
        if observed is None:
            raise CompanyScribeAdmissionError(
                "Company role package registration disappeared"
            )
        keys = (
            "id", "company_id", "role_type", "deployment_id",
            "kernel_release_sha256", "package_sha256", "source_commit_sha",
            "source_tree_sha", "host_kind", "installed_root",
            "package_manifest", "state",
        )
        row = dict(observed) if isinstance(observed, Mapping) else dict(
            zip(keys, observed, strict=True)
        )
        expected = {
            "id": package_id,
            "company_id": profile.company_id,
            "role_type": profile.role_type,
            "deployment_id": profile.deployment_id,
            "kernel_release_sha256": manifest["kernel_release_sha256"],
            "package_sha256": manifest["package_sha256"],
            "source_commit_sha": manifest["source_commit_sha"],
            "source_tree_sha": manifest["source_tree_sha"],
            "host_kind": profile.host_kind,
            "installed_root": installed_root,
            "package_manifest": manifest,
        }
        if any(row[key] != value for key, value in expected.items()):
            raise CompanyScribeAdmissionError(
                "Company role package id belongs to different bytes or role"
            )
        if row["state"] == "held":
            conn.execute(
                "UPDATE kernel_company_scribe_packages SET state='installed' "
                "WHERE id=%s AND state='held'",
                (package_id,),
            )
        elif row["state"] != "installed":
            raise CompanyScribeAdmissionError(
                "Company role package registration is retired"
            )
    return {
        **manifest,
        "installed_root": installed_root,
        "database_state": "installed",
    }


def register_installed_package(
    conn: psycopg.Connection,
    *,
    package_dir: Path,
) -> dict[str, Any]:
    """Register exact installed bytes in PostgreSQL and mark only that row installed."""
    manifest = verify_complete_release_package(package_dir)
    package_id = uuid.UUID(str(manifest["package_id"]))
    installed_root = str(package_dir.resolve(strict=True))
    with conn.transaction():
        conn.execute(
            "INSERT INTO kernel_company_scribe_packages "
            "(id,company_id,role_type,deployment_id,kernel_release_sha256,"
            "package_sha256,source_commit_sha,source_tree_sha,"
            "installed_root,package_manifest) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO NOTHING",
            (
                package_id, COMPANY_ID, "scribe", DEPLOYMENT_ID,
                manifest["kernel_release_sha256"],
                manifest["package_sha256"], manifest["source_commit_sha"],
                manifest["source_tree_sha"], installed_root,
                Jsonb(manifest),
            ),
        )
        observed = conn.execute(
            "SELECT id,company_id,deployment_id,kernel_release_sha256,"
            "package_sha256,source_commit_sha,source_tree_sha,"
            "installed_root,package_manifest,state "
            "FROM kernel_company_scribe_packages WHERE id=%s FOR UPDATE",
            (package_id,),
        ).fetchone()
        if observed is None:
            raise CompanyScribeAdmissionError(
                "installed Scribe package registration disappeared"
            )
        keys = (
            "id", "company_id", "deployment_id",
            "kernel_release_sha256", "package_sha256",
            "source_commit_sha", "source_tree_sha",
            "installed_root", "package_manifest", "state",
        )
        row = (
            dict(observed)
            if isinstance(observed, Mapping)
            else dict(zip(keys, observed, strict=True))
        )
        expected = {
            "id": package_id,
            "company_id": COMPANY_ID,
            "deployment_id": DEPLOYMENT_ID,
            "kernel_release_sha256": manifest["kernel_release_sha256"],
            "package_sha256": manifest["package_sha256"],
            "source_commit_sha": manifest["source_commit_sha"],
            "source_tree_sha": manifest["source_tree_sha"],
            "installed_root": installed_root,
            "package_manifest": manifest,
        }
        if any(row[key] != value for key, value in expected.items()):
            raise CompanyScribeAdmissionError(
                "package id is already registered to different installed bytes"
            )
        if row["state"] == "held":
            conn.execute(
                "UPDATE kernel_company_scribe_packages "
                "SET state='installed' WHERE id=%s AND state='held'",
                (package_id,),
            )
        elif row["state"] != "installed":
            raise CompanyScribeAdmissionError(
                "Scribe package registration is retired"
            )
    return {
        **manifest,
        "installed_root": installed_root,
        "database_state": "installed",
    }


def rollback_held_package(*, install_root: Path) -> dict[str, Any]:
    """Restore the prior verified HELD pointer; never create a live admission."""
    rollback = install_root / "ROLLBACK.json"
    try:
        rollback.lstat()
    except FileNotFoundError:
        raise CompanyScribeAdmissionError("no prior held Scribe package to restore")
    try:
        prior = json.loads(_read_private_file(rollback))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe rollback pointer is unreadable"
        ) from exc
    if prior.get("schema") != HELD_POINTER_SCHEMA or prior.get("state") != "held":
        raise CompanyScribeAdmissionError("Scribe rollback pointer is malformed")
    package = install_root / "packages" / _exact_sha(
        prior.get("package_sha256"), "rollback package", _SHA256,
    )
    manifest = verify_package(package)
    if (
        manifest["package_id"] != prior.get("package_id")
        or manifest["kernel_release_sha256"] != prior.get("kernel_release_sha256")
        or manifest["source_commit_sha"] != prior.get("source_commit_sha")
        or manifest["source_tree_sha"] != prior.get("source_tree_sha")
    ):
        raise CompanyScribeAdmissionError("Scribe rollback package binding changed")
    current = install_root / "HELD.json"
    try:
        current.lstat()
    except FileNotFoundError:
        current_payload = None
    else:
        try:
            current_payload = json.loads(_read_private_file(current))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanyScribeAdmissionError(
                "current Scribe pointer is unreadable"
            ) from exc
    _write_pointer(current, prior)
    if current_payload is not None:
        _write_pointer(rollback, current_payload)
    return manifest
