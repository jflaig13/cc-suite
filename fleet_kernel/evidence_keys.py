# SPDX-License-Identifier: MPL-2.0
"""Signed, short-lived evidence tokens for Payroll Work.

Tokens are transport integrity only.  They do not grant authority by
themselves: every consumer must re-derive the tenant, session, objective,
revision, evidence, action, and artifact predicates in one serializable
transaction before consuming the nonce.

Raw key material is supplied by a deployment-specific secret resolver.  This
module intentionally has no environment-variable fallback and never includes
secret references or key bytes in an exception.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ENVIRONMENT = re.compile(r"^[a-z0-9][a-z0-9._]{0,31}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TOKEN_VERSION = 1
_MAX_TTL = timedelta(minutes=10)
_MAX_TOKEN_LENGTH = 4096
_MAX_REVISION = 2**63 - 1
_PURPOSES = frozenset({
    "operator_action",
    "current_artifact_download",
    "superseded_artifact_download",
})
_CLAIM_KEYS = frozenset({
    "version",
    "key_id",
    "purpose",
    "tenant_subject_sha256",
    "objective_id",
    "bundle_id",
    "artifact_id",
    "approval_id",
    "expected_revision",
    "evidence_sha256",
    "manifest_sha256",
    "artifact_sha256",
    "approval_receipt_sha256",
    "action",
    "nonce",
    "session_subject_sha256",
    "csrf_context_sha256",
    "issued_at",
    "expires_at",
})


class PayrollEvidenceKeyError(RuntimeError):
    """The evidence-token key configuration or resolver is unavailable."""


class PayrollEvidenceTokenError(ValueError):
    """A signed evidence token is malformed, stale, or context-mismatched."""


SecretResolver = Callable[[str, str], bytes]


@dataclass(frozen=True, slots=True)
class EvidenceKeyReference:
    """One non-secret deployment reference to server-held key material."""

    key_id: str
    secret_ref: str
    secret_version: str


@dataclass(frozen=True, slots=True)
class EvidenceKeyRing:
    """Active mint key plus at most one bounded prior verify-only key."""

    environment: str
    active: EvidenceKeyReference
    prior: EvidenceKeyReference | None = None
    prior_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PayrollEvidenceClaims:
    """Canonical evidence token claims.

    Tenant and session bindings are SHA-256 subjects, never raw database or
    browser identifiers.  Artifact fields are mandatory only for artifact
    purposes and forbidden for ordinary operator actions.
    """

    purpose: str
    tenant_subject_sha256: str
    objective_id: uuid.UUID
    expected_revision: int
    evidence_sha256: str
    action: str
    nonce: uuid.UUID
    session_subject_sha256: str
    csrf_context_sha256: str
    issued_at: datetime
    expires_at: datetime
    bundle_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    approval_id: uuid.UUID | None = None
    manifest_sha256: str | None = None
    artifact_sha256: str | None = None
    approval_receipt_sha256: str | None = None


def sha256_subject(value: str) -> str:
    """Hash an already-authenticated, opaque, high-entropy subject.

    This helper is not an anonymizer for tenant slugs, email addresses, or
    other guessable identifiers.  Callers compose the product-session subject
    as ``session_id + "\\x1f" + operator_id`` before calling this function.
    The application service is responsible for enforcing that composition and
    for supplying identifiers that are already opaque.
    """

    if not isinstance(value, str) or not value or value != value.strip():
        raise PayrollEvidenceTokenError("evidence subject is invalid")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value: object, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PayrollEvidenceTokenError(f"{field} is invalid")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PayrollEvidenceTokenError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PayrollEvidenceTokenError(f"{field} is invalid") from exc
    if _timestamp(parsed) != value:
        raise PayrollEvidenceTokenError(f"{field} is not canonical")
    return parsed


def _key_utc(value: object, field: str) -> datetime:
    """Validate deployment/key timestamps under the key-error contract."""

    invalid = False
    try:
        return _utc(value, field)
    except PayrollEvidenceTokenError:
        invalid = True
    if invalid:
        raise PayrollEvidenceKeyError(
            "payroll evidence key timing configuration is invalid"
        )
    raise AssertionError("unreachable key timestamp state")


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise PayrollEvidenceTokenError(f"{field} is invalid")
    return value


def _optional_digest(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _digest(value, field)


def _identifier(value: object, field: str) -> uuid.UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PayrollEvidenceTokenError(f"{field} is invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise PayrollEvidenceTokenError(f"{field} is invalid") from exc
    if str(parsed) != value or parsed.int == 0:
        raise PayrollEvidenceTokenError(f"{field} is invalid")
    return parsed


def _required_identifier(value: object, field: str) -> uuid.UUID:
    parsed = _identifier(value, field)
    if parsed is None:
        raise PayrollEvidenceTokenError(f"{field} is invalid")
    return parsed


def _validate_key_reference(
    reference: EvidenceKeyReference, *, environment: str,
) -> EvidenceKeyReference:
    if not isinstance(reference, EvidenceKeyReference):
        raise PayrollEvidenceKeyError("payroll evidence key reference is invalid")
    if (
        _KEY_ID.fullmatch(reference.key_id) is None
        or not isinstance(reference.secret_ref, str)
        or reference.secret_ref !=
        f"mise-payroll-evidence-hmac-{environment}-{reference.key_id}"
        or not isinstance(reference.secret_version, str)
        or not reference.secret_version
        or reference.secret_version != reference.secret_version.strip()
        or len(reference.secret_version) > 128
    ):
        raise PayrollEvidenceKeyError("payroll evidence key reference is invalid")
    return reference


def validate_key_ring(
    ring: EvidenceKeyRing, *, now: datetime,
) -> EvidenceKeyRing:
    """Validate deployment key metadata without resolving secret material."""

    if not isinstance(ring, EvidenceKeyRing):
        raise PayrollEvidenceKeyError("payroll evidence key ring is invalid")
    if (
        not isinstance(ring.environment, str)
        or _ENVIRONMENT.fullmatch(ring.environment) is None
    ):
        raise PayrollEvidenceKeyError("payroll evidence environment is invalid")
    current = _key_utc(now, "clock")
    active = _validate_key_reference(ring.active, environment=ring.environment)
    if ring.prior is None:
        if ring.prior_expires_at is not None:
            raise PayrollEvidenceKeyError("payroll prior-key window is invalid")
        return ring
    prior = _validate_key_reference(ring.prior, environment=ring.environment)
    if prior.key_id == active.key_id:
        raise PayrollEvidenceKeyError("payroll evidence key IDs are not distinct")
    if ring.prior_expires_at is None:
        raise PayrollEvidenceKeyError("payroll prior-key window is invalid")
    expiry = _key_utc(ring.prior_expires_at, "prior key expiry")
    if expiry <= current:
        return EvidenceKeyRing(
            environment=ring.environment,
            active=active,
        )
    if expiry > current + timedelta(minutes=30):
        raise PayrollEvidenceKeyError("payroll prior-key window is invalid")
    return EvidenceKeyRing(
        environment=ring.environment,
        active=active,
        prior=prior,
        prior_expires_at=expiry,
    )


def _validated_claims(claims: PayrollEvidenceClaims) -> PayrollEvidenceClaims:
    if not isinstance(claims, PayrollEvidenceClaims):
        raise PayrollEvidenceTokenError("payroll evidence claims are invalid")
    if not isinstance(claims.purpose, str) or claims.purpose not in _PURPOSES:
        raise PayrollEvidenceTokenError("payroll evidence purpose is invalid")
    _digest(claims.tenant_subject_sha256, "tenant subject")
    if not isinstance(claims.objective_id, uuid.UUID) or claims.objective_id.int == 0:
        raise PayrollEvidenceTokenError("objective id is invalid")
    if (
        not isinstance(claims.expected_revision, int)
        or isinstance(claims.expected_revision, bool)
        or claims.expected_revision < 1
        or claims.expected_revision > _MAX_REVISION
    ):
        raise PayrollEvidenceTokenError("expected revision is invalid")
    _digest(claims.evidence_sha256, "evidence digest")
    if not isinstance(claims.action, str) or _ACTION.fullmatch(claims.action) is None:
        raise PayrollEvidenceTokenError("payroll evidence action is invalid")
    if not isinstance(claims.nonce, uuid.UUID) or claims.nonce.version != 4:
        raise PayrollEvidenceTokenError("nonce is invalid")
    _digest(claims.session_subject_sha256, "session subject")
    _digest(claims.csrf_context_sha256, "csrf context")
    issued = _utc(claims.issued_at, "issued at")
    expires = _utc(claims.expires_at, "expires at")
    if expires <= issued or expires - issued > _MAX_TTL:
        raise PayrollEvidenceTokenError("payroll evidence token lifetime is invalid")

    artifact_values = (
        claims.bundle_id,
        claims.artifact_id,
        claims.approval_id,
        claims.manifest_sha256,
        claims.artifact_sha256,
        claims.approval_receipt_sha256,
    )
    if claims.purpose == "operator_action":
        if claims.action == "download":
            raise PayrollEvidenceTokenError(
                "operator action token cannot confer download authority"
            )
        if any(value is not None for value in artifact_values):
            raise PayrollEvidenceTokenError(
                "operator action token contains artifact authority"
            )
    else:
        if (
            not all(value is not None for value in artifact_values[:5])
            or claims.action != "download"
        ):
            raise PayrollEvidenceTokenError(
                "artifact evidence binding is incomplete"
            )
        if claims.purpose == "current_artifact_download":
            if claims.approval_receipt_sha256 is not None:
                raise PayrollEvidenceTokenError(
                    "current artifact token contains superseded authority"
                )
        elif claims.approval_receipt_sha256 is None:
            raise PayrollEvidenceTokenError(
                "superseded artifact receipt binding is incomplete"
            )
        for field, value in (
            ("manifest digest", claims.manifest_sha256),
            ("artifact digest", claims.artifact_sha256),
            ("approval receipt digest", claims.approval_receipt_sha256),
        ):
            _optional_digest(value, field)
        for field, value in (
            ("bundle id", claims.bundle_id),
            ("artifact id", claims.artifact_id),
            ("approval id", claims.approval_id),
        ):
            if value is not None and (
                not isinstance(value, uuid.UUID) or value.int == 0
            ):
                raise PayrollEvidenceTokenError(f"{field} is invalid")
    return claims


def _document(claims: PayrollEvidenceClaims, key_id: str) -> dict[str, Any]:
    _validated_claims(claims)
    document: dict[str, Any] = {
        "version": _TOKEN_VERSION,
        "key_id": key_id,
        "purpose": claims.purpose,
        "tenant_subject_sha256": claims.tenant_subject_sha256,
        "objective_id": str(claims.objective_id),
        "bundle_id": str(claims.bundle_id) if claims.bundle_id else None,
        "artifact_id": str(claims.artifact_id) if claims.artifact_id else None,
        "approval_id": str(claims.approval_id) if claims.approval_id else None,
        "expected_revision": claims.expected_revision,
        "evidence_sha256": claims.evidence_sha256,
        "manifest_sha256": claims.manifest_sha256,
        "artifact_sha256": claims.artifact_sha256,
        "approval_receipt_sha256": claims.approval_receipt_sha256,
        "action": claims.action,
        "nonce": str(claims.nonce),
        "session_subject_sha256": claims.session_subject_sha256,
        "csrf_context_sha256": claims.csrf_context_sha256,
        "issued_at": _timestamp(_utc(claims.issued_at, "issued at")),
        "expires_at": _timestamp(_utc(claims.expires_at, "expires at")),
    }
    return document


def _canonical(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    if (
        not isinstance(value, str)
        or not value
        or "=" in value
        or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
    ):
        raise PayrollEvidenceTokenError("payroll evidence token is malformed")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            value + padding, altchars=b"-_", validate=True,
        )
    except (ValueError, base64.binascii.Error) as exc:
        raise PayrollEvidenceTokenError(
            "payroll evidence token is malformed"
        ) from exc
    if _b64_encode(decoded) != value:
        raise PayrollEvidenceTokenError("payroll evidence token is not canonical")
    return decoded


def _parse_document_unchecked(payload: bytes) -> dict[str, Any]:
    def no_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise PayrollEvidenceTokenError(
                    "payroll evidence token has duplicate claims"
                )
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise PayrollEvidenceTokenError(
            "payroll evidence token contains a non-finite number"
        )

    document = json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=no_duplicates,
        parse_constant=reject_constant,
    )
    if (
        not isinstance(document, dict)
        or frozenset(document) != _CLAIM_KEYS
        or _canonical(document) != payload
        or not isinstance(document["version"], int)
        or isinstance(document["version"], bool)
        or document["version"] != _TOKEN_VERSION
        or not isinstance(document["key_id"], str)
        or _KEY_ID.fullmatch(document["key_id"]) is None
    ):
        raise PayrollEvidenceTokenError("payroll evidence token claims are invalid")
    return document


def _claims_from_document_unchecked(
    document: dict[str, Any],
) -> PayrollEvidenceClaims:
    claims = PayrollEvidenceClaims(
        purpose=document["purpose"],
        tenant_subject_sha256=_digest(
            document["tenant_subject_sha256"], "tenant subject",
        ),
        objective_id=_required_identifier(document["objective_id"], "objective id"),
        bundle_id=_identifier(document["bundle_id"], "bundle id"),
        artifact_id=_identifier(document["artifact_id"], "artifact id"),
        approval_id=_identifier(document["approval_id"], "approval id"),
        expected_revision=document["expected_revision"],
        evidence_sha256=_digest(document["evidence_sha256"], "evidence digest"),
        manifest_sha256=_optional_digest(
            document["manifest_sha256"], "manifest digest",
        ),
        artifact_sha256=_optional_digest(
            document["artifact_sha256"], "artifact digest",
        ),
        approval_receipt_sha256=_optional_digest(
            document["approval_receipt_sha256"], "approval receipt digest",
        ),
        action=document["action"],
        nonce=_required_identifier(document["nonce"], "nonce"),
        session_subject_sha256=_digest(
            document["session_subject_sha256"], "session subject",
        ),
        csrf_context_sha256=_digest(
            document["csrf_context_sha256"], "csrf context",
        ),
        issued_at=_parse_timestamp(document["issued_at"], "issued at"),
        expires_at=_parse_timestamp(document["expires_at"], "expires at"),
    )
    return _validated_claims(claims)


def _normalize_token_errors(function: Callable[[], Any]) -> Any:
    """Expose only the declared hostile-token parsing error contract."""

    try:
        return function()
    except PayrollEvidenceTokenError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
    ):
        raise PayrollEvidenceTokenError(
            "payroll evidence token is malformed"
        ) from None


def _parse_document(payload: bytes) -> dict[str, Any]:
    """Parse only the structural/key envelope before signature verification."""

    return _normalize_token_errors(lambda: _parse_document_unchecked(payload))


def _claims_from_document(document: dict[str, Any]) -> PayrollEvidenceClaims:
    """Validate authority claims only after the signature is proven."""

    return _normalize_token_errors(
        lambda: _claims_from_document_unchecked(document),
    )


class PayrollEvidenceTokenCodec:
    """Mint and verify canonical evidence tokens with bounded key rotation."""

    def __init__(
        self,
        ring: EvidenceKeyRing,
        resolve_secret: SecretResolver,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(resolve_secret):
            raise PayrollEvidenceKeyError(
                "payroll evidence secret resolver is invalid"
            )
        self._ring = ring
        self._resolve_secret = resolve_secret
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _current_ring(self) -> tuple[EvidenceKeyRing, datetime]:
        failed = False
        try:
            clock_value = self._clock()
        except Exception:
            failed = True
            clock_value = None
        if failed:
            raise PayrollEvidenceKeyError(
                "payroll evidence clock is unavailable"
            )
        now = _key_utc(clock_value, "clock")
        return validate_key_ring(self._ring, now=now), now

    def _key(self, reference: EvidenceKeyReference) -> bytes:
        failed = False
        try:
            key = self._resolve_secret(reference.secret_ref, reference.secret_version)
        except Exception:
            failed = True
            key = None
        if failed:
            raise PayrollEvidenceKeyError(
                "payroll evidence key resolution failed"
            )
        if not isinstance(key, bytes) or len(key) < 32:
            raise PayrollEvidenceKeyError("payroll evidence key material is invalid")
        return key

    def mint(self, claims: PayrollEvidenceClaims) -> str:
        ring, now = self._current_ring()
        claims = _validated_claims(claims)
        issued = _utc(claims.issued_at, "issued at")
        if issued > now or claims.expires_at <= now:
            raise PayrollEvidenceTokenError(
                "payroll evidence token window is not current"
            )
        payload = _canonical(_document(claims, ring.active.key_id))
        signature = hmac.new(
            self._key(ring.active), payload, hashlib.sha256,
        ).digest()
        token = f"{_b64_encode(payload)}.{_b64_encode(signature)}"
        if len(token) > _MAX_TOKEN_LENGTH:
            raise PayrollEvidenceTokenError(
                "payroll evidence token exceeds its transport bound"
            )
        return token

    def verify(
        self,
        token: str,
        *,
        purpose: str,
        action: str,
        tenant_subject_sha256: str,
        objective_id: uuid.UUID,
        session_subject_sha256: str,
        csrf_context_sha256: str,
    ) -> PayrollEvidenceClaims:
        ring, now = self._current_ring()
        if (
            not isinstance(token, str)
            or len(token) > _MAX_TOKEN_LENGTH
            or token.count(".") != 1
        ):
            raise PayrollEvidenceTokenError("payroll evidence token is malformed")
        encoded_payload, encoded_signature = token.split(".", 1)
        payload = _b64_decode(encoded_payload)
        signature = _b64_decode(encoded_signature)
        if len(signature) != hashlib.sha256().digest_size:
            raise PayrollEvidenceTokenError("payroll evidence token is malformed")
        document = _parse_document(payload)

        reference = None
        if document["key_id"] == ring.active.key_id:
            reference = ring.active
        elif (
            ring.prior is not None
            and ring.prior_expires_at is not None
            # Defense in depth: validate_key_ring already proved this against
            # the same `now`, but the key-selection predicate remains explicit.
            # A stale configured prior intentionally fails the entire codec
            # under Section 9.2's invalid-ring fail-closed law.
            and now < _key_utc(ring.prior_expires_at, "prior key expiry")
            and document["key_id"] == ring.prior.key_id
        ):
            reference = ring.prior
        if reference is None:
            raise PayrollEvidenceTokenError(
                "payroll evidence token key is unavailable"
            )
        expected = hmac.new(self._key(reference), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise PayrollEvidenceTokenError(
                "payroll evidence token signature is invalid"
            )

        claims = _claims_from_document(document)
        if claims.issued_at > now or claims.expires_at <= now:
            raise PayrollEvidenceTokenError("payroll evidence token is expired")
        expected_context = (
            purpose,
            action,
            _digest(tenant_subject_sha256, "tenant subject"),
            objective_id,
            _digest(session_subject_sha256, "session subject"),
            _digest(csrf_context_sha256, "csrf context"),
        )
        actual_context = (
            claims.purpose,
            claims.action,
            claims.tenant_subject_sha256,
            claims.objective_id,
            claims.session_subject_sha256,
            claims.csrf_context_sha256,
        )
        if actual_context != expected_context:
            raise PayrollEvidenceTokenError(
                "payroll evidence token context changed"
            )
        return claims
