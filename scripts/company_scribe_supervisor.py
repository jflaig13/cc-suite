#!/usr/bin/env python3
"""Operational entrypoint for held install and one admitted Company Scribe.

Initial admission uses the exact local process, installed package, active
Company Fleet Scribe worker, and durable worker session. Recovery consumes
only the database-validated immutable rollback/restart lineage.
"""
from __future__ import annotations
from fleet_kernel.configuration import company_id, scribe_deployment_id, authority_issuer, deployment_binding, trusted_deployment_environment
from fleet_kernel.identity import company_role_spec

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fleet_kernel.company_scribe_admission import (
    CompanyScribeAdmissionError,
    ScribeVerificationEvidence,
    assert_company_role_host_admitted,
    record_company_role_host_refusal,
    _read_private_file,
    _write_pointer,
    build_complete_held_package,
    build_company_role_package,
    company_role_activation_subject_payload,
    install_held_package,
    install_company_role_package,
    load_binding,
    record_restart_epoch_receipt,
    register_installed_package,
    register_company_role_package,
    rollback_live_scribe,
    scribe_activation_subject,
    scribe_release_files,
    scribe_verification_subject,
    validate_database_route,
    verify_complete_release_package,
    verify_company_role_package,
    verify_offline_package_runtime,
)
from fleet_kernel.company_scribe_supervisor import (
    HEARTBEAT_SCHEMA,
    ScribeBootstrapRequest,
    CompanyRoleBootstrapRequest,
    admit_company_role_candidate,
    company_role_supervisor_database_lock,
    admit_approved_candidate,
    assert_supervisor_state_dir,
    load_candidate,
    load_company_role_profile_artifact,
    load_company_role_candidate,
    prepare_company_role_initial_candidate,
    prepare_company_role_recovery_candidate,
    prepare_admission_candidate,
    prepare_recovery_candidate,
    require_exact_company_role_target,
    supervisor_tick,
    supervise_company_role_once,
    write_company_role_candidate,
    write_candidate,
)
from fleet_kernel.founder_authority import (
    FounderActivationMandate,
    Phase4FounderActivationMandate,
    load_company_role_mandate,
)
from fleet_kernel.m5.approval_api import record_activation_step_receipt
from fleet_kernel.release import (
    runtime_release_manifest_bytes,
)


def _path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise argparse.ArgumentTypeError("path must be absolute without '..'")
    return path


