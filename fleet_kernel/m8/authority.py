# SPDX-License-Identifier: MPL-2.0
"""Cryptographically evidence-bound deterministic action authority."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from fleet_kernel.content_lint import content_receipt_kwargs, produce_content_evidence


class PolicyIntegrityError(RuntimeError):
    pass


class AuthorityDenied(PermissionError):
    def __init__(self, decision: "GateDecision") -> None:
        self.decision = decision
        super().__init__(
            f"kernel authority refused {decision.action.value}: {decision.reason}"
        )


class ActionClass(str, Enum):
    READ_TENANT_DATA = "read_tenant_data"
    DRAFT_COMPUTE = "draft_compute"
    WRITE_TENANT_STATE = "write_tenant_state"
    EXTERNAL_READ = "external_read"
    NOTIFY_CUSTOMER = "notify_customer"
    MONEY_ARTIFACT = "money_artifact"
    UPLOAD_CUSTOMER_SYSTEM = "upload_customer_system"
    SUBMIT_IRREVERSIBLE = "submit_irreversible"
    CREDENTIAL_CHANGE = "credential_change"
    PROD_CHANGE = "prod_change"
    POLICY_CHANGE = "policy_change"
    KILL_RESPAWN = "kill_respawn"
    SPEND_CHANGE = "spend_change"
    CROSS_TENANT = "cross_tenant"


class ConsequenceTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXCEPTIONAL = "exceptional"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class EvidenceReceipt:
    check_id: str
    intent_sha256: str
    verdict: str
    reason: str
    artifact_ref: str
    artifact_sha256: str
    issuer: str
    issued_at: str
    expires_at: str
    signature: bytes


@dataclass(frozen=True, slots=True)
class TrustedEvidenceIssuer:
    issuer: str
    family: str
    public_key: Ed25519PublicKey
    allowed_checks: frozenset[str]


@dataclass(frozen=True, slots=True)
class GateRequest:
    action: ActionClass
    actor_tenant_id: str
    request_tenant_id: str
    actor_role: str
    actor_family: str
    target: str
    objective_id: str = ""
    worker_identity: str = ""
    idempotency_key: str = ""
    why_ref: str = ""
    evidence: tuple[EvidenceReceipt, ...] = ()


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    action: ActionClass
    tier: ConsequenceTier
    reason: str
    checked: tuple[str, ...]
    decision_sha256: str
    escalate_to_scribe: bool = False


@dataclass(frozen=True, slots=True)
class ScribeEscalation:
    decision_sha256: str
    objective_id: str
    durable_ref: str


_INDEPENDENT_CHECKS = frozenset({"independent_verify", "cmcv", "review_3_plus_3"})


def intent_sha256(request: GateRequest) -> str:
    raw = json.dumps(
        {
            "action": request.action.value,
            "actor_tenant_id": request.actor_tenant_id,
            "request_tenant_id": request.request_tenant_id,
            "actor_role": request.actor_role,
            "actor_family": request.actor_family,
            "target": request.target,
            "objective_id": request.objective_id,
            "worker_identity": request.worker_identity,
            "idempotency_key": request.idempotency_key,
            "why_ref": request.why_ref,
        },
        sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _receipt_payload(receipt: EvidenceReceipt) -> bytes:
    return json.dumps(
        {
            "check_id": receipt.check_id,
            "intent_sha256": receipt.intent_sha256,
            "verdict": receipt.verdict,
            "reason": receipt.reason,
            "artifact_ref": receipt.artifact_ref,
            "artifact_sha256": receipt.artifact_sha256,
            "issuer": receipt.issuer,
            "issued_at": receipt.issued_at,
            "expires_at": receipt.expires_at,
        },
        sort_keys=True, separators=(",", ":"),
    ).encode()


def sign_evidence(
    private_key: Ed25519PrivateKey, *, request: GateRequest, check_id: str,
    issuer: str, artifact_ref: str, artifact_sha256: str,
    issued_at: datetime, expires_at: datetime, verdict: str = "pass", reason: str = "",
) -> EvidenceReceipt:
    unsigned = EvidenceReceipt(
        check_id, intent_sha256(request), verdict, reason, artifact_ref,
        artifact_sha256, issuer, issued_at.isoformat(), expires_at.isoformat(), b"",
    )
    return replace(unsigned, signature=private_key.sign(_receipt_payload(unsigned)))


def sign_content_evidence(
    private_key: Ed25519PrivateKey, *, request: GateRequest, text: str,
    issuer: str, issued_at: datetime, expires_at: datetime,
    allowlist: frozenset[str] = frozenset(),
) -> EvidenceReceipt:
    """Produce the ``content`` evidence receipt from ``content_lint``."""
    result = produce_content_evidence(text, allowlist=allowlist)
    fields = content_receipt_kwargs(result)
    return sign_evidence(
        private_key, request=request, issuer=issuer,
        issued_at=issued_at, expires_at=expires_at, **fields,
    )


class EvidenceTrust:
    def __init__(self, issuers: Mapping[str, TrustedEvidenceIssuer]) -> None:
        self._issuers = dict(issuers)

    def verified_receipts(self, request: GateRequest) -> dict[str, EvidenceReceipt]:
        intent = intent_sha256(request)
        verified: dict[str, EvidenceReceipt] = {}
        now = datetime.now(timezone.utc)
        for receipt in request.evidence:
            if receipt.check_id in verified:
                raise PolicyIntegrityError(f"duplicate authority evidence: {receipt.check_id}")
            issuer = self._issuers.get(receipt.issuer)
            if issuer is None or receipt.check_id not in issuer.allowed_checks:
                raise PolicyIntegrityError("authority evidence issuer/check scope is untrusted")
            if receipt.intent_sha256 != intent:
                raise PolicyIntegrityError("authority evidence is bound to another action intent")
            if len(receipt.artifact_sha256) != 64 or any(
                char not in "0123456789abcdef" for char in receipt.artifact_sha256
            ) or not receipt.artifact_ref:
                raise PolicyIntegrityError("authority evidence artifact binding is invalid")
            try:
                issued = datetime.fromisoformat(receipt.issued_at)
                expires = datetime.fromisoformat(receipt.expires_at)
            except ValueError as exc:
                raise PolicyIntegrityError("authority evidence timestamp is invalid") from exc
            if (
                issued.tzinfo is None or expires.tzinfo is None
                or not issued <= now <= expires or expires <= issued
            ):
                raise PolicyIntegrityError("authority evidence is not currently valid")
            if receipt.check_id in _INDEPENDENT_CHECKS and issuer.family == request.actor_family:
                raise PolicyIntegrityError("independent evidence came from actor model family")
            try:
                issuer.public_key.verify(receipt.signature, _receipt_payload(receipt))
            except InvalidSignature as exc:
                raise PolicyIntegrityError("authority evidence signature is invalid") from exc
            verified[receipt.check_id] = receipt
        return verified


Check = Callable[[GateRequest, Mapping[str, EvidenceReceipt]], tuple[bool, str]]


def _evidence(check_id: str, failure: str) -> Check:
    def check(_: GateRequest, receipts: Mapping[str, EvidenceReceipt]) -> tuple[bool, str]:
        receipt = receipts.get(check_id)
        if receipt is None:
            return False, failure
        if receipt.verdict != "pass":
            return False, receipt.reason or failure
        return True, failure
    return check


def _tenant_match(request: GateRequest, _: Mapping[str, EvidenceReceipt]) -> tuple[bool, str]:
    return (
        bool(request.actor_tenant_id)
        and request.actor_tenant_id == request.request_tenant_id,
        "actor tenant does not match requested tenant",
    )


def _idempotency(request: GateRequest, _: Mapping[str, EvidenceReceipt]) -> tuple[bool, str]:
    return (
        bool(request.idempotency_key and len(request.idempotency_key) >= 16),
        "stable idempotency key is missing",
    )


def _role(*allowed: str, reason: str) -> Check:
    return lambda request, _: (request.actor_role in allowed, reason)


def _tenant_zero(request: GateRequest, _: Mapping[str, EvidenceReceipt]) -> tuple[bool, str]:
    return (
        request.actor_tenant_id == request.request_tenant_id == "tenant-zero",
        "exceptional fleet action requires tenant-zero identity",
    )


def _why(request: GateRequest, _: Mapping[str, EvidenceReceipt]) -> tuple[bool, str]:
    return request.why_ref.startswith("docs/brain/"), "compiled policy WHY link is missing"


def _classifier(
    _: GateRequest, receipts: Mapping[str, EvidenceReceipt],
) -> tuple[bool, str]:
    receipt = receipts.get("classifier")
    if receipt is None:
        return False, "typed external-classifier receipt is missing"
    if receipt.verdict == "hold":
        return False, receipt.reason or "external classifier returned HOLD"
    if receipt.verdict != "pass":
        return False, "external classifier did not PASS"
    return True, "external classifier did not PASS"


def _forbidden(_: GateRequest, __: Mapping[str, EvidenceReceipt]) -> tuple[bool, str]:
    return False, "the kernel has no authority path for this action class"


_CHECKS: dict[str, Check] = {
    "tenant_match": _tenant_match,
    "storage_jail": _evidence("storage_jail", "tenant storage jail did not pass"),
    "role_scope": _evidence("role_scope", "role scope did not pass"),
    "spend": _evidence("spend", "spend ceiling did not pass"),
    "lease": _evidence("lease", "active ownership lease is missing"),
    "idempotency": _idempotency,
    "tool_permission": _evidence("tool_permission", "tool permission is missing"),
    "credential_scope": _evidence("credential_scope", "credential scope did not pass"),
    "source_truth": _evidence("source_truth", "source-of-truth check is missing"),
    "notify_role": _role("notify_leg", reason="customer send requires notify_leg"),
    "approval": _evidence("approval", "approval is missing"),
    "content": _evidence("content", "content gate did not pass"),
    "rate_limit": _evidence("rate_limit", "rate limit did not pass"),
    "independent_verify": _evidence(
        "independent_verify", "independent verification did not pass",
    ),
    "money_role": _role("domain_engine", reason="money artifact requires domain_engine"),
    "quarantine": _evidence("quarantine", "quarantine classification is incomplete"),
    "r7_basis": _evidence("r7_basis", "external ground-truth basis is missing"),
    "penny_reconcile": _evidence("penny_reconcile", "penny reconciliation did not pass"),
    "browser_role": _role("browser_executor", reason="upload requires browser_executor"),
    "mode_declared": _evidence("mode_declared", "dry-run/live mode is missing"),
    "deny_layers": _evidence("deny_layers", "browser deny layers did not pass"),
    "evidence_gate": _evidence("evidence_gate", "evidence gate did not pass"),
    "per_run_go": _evidence("per_run_go", "founder/customer per-run GO is missing"),
    "cmcv": _evidence("cmcv", "CMCV review is not CLEAN"),
    "tenant_zero": _tenant_zero,
    "maintenance_role": _role(
        "kernel_maintenance", reason="credential change requires maintenance",
    ),
    "founder_auth": _evidence("founder_auth", "founder authorization is missing"),
    "secret_manager": _evidence("secret_manager", "secret-manager write is missing"),
    "rotation": _evidence("rotation", "credential rotation record is missing"),
    "prod_role": _role("ccto", "ccde", reason="prod change requires ccto or ccde"),
    "rollback": _evidence("rollback", "rollback is not proven"),
    "ci": _evidence("ci", "required CI is not green"),
    "review_3_plus_3": _evidence("review_3_plus_3", "3+3 review is incomplete"),
    "compiled_controls": _evidence(
        "compiled_controls", "compiled-control production gate did not pass",
    ),
    "policy_role": _role("scribe", "ccto", reason="policy change requires scribe or ccto"),
    "version_bump": _evidence("version_bump", "policy version was not bumped"),
    "why": _why,
    "kill_role": _role("scribe", "cos", reason="kill or respawn requires scribe or cos"),
    "lease_consent": _evidence(
        "lease_consent", "lease holder neither consented nor expired",
    ),
    "classifier": _classifier,
    "founder_role": _role("founder", reason="spend change is founder-only"),
    "hmac_arm": _evidence("hmac_arm", "HMAC arm ceremony is invalid"),
    "attestation": _evidence("attestation", "attestation record is missing"),
    "forbidden": _forbidden,
}


_MATRIX: dict[ActionClass, tuple[ConsequenceTier, tuple[str, ...]]] = {
    ActionClass.READ_TENANT_DATA: (ConsequenceTier.LOW, ("tenant_match", "storage_jail")),
    ActionClass.DRAFT_COMPUTE: (ConsequenceTier.LOW, ("tenant_match", "role_scope", "spend")),
    ActionClass.WRITE_TENANT_STATE: (
        ConsequenceTier.MEDIUM,
        ("tenant_match", "role_scope", "spend", "lease", "idempotency"),
    ),
    ActionClass.EXTERNAL_READ: (
        ConsequenceTier.MEDIUM,
        ("tenant_match", "role_scope", "tool_permission", "credential_scope", "source_truth"),
    ),
    ActionClass.NOTIFY_CUSTOMER: (
        ConsequenceTier.HIGH,
        ("tenant_match", "notify_role", "approval", "content", "rate_limit", "independent_verify"),
    ),
    ActionClass.MONEY_ARTIFACT: (
        ConsequenceTier.HIGH,
        ("tenant_match", "money_role", "quarantine", "r7_basis", "penny_reconcile", "independent_verify"),
    ),
    ActionClass.UPLOAD_CUSTOMER_SYSTEM: (
        ConsequenceTier.HIGH,
        ("tenant_match", "browser_role", "mode_declared", "deny_layers", "evidence_gate", "per_run_go", "cmcv"),
    ),
    ActionClass.SUBMIT_IRREVERSIBLE: (ConsequenceTier.FORBIDDEN, ("forbidden",)),
    ActionClass.CREDENTIAL_CHANGE: (
        ConsequenceTier.EXCEPTIONAL,
        ("tenant_zero", "maintenance_role", "founder_auth", "secret_manager", "rotation", "independent_verify"),
    ),
    ActionClass.PROD_CHANGE: (
        ConsequenceTier.EXCEPTIONAL,
        ("tenant_zero", "prod_role", "founder_auth", "rollback", "ci", "review_3_plus_3", "compiled_controls"),
    ),
    ActionClass.POLICY_CHANGE: (
        ConsequenceTier.EXCEPTIONAL,
        ("tenant_zero", "policy_role", "founder_auth", "version_bump", "why", "independent_verify"),
    ),
    ActionClass.KILL_RESPAWN: (
        ConsequenceTier.HIGH,
        ("tenant_zero", "kill_role", "lease_consent", "classifier"),
    ),
    ActionClass.SPEND_CHANGE: (
        ConsequenceTier.EXCEPTIONAL,
        ("tenant_zero", "founder_role", "hmac_arm", "attestation"),
    ),
    ActionClass.CROSS_TENANT: (ConsequenceTier.FORBIDDEN, ("forbidden",)),
}


EXPECTED_ACTIONS = frozenset(ActionClass)
EXPECTED_CHECKS = frozenset({
    "approval", "attestation", "browser_role", "ci", "classifier", "cmcv",
    "compiled_controls", "content", "credential_scope", "deny_layers", "evidence_gate",
    "forbidden", "founder_auth", "founder_role", "hmac_arm", "idempotency",
    "independent_verify", "kill_role", "lease", "lease_consent", "maintenance_role", "mode_declared",
    "money_role", "notify_role", "penny_reconcile", "per_run_go", "policy_role",
    "prod_role", "quarantine", "r7_basis", "rate_limit", "review_3_plus_3", "role_scope",
    "rollback", "rotation", "secret_manager", "source_truth", "spend", "storage_jail",
    "tenant_match", "tenant_zero", "tool_permission", "version_bump", "why",
})
EXPECTED_MATRIX_SHA256 = "1e7323ef2c5cecd661ed03d575c3b494518e985c6e4fa127f750badf32844c7b"


def matrix_sha256() -> str:
    raw = json.dumps(
        {
            action.value: {"tier": tier.value, "checks": checks}
            for action, (tier, checks) in sorted(_MATRIX.items(), key=lambda item: item[0].value)
        },
        sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def assert_policy_integrity() -> None:
    if frozenset(_MATRIX) != EXPECTED_ACTIONS:
        raise PolicyIntegrityError("action-authority matrix action set is incomplete")
    if matrix_sha256() != EXPECTED_MATRIX_SHA256:
        raise PolicyIntegrityError("action-authority matrix digest is not the pinned policy")
    if frozenset(_CHECKS) != EXPECTED_CHECKS:
        raise PolicyIntegrityError("action-authority check registry is not the pinned set")


def _decision_digest(request: GateRequest, checked: tuple[str, ...], reason: str) -> str:
    bindings = sorted(
        (item.check_id, item.issuer, item.artifact_sha256, item.verdict)
        for item in request.evidence
    )
    raw = json.dumps(
        {"intent": intent_sha256(request), "evidence": bindings, "checked": checked, "reason": reason},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def evaluate_action(request: GateRequest, trust: EvidenceTrust) -> GateDecision:
    assert_policy_integrity()
    receipts = trust.verified_receipts(request)
    classifier = receipts.get("classifier")
    classifier_hold = bool(classifier is not None and classifier.verdict == "hold")
    tier, check_ids = _MATRIX[request.action]
    checked = []
    for check_id in check_ids:
        checked.append(check_id)
        passed, failure = _CHECKS[check_id](request, receipts)
        if not passed:
            return GateDecision(
                False, request.action, tier, failure, tuple(checked),
                _decision_digest(request, tuple(checked), failure), classifier_hold,
            )
    return GateDecision(
        True, request.action, tier, "authorized", tuple(checked),
        _decision_digest(request, tuple(checked), "authorized"), False,
    )


EscalationSink = Callable[[GateRequest, GateDecision], ScribeEscalation]
EscalationVerifier = Callable[[ScribeEscalation], bool]


def require_action(
    request: GateRequest, trust: EvidenceTrust,
    scribe_escalator: EscalationSink,
    scribe_verifier: EscalationVerifier,
) -> GateDecision:
    decision = evaluate_action(request, trust)
    if not decision.allowed:
        if decision.escalate_to_scribe:
            escalation = scribe_escalator(request, decision)
            if (
                not isinstance(escalation, ScribeEscalation)
                or escalation.decision_sha256 != decision.decision_sha256
                or escalation.objective_id != request.objective_id
                or not escalation.durable_ref.startswith("scribe://")
                or not scribe_verifier(escalation)
            ):
                raise PolicyIntegrityError(
                    "classifier HOLD did not produce a bound durable Scribe escalation"
                )
        raise AuthorityDenied(decision)
    return decision
