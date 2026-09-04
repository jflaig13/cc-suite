"""Unskippable objective verification, nightly failure families, calibration."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from psycopg.rows import dict_row


class VerificationError(RuntimeError):
    pass


_ID_NAMESPACE = uuid.UUID("f60e6090-2c40-48da-8b72-6cd0c8125197")


@dataclass(frozen=True, slots=True)
class VerificationAttestation:
    attestation_id: uuid.UUID
    tenant_id: str
    objective_id: uuid.UUID
    obligation_id: uuid.UUID
    verifier_identity: str
    verifier_family: str
    subject_family: str
    verdict: str
    evidence_ref: str
    evidence_sha256: str
    issued_at: datetime
    signature: bytes
    subject_ref: str | None = None
    subject_sha256: str | None = None
    evidence_byte_length: int | None = None


@dataclass(frozen=True, slots=True)
class TrustedVerifier:
    identity: str
    family: str
    public_key: Ed25519PublicKey


def _attestation_payload(attestation: VerificationAttestation) -> bytes:
    return json.dumps(
        {
            "attestation_id": str(attestation.attestation_id),
            "tenant_id": attestation.tenant_id,
            "objective_id": str(attestation.objective_id),
            "obligation_id": str(attestation.obligation_id),
            "verifier_identity": attestation.verifier_identity,
            "verifier_family": attestation.verifier_family,
            "subject_family": attestation.subject_family,
            "verdict": attestation.verdict,
            "evidence_ref": attestation.evidence_ref,
            "evidence_sha256": attestation.evidence_sha256,
            "issued_at": attestation.issued_at.astimezone(timezone.utc).isoformat(),
            "subject_ref": attestation.subject_ref,
            "subject_sha256": attestation.subject_sha256,
            "evidence_byte_length": attestation.evidence_byte_length,
        },
        sort_keys=True, separators=(",", ":"),
    ).encode()


def sign_attestation(
    private_key: Ed25519PrivateKey, *, tenant_id: str, objective_id: uuid.UUID,
    obligation_id: uuid.UUID, verifier_identity: str, verifier_family: str,
    subject_family: str, evidence_ref: str, evidence: bytes,
    issued_at: datetime, verdict: str = "pass",
    subject_ref: str | None = None,
    subject_sha256: str | None = None,
) -> VerificationAttestation:
    if (subject_ref is None) != (subject_sha256 is None):
        raise VerificationError("verification subject binding is incomplete")
    unsigned = VerificationAttestation(
        uuid.uuid4(), tenant_id, objective_id, obligation_id, verifier_identity,
        verifier_family, subject_family, verdict, evidence_ref,
        hashlib.sha256(evidence).hexdigest(), issued_at, b"",
        subject_ref, subject_sha256,
        len(evidence) if subject_ref is not None else None,
    )
    return replace(unsigned, signature=private_key.sign(_attestation_payload(unsigned)))


class VerificationTrust:
    def __init__(
        self, verifiers: Mapping[str, TrustedVerifier],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._verifiers = dict(verifiers)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def verify(
        self, attestation: VerificationAttestation,
        artifact_loader: Callable[[str], bytes],
    ) -> None:
        verifier = self._verifiers.get(attestation.verifier_identity)
        if verifier is None or verifier.family != attestation.verifier_family:
            raise VerificationError("verification identity/family is untrusted")
        if (
            not attestation.tenant_id or not attestation.evidence_ref
            or attestation.verifier_family == attestation.subject_family
            or attestation.verdict != "pass"
        ):
            raise VerificationError("verification is absent, non-PASS, or non-independent")
        now = self._clock()
        if (
            attestation.issued_at.tzinfo is None
            or attestation.issued_at.utcoffset() is None
            or now.tzinfo is None or now.utcoffset() is None
            or not now - timedelta(hours=24) <= attestation.issued_at <= now
        ):
            raise VerificationError("verification attestation is stale or future-dated")
        try:
            verifier.public_key.verify(attestation.signature, _attestation_payload(attestation))
        except InvalidSignature as exc:
            raise VerificationError("verification attestation signature is invalid") from exc
        try:
            artifact = artifact_loader(attestation.evidence_ref)
        except (KeyError, OSError) as exc:
            raise VerificationError("verification evidence is unavailable") from exc
        if hashlib.sha256(artifact).hexdigest() != attestation.evidence_sha256:
            raise VerificationError("verification evidence digest mismatch")
        subject_values = (
            attestation.subject_ref,
            attestation.subject_sha256,
            attestation.evidence_byte_length,
        )
        if any(value is not None for value in subject_values):
            if (
                not isinstance(attestation.subject_ref, str)
                or not attestation.subject_ref
                or not isinstance(attestation.subject_sha256, str)
                or len(attestation.subject_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in attestation.subject_sha256
                )
                or attestation.evidence_byte_length != len(artifact)
            ):
                raise VerificationError("verification subject binding is invalid")


def enroll_objective(
    conninfo: dict, *, tenant_id: str, objective_id: uuid.UUID,
    subject_family: str, obligations: Mapping[str, str],
) -> dict[str, uuid.UUID]:
    if (
        not tenant_id
        or not subject_family
        or not obligations
        or "\x1f" in tenant_id
        or "\x1f" in subject_family
    ):
        raise ValueError("verification enrollment requires tenant, subject family, and obligations")
    ids = {
        kind: uuid.uuid5(_ID_NAMESPACE, f"{tenant_id}\x1f{objective_id}\x1f{kind}")
        for kind in obligations
    }
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        with conn.transaction():
            objective = conn.execute(
                "SELECT tenant_id,state FROM objectives WHERE id=%s", (objective_id,),
            ).fetchone()
            if objective is None or objective["tenant_id"] != tenant_id or objective["state"] == "done":
                raise VerificationError("objective enrollment tenant/state mismatch")
            conn.execute(
                "INSERT INTO kernel_objective_verification_policy "
                "(objective_id,tenant_id,subject_family) VALUES (%s,%s,%s) "
                "ON CONFLICT (objective_id) DO NOTHING",
                (objective_id, tenant_id, subject_family),
            )
            policy = conn.execute(
                "SELECT tenant_id,subject_family FROM kernel_objective_verification_policy "
                "WHERE objective_id=%s", (objective_id,),
            ).fetchone()
            if policy != {"tenant_id": tenant_id, "subject_family": subject_family}:
                raise VerificationError("objective verification policy collision")
            for kind, family in obligations.items():
                if not kind or not family or "\x1f" in kind or "\x1f" in family:
                    raise ValueError("obligation kind/family must be non-empty")
                conn.execute(
                    "INSERT INTO kernel_verification_obligations "
                    "(id,tenant_id,objective_id,obligation_kind,required_family) "
                    "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (ids[kind], tenant_id, objective_id, kind, family),
                )
                row = conn.execute(
                    "SELECT required_family FROM kernel_verification_obligations "
                    "WHERE tenant_id=%s AND objective_id=%s AND obligation_kind=%s",
                    (tenant_id, objective_id, kind),
                ).fetchone()
                if row is None or row["required_family"] != family:
                    raise VerificationError("verification obligation collision")
    return ids


def satisfy_obligation(
    conninfo: dict, *, attestation: VerificationAttestation,
    trust: VerificationTrust, artifact_loader: Callable[[str], bytes],
) -> None:
    trust.verify(attestation, artifact_loader)
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT o.tenant_id,o.objective_id,o.obligation_kind,"
                "o.required_family,o.state,p.subject_family "
                "FROM kernel_verification_obligations o "
                "JOIN kernel_objective_verification_policy p ON p.objective_id=o.objective_id "
                "WHERE o.id=%s FOR UPDATE OF o", (attestation.obligation_id,),
            ).fetchone()
            if row is None or (
                row["tenant_id"] != attestation.tenant_id
                or row["objective_id"] != attestation.objective_id
                or row["required_family"] != attestation.verifier_family
                or row["subject_family"] != attestation.subject_family
                or row["state"] != "open"
            ):
                raise VerificationError("attestation does not satisfy the open obligation")
            payroll_manifest = conn.execute(
                "SELECT m.manifest_ref,m.manifest_sha256,m.byte_length "
                "FROM kernel_payroll_review_manifests m "
                "WHERE m.tenant_id=%s AND m.objective_id=%s "
                "AND %s = 'independent_review:attempt:' || m.attempt_number::TEXT",
                (
                    attestation.tenant_id,
                    attestation.objective_id,
                    row["obligation_kind"],
                ),
            ).fetchone()
            if payroll_manifest is not None:
                expected_subject = (
                    "kernel-domain-review://"
                    f"{attestation.tenant_id}/{attestation.objective_id}/"
                    "attempt/"
                    f"{row['obligation_kind'].rsplit(':', 1)[-1]}/"
                    f"{payroll_manifest['manifest_sha256']}"
                )
                if (
                    attestation.subject_ref != expected_subject
                    or attestation.subject_sha256
                    != payroll_manifest["manifest_sha256"]
                    or attestation.evidence_byte_length is None
                ):
                    raise VerificationError(
                        "payroll verification subject binding is required"
                    )
            conn.execute(
                "INSERT INTO kernel_verification_attestations "
                "(id,tenant_id,objective_id,obligation_id,verifier_identity,verifier_family,"
                "subject_family,verdict,evidence_ref,evidence_sha256,issued_at,signature,"
                "subject_ref,subject_sha256,evidence_byte_length) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    attestation.attestation_id, attestation.tenant_id,
                    attestation.objective_id, attestation.obligation_id,
                    attestation.verifier_identity, attestation.verifier_family,
                    attestation.subject_family, attestation.verdict,
                    attestation.evidence_ref, attestation.evidence_sha256,
                    attestation.issued_at, attestation.signature,
                    attestation.subject_ref, attestation.subject_sha256,
                    attestation.evidence_byte_length,
                ),
            )
            conn.execute(
                "UPDATE kernel_verification_obligations SET state='satisfied',satisfied_at=now() "
                "WHERE id=%s", (attestation.obligation_id,),
            )


def complete_verified_objective(
    conninfo: dict, *, tenant_id: str, objective_id: uuid.UUID,
) -> None:
    with psycopg.connect(**conninfo) as conn:
        result = conn.execute(
            "UPDATE objectives SET state='done',updated_at=now() "
            "WHERE id=%s AND tenant_id=%s AND state<>'done'",
            (objective_id, tenant_id),
        )
        if result.rowcount != 1:
            raise VerificationError("objective completion target is absent or already done")


def require_verified_conveyance(
    disposition: str, attestation: VerificationAttestation | None,
    trust: VerificationTrust, artifact_loader: Callable[[str], bytes],
) -> None:
    if disposition not in {"accept", "refuse"}:
        raise ValueError("conveyance disposition must be accept or refuse")
    if attestation is None:
        raise VerificationError(f"unverified {disposition} conveyance refused")
    trust.verify(attestation, artifact_loader)
    try:
        payload = json.loads(artifact_loader(attestation.evidence_ref))
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("conveyance verification artifact is invalid") from exc
    if payload != {
        "disposition": disposition,
        "objective_id": str(attestation.objective_id),
        "verification": "pass",
    }:
        raise VerificationError("verification artifact is bound to another conveyance")


@dataclass(frozen=True, slots=True)
class FailureFamily:
    name: str
    actual_node: str
    mutation_node: str
    origin_ref: str


FAMILIES = (
    FailureFamily(
        "duplicate_delivery",
        "tests/test_tier1_kernel_m8_verification_observability.py::test_duplicate_delivery_real_kernel",
        "tests/test_tier1_kernel_m8_verification_observability.py::test_duplicate_delivery_missing_stable_key_mutation",
        "D4:duplicate-delivery",
    ),
    FailureFamily(
        "crash_restart",
        "tests/test_tier1_kernel_m2_proving_spike.py::TestF_M2_1_CommittedEffectCrashResumeExactlyOnce::test_crash_before_the_shared_commit_never_produces_a_double_debit",
        "tests/test_tier1_kernel_m2_proving_spike.py::TestF_M2_1_CommittedEffectCrashResumeExactlyOnce::test_mutation_reverting_to_the_old_step_decorator_reproduces_the_double_debit",
        "D4:F-M2-1-real-process-crash",
    ),
    FailureFamily(
        "network_partition",
        "tests/test_tier1_kernel_m8_verification_observability.py::test_network_partition_real_socket_kill",
        "tests/test_tier1_kernel_m8_verification_observability.py::test_network_partition_commit_boundary_mutation",
        "D4:socket-kill",
    ),
    FailureFamily(
        "credential_revocation",
        "tests/test_tier1_kernel_m8_verification_observability.py::test_credential_revocation_real_database",
        "tests/test_tier1_kernel_m8_verification_observability.py::test_credential_revocation_privileged_credential_mutation",
        "D5:credential-revoke-reissue",
    ),
    FailureFamily(
        "timeout_stale_owner",
        "tests/test_tier1_kernel_m2_proving_spike.py::TestFencingStaleOwnerRefused::test_stale_owner_write_refused_after_transfer",
        "tests/test_tier1_kernel_m2_proving_spike.py::TestFencingStaleOwnerRefused::test_mutation_an_unguarded_write_would_corrupt",
        "D4:stale-owner-resumes",
    ),
    FailureFamily(
        "historical_failure",
        "tests/test_tier1_oil173_frozen_owner_gate.py::test_dead_marker_pid_plus_stale_init_is_frozen",
        "tests/test_tier1_kernel_m3_f1_golden_transcript.py::TestRealCheckedInCycle::test_manifest_exact_timestamp_refuses_nearest_row_substitution",
        "F1-real-cycle+OIL-224",
    ),
)
EXPECTED_FAMILIES = frozenset({
    "duplicate_delivery", "crash_restart", "network_partition",
    "credential_revocation", "timeout_stale_owner", "historical_failure",
})
EXPECTED_FAMILY_MANIFEST_SHA256 = "0565248225020db3e5f16d88e3ff0dcedc27980a486beefe2697d0eb340afdbf"


def family_manifest_sha256() -> str:
    raw = json.dumps(
        [asdict(item) for item in FAMILIES], sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def assert_family_manifest() -> None:
    names = [item.name for item in FAMILIES]
    if frozenset(names) != EXPECTED_FAMILIES or len(names) != len(EXPECTED_FAMILIES):
        raise VerificationError("nightly failure-family manifest is incomplete")
    if family_manifest_sha256() != EXPECTED_FAMILY_MANIFEST_SHA256:
        raise VerificationError("nightly failure-family manifest digest is not pinned")
    nodes = [node for item in FAMILIES for node in (item.actual_node, item.mutation_node)]
    if len(nodes) != len(set(nodes)) or any(not node.startswith("tests/") for node in nodes):
        raise VerificationError("nightly failure families require distinct executable nodes")


@dataclass(frozen=True, slots=True)
class CalibrationCase:
    case_id: str
    defective: bool
    verifier_passed: bool
    source_ref: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    measured_at: str
    corpus_size: int
    false_accepts: int
    false_rejects: int
    false_accept_rate: float
    false_reject_rate: float
    corpus_sha256: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def calibrate_verifier(
    cases: tuple[CalibrationCase, ...], *, measured_at: datetime,
) -> CalibrationReport:
    if (
        not cases or measured_at.tzinfo is None or measured_at.utcoffset() is None
        or not any(item.defective for item in cases)
        or not any(not item.defective for item in cases)
        or any(
            not item.case_id or not item.source_ref
            or len(item.source_sha256) != 64
            or any(char not in "0123456789abcdef" for char in item.source_sha256)
            for item in cases
        )
    ):
        raise VerificationError("calibration requires a timestamped mixed seeded corpus")
    defective = sum(item.defective for item in cases)
    clean = len(cases) - defective
    false_accepts = sum(item.defective and item.verifier_passed for item in cases)
    false_rejects = sum((not item.defective) and (not item.verifier_passed) for item in cases)
    raw = json.dumps(
        [asdict(item) for item in cases], sort_keys=True, separators=(",", ":"),
    ).encode()
    return CalibrationReport(
        measured_at.isoformat(), len(cases), false_accepts, false_rejects,
        false_accepts / defective, false_rejects / clean, hashlib.sha256(raw).hexdigest(),
    )