def _workspace_ref(value: str) -> str:
    if (
        not value.startswith("refs/heads/")
        or value == "refs/heads/"
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise argparse.ArgumentTypeError(
            "workspace ref must name one exact local branch"
        )
    return value


def _database_route() -> str:
    return validate_database_route(
        os.environ.get("COMPANY_SCRIBE_PROVISIONER_DATABASE_URL", "")
    )


def _company_role_database_route() -> str:
    return validate_database_route(
        os.environ.get("COMPANY_ROLE_PROVISIONER_DATABASE_URL", "")
    )


def _connect() -> psycopg.Connection:
    return psycopg.connect(_database_route())


def _connect_company_role() -> psycopg.Connection:
    return psycopg.connect(_company_role_database_route())


def _has_role_profile(args: argparse.Namespace) -> bool:
    artifact = getattr(args, "profile_artifact", None)
    digest = getattr(args, "profile_sha256", None)
    if (artifact is None) != (digest is None):
        raise CompanyScribeAdmissionError(
            "Company role commands require both profile artifact and digest"
        )
    return artifact is not None


def _output(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _load_evidence(path: Path) -> ScribeVerificationEvidence:
    try:
        value = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Scribe verification evidence is unreadable"
        ) from exc
    if not isinstance(value, dict) or frozenset(value) != {
        "approval_id", "admission_receipt_sha256",
        "singleton_probe_sha256", "persistence_probe_sha256",
        "restart_probe_sha256", "receipts_manifest_sha256",
    }:
        raise CompanyScribeAdmissionError(
            "Scribe verification evidence is not closed"
        )
    return ScribeVerificationEvidence(
        approval_id=uuid.UUID(str(value["approval_id"])),
        admission_receipt_sha256=str(value["admission_receipt_sha256"]),
        singleton_probe_sha256=str(value["singleton_probe_sha256"]),
        persistence_probe_sha256=str(value["persistence_probe_sha256"]),
        restart_probe_sha256=str(value["restart_probe_sha256"]),
        receipts_manifest_sha256=str(value["receipts_manifest_sha256"]),
    )


def _load_role_profile(args: argparse.Namespace):
    return load_company_role_profile_artifact(
        args.profile_artifact,
        expected_sha256=args.profile_sha256,
    )


def _cmd_inspect_role_profile(args: argparse.Namespace) -> None:
    profile = _load_role_profile(args)
    _output({
        "state": "profile-valid-held",
        "profile_sha256": profile.sha256,
        "profile": profile.canonical_payload,
        "activation_authorized": False,
    })


def _cmd_role_target_status(args: argparse.Namespace) -> None:
    """Read one exact target; the CLI exposes no free-form identity flags."""
    profile = _load_role_profile(args)
    with _connect() as conn:
        with company_role_supervisor_database_lock(conn, profile=profile):
            target = require_exact_company_role_target(conn, profile=profile)
    _output({
        "schema": profile.heartbeat_schema,
        "state": "eligible-held",
        "profile_sha256": profile.sha256,
        "company_id": target["company_id"],
        "role_type": target["role_type"],
        "deployment_id": target["deployment_id"],
        "worker_id": str(target["worker_id"]),
        "activation_authorized": False,
    })


def _resolve_worker_id(
    conn: psycopg.Connection, explicit: str | None,
) -> uuid.UUID:
    rows = conn.execute(
        "SELECT id FROM kernel_runtime_workers "
        f"WHERE tenant_id='{company_id()}' AND fleet_type='company' "
        "AND role_type='scribe' AND status='active' ORDER BY id"
    ).fetchall()
    if len(rows) != 1:
        raise CompanyScribeAdmissionError(
            "activation requires exactly one active Company Scribe worker"
        )
    worker_id = rows[0][0]
    if explicit is not None and uuid.UUID(explicit) != worker_id:
        raise CompanyScribeAdmissionError(
            "requested Scribe worker differs from the one active worker"
        )
    return worker_id


def _cmd_build(args: argparse.Namespace) -> None:
    kernel_release_sha256 = hashlib.sha256(
        runtime_release_manifest_bytes()
    ).hexdigest()
    if _has_role_profile(args):
        profile = _load_role_profile(args)
        package = build_company_role_package(
            profile=profile,
            repo=args.repo,
            output_dir=args.output,
            kernel_release_sha256=kernel_release_sha256,
        )
        manifest = verify_company_role_package(package, profile=profile)
        _output({
            "state": "held",
            "profile_sha256": profile.sha256,
            "role_type": profile.role_type,
            "deployment_id": profile.deployment_id,
            "package_path": str(package),
            "package_id": manifest["package_id"],
            "package_sha256": manifest["package_sha256"],
            "kernel_release_sha256": kernel_release_sha256,
        })
        return
    package = build_complete_held_package(
        repo=args.repo,
        output_dir=args.output,
        kernel_release_sha256=kernel_release_sha256,
        files=scribe_release_files(),
    )
    manifest = verify_offline_package_runtime(package)
    _output({
        "state": "held",
        "package_path": str(package),
        "package_id": manifest["package_id"],
        "package_sha256": manifest["package_sha256"],
        "kernel_release_sha256": kernel_release_sha256,
    })


def _cmd_install(args: argparse.Namespace) -> None:
    if _has_role_profile(args):
        profile = _load_role_profile(args)
        manifest = install_company_role_package(
            args.package, profile=profile, install_root=args.install_root,
        )
        installed = (
            args.install_root / "packages" / str(manifest["package_sha256"])
        )
        with _connect_company_role() as conn:
            registered = register_company_role_package(
                conn, profile=profile, package_dir=installed,
            )
        _output({
            "state": registered["database_state"],
            "profile_sha256": profile.sha256,
            "role_type": profile.role_type,
            "installed_root": registered["installed_root"],
            "package_id": registered["package_id"],
            "package_sha256": registered["package_sha256"],
        })
        return
    manifest = install_held_package(
        args.package, install_root=args.install_root,
    )
    installed = (
        args.install_root / "packages" / str(manifest["package_sha256"])
    )
    with _connect() as conn:
        registered = register_installed_package(
            conn, package_dir=installed,
        )
    _output({
        "state": registered["database_state"],
        "installed_root": registered["installed_root"],
        "package_id": registered["package_id"],
        "package_sha256": registered["package_sha256"],
    })


def _cmd_launch_runtime(args: argparse.Namespace) -> None:
    """Exec the interactive runtime with one closed private state layout."""
    if _has_role_profile(args):
        # Only the company-role runtime consumes --output-root; the Scribe
        # branch below has no output tree, so the flag is enforced here
        # rather than at the parser.
        if args.output_root is None:
            raise CompanyScribeAdmissionError(
                "--output-root is required to launch a company role runtime"
            )
        profile = _load_role_profile(args)
        # Host-family chokepoint (Claude-door retirement 2026-08-15): refuse
        # and record before any env export or exec for a seat whose official
        # model family does not match this Claude-code runtime.
        try:
            assert_company_role_host_admitted(
                profile.role_type, profile.runtime_kind,
            )
        except CompanyScribeAdmissionError:
            record_company_role_host_refusal(
                args.state_dir,
                role_type=profile.role_type,
                attempted_runtime_kind=profile.runtime_kind,
                surface="launch-runtime",
            )
            raise
        manifest = verify_company_role_package(args.package, profile=profile)
        payload_root = (args.package / "payload").resolve(strict=True)
        runtime_entrypoint = (
            payload_root / str(manifest["runtime_entrypoint"])
        ).resolve(strict=True)
        # Never resolve() the interpreter: a venv python is a symlink, and
        # following it escapes the venv (losing site-packages). Require the
        # path to exist and be absolute, but exec it as given.
        python = args.python_executable
        if not python.is_absolute() or not python.exists():
            raise CompanyScribeAdmissionError(
                "runtime python executable must be an existing absolute path"
            )
        claude = args.claude_executable.resolve(strict=True)
        mcp_config = args.mcp_config.resolve(strict=True)
        environment = dict(os.environ)
        environment.pop("COMPANY_ROLE_PROVISIONER_DATABASE_URL", None)
        environment.update({
            "MISE_COMPANY_ROLE_PROFILE_ARTIFACT": str(
                args.profile_artifact.resolve(strict=True)
            ),
            "MISE_COMPANY_ROLE_PROFILE_SHA256": profile.sha256,
            "MISE_COMPANY_ROLE_RELEASE_PACKAGE": str(
                args.package.resolve(strict=True)
            ),
            "MISE_COMPANY_ROLE_STATE_DIR": str(args.state_dir),
            "MISE_COMPANY_ROLE_OUTPUT_ROOT": str(args.output_root),
            "MISE_COMPANY_ROLE_WORKSPACE_REF": args.workspace_ref,
            "MISE_COMPANY_ROLE_MCP_CONFIG": str(mcp_config),
            "MISE_COMPANY_ROLE_CLAUDE_EXECUTABLE": str(claude),
            "MISE_COMPANY_ROLE_TASK_ID": (
                args.task_id
                or f"company-{profile.role_type}-{uuid.uuid4()}"
            ),
            "MISE_COMPANY_ROLE_SESSION_ID": (
                args.session_id or str(uuid.uuid4())
            ),
            "MISE_COMPANY_ROLE_ADMISSION_WAIT_SECONDS": str(
                args.wait_seconds
            ),
            "CCSUITE_REPO_ROOT": str(args.repo.resolve(strict=True)),
        })
        os.chdir(payload_root)
        os.execve(
            python,
            # -B: the byte-verified payload must never gain bytecode; -I
            # isolated mode ignores PYTHONDONTWRITEBYTECODE, so the flag
            # must ride argv on every interpreter spawn in this chain.
            [str(python), "-I", "-B", str(runtime_entrypoint)],
            environment,
        )
        raise AssertionError("Company role runtime exec unexpectedly returned")
    validate_database_route(
        os.environ.get("COMPANY_SCRIBE_DATABASE_URL", "")
    )
    verify_complete_release_package(args.package)
    assert_supervisor_state_dir(args.state_dir)
    channel_shared_dir = args.channel_shared_dir.resolve(strict=True)
    payload_root = (args.package / "payload").resolve(strict=True)
    launcher = (
        payload_root / "scripts" / "launch_company_scribe.sh"
    ).resolve(strict=True)
    environment = dict(os.environ)
    environment.pop(
        "COMPANY_SCRIBE_PROVISIONER_DATABASE_URL", None,
    )
    environment.update({
        "MISE_SCRIBE_RELEASE_PACKAGE": str(
            args.package.resolve(strict=True)
        ),
        "MISE_SCRIBE_ADMISSION_BINDING": str(
            args.state_dir / "ACTIVE.json"
        ),
        "MISE_SCRIBE_BOOTSTRAP_REQUEST": str(
            args.state_dir / "bootstrap-request.json"
        ),
        "MISE_SCRIBE_RUNTIME_LOCK": str(
            args.state_dir / "runtime.lock"
        ),
        "MISE_SCRIBE_TASK_ID": (
            args.task_id or f"company-scribe-{uuid.uuid4()}"
        ),
        "MISE_SCRIBE_CLAUDE_SESSION_ID": (
            args.session_id or str(uuid.uuid4())
        ),
        "MISE_SCRIBE_ADMISSION_WAIT_SECONDS": str(args.wait_seconds),
        "CCSUITE_REPO_ROOT": str(args.repo.resolve(strict=True)),
        "MISE_SCRIBE_CHANNEL_SHARED_DIR": str(channel_shared_dir),
        "MISE_SCRIBE_WORKSPACE_REF": args.workspace_ref,
        "MISE_SCRIBE_MODEL": os.environ.get("MISE_SCRIBE_MODEL", company_role_spec("scribe").default_model),
        "MISE_SCRIBE_EFFORT": os.environ.get("MISE_SCRIBE_EFFORT", "high"),
    })
    os.chdir(payload_root)
    os.execve(
        "/bin/bash",
        ["/bin/bash", str(launcher)],
        environment,
    )


def _cmd_prepare_initial(args: argparse.Namespace) -> None:
    if _has_role_profile(args):
        profile = _load_role_profile(args)
        request = CompanyRoleBootstrapRequest.from_path(
            args.request,
            profile=profile,
            profile_sha256=profile.sha256,
        )
        mandate = load_company_role_mandate(
            args.mandate, role_type=profile.role_type,
        )
        with _connect_company_role() as conn:
            candidate = prepare_company_role_initial_candidate(
                conn,
                profile=profile,
                request=request,
                mandate=mandate,
                predecessor_fence_receipt_sha256=(
                    args.predecessor_fence_receipt_sha256
                ),
                lease_seconds=args.lease_seconds,
            )
        write_company_role_candidate(
            args.candidate, profile=profile, binding=candidate,
        )
        subject = company_role_activation_subject_payload(
            profile=profile, binding=candidate,
        )
        _write_pointer(args.subject, subject)
        _output({
            "state": "ready-for-company-role-admission",
            "profile_sha256": profile.sha256,
            "role_type": profile.role_type,
            "candidate_path": str(args.candidate),
            "subject_path": str(args.subject),
            "authority_subject_ref": candidate.authority_subject_ref,
            "authority_subject_sha256": candidate.authority_subject_sha256,
        })
        return
    request = ScribeBootstrapRequest.from_path(args.request)
    mandate = FounderActivationMandate.from_path(args.mandate)
    with _connect() as conn:
        candidate = prepare_admission_candidate(
            conn,
            request=request,
            mandate=mandate,
            worker_id=_resolve_worker_id(conn, args.worker_id),
            lease_seconds=args.lease_seconds,
        )
    write_candidate(args.candidate, candidate)
    subject = scribe_activation_subject(candidate)
    _write_pointer(args.subject, subject.canonical_payload)
    _output({
        "state": "ready-for-local-admission",
        "candidate_path": str(args.candidate),
        "subject_path": str(args.subject),
        "authority_subject_ref": subject.subject_ref,
        "authority_subject_sha256": subject.sha256,
    })


def _cmd_prepare_recovery(args: argparse.Namespace) -> None:
    if _has_role_profile(args):
        profile = _load_role_profile(args)
        request = CompanyRoleBootstrapRequest.from_path(
            args.request,
            profile=profile,
            profile_sha256=profile.sha256,
        )
        mandate = load_company_role_mandate(
            args.mandate, role_type=profile.role_type,
        )
        with _connect_company_role() as conn:
            candidate = prepare_company_role_recovery_candidate(
                conn,
                profile=profile,
                request=request,
                mandate=mandate,
                restart_intent_path=args.restart_intent,
                lease_seconds=args.lease_seconds,
            )
        write_company_role_candidate(
            args.candidate, profile=profile, binding=candidate,
        )
        _output({
            "state": "prepared-company-role-recovery",
            "profile_sha256": profile.sha256,
            "role_type": profile.role_type,
            "candidate_path": str(args.candidate),
            "admission_id": str(candidate.admission_id),
            "admission_epoch": candidate.admission_epoch,
            "authority_root_admission_id": str(
                candidate.authority_root_admission_id
            ),
            "self_approval_permitted": False,
        })
        return
    request = ScribeBootstrapRequest.from_path(args.request)
    mandate = FounderActivationMandate.from_path(args.mandate)
    with _connect() as conn:
        candidate = prepare_recovery_candidate(
            conn,
            request=request,
            mandate=mandate,
            restart_intent_path=args.restart_intent,
            lease_seconds=args.lease_seconds,
        )
    write_candidate(args.candidate, candidate)
    _output({
        "state": "prepared-standing-lineage-recovery",
        "candidate_path": str(args.candidate),
        "admission_id": str(candidate.admission_id),
        "admission_epoch": candidate.admission_epoch,
        "self_approval_permitted": False,
    })


def _cmd_admit(args: argparse.Namespace) -> None:
    if _has_role_profile(args):
        profile = _load_role_profile(args)
        request = CompanyRoleBootstrapRequest.from_path(
            args.request,
            profile=profile,
            profile_sha256=profile.sha256,
        )
        candidate = load_company_role_candidate(
            args.candidate, profile=profile,
        )
        mandate = load_company_role_mandate(
            args.mandate, role_type=profile.role_type,
        )
        with _connect_company_role() as conn:
            admit_company_role_candidate(
                conn,
                profile=profile,
                request=request,
                candidate=candidate,
                mandate=mandate,
                binding_path=args.binding,
            )
        _output({
            "state": "live",
            "profile_sha256": profile.sha256,
            "role_type": profile.role_type,
            "binding_path": str(args.binding),
            "admission_id": str(candidate.admission_id),
            "admission_epoch": candidate.admission_epoch,
            "authority_mode": candidate.authority_mode,
        })
        return
    request = ScribeBootstrapRequest.from_path(args.request)
    candidate = load_candidate(args.candidate)
    mandate = FounderActivationMandate.from_path(args.mandate)
    with _connect() as conn:
        admit_approved_candidate(
            conn,
            request=request,
            candidate=candidate,
            mandate=mandate,
            binding_path=args.binding,
        )
    _output({
        "state": "live",
        "binding_path": str(args.binding),
        "admission_id": str(candidate.admission_id),
        "admission_epoch": candidate.admission_epoch,
        "authority_mode": candidate.authority_mode,
    })


def _cmd_supervise(args: argparse.Namespace) -> None:
    if _has_role_profile(args):
        profile = _load_role_profile(args)
        assert_supervisor_state_dir(args.state_dir)
        while True:
            try:
                with _connect_company_role() as conn:
                    with company_role_supervisor_database_lock(
                        conn, profile=profile,
                    ):
                        result = supervise_company_role_once(
                            conn,
                            profile=profile,
                            binding_path=args.binding,
                            state_dir=args.state_dir,
                            lease_seconds=args.lease_seconds,
                            supervisor_interval_seconds=args.interval_seconds,
                        )
            except Exception as exc:
                held = {
                    "schema": profile.heartbeat_schema,
                    "profile_sha256": profile.sha256,
                    "company_id": profile.company_id,
                    "role_type": profile.role_type,
                    "deployment_id": profile.deployment_id,
                    "action": "held-supervisor-error",
                    "error_type": type(exc).__name__,
                }
                _write_pointer(args.state_dir / "heartbeat.json", held)
                if args.once:
                    raise
                time.sleep(args.interval_seconds)
                continue
            _output(result)
            if args.once:
                return
            time.sleep(args.interval_seconds)
    assert_supervisor_state_dir(args.state_dir)
    while True:
        try:
            with _connect() as conn:
                result = supervisor_tick(
                    conn,
                    binding_path=args.binding,
                    state_dir=args.state_dir,
                    lease_seconds=args.lease_seconds,
                )
        except Exception as exc:
            held = {
                "schema": HEARTBEAT_SCHEMA,
                "company_id": company_id(),
                "deployment_id": scribe_deployment_id(),
                "action": "held-supervisor-error",
                "error_type": type(exc).__name__,
            }
            _write_pointer(args.state_dir / "heartbeat.json", held)
            if args.once:
                raise
            time.sleep(args.interval_seconds)
            continue
        _output(result)
        if args.once:
            return
        time.sleep(args.interval_seconds)


def _cmd_rollback(args: argparse.Namespace) -> None:
    with _connect() as conn:
        receipt = rollback_live_scribe(
            conn,
            binding_path=args.binding,
            archive_dir=args.archive_dir,
            receipt_path=args.receipt,
            reason=args.reason,
            quiesce_timeout_seconds=args.timeout_seconds,
        )
    _output({
        "state": receipt["rollback_state"],
        "receipt_path": str(args.receipt),
        "receipt_sha256": receipt["receipt_sha256"],
    })


def _cmd_restart_receipt(args: argparse.Namespace) -> None:
    binding = load_binding(args.binding)
    with _connect() as conn:
        receipt = record_restart_epoch_receipt(
            conn,
            prior_rollback_receipt_path=args.rollback_receipt,
            restarted_binding=binding,
            receipt_path=args.receipt,
        )
    _output({
        "state": receipt["restart_state"],
        "receipt_path": str(args.receipt),
        "receipt_sha256": receipt["receipt_sha256"],
    })


def _cmd_verification_subject(args: argparse.Namespace) -> None:
    binding = load_binding(args.binding)
    evidence = _load_evidence(args.evidence)
    subject = scribe_verification_subject(binding, evidence)
    _write_pointer(args.subject, subject.canonical_payload)
    _output({
        "state": "awaiting-exact-m5-decision",
        "subject_path": str(args.subject),
        "authority_subject_ref": subject.subject_ref,
        "authority_subject_sha256": subject.sha256,
    })


def _cmd_complete_verification(args: argparse.Namespace) -> None:
    binding = load_binding(args.binding)
    evidence = _load_evidence(args.evidence)
    mandate = FounderActivationMandate.from_path(args.mandate)
    subject = scribe_verification_subject(binding, evidence)
    payload = {
        "status": "completed",
        "activation_step": "scribe-verification",
        "authority_subject_sha256": subject.sha256,
        "singleton": "verified",
        "persistence": "verified",
        "restart": "passed",
        "receipts": "verified",
    }
    with _connect() as conn:
        record_activation_step_receipt(
            conn,
            mandate=mandate,
            subject=subject,
            receipt_sha256=args.receipt_sha256,
            receipt_payload=payload,
        )
    _output({
        "state": "activation-completed",
        "authority_subject_sha256": subject.sha256,
        "receipt_sha256": args.receipt_sha256,
    })


def _atomic_plist(path: Path, payload: dict[str, Any], *, replace_existing: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.resolve() != (
        Path.home() / "Library" / "LaunchAgents"
    ).resolve():
        raise CompanyScribeAdmissionError(
            "Scribe LaunchAgent must be installed in the user LaunchAgents directory"
        )
    data = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if replace_existing:
            os.replace(temporary, path)
        else:
            # link is an atomic no-clobber publish on the same filesystem.
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise CompanyScribeAdmissionError("LaunchAgent already exists; explicit --replace-existing is required") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _cmd_install_supervisor(args: argparse.Namespace) -> None:
    route = _database_route()
    assert_supervisor_state_dir(args.state_dir)
    manifest = verify_complete_release_package(args.package)
    payload_root = (args.package / "payload").resolve(strict=True)
    python = (
        payload_root / str(manifest["runtime"]["python_executable"])
    ).resolve(strict=True)
    supervisor_entrypoint = (
        payload_root / "scripts" / "company_scribe_supervisor.py"
    ).resolve(strict=True)
    plist = {
        "Label": args.label,
        "ProgramArguments": [
            str(python),
            "-I",
            str(supervisor_entrypoint),
            "supervise",
            "--binding", str(args.binding),
            "--state-dir", str(args.state_dir),
            "--lease-seconds", str(args.lease_seconds),
            "--interval-seconds", str(args.interval_seconds),
        ],
        "EnvironmentVariables": {
            **trusted_deployment_environment(),
            "COMPANY_SCRIBE_PROVISIONER_DATABASE_URL": route,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "PYTHONPATH": str(payload_root),
            "PYTHONHOME": str(payload_root / "runtime" / "python"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "KeepAlive": True,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "ThrottleInterval": max(10, args.interval_seconds),
        "StandardOutPath": str(args.state_dir / "supervisor.stdout.log"),
        "StandardErrorPath": str(args.state_dir / "supervisor.stderr.log"),
    }
    _atomic_plist(args.plist, plist, replace_existing=args.replace_existing)
    if args.bootstrap:
        result = subprocess.run(
            [
                "launchctl", "bootstrap", f"gui/{os.getuid()}",
                str(args.plist),
            ],
            check=False, capture_output=True, text=True,
        )
        if result.returncode:
            raise CompanyScribeAdmissionError(
                "launchctl refused the Company Scribe supervisor"
            )
    _output({
        "state": "installed" if args.bootstrap else "rendered-held",
        "launch_agent_path": str(args.plist),
        "label": args.label,
        "package_sha256": manifest["package_sha256"],
    })


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    commands = result.add_subparsers(dest="command", required=True)

    def add_role_profile(command: argparse.ArgumentParser) -> None:
        command.add_argument("--profile-artifact", type=_path)
        command.add_argument("--profile-sha256")

    inspect_profile = commands.add_parser("inspect-role-profile")
    inspect_profile.add_argument(
        "--profile-artifact", type=_path, required=True,
    )
    inspect_profile.add_argument("--profile-sha256", required=True)
    inspect_profile.set_defaults(run=_cmd_inspect_role_profile)

    role_status = commands.add_parser("role-target-status")
    role_status.add_argument(
        "--profile-artifact", type=_path, required=True,
    )
    role_status.add_argument("--profile-sha256", required=True)
    role_status.set_defaults(run=_cmd_role_target_status)

    build = commands.add_parser("build-package")
    add_role_profile(build)
    build.add_argument("--repo", type=_path, required=True)
    build.add_argument("--output", type=_path, required=True)
    build.set_defaults(run=_cmd_build)

    install = commands.add_parser("install-package")
    add_role_profile(install)
    install.add_argument("--package", type=_path, required=True)
    install.add_argument("--install-root", type=_path, required=True)
    install.set_defaults(run=_cmd_install)

    runtime = commands.add_parser("launch-runtime")
    add_role_profile(runtime)
    runtime.add_argument("--package", type=_path, required=True)
    runtime.add_argument("--repo", type=_path, required=True)
    runtime.add_argument("--state-dir", type=_path, required=True)
    runtime.add_argument("--output-root", type=_path)
    runtime.add_argument("--channel-shared-dir", type=_path, required=True)
    runtime.add_argument("--workspace-ref", type=_workspace_ref, required=True)
    runtime.add_argument("--task-id")
    runtime.add_argument("--session-id")
    runtime.add_argument("--wait-seconds", type=int, default=300)
    runtime.add_argument("--python-executable", type=_path)
    runtime.add_argument("--claude-executable", type=_path)
    runtime.add_argument("--mcp-config", type=_path)
    runtime.set_defaults(run=_cmd_launch_runtime)

    initial = commands.add_parser("prepare-initial")
    add_role_profile(initial)
    initial.add_argument("--request", type=_path, required=True)
    initial.add_argument("--mandate", type=_path, required=True)
    initial.add_argument("--candidate", type=_path, required=True)
    initial.add_argument("--subject", type=_path, required=True)
    initial.add_argument("--worker-id")
    initial.add_argument("--lease-seconds", type=int, default=120)
    initial.add_argument("--predecessor-fence-receipt-sha256")
    initial.set_defaults(run=_cmd_prepare_initial)

    recovery = commands.add_parser("prepare-recovery")
    add_role_profile(recovery)
    recovery.add_argument("--request", type=_path, required=True)
    recovery.add_argument("--mandate", type=_path, required=True)
    recovery.add_argument("--restart-intent", type=_path, required=True)
    recovery.add_argument("--candidate", type=_path, required=True)
    recovery.add_argument("--lease-seconds", type=int, default=120)
    recovery.set_defaults(run=_cmd_prepare_recovery)

    admit = commands.add_parser("admit")
    add_role_profile(admit)
    admit.add_argument("--request", type=_path, required=True)
    admit.add_argument("--candidate", type=_path, required=True)
    admit.add_argument("--mandate", type=_path, required=True)
    admit.add_argument("--binding", type=_path, required=True)
    admit.set_defaults(run=_cmd_admit)

    supervise = commands.add_parser("supervise")
    add_role_profile(supervise)
    supervise.add_argument("--binding", type=_path, required=True)
    supervise.add_argument("--state-dir", type=_path, required=True)
    supervise.add_argument("--lease-seconds", type=int, default=120)
    supervise.add_argument("--interval-seconds", type=int, default=30)
    supervise.add_argument("--once", action="store_true")
    supervise.set_defaults(run=_cmd_supervise)

    rollback = commands.add_parser("rollback")
    rollback.add_argument("--binding", type=_path, required=True)
    rollback.add_argument("--archive-dir", type=_path, required=True)
    rollback.add_argument("--receipt", type=_path, required=True)
    rollback.add_argument("--reason", required=True)
    rollback.add_argument("--timeout-seconds", type=float, default=10)
    rollback.set_defaults(run=_cmd_rollback)

    restart = commands.add_parser("restart-receipt")
    restart.add_argument("--binding", type=_path, required=True)
    restart.add_argument("--rollback-receipt", type=_path, required=True)
    restart.add_argument("--receipt", type=_path, required=True)
    restart.set_defaults(run=_cmd_restart_receipt)

    verification = commands.add_parser("verification-subject")
    verification.add_argument("--binding", type=_path, required=True)
    verification.add_argument("--evidence", type=_path, required=True)
    verification.add_argument("--subject", type=_path, required=True)
    verification.set_defaults(run=_cmd_verification_subject)

    complete = commands.add_parser("complete-verification")
    complete.add_argument("--binding", type=_path, required=True)
    complete.add_argument("--evidence", type=_path, required=True)
    complete.add_argument("--mandate", type=_path, required=True)
    complete.add_argument("--receipt-sha256", required=True)
    complete.set_defaults(run=_cmd_complete_verification)

    launch = commands.add_parser("install-supervisor")
    launch.add_argument("--package", type=_path, required=True)
    launch.add_argument("--binding", type=_path, required=True)
    launch.add_argument("--state-dir", type=_path, required=True)
    launch.add_argument("--plist", type=_path, required=True)
    launch.add_argument("--label", required=True)
    launch.add_argument("--replace-existing", action="store_true",
                        help="Explicitly replace an existing target LaunchAgent")
    launch.add_argument("--lease-seconds", type=int, default=120)
    launch.add_argument("--interval-seconds", type=int, default=30)
    launch.add_argument("--bootstrap", action="store_true")
    launch.set_defaults(run=_cmd_install_supervisor)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.run(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        CompanyScribeAdmissionError,
        psycopg.Error,
        OSError,
        ValueError,
    ) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(78) from exc
