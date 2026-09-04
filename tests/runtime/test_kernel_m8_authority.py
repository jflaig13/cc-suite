"""M8 cryptographically evidence-bound authority matrix tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from fleet_kernel.m8 import authority


KERNEL_KEY = Ed25519PrivateKey.generate()
FABLE_KEY = Ed25519PrivateKey.generate()


def _trust() -> authority.EvidenceTrust:
    deterministic = authority.EXPECTED_CHECKS - authority._INDEPENDENT_CHECKS - {
        "tenant_match", "idempotency", "notify_role", "money_role", "browser_role",
        "tenant_zero", "maintenance_role", "prod_role", "policy_role", "kill_role",
        "founder_role", "why", "forbidden",
    }
    return authority.EvidenceTrust({
        "kernel-evidence": authority.TrustedEvidenceIssuer(
            "kernel-evidence", "deterministic", KERNEL_KEY.public_key(),
            frozenset(deterministic),
        ),
        "fable-evidence": authority.TrustedEvidenceIssuer(
            "fable-evidence", "anthropic", FABLE_KEY.public_key(),
            authority._INDEPENDENT_CHECKS,
        ),
    })


def _base(action: authority.ActionClass) -> authority.GateRequest:
    role = {
        authority.ActionClass.NOTIFY_CUSTOMER: "notify_leg",
        authority.ActionClass.MONEY_ARTIFACT: "domain_engine",
        authority.ActionClass.UPLOAD_CUSTOMER_SYSTEM: "browser_executor",
        authority.ActionClass.CREDENTIAL_CHANGE: "kernel_maintenance",
        authority.ActionClass.PROD_CHANGE: "ccde",
        authority.ActionClass.POLICY_CHANGE: "scribe",
        authority.ActionClass.KILL_RESPAWN: "scribe",
        authority.ActionClass.SPEND_CHANGE: "founder",
    }.get(action, "worker")
    exceptional = action in {
        authority.ActionClass.CREDENTIAL_CHANGE, authority.ActionClass.PROD_CHANGE,
        authority.ActionClass.POLICY_CHANGE, authority.ActionClass.KILL_RESPAWN,
        authority.ActionClass.SPEND_CHANGE,
    }
    tenant = "tenant-zero" if exceptional else "example-tenant"
    return authority.GateRequest(
        action, tenant, tenant, role, "openai", "target", "obj-1", "worker-1",
        "idem-1234567890123456", "docs/brain/policy.md",
    )


def _valid(action: authority.ActionClass) -> authority.GateRequest:
    request = _base(action)
    checks = authority._MATRIX[action][1]
    now = datetime.now(timezone.utc)
    receipts = []
    direct = {
        "tenant_match", "idempotency", "notify_role", "money_role", "browser_role",
        "tenant_zero", "maintenance_role", "prod_role", "policy_role", "kill_role",
        "founder_role", "why", "forbidden",
    }
    for check_id in checks:
        if check_id in direct:
            continue
        independent = check_id in authority._INDEPENDENT_CHECKS
        receipts.append(authority.sign_evidence(
            FABLE_KEY if independent else KERNEL_KEY,
            request=request, check_id=check_id,
            issuer="fable-evidence" if independent else "kernel-evidence",
            artifact_ref=f"evidence/{check_id}.json", artifact_sha256="a" * 64,
            issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=5),
        ))
    return replace(request, evidence=tuple(receipts))


@pytest.mark.parametrize(
    "action",
    [item for item in authority.ActionClass if item not in {
        authority.ActionClass.SUBMIT_IRREVERSIBLE, authority.ActionClass.CROSS_TENANT,
    }],
)
def test_every_permitted_row_has_positive_path(action):
    assert authority.evaluate_action(_valid(action), _trust()).allowed is True


@pytest.mark.parametrize("action", list(authority.ActionClass))
def test_every_row_has_negative_path(action):
    request = _valid(action)
    if action not in {
        authority.ActionClass.SUBMIT_IRREVERSIBLE, authority.ActionClass.CROSS_TENANT,
    }:
        request = replace(request, actor_tenant_id="wrong", evidence=())
    assert authority.evaluate_action(request, _trust()).allowed is False
    with pytest.raises(authority.AuthorityDenied):
        authority.require_action(
            request, _trust(),
            lambda req, dec: authority.ScribeEscalation(
                dec.decision_sha256, req.objective_id, "scribe://unused",
            ),
            lambda _: True,
        )


def test_caller_cannot_forge_boolean_evidence_because_no_boolean_fields_exist():
    field_names = authority.GateRequest.__dataclass_fields__
    assert not any(name.endswith(("_pass", "_held", "_valid", "_green")) for name in field_names)
    request = _base(authority.ActionClass.MONEY_ARTIFACT)
    decision = authority.evaluate_action(request, _trust())
    assert decision.allowed is False and decision.reason == "quarantine classification is incomplete"


def test_evidence_is_signature_intent_artifact_expiry_and_family_bound():
    request = _valid(authority.ActionClass.NOTIFY_CUSTOMER)
    approval = next(item for item in request.evidence if item.check_id == "approval")
    with pytest.raises(authority.PolicyIntegrityError, match="signature"):
        authority.evaluate_action(
            replace(request, evidence=tuple(
                replace(item, artifact_sha256="b" * 64) if item is approval else item
                for item in request.evidence
            )), _trust(),
        )
    with pytest.raises(authority.PolicyIntegrityError, match="another action intent"):
        authority.evaluate_action(replace(request, target="changed"), _trust())
    wrong_family = authority.EvidenceTrust({
        **_trust()._issuers,
        "fable-evidence": authority.TrustedEvidenceIssuer(
            "fable-evidence", "openai", FABLE_KEY.public_key(),
            authority._INDEPENDENT_CHECKS,
        ),
    })
    with pytest.raises(authority.PolicyIntegrityError, match="actor model family"):
        authority.evaluate_action(request, wrong_family)


def test_classifier_hold_exact_reason_and_scribe_escalation():
    request = _base(authority.ActionClass.KILL_RESPAWN)
    now = datetime.now(timezone.utc)
    receipts = []
    for check_id in ("lease_consent", "classifier"):
        receipts.append(authority.sign_evidence(
            KERNEL_KEY, request=request, check_id=check_id, issuer="kernel-evidence",
            artifact_ref=f"evidence/{check_id}.json", artifact_sha256="c" * 64,
            issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=5),
            verdict="hold" if check_id == "classifier" else "pass",
            reason="platform requires direct provenance" if check_id == "classifier" else "",
        ))
    decision = authority.evaluate_action(replace(request, evidence=tuple(receipts)), _trust())
    assert decision.reason == "platform requires direct provenance"
    assert decision.escalate_to_scribe is True
    escalations = []
    denied_request = replace(request, evidence=tuple(receipts))
    def escalate(escalated_request, escalated_decision):
        escalations.append((escalated_request, escalated_decision))
        return authority.ScribeEscalation(
            escalated_decision.decision_sha256,
            escalated_request.objective_id,
            "scribe://escalations/hold-1",
        )
    with pytest.raises(authority.AuthorityDenied) as exc_info:
        authority.require_action(denied_request, _trust(), escalate, lambda _: True)
    assert escalations == [(denied_request, exc_info.value.decision)]
    assert exc_info.value.decision.escalate_to_scribe is True
    with pytest.raises(authority.PolicyIntegrityError, match="durable Scribe escalation"):
        authority.require_action(denied_request, _trust(), lambda *_: None, lambda _: True)
    with pytest.raises(authority.PolicyIntegrityError, match="durable Scribe escalation"):
        authority.require_action(denied_request, _trust(), escalate, lambda _: False)


def test_classifier_hold_escalates_even_when_an_earlier_check_refuses():
    request = replace(_base(authority.ActionClass.KILL_RESPAWN), actor_role="worker")
    now = datetime.now(timezone.utc)
    receipts = tuple(authority.sign_evidence(
        KERNEL_KEY, request=request, check_id=check_id, issuer="kernel-evidence",
        artifact_ref=f"evidence/{check_id}.json", artifact_sha256="d" * 64,
        issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=5),
        verdict="hold" if check_id == "classifier" else "pass",
        reason="platform provenance is ambiguous" if check_id == "classifier" else "",
    ) for check_id in ("lease_consent", "classifier"))
    decision = authority.evaluate_action(replace(request, evidence=receipts), _trust())
    assert decision.reason == "kill or respawn requires scribe or cos"
    assert decision.checked == ("tenant_zero", "kill_role")
    assert decision.escalate_to_scribe is True


@pytest.mark.parametrize("action", list(authority.ActionClass))
def test_check_registry_removal_mutation_fails_integrity(action, monkeypatch):
    monkeypatch.delitem(authority._CHECKS, authority._MATRIX[action][1][0])
    with pytest.raises(authority.PolicyIntegrityError, match="registry"):
        authority.evaluate_action(_valid(action), _trust())


@pytest.mark.parametrize("action", list(authority.ActionClass))
def test_matrix_row_check_removal_mutation_fails_pinned_digest(action, monkeypatch):
    tier, checks = authority._MATRIX[action]
    monkeypatch.setitem(authority._MATRIX, action, (tier, checks[1:] or ("tenant_match",)))
    with pytest.raises(authority.PolicyIntegrityError, match="digest"):
        authority.evaluate_action(_valid(action), _trust())


def test_forbidden_actions_have_no_path():
    for action in (authority.ActionClass.SUBMIT_IRREVERSIBLE, authority.ActionClass.CROSS_TENANT):
        decision = authority.evaluate_action(_valid(action), _trust())
        assert decision.allowed is False and decision.tier is authority.ConsequenceTier.FORBIDDEN
