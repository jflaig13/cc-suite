# SPDX-License-Identifier: MPL-2.0
"""External MacBook provisioner/supervisor for the writable Company Scribe.

This process is intentionally separate from Claude Code.  Its database route
must be a provisioner route that is never inherited by the Scribe runtime.
It can prepare an exact admission candidate, consume an already-approved M5
decision, renew bounded leases, and fence a dead process.  It never creates or
decides an approval.
"""
from __future__ import annotations
from fleet_kernel.configuration import company_id, scribe_deployment_id, authority_issuer, deployment_binding

import fcntl
import json
import os
import stat
import subprocess
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

import psycopg
from psycopg.rows import dict_row

from fleet_kernel import identity
from fleet_kernel.company_scribe_admission import (
    COMPANY_ROLE_PROFILE_SCHEMA,
    COMPANY_ID,
    DEPLOYMENT_ID,
    INITIAL_AUTHORITY_MODE,
    RECOVERY_AUTHORITY_MODE,
    RUNTIME_KIND,
    REVOKED_BINDING_POINTER_SCHEMA,
    CompanyScribeAdmissionError,
    CompanyRoleAdmissionProfile,
    CompanyRoleAdmissionBinding,
    ScribeAdmissionBinding,
    assert_company_role_host_admitted,
    _exact_sha,
    _canonical_json,
    _crash_checkpoint,
    _load_rollback_binding,
    _load_company_role_rollback_binding,
    _process_state,
    _read_private_file,
    _required,
    _SHA256,
    _GIT_SHA,
    _write_pointer,
    _sha256,
    boot_identity_sha256,
    company_role_activation_subject_payload,
    create_role_restart_intent,
    create_company_role_live_admission,
    create_restart_intent,
    create_live_admission,
    executable_sha256,
    host_identity_sha256,
    load_binding,
    load_role_restart_intent,
    load_restart_epoch_receipt,
    load_restart_intent,
    process_start_fingerprint,
    register_company_role_activation_subject,
    record_restart_epoch_receipt,
    renew_live_admission,
    rollback_live_scribe,
    rollback_live_company_role,
    scribe_activation_subject,
    verify_runtime_lock_held,
    write_current_lease_pointer,
)
from fleet_kernel.founder_authority import FounderActivationMandate


BOOTSTRAP_SCHEMA = "mise.company-scribe-bootstrap-request.v1"
CANDIDATE_SCHEMA = "mise.company-scribe-admission-candidate.v1"
HEARTBEAT_SCHEMA = "mise.company-scribe-supervisor-heartbeat.v1"
RESTART_REQUEST_SCHEMA = "mise.company-scribe-restart-request.v1"
COMPANY_ROLE_BOOTSTRAP_SCHEMA = "mise.company-role-bootstrap-request.v1"
COMPANY_ROLE_CANDIDATE_SCHEMA = "mise.company-role-admission-candidate.v1"
COMPANY_ROLE_HEARTBEAT_SCHEMA = "mise.company-role-supervisor-heartbeat.v1"
COMPANY_ROLE_RESTART_REQUEST_SCHEMA = "mise.company-role-restart-request.v1"
_PROFILE_ARTIFACT_KEYS = frozenset({
    "schema", "company_id", "role_type", "deployment_id", "worker_id",
    "host_kind", "runtime_kind", "mandate_id", "mandate_artifact_sha256",
    "package_schema", "bootstrap_schema", "candidate_schema",
    "heartbeat_schema", "restart_request_schema", "admission_lock_key",
    "supervisor_lock_key", "state_namespace",
})
_BOOTSTRAP_KEYS = frozenset({
    "schema", "company_id", "deployment_id", "runtime_kind", "task_id",
    "claude_session_id", "process_id",
    "process_start_fingerprint_sha256", "repo_commit_sha", "repo_tree_sha",
    "host_identity_sha256", "host_boot_id_sha256",
    "runtime_executable_path", "runtime_executable_sha256",
    "runtime_lock_path",
})


@dataclass(frozen=True, slots=True)
class ScribeBootstrapRequest:
    task_id: str
    claude_session_id: str
    process_id: int
    process_start_fingerprint_sha256: str
    repo_commit_sha: str
    repo_tree_sha: str
    host_identity_sha256: str
    host_boot_id_sha256: str
    runtime_executable_path: str
    runtime_executable_sha256: str
    runtime_lock_path: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": BOOTSTRAP_SCHEMA,
            "company_id": COMPANY_ID,
            "deployment_id": DEPLOYMENT_ID,
            "runtime_kind": RUNTIME_KIND,
            "task_id": self.task_id,
            "claude_session_id": self.claude_session_id,
            "process_id": self.process_id,
            "process_start_fingerprint_sha256": (
                self.process_start_fingerprint_sha256
            ),
            "repo_commit_sha": self.repo_commit_sha,
            "repo_tree_sha": self.repo_tree_sha,
            "host_identity_sha256": self.host_identity_sha256,
            "host_boot_id_sha256": self.host_boot_id_sha256,
            "runtime_executable_path": self.runtime_executable_path,
            "runtime_executable_sha256": self.runtime_executable_sha256,
            "runtime_lock_path": self.runtime_lock_path,
        }

    @classmethod
    def from_path(cls, path: Path) -> "ScribeBootstrapRequest":
        try:
            payload = json.loads(_read_private_file(path))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanyScribeAdmissionError(
                "Scribe bootstrap request is unreadable"
            ) from exc
        if (
            not isinstance(payload, dict)
            or frozenset(payload) != _BOOTSTRAP_KEYS
            or payload.get("schema") != BOOTSTRAP_SCHEMA
            or payload.get("company_id") != COMPANY_ID
            or payload.get("deployment_id") != DEPLOYMENT_ID
            or payload.get("runtime_kind") != RUNTIME_KIND
        ):
            raise CompanyScribeAdmissionError(
                "Scribe bootstrap request identity is invalid"
            )
        try:
            request = cls(
                task_id=_required(payload.get("task_id"), "task_id"),
                claude_session_id=_required(
                    payload.get("claude_session_id"), "claude_session_id",
                ),
                process_id=int(payload.get("process_id", 0)),
                process_start_fingerprint_sha256=_exact_sha(
                    payload.get("process_start_fingerprint_sha256"),
                    "process_start_fingerprint_sha256", _SHA256,
                ),
                repo_commit_sha=_exact_sha(
                    payload.get("repo_commit_sha"), "repo_commit_sha", _GIT_SHA,
                ),
                repo_tree_sha=_exact_sha(
                    payload.get("repo_tree_sha"), "repo_tree_sha", _GIT_SHA,
                ),
                host_identity_sha256=_exact_sha(
                    payload.get("host_identity_sha256"),
                    "host_identity_sha256", _SHA256,
                ),
                host_boot_id_sha256=_exact_sha(
                    payload.get("host_boot_id_sha256"),
                    "host_boot_id_sha256", _SHA256,
                ),
                runtime_executable_path=_required(
                    payload.get("runtime_executable_path"),
                    "runtime_executable_path",
                ),
                runtime_executable_sha256=_exact_sha(
                    payload.get("runtime_executable_sha256"),
                    "runtime_executable_sha256", _SHA256,
                ),
                runtime_lock_path=_required(
                    payload.get("runtime_lock_path"), "runtime_lock_path",
                ),
            )
        except (TypeError, ValueError) as exc:
            raise CompanyScribeAdmissionError(
                "Scribe bootstrap request is malformed"
            ) from exc
        if request.process_id <= 1:
            raise CompanyScribeAdmissionError(
                "Scribe bootstrap process id is invalid"
            )
        request.verify_local_process()
        return request

    def verify_local_process(self) -> None:
        if self.host_identity_sha256 != host_identity_sha256():
            raise CompanyScribeAdmissionError(
                "Scribe bootstrap belongs to another host"
            )
        if self.host_boot_id_sha256 != boot_identity_sha256():
            raise CompanyScribeAdmissionError(
                "Scribe bootstrap belongs to another boot"
            )
        if (
            process_start_fingerprint(self.process_id)
            != self.process_start_fingerprint_sha256
        ):
            raise CompanyScribeAdmissionError(
                "Scribe bootstrap PID start fingerprint changed"
            )
        executable = Path(self.runtime_executable_path)
        if (
            not executable.is_absolute()
            or str(executable.resolve(strict=True))
            != self.runtime_executable_path
            or executable_sha256(executable)
            != self.runtime_executable_sha256
        ):
            raise CompanyScribeAdmissionError(
                "Scribe bootstrap executable changed"
            )
        runtime_lock = Path(self.runtime_lock_path)
        if not runtime_lock.is_absolute() or ".." in runtime_lock.parts:
            raise CompanyScribeAdmissionError(
                "Scribe bootstrap runtime lock path is unsafe"
            )
        verify_runtime_lock_held(runtime_lock, expected_pid=self.process_id)


def load_company_role_profile_artifact(
    path: Path,
    *,
    expected_sha256: str,
) -> CompanyRoleAdmissionProfile:
    """Load one canonical profile artifact; no identity field comes from CLI."""
    raw = _read_private_file(path)
    if _sha256(raw) != _exact_sha(
        expected_sha256, "profile artifact SHA-256", _SHA256,
    ):
        raise CompanyScribeAdmissionError(
            "Company role profile artifact digest is not exact"
        )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role profile artifact is unreadable"
        ) from exc
    if not isinstance(payload, dict) or frozenset(payload) != _PROFILE_ARTIFACT_KEYS:
        raise CompanyScribeAdmissionError(
            "Company role profile artifact is not closed"
        )
    profile = CompanyRoleAdmissionProfile.from_mapping({
        field: payload[field]
        for field in (
            "company_id", "role_type", "deployment_id", "worker_id",
            "host_kind", "runtime_kind", "mandate_id",
            "mandate_artifact_sha256",
        )
    })
    if payload != profile.canonical_payload:
        raise CompanyScribeAdmissionError(
            "Company role profile contains independently supplied derived fields"
        )
    return profile


