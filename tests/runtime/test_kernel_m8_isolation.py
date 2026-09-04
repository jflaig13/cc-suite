"""M8 identity, credential, egress, signed-message, and memory isolation."""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from fleet_kernel.m8 import isolation


REPO = Path(__file__).resolve().parent.parent


def _identity(root: Path, tenant: str = "example-tenant") -> isolation.WorkerIdentity:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    # Synthetic worker storage must match the current process identity even
    # when the filesystem inherits a different parent group (for example macOS).
    os.chown(root, os.geteuid(), os.getegid())
    return isolation.WorkerIdentity(tenant, f"{tenant}-payroll", os.geteuid(), os.getegid(), root)


def test_m8_identity_bound_file_open_and_traversal_refusal(tmp_path):
    worker = _identity(tmp_path / "example-tenant")
    (worker.root / "policy.json").write_bytes(b'{"payroll":true}\n')
    assert isolation.open_worker_file(worker, Path("policy.json")) == b'{"payroll":true}\n'
    with pytest.raises(isolation.IsolationError, match="escapes"):
        isolation.open_worker_file(worker, Path("../coffee-shoppe/policy.json"))


def test_m8_wrong_process_identity_and_unsafe_root_are_refused(tmp_path):
    worker = _identity(tmp_path / "example-tenant")
    with pytest.raises(isolation.IsolationError, match="UID/GID"):
        isolation.verify_worker_identity(replace(worker, uid=worker.uid + 1))
    worker.root.chmod(0o755)
    with pytest.raises(isolation.IsolationError, match="mode 0700"):
        isolation.verify_worker_identity(worker)


def test_m8_os_permission_layer_refuses_cross_tenant_file_read(tmp_path):
    own = _identity(tmp_path / "example-tenant")
    other_root = tmp_path / "coffee-shoppe"
    other_root.mkdir(mode=0o700)
    other = isolation.WorkerIdentity(
        "coffee-shoppe", "coffee-payroll", own.uid + 1, own.gid + 1, other_root,
    )
    with pytest.raises(isolation.IsolationError, match="UID/GID"):
        isolation.open_worker_file(other, Path("payroll.json"))
    assert os.geteuid() != 0, "kernel tests must not run as root and mask POSIX denials"




def test_m8_credential_checkout_is_tenant_capability_scoped_and_expiring(tmp_path):
    now = datetime.now(timezone.utc)
    worker = _identity(tmp_path / "example-tenant")
    grant = isolation.CredentialGrant(
        "example-tenant", "toast-payroll", "secret://example-tenant/toast-payroll",
        now + timedelta(minutes=5),
    )
    broker = isolation.CredentialBroker({("example-tenant", "toast-payroll"): grant})
    assert broker.checkout(
        worker=worker, tenant_id="example-tenant", capability="toast-payroll", now=now,
    ) == grant
    with pytest.raises(isolation.IsolationError, match="tenant mismatch"):
        broker.checkout(
            worker=worker, tenant_id="coffee-shoppe", capability="toast-payroll", now=now,
        )
    with pytest.raises(isolation.IsolationError, match="not granted"):
        broker.checkout(worker=worker, tenant_id="example-tenant", capability="mercury", now=now)


def test_m8_default_deny_egress_allows_only_exact_capability_host():
    policy = isolation.EgressPolicy({
        "toast-payroll": frozenset({"ws-api.toasttab.com"}),
    })
    assert policy.require(
        capability="toast-payroll", url="https://ws-api.toasttab.com/labor/v1/employees",
    ) == "ws-api.toasttab.com"
    for url in (
        "https://evil.example", "http://ws-api.toasttab.com",
        "https://ws-api.toasttab.com.evil.example", "https://user@ws-api.toasttab.com",
    ):
        with pytest.raises(isolation.IsolationError):
            policy.require(capability="toast-payroll", url=url)


def test_m8_signed_message_rejects_tamper_cross_tenant_and_replay(tmp_path):
    key_a = Ed25519PrivateKey.generate()
    key_b = Ed25519PrivateKey.generate()
    payload = b'{"objective":"payroll"}'
    message = isolation.sign_message(
        key_a, tenant_id="example-tenant", sender="orchestrator",
        recipient="payroll-worker", sequence=1, payload=payload,
    )
    store = isolation.DurableSequenceStore(tmp_path / "sequences")
    verifier = isolation.MessageVerifier({
        ("example-tenant", "orchestrator"): key_a.public_key(),
        ("coffee-shoppe", "orchestrator"): key_b.public_key(),
    }, store)
    verifier.verify(
        message, expected_tenant="example-tenant", expected_recipient="payroll-worker",
        payload=payload,
    )
    with pytest.raises(isolation.IsolationError, match="replay"):
        isolation.MessageVerifier({("example-tenant", "orchestrator"): key_a.public_key()}, store).verify(
            message, expected_tenant="example-tenant", expected_recipient="payroll-worker",
            payload=payload,
        )
    fresh = isolation.sign_message(
        key_a, tenant_id="example-tenant", sender="orchestrator",
        recipient="payroll-worker", sequence=2, payload=payload,
    )
    with pytest.raises(isolation.IsolationError, match="digest"):
        isolation.MessageVerifier({("example-tenant", "orchestrator"): key_a.public_key()}, store).verify(
            fresh, expected_tenant="example-tenant", expected_recipient="payroll-worker",
            payload=b"tampered",
        )
    with pytest.raises(isolation.IsolationError, match="tenant"):
        isolation.MessageVerifier({("example-tenant", "orchestrator"): key_a.public_key()}, store).verify(
            fresh, expected_tenant="coffee-shoppe", expected_recipient="payroll-worker",
            payload=payload,
        )

