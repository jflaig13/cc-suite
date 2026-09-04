"""One bounded founder mandate with exact, ordered activation step subjects.

The founder mandate is durable authority for one closed sequence.  It is not a
bearer token and it cannot authorize work outside that sequence.  Each effect
still receives its own exact subject, M5 decision, idempotency key, and
completion receipt under the same mandate.
"""
from __future__ import annotations
from fleet_kernel.configuration import company_id, scribe_deployment_id, authority_issuer, deployment_binding

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_MANDATE_ID = re.compile(r"^activation-mandate-[A-Za-z0-9._-]+$")
MANDATE_SCHEMA = "mise.activation-mandate.v1"
SUBJECT_SCHEMA = "mise.founder-activation-step-subject.v1"
PHASE4_MANDATE_SCHEMA = "mise.cc-suite-activation-mandate.v1"
# Activation identity is intentionally absent during the implementation freeze.
# These constants must be replaced only from independently verified canonical
# artifact bytes in a separately authorized activation lane.
PHASE4_MANDATE_ID = deployment_binding('PHASE4_MANDATE_ID')
PHASE4_MANDATE_ARTIFACT_SHA256 = deployment_binding('PHASE4_MANDATE_ARTIFACT_SHA256')
PHASE4_MANDATE_KEYS = frozenset({
    "schema", "mandate_id", "status", "issuer", "source_kind",
    "quoted_instruction", "recorded_at", "governing_canon",
    "executable_plan", "company_id", "host_kind", "preserved_live_subject",
    "ordered_roles", "ordered_scope", "constraints",
    "requires_additional_founder_go_within_scope", "terminal_condition",
    "rollback_policy", "receipt_requirements",
})
PHASE4_COMPANY_ROLE_TYPES = frozenset({
    "cos", "ccto", "ccpo", "ccde", "ccro", "ccfo", "ccmo", "cclo",
    "ccgo", "ccco",
})
UTILITY_MANDATE_SCHEMA = "mise.utility-activation-mandate.v1"
UTILITY_MANDATE_ID = deployment_binding('UTILITY_MANDATE_ID')
UTILITY_MANDATE_ARTIFACT_SHA256 = deployment_binding('UTILITY_MANDATE_ARTIFACT_SHA256')
UTILITY_DEPLOYMENT_ID = deployment_binding('UTILITY_DEPLOYMENT_ID')
UTILITY_WORKER_ID = deployment_binding('UTILITY_WORKER_ID')
MANDATE_ARTIFACT_SHA256 = deployment_binding('MANDATE_ARTIFACT_SHA256')
EFFECTIVE_SCOPE_AMENDMENT_REF = deployment_binding('EFFECTIVE_SCOPE_AMENDMENT_REF')
# Digest of the record INCLUDING founder-witnessed Amendment 1 (the one-week
# live-run deferral, commit 5cc05d94).  The prior pin ff77185f… covered only
# the pre-amendment bytes and went stale the day Amendment 1 landed.
EFFECTIVE_SCOPE_AMENDMENT_SHA256 = deployment_binding('EFFECTIVE_SCOPE_AMENDMENT_SHA256')
MANDATE_ID = deployment_binding('MANDATE_ID')
MANDATE_QUOTED_INSTRUCTION = deployment_binding('MANDATE_QUOTED_INSTRUCTION')
MANDATE_GOVERNING_CANON = deployment_binding('MANDATE_GOVERNING_CANON')
MANDATE_EXECUTABLE_PLAN = deployment_binding('MANDATE_EXECUTABLE_PLAN')
MANDATE_RECORDED_AT = deployment_binding('MANDATE_RECORDED_AT')
MANDATE_KEYS = frozenset({
    "schema", "mandate_id", "status", "issuer", "source_kind",
    "quoted_instruction", "recorded_at", "governing_canon",
    "executable_plan", "ordered_scope", "constraints",
    "requires_additional_founder_go_within_scope", "terminal_condition",
})
MANDATE_ORDERED_SCOPE = (
    "finish_review_package_and_install_exact_kernel_release",
    "remove_and_verify_absent_mini_nopasswd_rule_after_last_privileged_operation",
    "earn_three_consecutive_real_shadow_mondays",
    "run_one_fenced_supervised_live_workflow",
    "transfer_exact_mini_authority_pointer",
    "verify_mini_canary_real_workflow_and_reconciliation",
    "admit_exactly_one_macbook_scribe_after_mini_activation",
    "verify_scribe_singleton_persistence_and_first_workflow",
    "verify_mini_and_scribe_restart_recovery_rollback_and_terminal_receipts",
)
MANDATE_CONSTRAINTS = (
    "no_gatef_evidence",
    "mini_kernel_before_macbook_scribe",
    "exact_technical_subject_and_receipt_per_step",
    "one_effect_path",
    "one_writable_scribe",
    "company_and_restaurant_fleet_data_isolation",
    "operator_approval_remains_required_where_the_workflow_requires_it",
    "nopasswd_must_be_absent_before_shadow_credit",
    "phase_4_remaining_cc_suite_migration_is_out_of_scope",
    "no_scope_expansion_beyond_ordered_scope",
)
MANDATE_TERMINAL_CONDITION = {
    "when": (
        "all_ordered_scope_steps_have_verified_completion_receipts_and_final_"
        "combined_recovery_receipt_is_verified"
    ),
    "terminal_status": "completed",
    "authorization_valid_after_terminal": False,
    "deterministic_recovery_contract": {
        "survives_terminal": True,
        "requires_additional_founder_go": False,
        "scope": (
            "same_exact_release_same_hosts_same_deployments_same_authority_"
            "root_monotonic_recovery_epochs_only"
        ),
        "forbids": (
            "new_release_new_host_new_deployment_scope_expansion_or_new_"
            "effect_authority"
        ),
    },
}


