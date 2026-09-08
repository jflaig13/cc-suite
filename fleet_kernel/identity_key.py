# SPDX-License-Identifier: MPL-2.0
"""The ONE identity-key builder — kernel-wide law (founder ruling 8/31).

Eradication campaign Step 3 (docs/ops/20260901__eradication-step*.md):
identity keys are computed from MONEY-MEANINGFUL CONTENT ONLY. Two
computations of an unchanged subject MUST byte-match forever — across
processes, machines, releases, and time. Everything about HOW or WHEN the
content was observed (timestamps, sessions, process state, host paths,
release/implementation identities, wrapper shape) is PROVENANCE: recorded
beside the key in a separate envelope, never inside the compared bytes.

Structural refusal, not convention: banned-class field names and
banned-type values are rejected at build time, so an infected key cannot
be constructed at all. New subject types must be registered here with an
explicit content-field contract — the census-registration tripwire in
tests/test_tier1_identity_key.py refuses unregistered comparison subjects.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import re
from typing import Any, Mapping

__all__ = [
    "IdentityKeyError",
    "SUBJECT_CONTRACTS",
    "identity_key",
    "provenance_envelope",
]


class IdentityKeyError(ValueError):
    """A key build violated the content-only law."""


# Field-NAME classes that may never appear inside compared bytes. These are
# the six 8/31 detonation families plus the 9/1 additions, generalized.
_BANNED_NAME_PATTERN = re.compile(
    r"("
    r"observed_at|occurred_at|recorded_at|created_at|updated_at|decided_at"
    r"|consumed_at|issued_at|expires_at|heartbeat|timestamp|_at$|^at$"
    r"|session|process|pid\b|census|boottime|boot_time|uptime"
    r"|hostname|host_path|abs_path|tmp_path"
    r"|request_id_header|trace|span"
    r"|release_commit|release_tree|release_sha|reviewed_release"
    r"|runtime_tree|release_binding|migration_commit|candidate_commit"
    r"|prosrc|source_sha|contract_sha"
    r"|stop_reason|usage|model_version"
    r")",
    re.IGNORECASE,
)

# Value TYPES that smuggle volatility regardless of field name.
_BANNED_TYPES = (
    _datetime.datetime,
    _datetime.date,
    _datetime.time,
)


# The registered subject types and their EXPLICIT content-field contracts.
# A key can only be built for a registered subject, with exactly the
# registered fields — nothing extra, nothing missing. Registration reviews
# happen in code review with this module's law in hand.
SUBJECT_CONTRACTS: dict[str, tuple[str, ...]] = {
    # What authority governs a live effect — the CONTENT of the route
    # decision, not the observation that produced it (organ #7 target
    # shape; conversion of _route_authority_sha256 call sites is Step-3c).
    "m9-live-route-authority/v2": (
        "tenant_id",
        "required_route",
        "resolved_route",
        "authority_plane",
        "activation_program_id",
        "activation_mandate_id",
        "package_id",
        "package_sha256",
        "dispatch_phase",
        "controller_boundary",
    ),
    # A terminal-validation checkpoint's own identity (organ #12 — already
    # content-pure; registered so the tripwire owns it).
    "m9-held-live-checkpoint/v1": (
        "tenant_id",
        "parent_payroll_run_id",
        "terminal_kind",
        "successor_request_sha256",
    ),
}


def _refuse(path: str, reason: str) -> None:
    raise IdentityKeyError(f"identity key refused at {path!r}: {reason}")


def _scan(value: Any, path: str) -> Any:
    if isinstance(value, _BANNED_TYPES):
        _refuse(path, f"banned volatile type {type(value).__name__}")
    if isinstance(value, float):
        # Floats invite platform-dependent repr drift; money is integral
        # cents, versions are strings.
        _refuse(path, "float values are banned (use integral cents/strings)")
    if isinstance(value, Mapping):
        out = {}
        for key in value:
            if not isinstance(key, str):
                _refuse(path, "non-string mapping key")
            if _BANNED_NAME_PATTERN.search(key):
                _refuse(f"{path}.{key}", "banned volatile field-name class")
            out[key] = _scan(value[key], f"{path}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [_scan(v, f"{path}[{i}]") for i, v in enumerate(value)]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    _refuse(path, f"unsupported value type {type(value).__name__}")


def identity_key(subject_type: str, content: Mapping[str, Any]) -> str:
    """Build THE identity key for a registered subject.

    Deterministic by construction: registered fields only, canonical JSON
    (sorted keys, tight separators), UTF-8, sha256 hex.
    """
    contract = SUBJECT_CONTRACTS.get(subject_type)
    if contract is None:
        raise IdentityKeyError(
            f"unregistered identity subject {subject_type!r} — register its "
            "content-field contract in fleet_kernel.identity_key"
        )
    missing = [f for f in contract if f not in content]
    extra = [f for f in content if f not in contract]
    if missing or extra:
        raise IdentityKeyError(
            f"subject {subject_type!r} content fields differ from the "
            f"registered contract (missing={missing}, extra={extra})"
        )
    cleaned = {field: _scan(content[field], field) for field in contract}
    payload = {
        "schema_id": f"mise.identity-key/{subject_type}",
        "content": cleaned,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def provenance_envelope(**fields: Any) -> dict[str, Any]:
    """The OTHER half: where observation metadata lives.

    Anything goes here — timestamps, release identities, process facts —
    because this envelope is recorded next to a key and NEVER compared.
    The marker field makes accidental use inside a key build refuse loudly
    (the banned-name scan catches 'provenance_recorded_at').
    """
    envelope = dict(fields)
    envelope["provenance_recorded_at"] = (
        _datetime.datetime.now(_datetime.timezone.utc).isoformat()
    )
    envelope["schema_id"] = "mise.identity-provenance/v1"
    return envelope