def test_m8_signed_message_fuzzed_field_mutations_all_refuse(tmp_path):
    key = Ed25519PrivateKey.generate()
    payload = b"payload"
    message = isolation.sign_message(
        key, tenant_id="example-tenant", sender="worker", recipient="verifier",
        sequence=1, payload=payload,
    )
    mutations = (
        replace(message, sender="attacker"),
        replace(message, recipient="other"),
        replace(message, sequence=2),
        replace(message, issued_at="1970-01-01T00:00:00+00:00"),
        replace(message, payload_sha256="0" * 64),
        replace(message, signature=bytes(reversed(message.signature))),
    )
    for mutated in mutations:
        with pytest.raises(isolation.IsolationError):
            isolation.MessageVerifier(
                {("example-tenant", "worker"): key.public_key()},
                isolation.DurableSequenceStore(tmp_path / mutated.message_id),
            ).verify(
                mutated, expected_tenant="example-tenant", expected_recipient="verifier",
                payload=payload,
            )


def test_m8_signed_message_ttl_refuses_capture_on_fresh_verifier(tmp_path):
    key = Ed25519PrivateKey.generate()
    old = isolation.sign_message(
        key, tenant_id="example-tenant", sender="worker", recipient="verifier",
        sequence=1, payload=b"payload",
        issued_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    with pytest.raises(isolation.IsolationError, match="stale"):
        isolation.MessageVerifier(
            {("example-tenant", "worker"): key.public_key()},
            isolation.DurableSequenceStore(tmp_path / "ttl"),
        ).verify(
            old, expected_tenant="example-tenant", expected_recipient="verifier",
            payload=b"payload",
        )


def test_m8_worker_key_binding_refuses_sender_impersonation(tmp_path):
    victim_key = Ed25519PrivateKey.generate()
    attacker_key = Ed25519PrivateKey.generate()
    forged = isolation.sign_message(
        attacker_key, tenant_id="example-tenant", sender="victim-worker",
        recipient="verifier", sequence=999, payload=b"forged",
    )
    verifier = isolation.MessageVerifier({
        ("example-tenant", "victim-worker"): victim_key.public_key(),
        ("example-tenant", "attacker-worker"): attacker_key.public_key(),
    }, isolation.DurableSequenceStore(tmp_path / "sender-binding"))
    with pytest.raises(isolation.IsolationError, match="signature"):
        verifier.verify(
            forged, expected_tenant="example-tenant", expected_recipient="verifier",
            payload=b"forged",
        )


def test_m8_sequence_store_refuses_unsafe_lock_metadata(tmp_path):
    root = tmp_path / "sequences"
    store = isolation.DurableSequenceStore(root)
    assert store.advance(tenant_id="example-tenant", sender="worker", sequence=1)
    lock_path = next(root.glob("*.lock"))
    lock_path.chmod(0o644)
    with pytest.raises(isolation.IsolationError, match="lock is unsafe"):
        store.advance(tenant_id="example-tenant", sender="worker", sequence=2)


def test_m8_shared_learning_allows_logic_but_refuses_tenant_or_secret_data():
    tenants = frozenset({"Example Tenant", "coffee-shoppe"})
    isolation.assert_shared_learning_safe(
        b"When the Toast payroll modal is visible, select Import.",
        tenant_literals=tenants,
    )
    with pytest.raises(isolation.IsolationError, match="tenant literal"):
        isolation.assert_shared_learning_safe(
            b"Example Tenant employee roster", tenant_literals=tenants,
        )
    with pytest.raises(isolation.IsolationError, match="credential"):
        isolation.assert_shared_learning_safe(
            b"token sk-ant-example", tenant_literals=tenants,
        )
    for encoded in (
        b"Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        b"SK-ANT-EXAMPLE",
        b"0123456789abcdef0123456789abcdef0123456789abcdef",
        b"QWxhZGRpbjpvcGVuIHNlc2FtZSB0b2tlbiBmb3Igc2hhcmVkIG1lbW9yeQ==",
    ):
        with pytest.raises(isolation.IsolationError, match="credential"):
            isolation.assert_shared_learning_safe(encoded, tenant_literals=tenants)