class FounderAuthorityError(PermissionError):
    """A mandate or exact step subject is malformed or outside authority."""


class ActivationStep(str, Enum):
    FINAL_RELEASE_INSTALL = "final-release-install"
    PRIVILEGE_CLEANUP = "privilege-cleanup"
    SHADOW_MONDAY_1 = "shadow-monday-1"
    SHADOW_MONDAY_2 = "shadow-monday-2"
    SHADOW_MONDAY_3 = "shadow-monday-3"
    SUPERVISED_LIVE_UPLOAD = "supervised-live-upload"
    MINI_CUTOVER = "mini-cutover"
    MINI_VERIFICATION = "mini-verification"
    SCRIBE_ADMISSION = "scribe-admission"
    SCRIBE_VERIFICATION = "scribe-verification"


class ActivationOutcome(str, Enum):
    """The OUTCOME a registered step authorizes (scope addition b, 9/1).

    Machinery repairs never re-sign the mandate: the signed artifact keeps
    naming steps; consumers that care about what was authorized compare
    outcomes. prepare = a reviewed artifact exists; approve = the operator's
    conveyance exists; deliver = the effect reached its edge with submit
    fenced; install = the reviewed release is the one running; verify =
    an independent check of the delivered outcome.
    """
    INSTALL = "install"
    PREPARE = "prepare"
    APPROVE = "approve"
    DELIVER = "deliver"
    VERIFY = "verify"


STEP_OUTCOMES: dict[str, ActivationOutcome] = {
    ActivationStep.FINAL_RELEASE_INSTALL.value: ActivationOutcome.INSTALL,
    ActivationStep.PRIVILEGE_CLEANUP.value: ActivationOutcome.INSTALL,
    ActivationStep.SHADOW_MONDAY_1.value: ActivationOutcome.PREPARE,
    ActivationStep.SHADOW_MONDAY_2.value: ActivationOutcome.PREPARE,
    ActivationStep.SHADOW_MONDAY_3.value: ActivationOutcome.PREPARE,
    ActivationStep.SUPERVISED_LIVE_UPLOAD.value: ActivationOutcome.DELIVER,
    ActivationStep.MINI_CUTOVER.value: ActivationOutcome.APPROVE,
    ActivationStep.MINI_VERIFICATION.value: ActivationOutcome.VERIFY,
    ActivationStep.SCRIBE_ADMISSION.value: ActivationOutcome.INSTALL,
    ActivationStep.SCRIBE_VERIFICATION.value: ActivationOutcome.VERIFY,
}


def step_outcome(step: "ActivationStep | str") -> ActivationOutcome:
    """The outcome a registered step authorizes; unknown steps refuse."""
    key = step.value if isinstance(step, ActivationStep) else str(step)
    try:
        return STEP_OUTCOMES[key]
    except KeyError as exc:
        raise FounderAuthorityError(f"activation step has no registered outcome: {key}") from exc


MANDATE_SEQUENCE = (
    ActivationStep.FINAL_RELEASE_INSTALL.value,
    ActivationStep.PRIVILEGE_CLEANUP.value,
    ActivationStep.SUPERVISED_LIVE_UPLOAD.value,
    ActivationStep.MINI_CUTOVER.value,
    ActivationStep.MINI_VERIFICATION.value,
    ActivationStep.SCRIBE_ADMISSION.value,
    ActivationStep.SCRIBE_VERIFICATION.value,
)