@dataclass(frozen=True, slots=True)
class CompanyRoleBootstrapRequest:
    """Generic bootstrap tuple bound to one digest-verified role profile."""

    profile_sha256: str
    task_id: str
    claude_session_id: str
    process_id: int
    process_start_fingerprint_sha256: str
    repo_commit_sha: str
    repo_tree_sha: str
    host_identity_sha256: str
    host_boot_id_sha256: str
    runtime_executable_path: str
    runtime_executable_sha256: str
    runtime_lock_path: str

    def to_mapping(
        self,
        *,
        profile: CompanyRoleAdmissionProfile,
    ) -> dict[str, object]:
        return {
            "schema": profile.bootstrap_schema,
            "profile_sha256": self.profile_sha256,
            "company_id": profile.company_id,
            "role_type": profile.role_type,
            "deployment_id": profile.deployment_id,
            "worker_id": str(profile.worker_id),
            "host_kind": profile.host_kind,
            "runtime_kind": profile.runtime_kind,
            "task_id": self.task_id,
            "claude_session_id": self.claude_session_id,
            "process_id": self.process_id,
            "process_start_fingerprint_sha256": (
                self.process_start_fingerprint_sha256
            ),
            "repo_commit_sha": self.repo_commit_sha,
            "repo_tree_sha": self.repo_tree_sha,
            "host_identity_sha256": self.host_identity_sha256,
            "host_boot_id_sha256": self.host_boot_id_sha256,
            "runtime_executable_path": self.runtime_executable_path,
            "runtime_executable_sha256": self.runtime_executable_sha256,
            "runtime_lock_path": self.runtime_lock_path,
        }

    def verify_local_process(self) -> None:
        if self.host_identity_sha256 != host_identity_sha256():
            raise CompanyScribeAdmissionError(
                "Company role bootstrap belongs to another host"
            )
        if self.host_boot_id_sha256 != boot_identity_sha256():
            raise CompanyScribeAdmissionError(
                "Company role bootstrap belongs to another boot"
            )
        if (
            process_start_fingerprint(self.process_id)
            != self.process_start_fingerprint_sha256
        ):
            raise CompanyScribeAdmissionError(
                "Company role bootstrap PID start fingerprint changed"
            )
        executable = Path(self.runtime_executable_path)
        if (
            not executable.is_absolute()
            or str(executable.resolve(strict=True))
            != self.runtime_executable_path
            or executable_sha256(executable)
            != self.runtime_executable_sha256
        ):
            raise CompanyScribeAdmissionError(
                "Company role bootstrap executable changed"
            )
        runtime_lock_path = Path(self.runtime_lock_path)
        if (
            not runtime_lock_path.is_absolute()
            or ".." in runtime_lock_path.parts
        ):
            raise CompanyScribeAdmissionError(
                "Company role bootstrap runtime lock path is unsafe"
            )
        verify_runtime_lock_held(
            runtime_lock_path, expected_pid=self.process_id,
        )

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        profile: CompanyRoleAdmissionProfile,
        profile_sha256: str,
    ) -> "CompanyRoleBootstrapRequest":
        try:
            payload = json.loads(_read_private_file(path))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanyScribeAdmissionError(
                "Company role bootstrap request is unreadable"
            ) from exc
        keys = frozenset({
            "schema", "profile_sha256", "company_id", "role_type",
            "deployment_id", "worker_id", "runtime_kind", "task_id",
            "claude_session_id", "process_id",
            "process_start_fingerprint_sha256", "repo_commit_sha",
            "repo_tree_sha", "host_kind", "host_identity_sha256",
            "host_boot_id_sha256", "runtime_executable_path",
            "runtime_executable_sha256", "runtime_lock_path",
        })
        if not isinstance(payload, dict) or frozenset(payload) != keys:
            raise CompanyScribeAdmissionError(
                "Company role bootstrap request is not closed"
            )
        exact_profile_sha = _exact_sha(
            profile_sha256, "profile_sha256", _SHA256,
        )
        if (
            payload["schema"] != COMPANY_ROLE_BOOTSTRAP_SCHEMA
            or payload["profile_sha256"] != exact_profile_sha
            or payload["company_id"] != profile.company_id
            or payload["role_type"] != profile.role_type
            or payload["deployment_id"] != profile.deployment_id
            or str(payload["worker_id"]) != str(profile.worker_id)
            or payload["host_kind"] != profile.host_kind
            or payload["runtime_kind"] != profile.runtime_kind
        ):
            raise CompanyScribeAdmissionError(
                "Company role bootstrap differs from its exact profile"
            )
        try:
            request = cls(
                profile_sha256=exact_profile_sha,
                task_id=_required(payload["task_id"], "task_id"),
                claude_session_id=_required(
                    payload["claude_session_id"], "claude_session_id",
                ),
                process_id=int(payload["process_id"]),
                process_start_fingerprint_sha256=_exact_sha(
                    payload["process_start_fingerprint_sha256"],
                    "process_start_fingerprint_sha256", _SHA256,
                ),
                repo_commit_sha=_exact_sha(
                    payload["repo_commit_sha"], "repo_commit_sha", _GIT_SHA,
                ),
                repo_tree_sha=_exact_sha(
                    payload["repo_tree_sha"], "repo_tree_sha", _GIT_SHA,
                ),
                host_identity_sha256=_exact_sha(
                    payload["host_identity_sha256"],
                    "host_identity_sha256", _SHA256,
                ),
                host_boot_id_sha256=_exact_sha(
                    payload["host_boot_id_sha256"],
                    "host_boot_id_sha256", _SHA256,
                ),
                runtime_executable_path=_required(
                    payload["runtime_executable_path"],
                    "runtime_executable_path",
                ),
                runtime_executable_sha256=_exact_sha(
                    payload["runtime_executable_sha256"],
                    "runtime_executable_sha256", _SHA256,
                ),
                runtime_lock_path=_required(
                    payload["runtime_lock_path"], "runtime_lock_path",
                ),
            )
        except (TypeError, ValueError) as exc:
            raise CompanyScribeAdmissionError(
                "Company role bootstrap request is malformed"
            ) from exc
        if request.process_id <= 1:
            raise CompanyScribeAdmissionError(
                "Company role bootstrap process id is invalid"
            )
        return request


