"""Fail-closed runtime bootstrap for one admitted non-Scribe Company role.

The immutable profile selects identity.  The installed package selects code.
The bootstrap request pins the exact Claude CLI process and the runtime waits
for the provisioner-created admission before executing that CLI.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg

from fleet_kernel.company_scribe_admission import (
    CompanyRoleAdmissionBinding,
    CompanyRoleAdmissionProfile,
    CompanyScribeAdmissionError,
    assert_company_role_host_admitted,
    record_company_role_host_refusal,
    _canonical_json,
    _git,
    _read_private_file,
    _secure_directory,
    _sha256,
    _write_pointer,
    bind_company_role_transaction,
    boot_identity_sha256,
    executable_sha256,
    host_identity_sha256,
    process_start_fingerprint,
    runtime_lock,
    validate_company_role_authority_subject,
    validate_database_route,
    verify_company_role_package,
    verify_runtime_lock_held,
)
from fleet_kernel.company_scribe_supervisor import (
    load_company_role_profile_artifact,
)


WORKSPACE_BINDING_SCHEMA = "mise.company-role-workspace-binding.v1"
_WORKSPACE_BINDING_LINEAGE_FIELDS = (
    "schema",
    "profile_sha256",
    "role_type",
    "deployment_id",
    "workspace_path",
    "workspace_ref",
    "output_root",
    "source_commit_sha",
    "source_tree_sha",
    "package_sha256",
    "mcp_config_path",
    "mcp_config_sha256",
    "channel_server",
)
RUNTIME_RECEIPT_SCHEMA = "mise.company-role-claude-runtime.v1"
_FORBIDDEN_DATABASE_NAMES = frozenset({
    "COMPANY_SCRIBE_DATABASE_URL",
    "COMPANY_SCRIBE_PROVISIONER_DATABASE_URL",
    "FLEET_KERNEL_DATABASE_URL",
    "FLEET_KERNEL_DDL_DATABASE_URL",
})
_PASSTHROUGH_ENV = frozenset({
    'CC_SUITE_COMPANY_ID', 'CC_SUITE_SCRIBE_DEPLOYMENT_ID', 'CC_SUITE_PACKAGE_ROOT', 'CC_SUITE_WORKSPACE', 'CC_SUITE_STATE_ROOT', 'CC_SUITE_CHANNEL_SHARED_DIR', 'CC_SUITE_PYTHON', 'CC_SUITE_BUN', 'CC_SUITE_HOST_AUTHORIZER', 'CC_SUITE_CLAUDE', 'CC_SUITE_PERMISSION_MODE',
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TERM",
    "COLORTERM", "LANG", "CLAUDE_CONFIG_DIR", "XPC_FLAGS",
    "XPC_SERVICE_NAME", "__CFBundleIdentifier",
})


def _absolute_path(value: str, *, field: str) -> Path:
    path = Path(value)
    if not value or not path.is_absolute() or ".." in path.parts:
        raise CompanyScribeAdmissionError(
            f"{field} must be one absolute traversal-free path"
        )
    return path


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CompanyScribeAdmissionError(f"{name} is required")
    return value


def _load_optional_restart(
    path: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
) -> Mapping[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role restart request is unreadable"
        ) from exc
    required = {
        "schema", "profile_sha256", "state", "role_type",
        "deployment_id",
    }
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or value["schema"] != profile.restart_request_schema
        or value["profile_sha256"] != profile.sha256
        or value["state"] != "awaiting-external-launch"
        or value["role_type"] != profile.role_type
        or value["deployment_id"] != profile.deployment_id
    ):
        raise CompanyScribeAdmissionError(
            "Company role restart request belongs to another role or state"
        )
    return value


def assert_initial_state_empty(
    state_dir: Path,
    *,
    restart: Mapping[str, Any] | None,
) -> None:
    """Initial admission starts with no inherited authority or workspace state.

    The external supervisor is allowed to start first.  Its four role-neutral
    coordination artifacts do not carry admission or workspace authority.
    """
    if restart is not None:
        return
    try:
        entries = tuple(state_dir.iterdir())
    except OSError as exc:
        raise CompanyScribeAdmissionError(
            "Company role state directory is unreadable"
        ) from exc
    supervisor_artifacts = {
        "supervisor.lock",
        "heartbeat.json",
        "supervisor.stdout.log",
        "supervisor.stderr.log",
    }
    unexpected = tuple(
        entry for entry in entries if entry.name not in supervisor_artifacts
    )
    if unexpected:
        raise CompanyScribeAdmissionError(
            "initial Company role admission requires no inherited "
            "non-supervisor state"
        )


def workspace_binding(
    *,
    profile: CompanyRoleAdmissionProfile,
    repo: Path,
    workspace_ref: str,
    package_manifest: Mapping[str, Any],
    role_mcp_config: Path,
    output_root: Path,
) -> dict[str, Any]:
    if (
        not workspace_ref.startswith("refs/heads/")
        or workspace_ref == "refs/heads/"
    ):
        raise CompanyScribeAdmissionError(
            "Company role workspace ref must name an exact branch"
        )
    if _git(repo, "symbolic-ref", "HEAD") != workspace_ref:
        raise CompanyScribeAdmissionError(
            "Company role workspace is on another symbolic ref"
        )
    config = role_mcp_config.resolve(strict=True)
    if config != (repo / f"channels/configs/role-{profile.role_type}.mcp.json").resolve(
        strict=True
    ):
        raise CompanyScribeAdmissionError(
            "Company role MCP config is not its own role-scoped config"
        )
    basis = {
        "schema": WORKSPACE_BINDING_SCHEMA,
        "profile_sha256": profile.sha256,
        "role_type": profile.role_type,
        "deployment_id": profile.deployment_id,
        "workspace_path": str(repo),
        "workspace_ref": workspace_ref,
        "output_root": str(output_root),
        "source_commit_sha": package_manifest["source_commit_sha"],
        "source_tree_sha": package_manifest["source_tree_sha"],
        "package_sha256": package_manifest["package_sha256"],
        "mcp_config_path": str(config),
        "mcp_config_sha256": _sha256(config.read_bytes()),
        "channel_server": f"{profile.role_type}-channel-push",
    }
    return {
        **basis,
        "workspace_binding_sha256": _sha256(_canonical_json(basis)),
    }


def _verify_recovery_workspace_binding(
    observed: Any,
    *,
    expected: Mapping[str, Any],
) -> None:
    """Compare the closed authority lineage and name every changed field.

    The payload digest is redundant evidence derived from the lineage basis,
    not a second authority input.  Older/evolved persisted payloads may omit it
    or carry non-authority diagnostic metadata without changing the lineage.
    When the digest is present, it must still authenticate the stored basis.
    """
    if not isinstance(observed, Mapping):
        raise CompanyScribeAdmissionError(
            "Company role recovery workspace binding is not an object"
        )
    changed = tuple(
        field
        for field in _WORKSPACE_BINDING_LINEAGE_FIELDS
        if field not in observed or observed[field] != expected[field]
    )
    if changed:
        raise CompanyScribeAdmissionError(
            "Company role recovery workspace binding changed fields: "
            + ",".join(changed)
        )
    observed_digest = observed.get("workspace_binding_sha256")
    if observed_digest is not None:
        observed_basis = {
            field: observed[field]
            for field in _WORKSPACE_BINDING_LINEAGE_FIELDS
        }
        if observed_digest != _sha256(_canonical_json(observed_basis)):
            raise CompanyScribeAdmissionError(
                "Company role recovery workspace binding has invalid field: "
                "workspace_binding_sha256"
            )


def verify_or_create_workspace_binding(
    *,
    profile: CompanyRoleAdmissionProfile,
    repo: Path,
    state_dir: Path,
    workspace_ref: str,
    package_manifest: Mapping[str, Any],
    role_mcp_config: Path,
    output_root: Path,
    restart: Mapping[str, Any] | None,
) -> str:
    if not output_root.is_absolute() or output_root != state_dir.parent / "outbox":
        raise CompanyScribeAdmissionError(
            "Company role output root must be its governed sibling outbox"
        )
    _secure_directory(output_root, create=True)
    expected = workspace_binding(
        profile=profile,
        repo=repo,
        workspace_ref=workspace_ref,
        package_manifest=package_manifest,
        role_mcp_config=role_mcp_config,
        output_root=output_root,
    )
    path = state_dir / "workspace-binding.json"
    if (
        _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
        or _git(repo, "rev-parse", "HEAD^{commit}")
        != package_manifest["source_commit_sha"]
        or _git(repo, "rev-parse", "HEAD^{tree}")
        != package_manifest["source_tree_sha"]
    ):
        raise CompanyScribeAdmissionError(
            "Company role package-source checkout must remain clean and pinned"
        )
    if restart is None:
        if path.exists():
            raise CompanyScribeAdmissionError(
                "initial Company role workspace binding already exists"
            )
        _write_pointer(path, expected)
        return "initial"
    try:
        observed = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role workspace binding is unreadable"
        ) from exc
    _verify_recovery_workspace_binding(observed, expected=expected)
    return "recovery"


def bootstrap_request(
    *,
    profile: CompanyRoleAdmissionProfile,
    package_manifest: Mapping[str, Any],
    executable: Path,
    lock_path: Path,
    task_id: str,
    session_id: str,
) -> dict[str, Any]:
    return {
        "schema": profile.bootstrap_schema,
        "profile_sha256": profile.sha256,
        "company_id": profile.company_id,
        "role_type": profile.role_type,
        "deployment_id": profile.deployment_id,
        "worker_id": str(profile.worker_id),
        "host_kind": profile.host_kind,
        "runtime_kind": profile.runtime_kind,
        "task_id": task_id,
        "claude_session_id": session_id,
        "process_id": os.getpid(),
        "process_start_fingerprint_sha256": process_start_fingerprint(os.getpid()),
        "repo_commit_sha": package_manifest["source_commit_sha"],
        "repo_tree_sha": package_manifest["source_tree_sha"],
        "host_identity_sha256": host_identity_sha256(),
        "host_boot_id_sha256": boot_identity_sha256(),
        "runtime_executable_path": str(executable),
        "runtime_executable_sha256": executable_sha256(executable),
        "runtime_lock_path": str(lock_path),
    }


def load_company_role_binding(
    path: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
) -> CompanyRoleAdmissionBinding:
    try:
        value = json.loads(_read_private_file(path))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyScribeAdmissionError(
            "Company role admission binding is unreadable"
        ) from exc
    if not isinstance(value, dict):
        raise CompanyScribeAdmissionError(
            "Company role admission binding is not an object"
        )
    return CompanyRoleAdmissionBinding.from_mapping(value, profile=profile)


def wait_for_binding(
    path: Path,
    *,
    profile: CompanyRoleAdmissionProfile,
    task_id: str,
    session_id: str,
    executable: Path,
    timeout_seconds: int,
) -> CompanyRoleAdmissionBinding:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            binding = load_company_role_binding(path, profile=profile)
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
            "Company role binding belongs to a prior launch epoch"
        )
        time.sleep(0.2)
    raise CompanyScribeAdmissionError(
        "timed out waiting for the exact Company role admission"
    ) from last_error


def verify_local_company_role_binding(
    *,
    profile: CompanyRoleAdmissionProfile,
    binding: CompanyRoleAdmissionBinding,
    package_root: Path,
    expected_pid: int,
) -> None:
    manifest = verify_company_role_package(package_root, profile=profile)
    if (
        binding.process_id != expected_pid
        or binding.process_start_fingerprint_sha256
        != process_start_fingerprint(expected_pid)
        or binding.repo_commit_sha != manifest["source_commit_sha"]
        or binding.repo_tree_sha != manifest["source_tree_sha"]
        or binding.kernel_release_sha256 != manifest["kernel_release_sha256"]
        or binding.package_sha256 != manifest["package_sha256"]
        or str(binding.package_id) != manifest["package_id"]
        or binding.host_identity_sha256 != host_identity_sha256()
        or binding.host_boot_id_sha256 != boot_identity_sha256()
    ):
        raise CompanyScribeAdmissionError(
            "Company role local process, package, or host binding changed"
        )
    executable = Path(binding.runtime_executable_path)
    if (
        str(executable.resolve(strict=True)) != binding.runtime_executable_path
        or executable_sha256(executable) != binding.runtime_executable_sha256
    ):
        raise CompanyScribeAdmissionError(
            "admitted Company role Claude executable changed"
        )
    verify_runtime_lock_held(
        Path(binding.runtime_lock_path), expected_pid=expected_pid,
    )
    validate_company_role_authority_subject(profile=profile, binding=binding)


def runtime_child_environment(
    source: Mapping[str, str],
    *,
    profile: CompanyRoleAdmissionProfile,
    repo: Path,
    output_root: Path,
    package_payload: Path,
) -> dict[str, str]:
    forbidden = sorted(
        name for name in source
        if name in _FORBIDDEN_DATABASE_NAMES
        or name == "DATABASE_URL"
        or (name.endswith("_DATABASE_URL") and name != "COMPANY_ROLE_DATABASE_URL")
        or name.startswith("MISE_SCRIBE")
        or name.startswith("COMPANY_SCRIBE")
    )
    if forbidden:
        raise CompanyScribeAdmissionError(
            "Company role runtime inherited another authority or database route: "
            + ",".join(forbidden)
        )
    result = {key: value for key, value in source.items() if key in _PASSTHROUGH_ENV}
    result.update({
        "MISE_ROLE": profile.role_type,
        "AGENT_ROLE": profile.role_type,
        "CCSUITE_ROLE": profile.role_type,
        "CCSUITE_ROLE_ID": profile.deployment_id,
        "CCSUITE_REPO_ROOT": str(repo),
        "CC_SUITE_PACKAGE_ROOT": str(package_payload),
        "CC_SUITE_WORKSPACE": str(repo),
        "MISE_COMPANY_ROLE_OUTPUT_ROOT": str(output_root),
        "MISE_COMPANY_ROLE_ADMITTED": "1",
        "MISE_COMPANY_ROLE_PROFILE_SHA256": profile.sha256,
    })
    return result


def main() -> int:
    profile_artifact = _absolute_path(
        _required_env("MISE_COMPANY_ROLE_PROFILE_ARTIFACT"),
        field="profile artifact",
    )
    profile = load_company_role_profile_artifact(
        profile_artifact,
        expected_sha256=_required_env("MISE_COMPANY_ROLE_PROFILE_SHA256"),
    )
    if profile.role_type == "scribe":
        raise CompanyScribeAdmissionError(
            "generic Company role runtime cannot assume Scribe identity"
        )
    # Host-family chokepoint (Claude-door retirement 2026-08-15): a seat whose
    # official model family does not match this runtime's host family refuses
    # before any package, workspace, or executable work — and records it.
    try:
        assert_company_role_host_admitted(
            profile.role_type, profile.runtime_kind,
        )
    except CompanyScribeAdmissionError:
        state_env = os.environ.get(
            "MISE_COMPANY_ROLE_STATE_DIR", "",
        ).strip()
        record_company_role_host_refusal(
            Path(state_env) if state_env else None,
            role_type=profile.role_type,
            attempted_runtime_kind=profile.runtime_kind,
            surface="company_role_runtime",
        )
        raise
    package_root = _absolute_path(
        _required_env("MISE_COMPANY_ROLE_RELEASE_PACKAGE"),
        field="release package",
    )
    manifest = verify_company_role_package(package_root, profile=profile)
    repo = _absolute_path(
        _required_env("CCSUITE_REPO_ROOT"), field="workspace",
    ).resolve(strict=True)
    state_dir = _absolute_path(
        _required_env("MISE_COMPANY_ROLE_STATE_DIR"), field="state directory",
    )
    _secure_directory(state_dir, create=True)
    info = state_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise CompanyScribeAdmissionError(
            "Company role state directory must be owner-only"
        )
    if state_dir == repo or repo in state_dir.parents:
        raise CompanyScribeAdmissionError(
            "Company role state directory cannot be inside its workspace"
        )
    output_root = _absolute_path(
        _required_env("MISE_COMPANY_ROLE_OUTPUT_ROOT"),
        field="role output root",
    )
    expected_output_root = state_dir.parent / "outbox"
    if output_root != expected_output_root:
        raise CompanyScribeAdmissionError(
            "Company role output root must be its governed sibling outbox"
        )
    _secure_directory(output_root, create=True)
    binding_path = state_dir / "ACTIVE.json"
    request_path = state_dir / "bootstrap-request.json"
    lock_path = state_dir / "runtime.lock"
    restart = _load_optional_restart(
        state_dir / "restart-request.json", profile=profile,
    )
    assert_initial_state_empty(state_dir, restart=restart)
    workspace_ref = _required_env("MISE_COMPANY_ROLE_WORKSPACE_REF")
    role_mcp_config = _absolute_path(
        _required_env("MISE_COMPANY_ROLE_MCP_CONFIG"), field="role MCP config",
    )
    verify_or_create_workspace_binding(
        profile=profile,
        repo=repo,
        state_dir=state_dir,
        workspace_ref=workspace_ref,
        package_manifest=manifest,
        role_mcp_config=role_mcp_config,
        output_root=output_root,
        restart=restart,
    )
    executable = _absolute_path(
        _required_env("MISE_COMPANY_ROLE_CLAUDE_EXECUTABLE"),
        field="Claude executable",
    ).resolve(strict=True)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise CompanyScribeAdmissionError("Claude CLI is unavailable")
    task_id = _required_env("MISE_COMPANY_ROLE_TASK_ID")
    session_id = _required_env("MISE_COMPANY_ROLE_SESSION_ID")
    try:
        wait_seconds = int(os.environ.get(
            "MISE_COMPANY_ROLE_ADMISSION_WAIT_SECONDS", "300",
        ))
    except ValueError as exc:
        raise CompanyScribeAdmissionError(
            "Company role admission wait is not an integer"
        ) from exc
    if wait_seconds < 1 or wait_seconds > 900:
        raise CompanyScribeAdmissionError(
            "Company role admission wait must be between 1 and 900 seconds"
        )
    database_url = validate_database_route(
        _required_env("COMPANY_ROLE_DATABASE_URL")
    )
    with runtime_lock(lock_path) as descriptor:
        _write_pointer(request_path, bootstrap_request(
            profile=profile,
            package_manifest=manifest,
            executable=executable,
            lock_path=lock_path,
            task_id=task_id,
            session_id=session_id,
        ))
        binding = wait_for_binding(
            binding_path,
            profile=profile,
            task_id=task_id,
            session_id=session_id,
            executable=executable,
            timeout_seconds=wait_seconds,
        )
        verify_local_company_role_binding(
            profile=profile,
            binding=binding,
            package_root=package_root,
            expected_pid=os.getpid(),
        )
        with psycopg.connect(database_url) as conn, conn.transaction():
            bind_company_role_transaction(
                conn, profile=profile, binding=binding,
            )
        _write_pointer(state_dir / "claude-runtime.json", {
            "schema": RUNTIME_RECEIPT_SCHEMA,
            "profile_sha256": profile.sha256,
            "role_type": profile.role_type,
            "deployment_id": profile.deployment_id,
            "runtime_kind": profile.runtime_kind,
            "executable_path": str(executable),
            "executable_sha256": executable_sha256(executable),
            "runtime_session_id": session_id,
            "package_sha256": manifest["package_sha256"],
        })
        child_source = dict(os.environ)
        child_source.pop("COMPANY_ROLE_DATABASE_URL", None)
        environment = runtime_child_environment(
            child_source, profile=profile, repo=repo,
            output_root=output_root,
            package_payload=package_root / "payload",
        )
        argv = [
            str(executable), "--print",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose", "--permission-mode", "auto",
            "--strict-mcp-config", "--mcp-config", str(role_mcp_config),
            "--dangerously-load-development-channels",
            f"server:{profile.role_type}-channel-push",
            "--name", f"CC-Suite {profile.deployment_id}",
        ]
        os.set_inheritable(descriptor, True)
        os.chdir(repo)
        os.execve(executable, argv, environment)
    raise AssertionError("Company role runtime exec unexpectedly returned")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        CompanyScribeAdmissionError,
        psycopg.Error,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(78) from exc