def _required(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise FounderAuthorityError(f"{field} is required")
    text = value.strip()
    if not text or text != value or any(ord(char) < 32 for char in text):
        raise FounderAuthorityError(f"{field} is not canonical")
    return text


def _digest(value: object, field: str, pattern: re.Pattern[str] = _SHA256) -> str:
    text = _required(value, field)
    if pattern.fullmatch(text) is None:
        raise FounderAuthorityError(f"{field} is not an exact digest")
    return text


def _uuid(value: object, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise FounderAuthorityError(f"{field} is not a UUID") from exc


def _positive(value: object, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise FounderAuthorityError(f"{field} is not an integer") from exc
    if result <= 0:
        raise FounderAuthorityError(f"{field} must be positive")
    return result


def _timestamp(value: object, field: str) -> str:
    text = _required(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FounderAuthorityError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise FounderAuthorityError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_mapping(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise FounderAuthorityError(f"{field} must be a non-empty object")
    try:
        encoded = json.dumps(
            dict(value), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
        result = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise FounderAuthorityError(f"{field} is not canonical JSON") from exc
    if not isinstance(result, dict):
        raise FounderAuthorityError(f"{field} must be a JSON object")
    return result


def _closed(
    value: Mapping[str, Any], *, field: str, keys: frozenset[str],
) -> dict[str, Any]:
    result = _canonical_mapping(value, field)
    if frozenset(result) != keys:
        missing = sorted(keys - frozenset(result))
        extra = sorted(frozenset(result) - keys)
        raise FounderAuthorityError(
            f"{field} has wrong keys (missing={missing}, extra={extra})"
        )
    return result


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class FounderActivationMandate:
    mandate_id: str
    artifact: Mapping[str, Any]
    artifact_sha256: str
    artifact_bytes: bytes

    def __post_init__(self) -> None:
        mandate_id = _required(self.mandate_id, "mandate_id")
        if _MANDATE_ID.fullmatch(mandate_id) is None:
            raise FounderAuthorityError("mandate_id is invalid")
        object.__setattr__(self, "mandate_id", mandate_id)
        artifact = _closed(
            self.artifact, field="mandate artifact", keys=MANDATE_KEYS,
        )
        if not isinstance(self.artifact_bytes, bytes):
            raise FounderAuthorityError("mandate artifact bytes are required")
        raw_sha256 = hashlib.sha256(self.artifact_bytes).hexdigest()
        try:
            raw_artifact = json.loads(self.artifact_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FounderAuthorityError(
                "mandate artifact bytes are not valid JSON"
            ) from exc
        if (
            artifact.get("schema") != MANDATE_SCHEMA
            or mandate_id != MANDATE_ID
            or artifact.get("mandate_id") != MANDATE_ID
            or artifact.get("status") != "active"
            or artifact.get("issuer") != authority_issuer()
            or artifact.get("source_kind") != "direct_founder_instruction"
            or artifact.get("quoted_instruction") != MANDATE_QUOTED_INSTRUCTION
            or artifact.get("recorded_at") != MANDATE_RECORDED_AT
            or artifact.get("governing_canon") != MANDATE_GOVERNING_CANON
            or artifact.get("executable_plan") != MANDATE_EXECUTABLE_PLAN
            or artifact.get("ordered_scope") != list(MANDATE_ORDERED_SCOPE)
            or artifact.get("constraints") != list(MANDATE_CONSTRAINTS)
            or artifact.get("requires_additional_founder_go_within_scope") is not False
            or artifact.get("terminal_condition") != MANDATE_TERMINAL_CONDITION
            or raw_artifact != artifact
            or raw_sha256 != MANDATE_ARTIFACT_SHA256
            or self.artifact_sha256 != MANDATE_ARTIFACT_SHA256
        ):
            raise FounderAuthorityError(
                "founder activation mandate scope or provenance is not exact"
            )
        _timestamp(artifact.get("recorded_at"), "recorded_at")
        object.__setattr__(self, "artifact", artifact)
        object.__setattr__(
            self, "artifact_sha256",
            _digest(self.artifact_sha256, "mandate artifact SHA-256"),
        )
        object.__setattr__(self, "artifact_bytes", bytes(self.artifact_bytes))

    @classmethod
    def from_bytes(cls, raw: bytes) -> "FounderActivationMandate":
        try:
            artifact = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FounderAuthorityError("mandate artifact is not valid JSON") from exc
        if not isinstance(artifact, dict):
            raise FounderAuthorityError("mandate artifact must be an object")
        return cls(
            mandate_id=str(artifact.get("mandate_id", "")),
            artifact=artifact,
            artifact_sha256=hashlib.sha256(raw).hexdigest(),
            artifact_bytes=raw,
        )

    @classmethod
    def from_path(cls, path: Path) -> "FounderActivationMandate":
        return cls.from_bytes(Path(path).read_bytes())

    @property
    def issued_at(self) -> datetime:
        return datetime.fromisoformat(
            str(self.artifact["recorded_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)

    @property
    def authority_ref(self) -> str:
        return f"founder://activation-mandate/{self.mandate_id}"


@dataclass(frozen=True, slots=True)
class Phase4FounderActivationMandate:
    """Schema-valid Phase-4 mandate that remains unusable while pins are unset."""

    mandate_id: str
    artifact: Mapping[str, Any]
    artifact_sha256: str
    artifact_bytes: bytes

    def __post_init__(self) -> None:
        mandate_id = _required(self.mandate_id, "mandate_id")
        if _MANDATE_ID.fullmatch(mandate_id) is None:
            raise FounderAuthorityError("Phase-4 mandate_id is invalid")
        artifact = _closed(
            self.artifact,
            field="Phase-4 mandate artifact",
            keys=PHASE4_MANDATE_KEYS,
        )
        if not isinstance(self.artifact_bytes, bytes):
            raise FounderAuthorityError("Phase-4 mandate artifact bytes are required")
        try:
            raw_artifact = json.loads(self.artifact_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FounderAuthorityError(
                "Phase-4 mandate artifact bytes are not valid JSON"
            ) from exc
        raw_sha256 = hashlib.sha256(self.artifact_bytes).hexdigest()
        if raw_artifact != artifact or raw_sha256 != self.artifact_sha256:
            raise FounderAuthorityError(
                "Phase-4 mandate artifact bytes or digest are not exact"
            )
        if (
            artifact["schema"] != PHASE4_MANDATE_SCHEMA
            or artifact["mandate_id"] != mandate_id
            or artifact["status"] != "active"
            or artifact["issuer"] != authority_issuer()
            or artifact["source_kind"] != "direct_founder_instruction"
            or artifact["company_id"] != company_id()
            or artifact["host_kind"] != "macbook"
            or not isinstance(
                artifact["requires_additional_founder_go_within_scope"], bool,
            )
        ):
            raise FounderAuthorityError(
                "Phase-4 mandate identity or provenance is not exact"
            )
        _required(artifact["quoted_instruction"], "quoted_instruction")
        _timestamp(artifact["recorded_at"], "recorded_at")
        governing = _closed(
            artifact["governing_canon"],
            field="governing_canon",
            keys=frozenset({"path", "sha256"}),
        )
        _required(governing["path"], "governing_canon.path")
        _digest(governing["sha256"], "governing_canon.sha256")
        plan = _closed(
            artifact["executable_plan"],
            field="executable_plan",
            keys=frozenset({"path", "commit_sha", "tree_sha"}),
        )
        _required(plan["path"], "executable_plan.path")
        _digest(plan["commit_sha"], "executable_plan.commit_sha", _GIT_SHA)
        _digest(plan["tree_sha"], "executable_plan.tree_sha", _GIT_SHA)
        preserved = _closed(
            artifact["preserved_live_subject"],
            field="preserved_live_subject",
            keys=frozenset({
                "deployment_id", "package_sha256", "admission_epoch",
                "process_id", "preservation_rule",
            }),
        )
        if preserved["deployment_id"] != scribe_deployment_id():
            raise FounderAuthorityError(
                "Phase-4 mandate must preserve the configured Scribe deployment"
            )
        _digest(preserved["package_sha256"], "preserved_live_subject.package_sha256")
        _positive(preserved["admission_epoch"], "preserved_live_subject.admission_epoch")
        if _positive(preserved["process_id"], "preserved_live_subject.process_id") <= 1:
            raise FounderAuthorityError(
                "preserved_live_subject.process_id is invalid"
            )
        _required(preserved["preservation_rule"], "preserved_live_subject.preservation_rule")

        ordered_roles = artifact["ordered_roles"]
        if not isinstance(ordered_roles, list) or not ordered_roles:
            raise FounderAuthorityError("ordered_roles must be a non-empty array")
        seen_roles: set[str] = set()
        seen_deployments: set[str] = set()
        seen_workers: set[str] = set()
        canonical_roles: list[dict[str, Any]] = []
        for index, item in enumerate(ordered_roles):
            role = _closed(
                item,
                field=f"ordered_roles[{index}]",
                keys=frozenset({"role_type", "deployment_id", "worker_id"}),
            )
            role_type = _required(role["role_type"], "role_type")
            deployment_id = _required(role["deployment_id"], "deployment_id")
            worker_id = _uuid(role["worker_id"], "worker_id")
            if role_type not in PHASE4_COMPANY_ROLE_TYPES:
                raise FounderAuthorityError(
                    "ordered_roles contains a non-Phase-4 role"
                )
            if (
                role_type in seen_roles
                or deployment_id in seen_deployments
                or worker_id in seen_workers
            ):
                raise FounderAuthorityError(
                    "ordered_roles reuses a role, deployment, or worker"
                )
            seen_roles.add(role_type)
            seen_deployments.add(deployment_id)
            seen_workers.add(worker_id)
            canonical_roles.append({
                "role_type": role_type,
                "deployment_id": deployment_id,
                "worker_id": worker_id,
            })

        for field in ("ordered_scope", "constraints", "receipt_requirements"):
            items = artifact[field]
            if (
                not isinstance(items, list)
                or not items
                or any(not isinstance(item, str) or not item.strip() for item in items)
                or len(set(items)) != len(items)
            ):
                raise FounderAuthorityError(
                    f"{field} must be a non-empty unique string array"
                )
        terminal = _closed(
            artifact["terminal_condition"],
            field="terminal_condition",
            keys=frozenset({
                "completion_rule", "terminal_status",
                "authorization_valid_after_terminal", "recovery_only_contract",
            }),
        )
        _required(terminal["completion_rule"], "terminal_condition.completion_rule")
        if terminal["terminal_status"] != "completed":
            raise FounderAuthorityError("Phase-4 terminal status must be completed")
        if terminal["authorization_valid_after_terminal"] is not False:
            raise FounderAuthorityError(
                "Phase-4 authority must terminate at completion"
            )
        recovery = _closed(
            terminal["recovery_only_contract"],
            field="terminal_condition.recovery_only_contract",
            keys=frozenset({
                "same_role", "same_deployment", "same_release", "same_host",
                "same_authority_root", "monotonic_epochs_only",
            }),
        )
        if any(value is not True for value in recovery.values()):
            raise FounderAuthorityError(
                "Phase-4 recovery-only contract must preserve every equality axis"
            )
        rollback = _closed(
            artifact["rollback_policy"],
            field="rollback_policy",
            keys=frozenset({"per_role_target", "fence_order", "proof_required"}),
        )
        for field in rollback:
            _required(rollback[field], f"rollback_policy.{field}")
        object.__setattr__(self, "mandate_id", mandate_id)
        object.__setattr__(self, "artifact", artifact)
        object.__setattr__(
            self, "artifact_sha256", _digest(
                self.artifact_sha256, "Phase-4 mandate artifact SHA-256",
            ),
        )
        object.__setattr__(self, "artifact_bytes", bytes(self.artifact_bytes))

    def require_recordable(self) -> None:
        """Refuse recording until independently verified pins replace UNSET."""
        if (
            PHASE4_MANDATE_ID is None
            or PHASE4_MANDATE_ARTIFACT_SHA256 is None
        ):
            raise FounderAuthorityError(
                "Phase-4 activation mandate identity is UNSET"
            )
        if (
            self.mandate_id != PHASE4_MANDATE_ID
            or self.artifact_sha256 != PHASE4_MANDATE_ARTIFACT_SHA256
        ):
            raise FounderAuthorityError(
                "Phase-4 activation mandate does not match the pinned artifact"
            )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Phase4FounderActivationMandate":
        try:
            artifact = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FounderAuthorityError(
                "Phase-4 mandate artifact is not valid JSON"
            ) from exc
        if not isinstance(artifact, dict):
            raise FounderAuthorityError(
                "Phase-4 mandate artifact must be an object"
            )
        return cls(
            mandate_id=str(artifact.get("mandate_id", "")),
            artifact=artifact,
            artifact_sha256=hashlib.sha256(raw).hexdigest(),
            artifact_bytes=raw,
        )

    @classmethod
    def from_path(cls, path: PurePosixPath) -> "Phase4FounderActivationMandate":
        candidate = PurePosixPath(path)
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise FounderAuthorityError(
                "Phase-4 mandate path must be absolute and traversal-free"
            )
        return cls.from_bytes(Path(str(candidate)).read_bytes())

    @property
    def issued_at(self) -> datetime:
        return datetime.fromisoformat(
            str(self.artifact["recorded_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)

    @property
    def authority_ref(self) -> str:
        return f"founder://activation-mandate/{self.mandate_id}"


@dataclass(frozen=True, slots=True)
class UtilityFounderActivationMandate:
    """Founder mandate that admits only Utility. Phase-4 pins stay frozen."""

    mandate_id: str
    artifact: Mapping[str, Any]
    artifact_sha256: str
    artifact_bytes: bytes

    def __post_init__(self) -> None:
        mandate_id = _required(self.mandate_id, "mandate_id")
        if mandate_id != UTILITY_MANDATE_ID:
            raise FounderAuthorityError("Utility mandate_id is invalid")
        artifact = _closed(
            self.artifact,
            field="Utility mandate artifact",
            keys=PHASE4_MANDATE_KEYS,
        )
        if not isinstance(self.artifact_bytes, bytes):
            raise FounderAuthorityError("Utility mandate artifact bytes are required")
        try:
            raw_artifact = json.loads(self.artifact_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FounderAuthorityError(
                "Utility mandate artifact bytes are not valid JSON"
            ) from exc
        raw_sha256 = hashlib.sha256(self.artifact_bytes).hexdigest()
        if raw_artifact != artifact or raw_sha256 != self.artifact_sha256:
            raise FounderAuthorityError(
                "Utility mandate artifact bytes or digest are not exact"
            )
        if (
            artifact["schema"] != UTILITY_MANDATE_SCHEMA
            or artifact["mandate_id"] != mandate_id
            or artifact["status"] != "active"
            or artifact["issuer"] != authority_issuer()
            or artifact["source_kind"] != "direct_founder_instruction"
            or artifact["company_id"] != company_id()
            or artifact["host_kind"] != "macbook"
        ):
            raise FounderAuthorityError(
                "Utility mandate identity or provenance is not exact"
            )
        ordered_roles = artifact["ordered_roles"]
        if (
            not isinstance(ordered_roles, list)
            or len(ordered_roles) != 1
        ):
            raise FounderAuthorityError("Utility mandate must name exactly one role")
        role = _closed(
            ordered_roles[0],
            field="ordered_roles[0]",
            keys=frozenset({"role_type", "deployment_id", "worker_id"}),
        )
        if (
            role["role_type"] != "utility"
            or role["deployment_id"] != UTILITY_DEPLOYMENT_ID
            or str(role["worker_id"]) != UTILITY_WORKER_ID
        ):
            raise FounderAuthorityError(
                "Utility mandate role/deployment/worker is not exact"
            )
        object.__setattr__(self, "mandate_id", mandate_id)
        object.__setattr__(self, "artifact", artifact)
        object.__setattr__(
            self, "artifact_sha256", _digest(
                self.artifact_sha256, "Utility mandate artifact SHA-256",
            ),
        )
        object.__setattr__(self, "artifact_bytes", bytes(self.artifact_bytes))

    def require_recordable(self) -> None:
        if (
            self.mandate_id != UTILITY_MANDATE_ID
            or self.artifact_sha256 != UTILITY_MANDATE_ARTIFACT_SHA256
        ):
            raise FounderAuthorityError(
                "Utility activation mandate does not match the pinned artifact"
            )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "UtilityFounderActivationMandate":
        try:
            artifact = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FounderAuthorityError(
                "Utility mandate artifact is not valid JSON"
            ) from exc
        if not isinstance(artifact, dict):
            raise FounderAuthorityError(
                "Utility mandate artifact must be an object"
            )
        return cls(
            mandate_id=str(artifact.get("mandate_id", "")),
            artifact=artifact,
            artifact_sha256=hashlib.sha256(raw).hexdigest(),
            artifact_bytes=raw,
        )

    @classmethod
    def from_path(cls, path: PurePosixPath) -> "UtilityFounderActivationMandate":
        candidate = PurePosixPath(path)
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise FounderAuthorityError(
                "Utility mandate path must be absolute and traversal-free"
            )
        return cls.from_bytes(Path(str(candidate)).read_bytes())

    @property
    def issued_at(self) -> datetime:
        return datetime.fromisoformat(
            str(self.artifact["recorded_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)

    @property
    def authority_ref(self) -> str:
        return f"founder://activation-mandate/{self.mandate_id}"


def load_company_role_mandate(path: PurePosixPath, *, role_type: str):
    """Load the Phase-4 mandate or the Utility-only mandate."""
    if role_type == "utility":
        return UtilityFounderActivationMandate.from_path(path)
    return Phase4FounderActivationMandate.from_path(path)


_STABLE_RUNTIME_BINDING_KEYS = frozenset({
    "approval_id", "runtime_host_identity_sha256",
    "runtime_executable_sha256",
})
_COMMON_BINDING_KEYS = _STABLE_RUNTIME_BINDING_KEYS | frozenset({
    "runtime_boot_id_sha256",
})
_STEP_BINDING_KEYS: dict[ActivationStep, frozenset[str]] = {
    ActivationStep.FINAL_RELEASE_INSTALL: _COMMON_BINDING_KEYS | frozenset({
        "review_receipt_sha256", "installation_root_sha256",
        "installer_plan_sha256",
    }),
    ActivationStep.PRIVILEGE_CLEANUP: _COMMON_BINDING_KEYS | frozenset({
        "sudoers_rule_sha256", "cleanup_plan_sha256",
        "absence_probe_plan_sha256",
    }),
    ActivationStep.SHADOW_MONDAY_1: _COMMON_BINDING_KEYS | frozenset({
        "shadow_ordinal", "service_date", "shadow_input_sha256",
        "shadow_plan_sha256", "fence_mode",
    }),
    ActivationStep.SHADOW_MONDAY_2: _COMMON_BINDING_KEYS | frozenset({
        "shadow_ordinal", "service_date", "shadow_input_sha256",
        "shadow_plan_sha256", "fence_mode",
    }),
    ActivationStep.SHADOW_MONDAY_3: _COMMON_BINDING_KEYS | frozenset({
        "shadow_ordinal", "service_date", "shadow_input_sha256",
        "shadow_plan_sha256", "fence_mode",
    }),
    ActivationStep.SUPERVISED_LIVE_UPLOAD: _COMMON_BINDING_KEYS | frozenset({
        "operator_approval_id", "objective_id", "run_id", "attempt_id",
        "idempotency_key",
        "effect_payload_sha256", "prepared_output_sha256",
        "legacy_authority_sha256", "mode",
    }),
    ActivationStep.MINI_CUTOVER: _STABLE_RUNTIME_BINDING_KEYS | frozenset({
        "migration_id", "legacy_disable_receipt_sha256",
        "host_attestation_sha256", "authority_root_sha256",
        "cutover_pointer_before_sha256", "cutover_pointer_after_sha256",
    }),
    ActivationStep.MINI_VERIFICATION: _COMMON_BINDING_KEYS | frozenset({
        "migration_id", "canary_receipt_sha256",
        "real_workflow_receipt_sha256", "reconciliation_receipt_sha256",
        "restart_receipt_sha256", "rollback_receipt_sha256",
        "final_transition_receipt_sha256",
    }),
    ActivationStep.SCRIBE_ADMISSION: _COMMON_BINDING_KEYS | frozenset({
        "admission_id", "deployment_id", "worker_id", "worker_session_id",
        "agent_release_sha256", "task_id", "claude_session_id", "process_id",
        "process_start_fingerprint_sha256", "admission_epoch",
        "lease_expires_at",
        "runtime_executable_path", "runtime_lock_path", "authority_mode",
        "authority_root_admission_id", "prior_admission_id",
        "recovery_intent_id", "prior_rollback_receipt_sha256",
        "restart_intent_sha256",
    }),
    ActivationStep.SCRIBE_VERIFICATION: _COMMON_BINDING_KEYS | frozenset({
        "admission_id", "deployment_id", "worker_id", "admission_epoch",
        "admission_receipt_sha256", "singleton_probe_sha256",
        "persistence_probe_sha256", "restart_probe_sha256",
        "receipts_manifest_sha256",
    }),
}
_PRE_STATES: dict[ActivationStep, dict[str, Any]] = {
    ActivationStep.FINAL_RELEASE_INSTALL: {
        "release_state": "reviewed-held", "effect_state": "not-started",
    },
    ActivationStep.PRIVILEGE_CLEANUP: {
        "prior_step": "final-release-install", "nopasswd_rule": "present",
    },
    ActivationStep.SHADOW_MONDAY_1: {
        "prior_step": "privilege-cleanup", "effect_state": "fenced",
    },
    ActivationStep.SHADOW_MONDAY_2: {
        "prior_step": "shadow-monday-1", "effect_state": "fenced",
    },
    ActivationStep.SHADOW_MONDAY_3: {
        "prior_step": "shadow-monday-2", "effect_state": "fenced",
    },
    ActivationStep.SUPERVISED_LIVE_UPLOAD: {
        # T9 amend 38: Founder Decision 4E (DDL 056, SIGNED 8/3) removed
        # the shadow-Monday floor — privilege-cleanup is the prior step.
        # The 8/30 deploy regressed the DB trigger; DDL 056 re-applied.
        "prior_step": "privilege-cleanup", "upload_effect": "not-started",
        "submit_effect": "fenced",
    },
    ActivationStep.MINI_CUTOVER: {
        "prior_step": "supervised-live-upload",
        "restaurant_fleet_authority": "legacy-runtime",
        "mini_kernel_authority": "installed-held",
    },
    ActivationStep.MINI_VERIFICATION: {
        "prior_step": "mini-cutover",
        "restaurant_fleet_authority": "mini-kernel",
        "verification_state": "unverified",
    },
    ActivationStep.SCRIBE_ADMISSION: {
        "company_fleet": "active", "scribe_authority": "absent",
    },
    ActivationStep.SCRIBE_VERIFICATION: {
        "prior_step": "scribe-admission", "scribe_authority": "live",
        "verification_state": "unverified",
    },
}
_POST_STATES: dict[ActivationStep, dict[str, Any]] = {
    ActivationStep.FINAL_RELEASE_INSTALL: {
        "release_state": "installed-held", "effect_state": "completed",
    },
    ActivationStep.PRIVILEGE_CLEANUP: {
        "step_state": "completed", "nopasswd_rule": "absent",
    },
    ActivationStep.SHADOW_MONDAY_1: {
        "step_state": "completed", "external_effect": "none",
    },
    ActivationStep.SHADOW_MONDAY_2: {
        "step_state": "completed", "external_effect": "none",
    },
    ActivationStep.SHADOW_MONDAY_3: {
        "step_state": "completed", "external_effect": "none",
    },
    ActivationStep.SUPERVISED_LIVE_UPLOAD: {
        "step_state": "completed-never-submit",
        "external_effect": "upload-completed", "submission_effect": "none",
    },
    ActivationStep.MINI_CUTOVER: {
        "restaurant_fleet_authority": "mini-kernel",
        "legacy_runtime": "disabled",
    },
    ActivationStep.MINI_VERIFICATION: {
        "restaurant_fleet_authority": "mini-kernel",
        "canary": "passed", "real_workflow": "reconciled",
        "reconciliation": "passed", "restart": "passed",
        "rollback": "passed", "recutover": "passed",
        "final_route": "kernel",
    },
    ActivationStep.SCRIBE_ADMISSION: {
        "company_fleet": "active", "scribe_authority": "live",
        "deployment_id": scribe_deployment_id(),
    },
    ActivationStep.SCRIBE_VERIFICATION: {
        "scribe_authority": "live", "singleton": "verified",
        "persistence": "verified", "restart": "passed",
        "receipts": "verified",
    },
}
_STEP_ROLES = {
    ActivationStep.FINAL_RELEASE_INSTALL: "ccto",
    ActivationStep.PRIVILEGE_CLEANUP: "ccto",
    ActivationStep.SHADOW_MONDAY_1: "ccto",
    ActivationStep.SHADOW_MONDAY_2: "ccto",
    ActivationStep.SHADOW_MONDAY_3: "ccto",
    ActivationStep.SUPERVISED_LIVE_UPLOAD: "browser_navigator",
    ActivationStep.MINI_CUTOVER: "ccto",
    ActivationStep.MINI_VERIFICATION: "ccto",
    ActivationStep.SCRIBE_ADMISSION: "scribe",
    ActivationStep.SCRIBE_VERIFICATION: "scribe",
}


@dataclass(frozen=True, slots=True)
class ActivationStepSubject:
    mandate_id: str
    step: ActivationStep
    tenant_id: str
    role: str
    release_commit_sha: str
    release_tree_sha: str
    release_digest_sha256: str
    package_id: uuid.UUID
    package_sha256: str
    attempt_key: str
    pre_state: Mapping[str, Any]
    post_state: Mapping[str, Any]
    activation_binding: Mapping[str, Any]

    def __post_init__(self) -> None:
        mandate_id = _required(self.mandate_id, "mandate_id")
        if _MANDATE_ID.fullmatch(mandate_id) is None:
            raise FounderAuthorityError("subject mandate_id is invalid")
        object.__setattr__(self, "mandate_id", mandate_id)
        if not isinstance(self.step, ActivationStep):
            try:
                object.__setattr__(self, "step", ActivationStep(self.step))
            except ValueError as exc:
                raise FounderAuthorityError("activation step is invalid") from exc
        object.__setattr__(self, "tenant_id", _required(self.tenant_id, "tenant_id"))
        expected_role = _STEP_ROLES[self.step]
        if self.role != expected_role:
            raise FounderAuthorityError(
                f"{self.step.value} requires role {expected_role}"
            )
        object.__setattr__(
            self, "release_commit_sha",
            _digest(self.release_commit_sha, "release_commit_sha", _GIT_SHA),
        )
        object.__setattr__(
            self, "release_tree_sha",
            _digest(self.release_tree_sha, "release_tree_sha", _GIT_SHA),
        )
        object.__setattr__(
            self, "release_digest_sha256",
            _digest(self.release_digest_sha256, "release_digest_sha256"),
        )
        if not isinstance(self.package_id, uuid.UUID):
            try:
                object.__setattr__(self, "package_id", uuid.UUID(str(self.package_id)))
            except ValueError as exc:
                raise FounderAuthorityError("package_id is invalid") from exc
        object.__setattr__(
            self, "package_sha256", _digest(self.package_sha256, "package_sha256"),
        )
        attempt = _required(self.attempt_key, "attempt_key")
        if len(attempt) < 16:
            raise FounderAuthorityError("attempt_key must be stable and at least 16 bytes")
        object.__setattr__(self, "attempt_key", attempt)
        pre = _canonical_mapping(self.pre_state, "pre_state")
        post = _canonical_mapping(self.post_state, "post_state")
        if pre != _PRE_STATES[self.step] or post != _POST_STATES[self.step]:
            raise FounderAuthorityError(
                f"{self.step.value} pre/post state is incomplete or non-canonical"
            )
        binding = _closed(
            self.activation_binding, field="activation_binding",
            keys=_STEP_BINDING_KEYS[self.step],
        )
        self._validate_binding(binding)
        object.__setattr__(self, "pre_state", pre)
        object.__setattr__(self, "post_state", post)
        object.__setattr__(self, "activation_binding", binding)

    def _validate_binding(self, binding: dict[str, Any]) -> None:
        _uuid(binding["approval_id"], "approval_id")
        for field in (
            "runtime_host_identity_sha256", "runtime_executable_sha256",
        ):
            _digest(binding[field], field)
        if self.step is not ActivationStep.MINI_CUTOVER:
            _digest(binding["runtime_boot_id_sha256"], "runtime_boot_id_sha256")
        if self.step is ActivationStep.FINAL_RELEASE_INSTALL:
            for field in (
                "review_receipt_sha256", "installation_root_sha256",
                "installer_plan_sha256",
            ):
                _digest(binding[field], field)
        elif self.step is ActivationStep.PRIVILEGE_CLEANUP:
            for field in (
                "sudoers_rule_sha256", "cleanup_plan_sha256",
                "absence_probe_plan_sha256",
            ):
                _digest(binding[field], field)
        elif self.step in {
            ActivationStep.SHADOW_MONDAY_1,
            ActivationStep.SHADOW_MONDAY_2,
            ActivationStep.SHADOW_MONDAY_3,
        }:
            ordinal = {
                ActivationStep.SHADOW_MONDAY_1: 1,
                ActivationStep.SHADOW_MONDAY_2: 2,
                ActivationStep.SHADOW_MONDAY_3: 3,
            }[self.step]
            if _positive(binding["shadow_ordinal"], "shadow_ordinal") != ordinal:
                raise FounderAuthorityError("shadow ordinal does not match its mandate step")
            try:
                service_date = date.fromisoformat(_required(
                    binding["service_date"], "service_date",
                ))
            except ValueError as exc:
                raise FounderAuthorityError("service_date is invalid") from exc
            if service_date.weekday() != 0:
                raise FounderAuthorityError("shadow service_date must be a real Monday")
            for field in ("shadow_input_sha256", "shadow_plan_sha256"):
                _digest(binding[field], field)
            if binding["fence_mode"] != "shadow-no-effect":
                raise FounderAuthorityError("shadow step must remain no-effect")
        elif self.step is ActivationStep.SUPERVISED_LIVE_UPLOAD:
            for field in ("operator_approval_id", "objective_id", "run_id"):
                _uuid(binding[field], field)
            _required(binding["attempt_id"], "attempt_id")
            if len(_required(binding["idempotency_key"], "idempotency_key")) < 16:
                raise FounderAuthorityError("upload idempotency_key is too short")
            for field in (
                "effect_payload_sha256", "prepared_output_sha256",
                "legacy_authority_sha256",
            ):
                _digest(binding[field], field)
            if binding["mode"] != "never-submit":
                raise FounderAuthorityError("supervised upload mandate is never-submit only")
        elif self.step is ActivationStep.MINI_CUTOVER:
            _uuid(binding["migration_id"], "migration_id")
            for field in (
                "legacy_disable_receipt_sha256", "host_attestation_sha256",
                "authority_root_sha256", "cutover_pointer_before_sha256",
                "cutover_pointer_after_sha256",
            ):
                _digest(binding[field], field)
            if (
                binding["cutover_pointer_before_sha256"]
                == binding["cutover_pointer_after_sha256"]
            ):
                raise FounderAuthorityError("Mini cutover pointer would not change")
        elif self.step is ActivationStep.MINI_VERIFICATION:
            _uuid(binding["migration_id"], "migration_id")
            for field in (
                "canary_receipt_sha256", "real_workflow_receipt_sha256",
                "reconciliation_receipt_sha256", "restart_receipt_sha256",
                "rollback_receipt_sha256",
                "final_transition_receipt_sha256",
            ):
                _digest(binding[field], field)
        elif self.step is ActivationStep.SCRIBE_ADMISSION:
            for field in (
                "admission_id", "worker_id", "worker_session_id",
            ):
                _uuid(binding[field], field)
            if binding["deployment_id"] != scribe_deployment_id():
                raise FounderAuthorityError("only the configured Scribe deployment may be admitted")
            _digest(binding["agent_release_sha256"], "agent_release_sha256")
            _required(binding["task_id"], "task_id")
            _required(binding["claude_session_id"], "claude_session_id")
            if _positive(binding["process_id"], "process_id") <= 1:
                raise FounderAuthorityError("Scribe process_id is invalid")
            _digest(
                binding["process_start_fingerprint_sha256"],
                "process_start_fingerprint_sha256",
            )
            _positive(binding["admission_epoch"], "admission_epoch")
            _timestamp(binding["lease_expires_at"], "lease_expires_at")
            executable = PurePosixPath(_required(
                binding["runtime_executable_path"], "runtime_executable_path",
            ))
            if not executable.is_absolute() or ".." in executable.parts:
                raise FounderAuthorityError("runtime_executable_path is unsafe")
            runtime_lock = PurePosixPath(_required(
                binding["runtime_lock_path"], "runtime_lock_path",
            ))
            if not runtime_lock.is_absolute() or ".." in runtime_lock.parts:
                raise FounderAuthorityError("runtime_lock_path is unsafe")
            authority_mode = binding["authority_mode"]
            root_admission_id = _uuid(
                binding["authority_root_admission_id"],
                "authority_root_admission_id",
            )
            admission_id = _uuid(binding["admission_id"], "admission_id")
            recovery_fields = (
                "prior_admission_id", "recovery_intent_id",
                "prior_rollback_receipt_sha256", "restart_intent_sha256",
            )
            if authority_mode == "initial-local-activation":
                if root_admission_id != admission_id or any(
                    binding[field] is not None for field in recovery_fields
                ):
                    raise FounderAuthorityError(
                        "initial Scribe admission has recovery authority residue"
                    )
            elif authority_mode == "standing-mandate-recovery":
                if root_admission_id == admission_id:
                    raise FounderAuthorityError(
                        "recovered Scribe admission cannot be its authority root"
                    )
                _uuid(binding["prior_admission_id"], "prior_admission_id")
                _uuid(binding["recovery_intent_id"], "recovery_intent_id")
                _digest(
                    binding["prior_rollback_receipt_sha256"],
                    "prior_rollback_receipt_sha256",
                )
                _digest(
                    binding["restart_intent_sha256"],
                    "restart_intent_sha256",
                )
            else:
                raise FounderAuthorityError(
                    "Scribe admission authority_mode is invalid"
                )
        elif self.step is ActivationStep.SCRIBE_VERIFICATION:
            _uuid(binding["admission_id"], "admission_id")
            _uuid(binding["worker_id"], "worker_id")
            if binding["deployment_id"] != scribe_deployment_id():
                raise FounderAuthorityError("only the configured Scribe deployment may be verified")
            _positive(binding["admission_epoch"], "admission_epoch")
            for field in (
                "admission_receipt_sha256", "singleton_probe_sha256",
                "persistence_probe_sha256", "restart_probe_sha256",
                "receipts_manifest_sha256",
            ):
                _digest(binding[field], field)

    @classmethod
    def exact(
        cls,
        *,
        mandate_id: str,
        step: ActivationStep,
        tenant_id: str,
        release_commit_sha: str,
        release_tree_sha: str,
        release_digest_sha256: str,
        package_id: uuid.UUID,
        package_sha256: str,
        attempt_key: str,
        activation_binding: Mapping[str, Any],
    ) -> "ActivationStepSubject":
        """Construct the only canonical pre/post form for a named step."""
        return cls(
            mandate_id=mandate_id, step=step, tenant_id=tenant_id,
            role=_STEP_ROLES[step], release_commit_sha=release_commit_sha,
            release_tree_sha=release_tree_sha,
            release_digest_sha256=release_digest_sha256,
            package_id=package_id, package_sha256=package_sha256,
            attempt_key=attempt_key, pre_state=_PRE_STATES[step],
            post_state=_POST_STATES[step],
            activation_binding=activation_binding,
        )

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema": SUBJECT_SCHEMA,
            "mandate_id": self.mandate_id,
            "step": self.step.value,
            "attempt_key": self.attempt_key,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "release": {
                "commit_sha": self.release_commit_sha,
                "tree_sha": self.release_tree_sha,
                "digest_sha256": self.release_digest_sha256,
            },
            "package": {
                "id": str(self.package_id), "sha256": self.package_sha256,
            },
            "pre_state": dict(self.pre_state),
            "post_state": dict(self.post_state),
            "activation_binding": dict(self.activation_binding),
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.canonical_payload)).hexdigest()

    @property
    def subject_ref(self) -> str:
        return (
            f"founder://activation-step/{self.mandate_id}/"
            f"{self.step.value}/{self.sha256}"
        )

    @property
    def approval_id(self) -> uuid.UUID:
        return uuid.UUID(str(self.activation_binding["approval_id"]))