def require_exact_company_role_target(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
) -> Mapping[str, Any]:
    """Read one eligible target with every role/deployment/worker pin scoped."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT company_id,role_type,deployment_id,worker_id,host_kind,"
            "runtime_kind,mandate_id,mandate_artifact_sha256,state "
            "FROM kernel_company_role_admission_targets "
            "WHERE company_id=%s AND role_type=%s AND deployment_id=%s "
            "AND worker_id=%s AND host_kind=%s AND runtime_kind=%s "
            "AND mandate_id=%s AND mandate_artifact_sha256=%s "
            "AND state='eligible'",
            (
                profile.company_id, profile.role_type, profile.deployment_id,
                profile.worker_id, profile.host_kind, profile.runtime_kind,
                profile.mandate_id, profile.mandate_artifact_sha256,
            ),
        ).fetchall()
    if len(rows) != 1:
        raise CompanyScribeAdmissionError(
            "Company role profile has no one exact eligible database target"
        )
    return rows[0]


def register_company_role_admission_target(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
) -> Mapping[str, Any]:
    """Register and make eligible only the exact mandate/worker target."""
    if profile.role_type == "scribe":
        raise CompanyScribeAdmissionError(
            "generic Company role target registration cannot target Scribe"
        )
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        mandate = cursor.execute(
            "SELECT status,artifact_sha256 FROM "
            "kernel_founder_activation_mandates WHERE mandate_id=%s FOR SHARE",
            (profile.mandate_id,),
        ).fetchone()
        principal = cursor.execute(
            "SELECT principal.worker_id,principal.company_id,"
            "principal.role_type,worker.status,worker.fleet_type,"
            "manifest.status AS manifest_status "
            "FROM kernel_company_worker_principals principal "
            "JOIN kernel_runtime_workers worker "
            "ON worker.id=principal.worker_id "
            "AND worker.tenant_id=principal.company_id "
            "JOIN kernel_tenant_fleet_manifests manifest "
            "ON manifest.id=worker.manifest_id "
            "WHERE principal.worker_id=%s AND principal.company_id=%s "
            "AND principal.role_type=%s FOR SHARE OF worker,manifest",
            (profile.worker_id, profile.company_id, profile.role_type),
        ).fetchone()
        if (
            mandate is None
            or mandate["status"] != "active"
            or mandate["artifact_sha256"]
            != profile.mandate_artifact_sha256
            or principal is None
            or principal["status"] != "active"
            or principal["fleet_type"] != "company"
            or principal["manifest_status"] != "active"
        ):
            raise CompanyScribeAdmissionError(
                "Company role target requires its registered mandate and principal"
            )
        cursor.execute(
            "INSERT INTO kernel_company_role_admission_targets ("
            "company_id,role_type,deployment_id,worker_id,host_kind,"
            "runtime_kind,mandate_id,mandate_artifact_sha256,state) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'held') "
            "ON CONFLICT (company_id,role_type) DO NOTHING",
            (
                profile.company_id, profile.role_type,
                profile.deployment_id, profile.worker_id,
                profile.host_kind, profile.runtime_kind,
                profile.mandate_id, profile.mandate_artifact_sha256,
            ),
        )
        row = cursor.execute(
            "SELECT * FROM kernel_company_role_admission_targets "
            "WHERE company_id=%s AND role_type=%s FOR UPDATE",
            (profile.company_id, profile.role_type),
        ).fetchone()
        exact = (
            row is not None
            and row["deployment_id"] == profile.deployment_id
            and row["worker_id"] == profile.worker_id
            and row["host_kind"] == profile.host_kind
            and row["runtime_kind"] == profile.runtime_kind
            and row["mandate_id"] == profile.mandate_id
            and row["mandate_artifact_sha256"]
            == profile.mandate_artifact_sha256
            and row["state"] in {"held", "eligible"}
        )
        if not exact:
            raise CompanyScribeAdmissionError(
                "Company role target slot belongs to another exact tuple"
            )
        if row["state"] == "held":
            row = cursor.execute(
                "UPDATE kernel_company_role_admission_targets "
                "SET state='eligible',eligible_at=now() "
                "WHERE company_id=%s AND role_type=%s AND state='held' "
                "RETURNING *",
                (profile.company_id, profile.role_type),
            ).fetchone()
    return row


@contextmanager
def company_role_supervisor_database_lock(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
):
    """Cross-host lock scoped only by the canonical role from the profile."""
    info = getattr(conn, "info", None)
    status = getattr(info, "transaction_status", None)
    if status is not None and status != psycopg.pq.TransactionStatus.IDLE:
        raise CompanyScribeAdmissionError(
            "Company role supervisor requires an idle provisioner connection"
        )
    if hasattr(conn, "autocommit"):
        conn.autocommit = True
    conn.execute(
        "SELECT pg_advisory_lock(hashtextextended(%s,0))",
        (profile.supervisor_lock_key,),
    )
    try:
        yield
    finally:
        conn.execute(
            "SELECT pg_advisory_unlock(hashtextextended(%s,0))",
            (profile.supervisor_lock_key,),
        )


def prepare_company_role_initial_candidate(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
    request: CompanyRoleBootstrapRequest,
    mandate,
    predecessor_fence_receipt_sha256: str,
    lease_seconds: int = 120,
) -> CompanyRoleAdmissionBinding:
    """Prepare and register one exact initial non-Scribe admission subject."""
    assert_company_role_host_admitted(profile.role_type, profile.runtime_kind)
    from fleet_kernel.founder_authority import (  # noqa: PLC0415
        Phase4FounderActivationMandate,
        UtilityFounderActivationMandate,
    )

    if profile.role_type == "utility":
        if not isinstance(mandate, UtilityFounderActivationMandate):
            raise CompanyScribeAdmissionError(
                "Utility initial candidate requires the Utility mandate"
            )
    elif not isinstance(mandate, Phase4FounderActivationMandate):
        raise CompanyScribeAdmissionError(
            "Company role initial candidate requires the Phase-4 mandate"
        )
    if profile.role_type == "scribe":
        raise CompanyScribeAdmissionError(
            "generic Company role admission cannot target Scribe"
        )
    mandate.require_recordable()
    request.verify_local_process()
    fence_sha = _exact_sha(
        predecessor_fence_receipt_sha256,
        "predecessor fence receipt", _SHA256,
    )
    if lease_seconds < 30 or lease_seconds > 300:
        raise CompanyScribeAdmissionError(
            "Company role initial lease must be between 30 and 300 seconds"
        )
    if (
        request.profile_sha256 != profile.sha256
        or mandate.mandate_id != profile.mandate_id
        or mandate.artifact_sha256 != profile.mandate_artifact_sha256
    ):
        raise CompanyScribeAdmissionError(
            "Company role initial candidate differs from its profile or mandate"
        )
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (profile.admission_lock_key,),
        )
        require_exact_company_role_target(conn, profile=profile)
        mandate_row = cursor.execute(
            "SELECT status,artifact_sha256 FROM "
            "kernel_founder_activation_mandates WHERE mandate_id=%s FOR SHARE",
            (profile.mandate_id,),
        ).fetchone()
        worker = cursor.execute(
            "SELECT worker.*,manifest.status AS manifest_status "
            "FROM kernel_runtime_workers worker "
            "JOIN kernel_tenant_fleet_manifests manifest "
            "ON manifest.id=worker.manifest_id "
            "AND manifest.tenant_id=worker.tenant_id "
            "WHERE worker.id=%s AND worker.tenant_id=%s "
            "AND worker.role_type=%s FOR SHARE OF worker,manifest",
            (profile.worker_id, profile.company_id, profile.role_type),
        ).fetchone()
        packages = cursor.execute(
            "SELECT id,kernel_release_sha256,package_sha256,source_commit_sha,"
            "source_tree_sha,package_manifest FROM "
            "kernel_company_scribe_packages WHERE company_id=%s "
            "AND role_type=%s AND deployment_id=%s AND state='installed'",
            (profile.company_id, profile.role_type, profile.deployment_id),
        ).fetchall()
        live_count = cursor.execute(
            "SELECT count(*) AS count FROM kernel_company_scribe_admissions "
            "WHERE company_id=%s AND role_type=%s AND state='live'",
            (profile.company_id, profile.role_type),
        ).fetchone()["count"]
        previous_epoch = cursor.execute(
            "SELECT COALESCE(max(admission_epoch),0) AS epoch FROM "
            "kernel_company_scribe_admissions WHERE company_id=%s "
            "AND role_type=%s",
            (profile.company_id, profile.role_type),
        ).fetchone()["epoch"]
        active_sessions = cursor.execute(
            "SELECT count(*) AS count FROM kernel_worker_sessions "
            "WHERE tenant_id=%s AND worker_id=%s AND role_type=%s "
            "AND state='active' AND lease_expires_at>now()",
            (profile.company_id, profile.worker_id, profile.role_type),
        ).fetchone()["count"]
        if (
            mandate_row is None
            or mandate_row["status"] != "active"
            or mandate_row["artifact_sha256"]
            != profile.mandate_artifact_sha256
        ):
            raise CompanyScribeAdmissionError(
                "Company role candidate requires the exact active mandate"
            )
        if (
            worker is None
            or worker["fleet_type"] != "company"
            or worker["status"] != "active"
            or worker["manifest_status"] != "active"
        ):
            raise CompanyScribeAdmissionError(
                "Company role candidate requires its exact active worker"
            )
        if len(packages) != 1:
            raise CompanyScribeAdmissionError(
                "Company role candidate requires one exact installed package"
            )
        package = packages[0]
        package_manifest = package["package_manifest"]
        if (
            package["source_commit_sha"] != request.repo_commit_sha
            or package["source_tree_sha"] != request.repo_tree_sha
            or package_manifest.get("profile_sha256") != profile.sha256
            or package_manifest.get("role_type") != profile.role_type
            or package_manifest.get("deployment_id") != profile.deployment_id
        ):
            raise CompanyScribeAdmissionError(
                "Company role bootstrap differs from its installed package"
            )
        if live_count != 0 or active_sessions != 0:
            raise CompanyScribeAdmissionError(
                "Company role initial candidate requires no live role lineage"
            )
        _end_stale_company_role_sessions(
            cursor, profile=profile,
        )
        session = identity.open_worker_session(
            conn,
            tenant_id=profile.company_id,
            worker_id=profile.worker_id,
            lease_duration=timedelta(seconds=lease_seconds),
        )
        admission_id = uuid.uuid4()
        provisional = CompanyRoleAdmissionBinding(
            admission_id=admission_id,
            worker_id=profile.worker_id,
            worker_session_id=session.session_id,
            package_id=package["id"],
            agent_release_sha256=worker["agent_release_sha256"],
            kernel_release_sha256=package["kernel_release_sha256"],
            package_sha256=package["package_sha256"],
            task_id=request.task_id,
            claude_session_id=request.claude_session_id,
            process_id=request.process_id,
            process_start_fingerprint_sha256=(
                request.process_start_fingerprint_sha256
            ),
            repo_commit_sha=request.repo_commit_sha,
            repo_tree_sha=request.repo_tree_sha,
            lease_token=session.lease_token,
            admission_epoch=int(previous_epoch) + 1,
            lease_expires_at=session.lease_expires_at,
            mandate_id=profile.mandate_id,
            mandate_artifact_sha256=profile.mandate_artifact_sha256,
            authority_approval_id=admission_id,
            authority_subject_ref="pending",
            authority_subject_sha256="0" * 64,
            mini_activation_receipt_sha256=fence_sha,
            host_identity_sha256=request.host_identity_sha256,
            host_boot_id_sha256=request.host_boot_id_sha256,
            runtime_executable_path=request.runtime_executable_path,
            runtime_executable_sha256=request.runtime_executable_sha256,
            runtime_lock_path=request.runtime_lock_path,
            authority_mode=INITIAL_AUTHORITY_MODE,
            authority_root_admission_id=admission_id,
            deployment_id=profile.deployment_id,
            company_id=profile.company_id,
            role_type=profile.role_type,
            runtime_kind=profile.runtime_kind,
        )
        payload = company_role_activation_subject_payload(
            profile=profile, binding=provisional,
        )
        digest = _sha256(_canonical_json(payload))
        binding = replace(
            provisional,
            authority_subject_ref=(
                f"founder://company-role-activation/{profile.mandate_id}/"
                f"{profile.role_type}/{digest}"
            ),
            authority_subject_sha256=digest,
        )
        register_company_role_activation_subject(
            conn,
            profile=profile,
            binding=binding,
            mandate=mandate,
        )
    return binding


def _end_stale_company_role_sessions(
    cursor,
    *,
    profile: CompanyRoleAdmissionProfile,
) -> None:
    """End expired sessions and clear only their exact role worker lease."""
    stale = cursor.execute(
        "UPDATE kernel_worker_sessions SET state='ended',"
        "ended_at=COALESCE(ended_at,now()) WHERE tenant_id=%s "
        "AND worker_id=%s AND role_type=%s AND state='active' "
        "AND lease_expires_at<=now() RETURNING lease_token",
        (profile.company_id, profile.worker_id, profile.role_type),
    ).fetchall()
    for row in stale:
        cursor.execute(
            "UPDATE kernel_runtime_workers SET lease_token=NULL,"
            "lease_expires_at=NULL WHERE id=%s AND tenant_id=%s "
            "AND role_type=%s AND lease_token=%s",
            (
                profile.worker_id, profile.company_id, profile.role_type,
                row["lease_token"],
            ),
        )


def prepare_company_role_recovery_candidate(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
    request: CompanyRoleBootstrapRequest,
    mandate,
    restart_intent_path: Path,
    lease_seconds: int = 120,
) -> CompanyRoleAdmissionBinding:
    """Prepare one exact next epoch without reading another role lineage."""
    # A recovery is a next start; the rollback receipt is the sanctioned
    # exit to the seat's official host (Claude-door retirement 2026-08-15).
    assert_company_role_host_admitted(profile.role_type, profile.runtime_kind)
    from fleet_kernel.founder_authority import (  # noqa: PLC0415
        Phase4FounderActivationMandate,
        UtilityFounderActivationMandate,
    )

    if profile.role_type == "utility":
        if not isinstance(mandate, UtilityFounderActivationMandate):
            raise CompanyScribeAdmissionError(
                "Utility recovery requires the Utility mandate"
            )
    elif not isinstance(mandate, Phase4FounderActivationMandate):
        raise CompanyScribeAdmissionError(
            "Company role recovery requires the Phase-4 mandate"
        )
    if profile.role_type == "scribe":
        raise CompanyScribeAdmissionError(
            "generic Company role recovery cannot target Scribe"
        )
    if lease_seconds < 30 or lease_seconds > 300:
        raise CompanyScribeAdmissionError(
            "Company role recovery lease must be between 30 and 300 seconds"
        )
    request.verify_local_process()
    intent = load_role_restart_intent(
        restart_intent_path, profile=profile,
    )
    if (
        mandate.mandate_id != profile.mandate_id
        or mandate.artifact_sha256 != profile.mandate_artifact_sha256
        or request.profile_sha256 != profile.sha256
        or intent["repo_commit_sha"] != request.repo_commit_sha
        or intent["repo_tree_sha"] != request.repo_tree_sha
        or intent["host_identity_sha256"] != request.host_identity_sha256
        or intent["runtime_executable_path"]
        != request.runtime_executable_path
        or intent["runtime_executable_sha256"]
        != request.runtime_executable_sha256
        or intent["runtime_lock_path"] != request.runtime_lock_path
    ):
        raise CompanyScribeAdmissionError(
            "Company role recovery changes release, package, host, or runtime"
        )
    root_id = uuid.UUID(intent["authority_root_admission_id"])
    prior_id = uuid.UUID(intent["prior_admission_id"])
    package_id = uuid.UUID(intent["package_id"])
    root_approval_id = uuid.UUID(intent["authority_approval_id"])
    request_sha256 = _sha256(
        _canonical_json(request.to_mapping(profile=profile))
    )
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (profile.admission_lock_key,),
        )
        require_exact_company_role_target(conn, profile=profile)
        mandate_row = cursor.execute(
            "SELECT status,artifact_sha256 FROM "
            "kernel_founder_activation_mandates WHERE mandate_id=%s FOR SHARE",
            (profile.mandate_id,),
        ).fetchone()
        root = cursor.execute(
            "SELECT * FROM kernel_company_scribe_admissions "
            "WHERE id=%s AND company_id=%s AND role_type=%s "
            "AND deployment_id=%s AND worker_id=%s FOR SHARE",
            (
                root_id, profile.company_id, profile.role_type,
                profile.deployment_id, profile.worker_id,
            ),
        ).fetchone()
        prior = cursor.execute(
            "SELECT admission.*,session.state AS session_state "
            "FROM kernel_company_scribe_admissions admission "
            "JOIN kernel_worker_sessions session "
            "ON session.id=admission.worker_session_id "
            "WHERE admission.id=%s AND admission.company_id=%s "
            "AND admission.role_type=%s AND admission.deployment_id=%s "
            "AND admission.worker_id=%s FOR SHARE",
            (
                prior_id, profile.company_id, profile.role_type,
                profile.deployment_id, profile.worker_id,
            ),
        ).fetchone()
        package = cursor.execute(
            "SELECT * FROM kernel_company_scribe_packages "
            "WHERE id=%s AND company_id=%s AND role_type=%s "
            "AND deployment_id=%s FOR SHARE",
            (
                package_id, profile.company_id, profile.role_type,
                profile.deployment_id,
            ),
        ).fetchone()
        worker = cursor.execute(
            "SELECT * FROM kernel_runtime_workers WHERE id=%s "
            "AND tenant_id=%s AND role_type=%s FOR NO KEY UPDATE",
            (profile.worker_id, profile.company_id, profile.role_type),
        ).fetchone()
        live_count = cursor.execute(
            "SELECT count(*) AS count FROM kernel_company_scribe_admissions "
            "WHERE company_id=%s AND role_type=%s AND state='live'",
            (profile.company_id, profile.role_type),
        ).fetchone()["count"]
        open_intent = cursor.execute(
            "SELECT recovery.*,session.lease_token,"
            "session.state AS session_state,admission.state AS admission_state "
            "FROM kernel_company_scribe_recovery_intents recovery "
            "JOIN kernel_worker_sessions session "
            "ON session.id=recovery.worker_session_id "
            "LEFT JOIN kernel_company_scribe_admissions admission "
            "ON admission.id=recovery.admission_id "
            "AND admission.company_id=recovery.company_id "
            "AND admission.role_type=recovery.role_type "
            "WHERE recovery.company_id=%s AND recovery.role_type=%s "
            "AND recovery.deployment_id=%s AND recovery.worker_id=%s "
            "AND recovery.prior_admission_id=%s "
            "AND recovery.state IN ('prepared','consumed') "
            "FOR UPDATE OF recovery,session",
            (
                profile.company_id, profile.role_type,
                profile.deployment_id, profile.worker_id, prior_id,
            ),
        ).fetchone()
        if open_intent is not None:
            if (
                open_intent["bootstrap_request_sha256"] == request_sha256
                and open_intent["restart_intent_sha256"]
                == intent["restart_intent_sha256"]
                and open_intent["session_state"] == "active"
                and open_intent["lease_expires_at"]
                > datetime.now(open_intent["lease_expires_at"].tzinfo)
                and (
                    open_intent["state"] == "prepared"
                    or open_intent["admission_state"] == "live"
                )
            ):
                return _company_role_binding_from_recovery_row(
                    open_intent, profile=profile,
                )
            raise CompanyScribeAdmissionError(
                "Company role recovery epoch has another exact candidate"
            )
        static_exact = (
            mandate_row is not None
            and mandate_row["status"] in {"active", "completed"}
            and mandate_row["artifact_sha256"]
            == profile.mandate_artifact_sha256
            and root is not None
            and root["authority_mode"] == INITIAL_AUTHORITY_MODE
            and root["authority_root_admission_id"] == root_id
            and root["authority_approval_id"] == root_approval_id
            and prior is not None
            and prior["state"] == "revoked"
            and prior["session_state"] == "ended"
            and prior["authority_root_admission_id"] == root_id
            and prior["admission_epoch"] + 1 == intent["next_admission_epoch"]
            and prior["package_id"] == package_id
            and prior["package_sha256"] == intent["package_sha256"]
            and prior["agent_release_sha256"]
            == intent["agent_release_sha256"]
            and prior["kernel_release_sha256"]
            == intent["kernel_release_sha256"]
            and prior["repo_commit_sha"] == request.repo_commit_sha
            and prior["repo_tree_sha"] == request.repo_tree_sha
            and prior["host_identity_sha256"]
            == request.host_identity_sha256
            and package is not None
            and package["state"] == "installed"
            and package["package_sha256"] == intent["package_sha256"]
            and package["kernel_release_sha256"]
            == intent["kernel_release_sha256"]
            and package["source_commit_sha"] == request.repo_commit_sha
            and package["source_tree_sha"] == request.repo_tree_sha
            and worker is not None
            and worker["status"] == "active"
            and worker["agent_release_sha256"]
            == intent["agent_release_sha256"]
            and live_count == 0
        )
        if not static_exact:
            raise CompanyScribeAdmissionError(
                "Company role recovery lineage is stale or no longer exact"
            )
        _end_stale_company_role_sessions(cursor, profile=profile)
        session = identity.open_worker_session(
            conn,
            tenant_id=profile.company_id,
            worker_id=profile.worker_id,
            lease_duration=timedelta(seconds=lease_seconds),
        )
        intent_id = uuid.uuid4()
        admission_id = uuid.uuid4()
        provisional = CompanyRoleAdmissionBinding(
            admission_id=admission_id,
            worker_id=profile.worker_id,
            worker_session_id=session.session_id,
            package_id=package_id,
            agent_release_sha256=intent["agent_release_sha256"],
            kernel_release_sha256=intent["kernel_release_sha256"],
            package_sha256=intent["package_sha256"],
            task_id=request.task_id,
            claude_session_id=request.claude_session_id,
            process_id=request.process_id,
            process_start_fingerprint_sha256=(
                request.process_start_fingerprint_sha256
            ),
            repo_commit_sha=request.repo_commit_sha,
            repo_tree_sha=request.repo_tree_sha,
            lease_token=session.lease_token,
            admission_epoch=int(intent["next_admission_epoch"]),
            lease_expires_at=session.lease_expires_at,
            mandate_id=profile.mandate_id,
            mandate_artifact_sha256=profile.mandate_artifact_sha256,
            authority_approval_id=admission_id,
            authority_subject_ref="pending",
            authority_subject_sha256="0" * 64,
            mini_activation_receipt_sha256=(
                intent["predecessor_fence_receipt_sha256"]
            ),
            host_identity_sha256=request.host_identity_sha256,
            host_boot_id_sha256=request.host_boot_id_sha256,
            runtime_executable_path=request.runtime_executable_path,
            runtime_executable_sha256=request.runtime_executable_sha256,
            runtime_lock_path=request.runtime_lock_path,
            authority_mode=RECOVERY_AUTHORITY_MODE,
            authority_root_admission_id=root_id,
            prior_admission_id=prior_id,
            recovery_intent_id=intent_id,
            prior_rollback_receipt_sha256=(
                intent["prior_rollback_receipt_sha256"]
            ),
            restart_intent_sha256=intent["restart_intent_sha256"],
            deployment_id=profile.deployment_id,
            company_id=profile.company_id,
            role_type=profile.role_type,
            runtime_kind=profile.runtime_kind,
        )
        payload = company_role_activation_subject_payload(
            profile=profile, binding=provisional,
        )
        digest = _sha256(_canonical_json(payload))
        binding = replace(
            provisional,
            authority_subject_ref=(
                f"founder://company-role-activation/{profile.mandate_id}/"
                f"{profile.role_type}/{digest}"
            ),
            authority_subject_sha256=digest,
        )
        register_company_role_activation_subject(
            conn, profile=profile, binding=binding, mandate=mandate,
        )
        cursor.execute(
            "INSERT INTO kernel_company_scribe_recovery_intents ("
            "id,company_id,role_type,deployment_id,mandate_id,"
            "mandate_artifact_sha256,authority_root_admission_id,"
            "prior_admission_id,next_admission_epoch,worker_id,"
            "worker_session_id,package_id,agent_release_sha256,"
            "kernel_release_sha256,package_sha256,repo_commit_sha,"
            "repo_tree_sha,host_identity_sha256,"
            "mini_activation_receipt_sha256,"
            "prior_rollback_receipt_sha256,restart_intent_sha256,"
            "bootstrap_request_sha256,admission_id,authority_approval_id,"
            "authority_subject_ref,authority_subject_sha256,task_id,"
            "claude_session_id,process_id,process_start_fingerprint_sha256,"
            "host_boot_id_sha256,runtime_executable_path,"
            "runtime_executable_sha256,runtime_lock_path,"
            "lease_token_sha256,lease_expires_at) VALUES ("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                intent_id, profile.company_id, profile.role_type,
                profile.deployment_id, profile.mandate_id,
                profile.mandate_artifact_sha256, root_id, prior_id,
                binding.admission_epoch, profile.worker_id,
                binding.worker_session_id, package_id,
                binding.agent_release_sha256, binding.kernel_release_sha256,
                binding.package_sha256, binding.repo_commit_sha,
                binding.repo_tree_sha, binding.host_identity_sha256,
                binding.mini_activation_receipt_sha256,
                binding.prior_rollback_receipt_sha256,
                binding.restart_intent_sha256, request_sha256,
                binding.admission_id, binding.authority_approval_id,
                binding.authority_subject_ref,
                binding.authority_subject_sha256, binding.task_id,
                binding.claude_session_id, binding.process_id,
                binding.process_start_fingerprint_sha256,
                binding.host_boot_id_sha256,
                binding.runtime_executable_path,
                binding.runtime_executable_sha256,
                binding.runtime_lock_path,
                _sha256(binding.lease_token.encode("utf-8")),
                binding.lease_expires_at,
            ),
        )
    return binding


def write_company_role_candidate(
    path: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
    binding: CompanyRoleAdmissionBinding,
) -> None:
    _write_pointer(path, {
        "schema": profile.candidate_schema,
        "profile_sha256": profile.sha256,
        "binding": binding.to_mapping(),
    })


def load_company_role_candidate(
    path: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
) -> CompanyRoleAdmissionBinding:
    try:
        value = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role candidate is unreadable"
        ) from exc
    if (
        not isinstance(value, dict)
        or frozenset(value) != {"schema", "profile_sha256", "binding"}
        or value["schema"] != profile.candidate_schema
        or value["profile_sha256"] != profile.sha256
        or not isinstance(value["binding"], dict)
    ):
        raise CompanyScribeAdmissionError(
            "Company role candidate differs from its exact profile"
        )
    return CompanyRoleAdmissionBinding.from_mapping(
        value["binding"], profile=profile,
    )


def admit_company_role_candidate(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
    request: CompanyRoleBootstrapRequest,
    candidate: CompanyRoleAdmissionBinding,
    mandate,
    binding_path: Path,
) -> None:
    """Consume one exact prepared subject and publish its local binding."""
    assert_company_role_host_admitted(profile.role_type, profile.runtime_kind)
    request.verify_local_process()
    if (
        request.profile_sha256 != profile.sha256
        or request.task_id != candidate.task_id
        or request.claude_session_id != candidate.claude_session_id
        or request.process_id != candidate.process_id
        or request.process_start_fingerprint_sha256
        != candidate.process_start_fingerprint_sha256
        or request.repo_commit_sha != candidate.repo_commit_sha
        or request.repo_tree_sha != candidate.repo_tree_sha
        or request.host_identity_sha256 != candidate.host_identity_sha256
        or request.host_boot_id_sha256 != candidate.host_boot_id_sha256
        or request.runtime_executable_path
        != candidate.runtime_executable_path
        or request.runtime_executable_sha256
        != candidate.runtime_executable_sha256
        or request.runtime_lock_path != candidate.runtime_lock_path
    ):
        raise CompanyScribeAdmissionError(
            "Company role admission request differs from its candidate"
        )
    create_company_role_live_admission(
        conn, profile=profile, binding=candidate, mandate=mandate,
    )
    _write_pointer(binding_path, candidate.to_mapping())


def renew_company_role_admission(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
    binding: CompanyRoleAdmissionBinding,
    lease_seconds: int = 120,
) -> datetime:
    """Provisioner-side exact renewal for one role-scoped live admission."""
    if lease_seconds < 30 or lease_seconds > 300:
        raise CompanyScribeAdmissionError(
            "Company role renewal must be between 30 and 300 seconds"
        )
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (profile.admission_lock_key,),
        )
        clock = cursor.execute(
            "SELECT clock_timestamp() AS now,"
            "clock_timestamp()+(%s * interval '1 second') AS expires",
            (lease_seconds,),
        ).fetchone()
        expires = clock["expires"]
        session = cursor.execute(
            "UPDATE kernel_worker_sessions SET heartbeat_at=%s,"
            "lease_expires_at=%s WHERE id=%s AND tenant_id=%s "
            "AND worker_id=%s AND role_type=%s AND state='active' "
            "AND lease_token=%s RETURNING id",
            (
                clock["now"], expires, binding.worker_session_id,
                profile.company_id, profile.worker_id, profile.role_type,
                binding.lease_token,
            ),
        ).fetchone()
        worker = cursor.execute(
            "UPDATE kernel_runtime_workers SET lease_expires_at=%s "
            "WHERE id=%s AND tenant_id=%s AND role_type=%s "
            "AND status='active' AND lease_token=%s RETURNING id",
            (
                expires, profile.worker_id, profile.company_id,
                profile.role_type, binding.lease_token,
            ),
        ).fetchone()
        admission = cursor.execute(
            "UPDATE kernel_company_scribe_admissions SET heartbeat_at=%s,"
            "lease_expires_at=%s WHERE id=%s AND company_id=%s "
            "AND role_type=%s AND deployment_id=%s AND worker_id=%s "
            "AND worker_session_id=%s AND package_id=%s AND state='live' "
            "AND lease_token_sha256=%s AND authority_subject_sha256=%s "
            "RETURNING id",
            (
                clock["now"], expires, binding.admission_id,
                profile.company_id, profile.role_type, profile.deployment_id,
                profile.worker_id, binding.worker_session_id,
                binding.package_id,
                _sha256(binding.lease_token.encode("utf-8")),
                binding.authority_subject_sha256,
            ),
        ).fetchone()
        if session is None or worker is None or admission is None:
            raise CompanyScribeAdmissionError(
                "Company role admission renewal lost its exact worker, "
                "session, or row"
            )
    return expires


def _admission_row_lineage_broken(lease: Mapping[str, Any] | None) -> bool:
    """True when the admission/session/worker row is gone or not live.

    An expired clock on an otherwise live row is not lineage loss. The
    supervisor renews that row if the local process still holds the lock.
    """
    return (
        lease is None
        or lease["state"] != "live"
        or lease["session_state"] != "active"
        or lease["worker_status"] != "active"
        or lease["worker_lease_expires_at"] is None
    )


def supervise_company_role_once(
    conn: psycopg.Connection,
    *,
    profile: CompanyRoleAdmissionProfile,
    binding_path: Path,
    state_dir: Path,
    lease_seconds: int = 120,
    supervisor_interval_seconds: int = 30,
) -> dict[str, Any]:
    """Renew one live role without querying or mutating another role lane."""
    assert_supervisor_state_dir(state_dir)
    from fleet_kernel.company_role_runtime import (  # noqa: PLC0415
        load_company_role_binding,
    )

    database_authority_lost = False
    try:
        binding = load_company_role_binding(binding_path, profile=profile)
        process_state = _process_state(binding)
    except CompanyScribeAdmissionError:
        binding = _load_company_role_rollback_binding(
            binding_path, profile=profile,
        )
        process_state = "revoked"
        database_authority_lost = True
    if process_state == "exact_live":
        try:
            verify_runtime_lock_held(
                Path(binding.runtime_lock_path),
                expected_pid=binding.process_id,
            )
        except CompanyScribeAdmissionError as exc:
            if "not held by a live launcher" not in str(exc):
                raise
            process_state = "runtime_lock_unheld"
    if process_state == "exact_live":
        with conn.cursor(row_factory=dict_row) as cursor:
            lease = cursor.execute(
                "SELECT admission.state,admission.lease_expires_at,"
                "session.state AS session_state,"
                "session.lease_expires_at AS session_lease_expires_at,"
                "worker.status AS worker_status,"
                "worker.lease_expires_at AS worker_lease_expires_at "
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
                "AND admission.authority_subject_sha256=%s",
                (
                    binding.admission_id, profile.company_id,
                    profile.role_type, profile.deployment_id,
                    profile.worker_id, binding.worker_session_id,
                    binding.package_id, binding.admission_epoch,
                    binding.authority_root_admission_id,
                    binding.authority_subject_sha256,
                ),
            ).fetchone()
        if _admission_row_lineage_broken(lease):
            process_state = "lease_or_lineage_stale"
            database_authority_lost = True
    if process_state != "exact_live":
        rollback_path = state_dir / (
            f"rollback-{binding.admission_id}-"
            f"epoch-{binding.admission_epoch}.json"
        )
        rollback = rollback_live_company_role(
            conn,
            profile=profile,
            binding_path=binding_path,
            archive_dir=state_dir / "revoked-bindings",
            receipt_path=rollback_path,
            reason=(
                "supervisor fenced exact role admission "
                f"epoch {binding.admission_epoch}"
            ),
            allow_exact_live_process_if_authority_lost=(
                database_authority_lost
            ),
        )
        intent_path = state_dir / (
            f"restart-intent-epoch-{binding.admission_epoch + 1}.json"
        )
        intent = create_role_restart_intent(
            profile=profile,
            prior_binding=binding,
            rollback_receipt_path=rollback_path,
            intent_path=intent_path,
        )
        request = {
            "schema": profile.restart_request_schema,
            "profile_sha256": profile.sha256,
            "state": "awaiting-external-launch",
            "company_id": profile.company_id,
            "role_type": profile.role_type,
            "deployment_id": profile.deployment_id,
            "worker_id": str(profile.worker_id),
            "authority_root_admission_id": str(
                binding.authority_root_admission_id
            ),
            "prior_admission_id": str(binding.admission_id),
            "prior_admission_epoch": binding.admission_epoch,
            "next_admission_epoch": binding.admission_epoch + 1,
            "package_id": str(binding.package_id),
            "package_sha256": binding.package_sha256,
            "rollback_receipt_path": str(rollback_path),
            "rollback_receipt_sha256": rollback["receipt_sha256"],
            "restart_intent_path": str(intent_path),
            "restart_intent_sha256": intent["restart_intent_sha256"],
            "fenced_process_state": process_state,
            "launch_owner": "external-worker-agent",
        }
        _write_pointer(state_dir / "restart-request.json", request)
        heartbeat = {
            "schema": profile.heartbeat_schema,
            "profile_sha256": profile.sha256,
            "company_id": profile.company_id,
            "role_type": profile.role_type,
            "deployment_id": profile.deployment_id,
            "worker_id": str(profile.worker_id),
            "admission_id": str(binding.admission_id),
            "admission_epoch": binding.admission_epoch,
            "process_id": binding.process_id,
            "package_sha256": binding.package_sha256,
            "action": "revoked-awaiting-restart",
            "process_state": process_state,
            "next_admission_epoch": binding.admission_epoch + 1,
            "restart_intent_sha256": intent["restart_intent_sha256"],
            "recorded_at": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "supervisor_interval_seconds": supervisor_interval_seconds,
        }
        _write_pointer(state_dir / "heartbeat.json", heartbeat)
        return heartbeat
    expires = renew_company_role_admission(
        conn,
        profile=profile,
        binding=binding,
        lease_seconds=lease_seconds,
    )
    heartbeat = {
        "schema": profile.heartbeat_schema,
        "profile_sha256": profile.sha256,
        "company_id": profile.company_id,
        "role_type": profile.role_type,
        "deployment_id": profile.deployment_id,
        "worker_id": str(profile.worker_id),
        "admission_id": str(binding.admission_id),
        "admission_epoch": binding.admission_epoch,
        "process_id": binding.process_id,
        "package_sha256": binding.package_sha256,
        "lease_expires_at": expires.isoformat().replace("+00:00", "Z"),
        "action": "exact_live",
        "recorded_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "supervisor_interval_seconds": supervisor_interval_seconds,
    }
    _write_pointer(state_dir / "heartbeat.json", heartbeat)
    return heartbeat


def _binding_from_recovery_row(
    row: Mapping[str, Any],
) -> ScribeAdmissionBinding:
    return ScribeAdmissionBinding.from_mapping({
        "admission_id": row["admission_id"],
        "worker_id": row["worker_id"],
        "worker_session_id": row["worker_session_id"],
        "package_id": row["package_id"],
        "agent_release_sha256": row["agent_release_sha256"],
        "kernel_release_sha256": row["kernel_release_sha256"],
        "package_sha256": row["package_sha256"],
        "task_id": row["task_id"],
        "claude_session_id": row["claude_session_id"],
        "process_id": row["process_id"],
        "process_start_fingerprint_sha256": (
            row["process_start_fingerprint_sha256"]
        ),
        "repo_commit_sha": row["repo_commit_sha"],
        "repo_tree_sha": row["repo_tree_sha"],
        "lease_token": row["lease_token"],
        "admission_epoch": row["next_admission_epoch"],
        "lease_expires_at": row["lease_expires_at"],
        "mandate_id": row["mandate_id"],
        "mandate_artifact_sha256": row["mandate_artifact_sha256"],
        "authority_approval_id": row["authority_approval_id"],
        "authority_subject_ref": row["authority_subject_ref"],
        "authority_subject_sha256": row["authority_subject_sha256"],
        "mini_activation_receipt_sha256": (
            row["mini_activation_receipt_sha256"]
        ),
        "host_identity_sha256": row["host_identity_sha256"],
        "host_boot_id_sha256": row["host_boot_id_sha256"],
        "runtime_executable_path": row["runtime_executable_path"],
        "runtime_executable_sha256": row["runtime_executable_sha256"],
        "runtime_lock_path": row["runtime_lock_path"],
        "authority_mode": RECOVERY_AUTHORITY_MODE,
        "authority_root_admission_id": (
            row["authority_root_admission_id"]
        ),
        "prior_admission_id": row["prior_admission_id"],
        "recovery_intent_id": row["id"],
        "prior_rollback_receipt_sha256": (
            row["prior_rollback_receipt_sha256"]
        ),
        "restart_intent_sha256": row["restart_intent_sha256"],
    })


def _company_role_binding_from_recovery_row(
    row: Mapping[str, Any],
    *,
    profile: CompanyRoleAdmissionProfile,
) -> CompanyRoleAdmissionBinding:
    return CompanyRoleAdmissionBinding.from_mapping({
        "admission_id": row["admission_id"],
        "worker_id": row["worker_id"],
        "worker_session_id": row["worker_session_id"],
        "package_id": row["package_id"],
        "agent_release_sha256": row["agent_release_sha256"],
        "kernel_release_sha256": row["kernel_release_sha256"],
        "package_sha256": row["package_sha256"],
        "task_id": row["task_id"],
        "claude_session_id": row["claude_session_id"],
        "process_id": row["process_id"],
        "process_start_fingerprint_sha256": (
            row["process_start_fingerprint_sha256"]
        ),
        "repo_commit_sha": row["repo_commit_sha"],
        "repo_tree_sha": row["repo_tree_sha"],
        "lease_token": row["lease_token"],
        "admission_epoch": row["next_admission_epoch"],
        "lease_expires_at": row["lease_expires_at"],
        "mandate_id": row["mandate_id"],
        "mandate_artifact_sha256": row["mandate_artifact_sha256"],
        "authority_approval_id": row["authority_approval_id"],
        "authority_subject_ref": row["authority_subject_ref"],
        "authority_subject_sha256": row["authority_subject_sha256"],
        "mini_activation_receipt_sha256": (
            row["mini_activation_receipt_sha256"]
        ),
        "host_identity_sha256": row["host_identity_sha256"],
        "host_boot_id_sha256": row["host_boot_id_sha256"],
        "runtime_executable_path": row["runtime_executable_path"],
        "runtime_executable_sha256": row["runtime_executable_sha256"],
        "runtime_lock_path": row["runtime_lock_path"],
        "authority_mode": RECOVERY_AUTHORITY_MODE,
        "authority_root_admission_id": row["authority_root_admission_id"],
        "prior_admission_id": row["prior_admission_id"],
        "recovery_intent_id": row["id"],
        "prior_rollback_receipt_sha256": (
            row["prior_rollback_receipt_sha256"]
        ),
        "restart_intent_sha256": row["restart_intent_sha256"],
        "deployment_id": profile.deployment_id,
        "company_id": profile.company_id,
        "role_type": profile.role_type,
        "runtime_kind": profile.runtime_kind,
    }, profile=profile)


def _binding_from_admission_row(
    row: Mapping[str, Any],
) -> ScribeAdmissionBinding:
    payload = dict(row)
    payload["admission_id"] = payload.pop("id")
    payload["lease_token"] = row["worker_session_lease_token"]
    payload["lease_expires_at"] = row["authority_lease_expires_at"]
    return ScribeAdmissionBinding.from_mapping(payload)


@contextmanager
def _supervisor_singleton_lock(path: Path):
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
                "Scribe supervisor lock owner/type/mode is unsafe"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CompanyScribeAdmissionError(
                "another Company Scribe supervisor is active"
            ) from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def prepare_admission_candidate(
    conn: psycopg.Connection,
    *,
    request: ScribeBootstrapRequest,
    mandate: FounderActivationMandate,
    worker_id: uuid.UUID,
    lease_seconds: int = 120,
) -> ScribeAdmissionBinding:
    """Open a short worker session for one exact local Scribe process."""
    if lease_seconds < 30 or lease_seconds > 300:
        raise CompanyScribeAdmissionError(
            "Scribe candidate lease must be between 30 and 300 seconds"
        )
    request.verify_local_process()
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock("
            f"hashtextextended('{company_id()}:scribe-admission',0))"
        )
        mandate_row = cursor.execute(
            "SELECT status,artifact_sha256 FROM kernel_founder_activation_mandates "
            "WHERE mandate_id=%s",
            (mandate.mandate_id,),
        ).fetchone()
        worker = cursor.execute(
            "SELECT id,agent_release_sha256,status FROM kernel_runtime_workers "
            "WHERE id=%s AND tenant_id=%s AND fleet_type='company' "
            "AND role_type='scribe'",
            (worker_id, COMPANY_ID),
        ).fetchone()
        packages = cursor.execute(
            "SELECT id,kernel_release_sha256,package_sha256,"
            "source_commit_sha,source_tree_sha "
            "FROM kernel_company_scribe_packages "
            "WHERE company_id=%s AND deployment_id=%s AND state='installed'",
            (COMPANY_ID, DEPLOYMENT_ID),
        ).fetchall()
        live_count = cursor.execute(
            "SELECT count(*) AS count FROM kernel_company_scribe_admissions "
            "WHERE company_id=%s AND role_type='scribe' AND state='live'",
            (COMPANY_ID,),
        ).fetchone()["count"]
        previous_epoch = cursor.execute(
            "SELECT COALESCE(max(admission_epoch),0) AS epoch "
            "FROM kernel_company_scribe_admissions "
            "WHERE company_id=%s AND role_type='scribe'",
            (COMPANY_ID,),
        ).fetchone()["epoch"]
        stale_sessions = cursor.execute(
            "SELECT id,lease_token FROM kernel_worker_sessions "
            "WHERE tenant_id=%s AND worker_id=%s AND role_type='scribe' "
            "AND state='active' AND lease_expires_at<=now() FOR UPDATE",
            (COMPANY_ID, worker_id),
        ).fetchall()
        for stale in stale_sessions:
            cursor.execute(
                "UPDATE kernel_worker_sessions SET state='ended' "
                "WHERE id=%s AND state='active'",
                (stale["id"],),
            )
            cursor.execute(
                "UPDATE kernel_runtime_workers "
                "SET lease_token=NULL,lease_expires_at=NULL "
                "WHERE id=%s AND tenant_id=%s AND lease_token=%s",
                (worker_id, COMPANY_ID, stale["lease_token"]),
            )
        if (
            mandate_row is None
            or mandate_row["status"] != "active"
            or mandate_row["artifact_sha256"] != mandate.artifact_sha256
        ):
            raise CompanyScribeAdmissionError(
                "Scribe candidate requires the exact active founder mandate"
            )
        if worker is None or worker["status"] != "active":
            raise CompanyScribeAdmissionError(
                "Scribe candidate requires the active Company Scribe worker"
            )
        if len(packages) != 1:
            raise CompanyScribeAdmissionError(
                "Scribe candidate requires exactly one installed package"
            )
        package = packages[0]
        if (
            package["source_commit_sha"] != request.repo_commit_sha
            or package["source_tree_sha"] != request.repo_tree_sha
        ):
            raise CompanyScribeAdmissionError(
                "Scribe bootstrap repository differs from the installed package"
            )
        if live_count != 0:
            raise CompanyScribeAdmissionError(
                "Scribe candidate requires no existing live admission"
            )
        session = identity.open_worker_session(
            conn,
            tenant_id=COMPANY_ID,
            worker_id=worker_id,
            lease_duration=timedelta(seconds=lease_seconds),
        )
        admission_id = uuid.uuid4()
        binding = ScribeAdmissionBinding(
            admission_id=admission_id,
            worker_id=worker_id,
            worker_session_id=session.session_id,
            package_id=package["id"],
            agent_release_sha256=worker["agent_release_sha256"],
            kernel_release_sha256=package["kernel_release_sha256"],
            package_sha256=package["package_sha256"],
            task_id=request.task_id,
            claude_session_id=request.claude_session_id,
            process_id=request.process_id,
            process_start_fingerprint_sha256=(
                request.process_start_fingerprint_sha256
            ),
            repo_commit_sha=request.repo_commit_sha,
            repo_tree_sha=request.repo_tree_sha,
            lease_token=session.lease_token,
            admission_epoch=int(previous_epoch) + 1,
            lease_expires_at=session.lease_expires_at,
            mandate_id=mandate.mandate_id,
            mandate_artifact_sha256=mandate.artifact_sha256,
            # This field predates local Scribe-first admission.  Use the exact
            # admission identity for stable lineage; no model/M5 decision is
            # queried, created, or consumed.
            authority_approval_id=admission_id,
            authority_subject_ref="pending",
            authority_subject_sha256="0" * 64,
            # Compatibility sentinel for existing recovery/pointer schemas.
            # Initial admission never queries or requires a Mini receipt.
            mini_activation_receipt_sha256="0" * 64,
            host_identity_sha256=request.host_identity_sha256,
            host_boot_id_sha256=request.host_boot_id_sha256,
            runtime_executable_path=request.runtime_executable_path,
            runtime_executable_sha256=request.runtime_executable_sha256,
            runtime_lock_path=request.runtime_lock_path,
            authority_mode=INITIAL_AUTHORITY_MODE,
            authority_root_admission_id=admission_id,
        )
        subject = scribe_activation_subject(binding)
    return replace(
        binding,
        authority_subject_ref=subject.subject_ref,
        authority_subject_sha256=subject.sha256,
    )


def prepare_recovery_candidate(
    conn: psycopg.Connection,
    *,
    request: ScribeBootstrapRequest,
    mandate: FounderActivationMandate,
    restart_intent_path: Path,
    lease_seconds: int = 120,
) -> ScribeAdmissionBinding:
    """Prepare exactly one next epoch from the standing mandate recovery chain."""
    if lease_seconds < 30 or lease_seconds > 300:
        raise CompanyScribeAdmissionError(
            "Scribe recovery lease must be between 30 and 300 seconds"
        )
    request.verify_local_process()
    intent = load_restart_intent(restart_intent_path)
    if (
        intent["mandate_id"] != mandate.mandate_id
        or intent["mandate_artifact_sha256"] != mandate.artifact_sha256
        or intent["company_id"] != COMPANY_ID
        or intent["deployment_id"] != DEPLOYMENT_ID
        or intent["repo_commit_sha"] != request.repo_commit_sha
        or intent["repo_tree_sha"] != request.repo_tree_sha
        or intent["host_identity_sha256"] != request.host_identity_sha256
        or intent["runtime_executable_path"]
        != request.runtime_executable_path
        or intent["runtime_executable_sha256"]
        != request.runtime_executable_sha256
        or intent["runtime_lock_path"] != request.runtime_lock_path
    ):
        raise CompanyScribeAdmissionError(
            "Scribe recovery bootstrap changes release, host, or scope"
        )
    root_id = uuid.UUID(str(intent["authority_root_admission_id"]))
    prior_id = uuid.UUID(str(intent["prior_admission_id"]))
    worker_id = uuid.UUID(str(intent["worker_id"]))
    package_id = uuid.UUID(str(intent["package_id"]))
    authority_approval_id = uuid.UUID(str(intent["authority_approval_id"]))
    rollback_path = Path(str(intent["rollback_receipt_path"]))
    if not rollback_path.is_absolute():
        raise CompanyScribeAdmissionError(
            "Scribe recovery rollback receipt path is unsafe"
        )
    # The immutable file digest and the embedded rollback digest are checked by
    # load_restart_intent; the database repeats every lineage check below.
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock("
            f"hashtextextended('{company_id()}:scribe-admission',0))"
        )
        mandate_row = cursor.execute(
            "SELECT status,artifact_sha256 "
            "FROM kernel_founder_activation_mandates WHERE mandate_id=%s "
            "FOR SHARE",
            (mandate.mandate_id,),
        ).fetchone()
        root = cursor.execute(
            "SELECT * FROM kernel_company_scribe_admissions "
            "WHERE id=%s AND company_id=%s FOR SHARE",
            (root_id, COMPANY_ID),
        ).fetchone()
        prior = cursor.execute(
            "SELECT admission.*,session.state AS session_state "
            "FROM kernel_company_scribe_admissions admission "
            "JOIN kernel_worker_sessions session "
            "ON session.id=admission.worker_session_id "
            "WHERE admission.id=%s AND admission.company_id=%s FOR SHARE",
            (prior_id, COMPANY_ID),
        ).fetchone()
        package = cursor.execute(
            "SELECT * FROM kernel_company_scribe_packages "
            "WHERE id=%s AND company_id=%s FOR SHARE",
            (package_id, COMPANY_ID),
        ).fetchone()
        worker = cursor.execute(
            "SELECT * FROM kernel_runtime_workers "
            "WHERE id=%s AND tenant_id=%s FOR NO KEY UPDATE",
            (worker_id, COMPANY_ID),
        ).fetchone()
        live_count = cursor.execute(
            "SELECT count(*) AS count "
            "FROM kernel_company_scribe_admissions "
            "WHERE company_id=%s AND state='live'",
            (COMPANY_ID,),
        ).fetchone()["count"]
        final_receipt = cursor.execute(
            "SELECT receipt_payload "
            "FROM kernel_founder_activation_step_receipts "
            "WHERE mandate_id=%s AND activation_step='scribe-verification'",
            (mandate.mandate_id,),
        ).fetchone()
        open_intent = cursor.execute(
            "SELECT recovery.*,session.lease_token,"
            "session.state AS session_state,"
            "admission.state AS admission_state "
            "FROM kernel_company_scribe_recovery_intents recovery "
            "JOIN kernel_worker_sessions session "
            "ON session.id=recovery.worker_session_id "
            "LEFT JOIN kernel_company_scribe_admissions admission "
            "ON admission.id=recovery.admission_id "
            "AND admission.company_id=recovery.company_id "
            "WHERE recovery.company_id=%s "
            "AND recovery.prior_admission_id=%s "
            "AND recovery.state IN ('prepared','consumed') "
            "FOR UPDATE OF recovery,session",
            (COMPANY_ID, prior_id),
        ).fetchone()
        if open_intent is not None:
            request_sha = _sha256(_canonical_json(request.to_mapping()))
            if (
                open_intent["bootstrap_request_sha256"] == request_sha
                and open_intent["restart_intent_sha256"]
                == intent["restart_intent_sha256"]
                and open_intent["session_state"] == "active"
                and open_intent["lease_expires_at"]
                > datetime.now(open_intent["lease_expires_at"].tzinfo)
                and (
                    open_intent["state"] == "prepared"
                    or open_intent["admission_state"] == "live"
                )
            ):
                return _binding_from_recovery_row(open_intent)
            if (
                open_intent["state"] == "prepared"
                and open_intent["lease_expires_at"]
                <= datetime.now(open_intent["lease_expires_at"].tzinfo)
            ):
                cursor.execute(
                    "UPDATE kernel_worker_sessions SET state='ended' "
                    "WHERE id=%s AND state='active'",
                    (open_intent["worker_session_id"],),
                )
                cursor.execute(
                    "UPDATE kernel_runtime_workers "
                    "SET lease_token=NULL,lease_expires_at=NULL "
                    "WHERE id=%s AND tenant_id=%s AND lease_token=%s",
                    (
                        open_intent["worker_id"], COMPANY_ID,
                        open_intent["lease_token"],
                    ),
                )
                cursor.execute(
                    "UPDATE kernel_company_scribe_recovery_intents "
                    "SET state='abandoned' WHERE id=%s AND state='prepared'",
                    (open_intent["id"],),
                )
            else:
                raise CompanyScribeAdmissionError(
                    "Scribe recovery epoch has another live exact candidate"
                )
        static_exact = (
            mandate_row is not None
            and mandate_row["status"] in {"active", "completed"}
            and mandate_row["artifact_sha256"] == mandate.artifact_sha256
            and root is not None
            and root["authority_mode"] == INITIAL_AUTHORITY_MODE
            and root["authority_root_admission_id"] == root_id
            and root["authority_approval_id"] == authority_approval_id
            and prior is not None
            and prior["state"] == "revoked"
            and prior["session_state"] == "ended"
            and prior["authority_root_admission_id"] == root_id
            and prior["admission_epoch"] + 1
            == int(intent["next_admission_epoch"])
            and prior["worker_id"] == worker_id
            and prior["package_id"] == package_id
            and prior["mandate_id"] == mandate.mandate_id
            and prior["mandate_artifact_sha256"]
            == mandate.artifact_sha256
            and prior["agent_release_sha256"]
            == intent["agent_release_sha256"]
            and prior["kernel_release_sha256"]
            == intent["kernel_release_sha256"]
            and prior["package_sha256"] == intent["package_sha256"]
            and prior["repo_commit_sha"] == request.repo_commit_sha
            and prior["repo_tree_sha"] == request.repo_tree_sha
            and prior["host_identity_sha256"]
            == request.host_identity_sha256
            and package is not None
            and package["state"] == "installed"
            and package["kernel_release_sha256"]
            == intent["kernel_release_sha256"]
            and package["package_sha256"] == intent["package_sha256"]
            and package["source_commit_sha"] == request.repo_commit_sha
            and package["source_tree_sha"] == request.repo_tree_sha
            and worker is not None
            and worker["status"] == "active"
            and worker["role_type"] == "scribe"
            and worker["agent_release_sha256"]
            == intent["agent_release_sha256"]
            and live_count == 0
            and (
                mandate_row["status"] == "active"
                or (
                    final_receipt is not None
                    and final_receipt["receipt_payload"].get("status")
                    == "completed"
                    and final_receipt["receipt_payload"].get("singleton")
                    == "verified"
                    and final_receipt["receipt_payload"].get("persistence")
                    == "verified"
                    and final_receipt["receipt_payload"].get("restart")
                    == "passed"
                    and final_receipt["receipt_payload"].get("receipts")
                    == "verified"
                )
            )
        )
        if not static_exact:
            raise CompanyScribeAdmissionError(
                "Scribe recovery lineage is stale or no longer exact"
            )
        session = identity.open_worker_session(
            conn,
            tenant_id=COMPANY_ID,
            worker_id=worker_id,
            lease_duration=timedelta(seconds=lease_seconds),
        )
        intent_id = uuid.uuid4()
        admission_id = uuid.uuid4()
        provisional = ScribeAdmissionBinding(
            admission_id=admission_id,
            worker_id=worker_id,
            worker_session_id=session.session_id,
            package_id=package_id,
            agent_release_sha256=str(intent["agent_release_sha256"]),
            kernel_release_sha256=str(intent["kernel_release_sha256"]),
            package_sha256=str(intent["package_sha256"]),
            task_id=request.task_id,
            claude_session_id=request.claude_session_id,
            process_id=request.process_id,
            process_start_fingerprint_sha256=(
                request.process_start_fingerprint_sha256
            ),
            repo_commit_sha=request.repo_commit_sha,
            repo_tree_sha=request.repo_tree_sha,
            lease_token=session.lease_token,
            admission_epoch=int(intent["next_admission_epoch"]),
            lease_expires_at=session.lease_expires_at,
            mandate_id=mandate.mandate_id,
            mandate_artifact_sha256=mandate.artifact_sha256,
            authority_approval_id=authority_approval_id,
            authority_subject_ref="pending",
            authority_subject_sha256="0" * 64,
            mini_activation_receipt_sha256=str(
                intent["mini_activation_receipt_sha256"]
            ),
            host_identity_sha256=request.host_identity_sha256,
            host_boot_id_sha256=request.host_boot_id_sha256,
            runtime_executable_path=request.runtime_executable_path,
            runtime_executable_sha256=request.runtime_executable_sha256,
            runtime_lock_path=request.runtime_lock_path,
            authority_mode=RECOVERY_AUTHORITY_MODE,
            authority_root_admission_id=root_id,
            prior_admission_id=prior_id,
            recovery_intent_id=intent_id,
            prior_rollback_receipt_sha256=str(
                intent["prior_rollback_receipt_sha256"]
            ),
            restart_intent_sha256=str(intent["restart_intent_sha256"]),
        )
        subject = scribe_activation_subject(provisional)
        binding = replace(
            provisional,
            authority_subject_ref=subject.subject_ref,
            authority_subject_sha256=subject.sha256,
        )
        cursor.execute(
            "INSERT INTO kernel_company_scribe_recovery_intents "
            "(id,company_id,role_type,deployment_id,mandate_id,"
            "mandate_artifact_sha256,authority_root_admission_id,"
            "prior_admission_id,next_admission_epoch,worker_id,"
            "worker_session_id,package_id,agent_release_sha256,"
            "kernel_release_sha256,package_sha256,repo_commit_sha,"
            "repo_tree_sha,host_identity_sha256,"
            "mini_activation_receipt_sha256,"
            "prior_rollback_receipt_sha256,restart_intent_sha256,"
            "bootstrap_request_sha256,admission_id,"
            "authority_approval_id,authority_subject_ref,"
            "authority_subject_sha256,task_id,claude_session_id,"
            "process_id,process_start_fingerprint_sha256,"
            "host_boot_id_sha256,runtime_executable_path,"
            "runtime_executable_sha256,runtime_lock_path,"
            "lease_token_sha256,lease_expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                intent_id, COMPANY_ID, "scribe", DEPLOYMENT_ID,
                mandate.mandate_id,
                mandate.artifact_sha256, root_id, prior_id,
                binding.admission_epoch, worker_id,
                binding.worker_session_id, package_id,
                binding.agent_release_sha256,
                binding.kernel_release_sha256, binding.package_sha256,
                binding.repo_commit_sha, binding.repo_tree_sha,
                binding.host_identity_sha256,
                binding.mini_activation_receipt_sha256,
                binding.prior_rollback_receipt_sha256,
                binding.restart_intent_sha256,
                _sha256(_canonical_json(request.to_mapping())),
                binding.admission_id, binding.authority_approval_id,
                binding.authority_subject_ref,
                binding.authority_subject_sha256, binding.task_id,
                binding.claude_session_id, binding.process_id,
                binding.process_start_fingerprint_sha256,
                binding.host_boot_id_sha256,
                binding.runtime_executable_path,
                binding.runtime_executable_sha256,
                binding.runtime_lock_path,
                _sha256(binding.lease_token.encode("utf-8")),
                binding.lease_expires_at,
            ),
        )
    return binding


def write_candidate(path: Path, binding: ScribeAdmissionBinding) -> None:
    _write_pointer(path, {
        "schema": CANDIDATE_SCHEMA,
        "binding": binding.to_mapping(),
    })


def load_candidate(path: Path) -> ScribeAdmissionBinding:
    try:
        payload = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe admission candidate is unreadable"
        ) from exc
    if (
        not isinstance(payload, dict)
        or frozenset(payload) != {"schema", "binding"}
        or payload.get("schema") != CANDIDATE_SCHEMA
        or not isinstance(payload.get("binding"), dict)
    ):
        raise CompanyScribeAdmissionError(
            "Scribe admission candidate is malformed"
        )
    return ScribeAdmissionBinding.from_mapping(payload["binding"])


def admit_approved_candidate(
    conn: psycopg.Connection,
    *,
    request: ScribeBootstrapRequest,
    candidate: ScribeAdmissionBinding,
    mandate: FounderActivationMandate,
    binding_path: Path,
) -> None:
    """Consume exact initial authority or a prepared recovery intent."""
    request.verify_local_process()
    if (
        candidate.task_id != request.task_id
        or candidate.claude_session_id != request.claude_session_id
        or candidate.process_id != request.process_id
        or candidate.process_start_fingerprint_sha256
        != request.process_start_fingerprint_sha256
        or candidate.repo_commit_sha != request.repo_commit_sha
        or candidate.repo_tree_sha != request.repo_tree_sha
        or candidate.host_identity_sha256 != request.host_identity_sha256
        or candidate.host_boot_id_sha256 != request.host_boot_id_sha256
        or candidate.runtime_executable_path
        != request.runtime_executable_path
        or candidate.runtime_executable_sha256
        != request.runtime_executable_sha256
        or candidate.runtime_lock_path != request.runtime_lock_path
    ):
        raise CompanyScribeAdmissionError(
            "approved Scribe candidate differs from the bootstrap request"
        )
    create_live_admission(conn, binding=candidate, mandate=mandate)
    write_current_lease_pointer(
        binding_path.parent / "current-lease.json",
        binding=candidate,
        lease_expires_at=candidate.lease_expires_at,
    )
    _write_pointer(binding_path, candidate.to_mapping())


def _load_restart_request(
    path: Path, *, state_dir: Path,
) -> dict[str, Any] | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    try:
        value = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe restart request is unreadable"
        ) from exc
    if not isinstance(value, dict):
        raise CompanyScribeAdmissionError(
            "Scribe restart request is malformed"
        )
    common_keys = {
        "schema", "state", "company_id", "deployment_id",
        "authority_root_admission_id", "prior_admission_id",
        "prior_admission_epoch", "next_admission_epoch",
        "rollback_receipt_path", "rollback_receipt_sha256",
        "restart_intent_path", "restart_intent_sha256",
        "self_approval_permitted", "legacy_runner_recovery",
    }
    expected_keys = (
        common_keys
        if value.get("state") == "awaiting-external-launch"
        else common_keys | {"restart_receipt_path", "restart_receipt_sha256"}
    )
    if (
        frozenset(value) != expected_keys
        or value.get("schema") != RESTART_REQUEST_SCHEMA
        or value.get("state")
        not in {"awaiting-external-launch", "completed"}
        or value.get("company_id") != COMPANY_ID
        or value.get("deployment_id") != DEPLOYMENT_ID
        or value.get("self_approval_permitted") is not False
        or value.get("legacy_runner_recovery")
        != "outside-normal-activation"
        or int(value.get("next_admission_epoch", 0))
        != int(value.get("prior_admission_epoch", 0)) + 1
    ):
        raise CompanyScribeAdmissionError(
            "Scribe restart request is malformed"
        )
    state_root = state_dir.resolve(strict=True)
    for key in (
        "rollback_receipt_path", "restart_intent_path",
        *(
            ("restart_receipt_path",)
            if value["state"] == "completed"
            else ()
        ),
    ):
        artifact_path = Path(str(value[key]))
        if (
            not artifact_path.is_absolute()
            or artifact_path.parent.resolve(strict=True) != state_root
        ):
            raise CompanyScribeAdmissionError(
                "Scribe restart request escapes its private state directory"
            )
    intent = load_restart_intent(Path(str(value["restart_intent_path"])))
    if (
        intent["restart_intent_sha256"]
        != value["restart_intent_sha256"]
        or intent["prior_rollback_receipt_sha256"]
        != value["rollback_receipt_sha256"]
        or intent["authority_root_admission_id"]
        != value["authority_root_admission_id"]
        or intent["prior_admission_id"] != value["prior_admission_id"]
        or intent["prior_admission_epoch"]
        != value["prior_admission_epoch"]
        or intent["next_admission_epoch"]
        != value["next_admission_epoch"]
    ):
        raise CompanyScribeAdmissionError(
            "Scribe restart request lineage is not exact"
        )
    if value["state"] == "completed":
        receipt = load_restart_epoch_receipt(
            Path(str(value["restart_receipt_path"]))
        )
        if (
            receipt["receipt_sha256"]
            != value["restart_receipt_sha256"]
            or receipt["prior_rollback_receipt_sha256"]
            != value["rollback_receipt_sha256"]
            or receipt["authority_root_admission_id"]
            != value["authority_root_admission_id"]
            or receipt["prior_admission_id"]
            != value["prior_admission_id"]
            or receipt["prior_admission_epoch"]
            != value["prior_admission_epoch"]
            or receipt["restarted_admission_epoch"]
            != value["next_admission_epoch"]
            or receipt["restart_intent_sha256"]
            != value["restart_intent_sha256"]
        ):
            raise CompanyScribeAdmissionError(
                "Scribe completed restart request lost its exact receipt"
            )
    return value


def _resume_revoked_restart(
    conn: psycopg.Connection,
    *,
    binding_path: Path,
    state_dir: Path,
) -> dict[str, Any]:
    binding = _load_rollback_binding(binding_path)
    rollback_path = state_dir / (
        f"rollback-{binding.admission_id}-"
        f"epoch-{binding.admission_epoch}.json"
    )
    receipt = rollback_live_scribe(
        conn,
        binding_path=binding_path,
        archive_dir=state_dir / "revoked-bindings",
        receipt_path=rollback_path,
        reason=(
            "supervisor fenced exact admission "
            f"epoch {binding.admission_epoch}"
        ),
        quiesce_timeout_seconds=0,
    )
    restart_intent_path = state_dir / (
        f"restart-intent-epoch-{binding.admission_epoch + 1}.json"
    )
    restart_intent = create_restart_intent(
        prior_binding=binding,
        rollback_receipt_path=rollback_path,
        intent_path=restart_intent_path,
    )
    _crash_checkpoint("restart-intent-written")
    request = {
        "schema": RESTART_REQUEST_SCHEMA,
        "state": "awaiting-external-launch",
        "company_id": binding.company_id,
        "deployment_id": binding.deployment_id,
        "authority_root_admission_id": str(
            binding.authority_root_admission_id
        ),
        "prior_admission_id": str(binding.admission_id),
        "prior_admission_epoch": binding.admission_epoch,
        "next_admission_epoch": binding.admission_epoch + 1,
        "rollback_receipt_path": str(rollback_path),
        "rollback_receipt_sha256": receipt["receipt_sha256"],
        "restart_intent_path": str(restart_intent_path),
        "restart_intent_sha256": (
            restart_intent["restart_intent_sha256"]
        ),
        "self_approval_permitted": False,
        "legacy_runner_recovery": "outside-normal-activation",
    }
    _write_pointer(state_dir / "restart-request.json", request)
    _crash_checkpoint("restart-request-written")
    return request


def _load_database_live_binding(
    conn: psycopg.Connection,
) -> ScribeAdmissionBinding | None:
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT admission.*,"
            "session.lease_token AS worker_session_lease_token "
            "FROM kernel_company_scribe_admissions admission "
            "JOIN kernel_worker_sessions session "
            "ON session.id=admission.worker_session_id "
            "WHERE admission.company_id=%s AND admission.role_type='scribe' "
            "AND admission.state='live'",
            (COMPANY_ID,),
        ).fetchall()
    if len(rows) > 1:
        raise CompanyScribeAdmissionError(
            "database contains duplicate live Company Scribe admissions"
        )
    return None if not rows else _binding_from_admission_row(rows[0])


def _load_database_latest_revoked_binding(
    conn: psycopg.Connection,
) -> ScribeAdmissionBinding | None:
    """Recover exact local replay input after a DB-commit/local-write crash."""
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT admission.*,"
            "session.lease_token AS worker_session_lease_token "
            "FROM kernel_company_scribe_admissions admission "
            "JOIN kernel_worker_sessions session "
            "ON session.id=admission.worker_session_id "
            "WHERE admission.company_id=%s AND admission.role_type='scribe' "
            "AND admission.state='revoked' "
            "ORDER BY admission.admission_epoch DESC LIMIT 1",
            (COMPANY_ID,),
        ).fetchall()
    return None if not rows else _binding_from_admission_row(rows[0])


@contextmanager
def _supervisor_database_lock(conn: psycopg.Connection):
    """Hold the cross-host supervisor lock without wrapping local effects."""
    info = getattr(conn, "info", None)
    status = getattr(info, "transaction_status", None)
    if status is not None and status != psycopg.pq.TransactionStatus.IDLE:
        raise CompanyScribeAdmissionError(
            "Scribe supervisor requires an idle provisioner connection"
        )
    if hasattr(conn, "autocommit"):
        conn.autocommit = True
    conn.execute(
        "SELECT pg_advisory_lock("
        f"hashtextextended('{company_id()}:scribe-supervisor',0))"
    )
    try:
        yield
    finally:
        conn.execute(
            "SELECT pg_advisory_unlock("
            f"hashtextextended('{company_id()}:scribe-supervisor',0))"
        )


def supervisor_tick(
    conn: psycopg.Connection,
    *,
    binding_path: Path,
    state_dir: Path,
    lease_seconds: int = 120,
) -> dict[str, Any]:
    """Run one fail-closed supervision cycle."""
    assert_supervisor_state_dir(state_dir)
    with _supervisor_singleton_lock(state_dir / "supervisor.lock"):
        with _supervisor_database_lock(conn):
            return _supervisor_tick_locked(
                conn,
                binding_path=binding_path,
                state_dir=state_dir,
                lease_seconds=lease_seconds,
            )


def _supervisor_tick_locked(
    conn: psycopg.Connection,
    *,
    binding_path: Path,
    state_dir: Path,
    lease_seconds: int,
) -> dict[str, Any]:
    heartbeat_path = state_dir / "heartbeat.json"
    binding: ScribeAdmissionBinding | None = None
    raw: Any = None
    try:
        binding = load_binding(binding_path)
    except CompanyScribeAdmissionError as binding_error:
        try:
            raw = json.loads(_read_private_file(binding_path))
        except (
            CompanyScribeAdmissionError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            database_binding = _load_database_live_binding(conn)
            if database_binding is not None:
                _write_pointer(binding_path, database_binding.to_mapping())
                binding = database_binding
            else:
                revoked_binding = _load_database_latest_revoked_binding(conn)
                if revoked_binding is not None:
                    _write_pointer(
                        binding_path, revoked_binding.to_mapping(),
                    )
                    binding = revoked_binding
                else:
                    heartbeat = {
                        "schema": HEARTBEAT_SCHEMA,
                        "company_id": COMPANY_ID,
                        "deployment_id": DEPLOYMENT_ID,
                        "action": "awaiting-external-launch",
                        "process_state": "absent",
                    }
                    _write_pointer(heartbeat_path, heartbeat)
                    return heartbeat
        if (
            isinstance(raw, dict)
            and raw.get("schema") == REVOKED_BINDING_POINTER_SCHEMA
            and raw.get("state") == "revoked"
        ):
            request = _resume_revoked_restart(
                conn, binding_path=binding_path, state_dir=state_dir,
            )
            heartbeat = {
                "schema": HEARTBEAT_SCHEMA,
                "company_id": COMPANY_ID,
                "deployment_id": DEPLOYMENT_ID,
                "action": "revoked-awaiting-restart",
                "process_state": "revoked",
                "prior_admission_id": request["prior_admission_id"],
                "next_admission_epoch": request["next_admission_epoch"],
                "restart_intent_sha256": (
                    request["restart_intent_sha256"]
                ),
            }
            _write_pointer(heartbeat_path, heartbeat)
            return heartbeat
        if binding is None:
            _write_pointer(heartbeat_path, {
                "schema": HEARTBEAT_SCHEMA,
                "company_id": COMPANY_ID,
                "deployment_id": DEPLOYMENT_ID,
                "action": "held-malformed-binding",
                "process_state": "unknown",
            })
            raise binding_error
    assert binding is not None
    process_state = _process_state(binding)
    with conn.cursor(row_factory=dict_row) as cursor:
        lease = cursor.execute(
            "SELECT admission.state,admission.lease_expires_at,"
            "session.state AS session_state,"
            "session.lease_expires_at AS session_lease_expires_at,"
            "worker.status AS worker_status,"
            "worker.lease_expires_at AS worker_lease_expires_at "
            "FROM kernel_company_scribe_admissions admission "
            "JOIN kernel_worker_sessions session "
            "ON session.id=admission.worker_session_id "
            "JOIN kernel_runtime_workers worker "
            "ON worker.id=admission.worker_id "
            "AND worker.tenant_id=admission.company_id "
            "WHERE admission.id=%s AND admission.company_id=%s",
            (binding.admission_id, binding.company_id),
        ).fetchone()
    if _admission_row_lineage_broken(lease):
        process_state = "lease_or_lineage_stale"
    restart_request_path = state_dir / "restart-request.json"
    if process_state != "exact_live":
        request = _resume_revoked_restart(
            conn, binding_path=binding_path, state_dir=state_dir,
        )
        _write_pointer(heartbeat_path, {
            "schema": HEARTBEAT_SCHEMA,
            "company_id": binding.company_id,
            "deployment_id": binding.deployment_id,
            "action": "revoked-awaiting-restart",
            "process_state": process_state,
            "prior_admission_id": str(binding.admission_id),
            "next_admission_epoch": binding.admission_epoch + 1,
            "restart_intent_sha256": (
                request["restart_intent_sha256"]
            ),
        })
        return {"action": "revoked-awaiting-restart", **request}

    verify_runtime_lock_held(
        Path(binding.runtime_lock_path), expected_pid=binding.process_id,
    )
    expires = renew_live_admission(
        conn, binding=binding, lease_seconds=lease_seconds,
    )
    write_current_lease_pointer(
        binding_path.parent / "current-lease.json",
        binding=binding,
        lease_expires_at=expires,
    )
    restart_request = _load_restart_request(
        restart_request_path, state_dir=state_dir,
    )
    restart_receipt: dict[str, Any] | None = None
    if (
        restart_request is not None
        and restart_request.get("state")
        == "awaiting-external-launch"
        and restart_request.get("next_admission_epoch")
        == binding.admission_epoch
    ):
        restart_receipt_path = state_dir / (
            f"restart-epoch-{binding.admission_epoch}.json"
        )
        restart_receipt = record_restart_epoch_receipt(
            conn,
            prior_rollback_receipt_path=Path(
                str(restart_request["rollback_receipt_path"])
            ),
            restarted_binding=binding,
            receipt_path=restart_receipt_path,
        )
        _write_pointer(restart_request_path, {
            **restart_request,
            "state": "completed",
            "restart_receipt_path": str(restart_receipt_path),
            "restart_receipt_sha256": restart_receipt["receipt_sha256"],
        })
    heartbeat = {
        "schema": HEARTBEAT_SCHEMA,
        "company_id": binding.company_id,
        "deployment_id": binding.deployment_id,
        "admission_id": str(binding.admission_id),
        "admission_epoch": binding.admission_epoch,
        "worker_session_id": str(binding.worker_session_id),
        "process_id": binding.process_id,
        "process_start_fingerprint_sha256": (
            binding.process_start_fingerprint_sha256
        ),
        "lease_expires_at": expires.isoformat().replace("+00:00", "Z"),
        "process_state": "exact_live",
        "restart_receipt_sha256": (
            None
            if restart_receipt is None
            else restart_receipt["receipt_sha256"]
        ),
    }
    _write_pointer(heartbeat_path, heartbeat)
    return {"action": "renewed", **heartbeat}


def assert_supervisor_state_dir(path: Path) -> None:
    """Static caller guard: state must be private and outside a Git worktree."""
    if not path.is_absolute():
        raise CompanyScribeAdmissionError(
            "Scribe supervisor state directory must be absolute"
        )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise CompanyScribeAdmissionError(
            "Scribe supervisor state directory must be private"
        )
    probe = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        check=False, capture_output=True, text=True,
    )
    if probe.returncode == 0 and probe.stdout.strip() == "true":
        raise CompanyScribeAdmissionError(
            "Scribe supervisor state directory cannot be inside a Git worktree"
        )
