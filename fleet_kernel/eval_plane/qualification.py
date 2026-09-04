"""EP-130 — qualification, staleness, expiration, and waiver semantics.

Governing contract: `fleet_kernel/fleet_kernel_evaluation_plane_full_project_directive.md`
(§6.7 `EvalQualification`, §6.8 `EvalWaiver`, §7.2 qualification lifecycle, §8.2
invalidation, §16.1 gate stages, §16.2 blocking-qualification requirements).

Reused (not rebuilt), per Gate A `docs/brain/080926__ep040-phase0-synthesis-gate-a.md` §8:

  * `QualificationState`, `GateMode`, `ResultState` and the `EvalWaiver` row all come
    from EP-100's `fleet_kernel/eval_plane/domain.py`. This module adds the *decision
    procedure* those types describe; it defines no parallel vocabulary.
  * The hash-bound, reproduce-or-refuse discipline of `fleet_kernel/m10/prove99.py`
    (`persist_snapshot`, `current_label`): a qualification is honored only while the
    exact hashes it was issued against still reproduce — generalized here from the
    fixed P8 metric set to an arbitrary suite via `ExamIdentity`.
  * `verifier_family <> subject_family` from
    `fleet_kernel/ddl/009_m8_verification_observability.sql` (line 41), mirrored in
    `Qualification.__post_init__` for the reviewer. The DDL CHECK stays the authority.

EP-110 integration point: `TargetFingerprintProvider` is the only surface this module
needs from the fingerprint work. It does not import EP-110 and does not care how the
fingerprint is computed — only that it is a stable 64-hex digest of the exact target.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from fleet_kernel.eval_plane.domain import (
    QUALIFICATION_TRANSITIONS,
    EvalQualification,
    EvalWaiver,
    GateMode,
    InconclusivePolicy,
    QualificationState,
    ResultState,
)
from fleet_kernel.eval_plane.graders import (
    AggregateOutcome,
    BlockingFinding,
    canonical_json,
    requirement_ref,
    sha256_hex,
)

__all__ = [
    "ExamIdentity", "TargetFingerprintProvider",
    "AppliedWaiver", "RejectedWaiver", "WaiverApplication", "apply_waivers",
    "waiver_requirement_ref",
    "Qualification", "issue_qualification", "evaluate_staleness", "compare_to_baseline",
    "ISSUABLE_STATES", "to_domain_qualification",
    "apply_waiver_to_rejected_qualification",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Every state issuance may produce. Derived from EP-100's transition table rather
# than restated, so a change to the lifecycle cannot leave this module issuing a
# state the record layer refuses — the divergence that made `_derive_state` return
# WAIVED out of PENDING, which both `QUALIFICATION_TRANSITIONS` and DDL 042:361 reject.
ISSUABLE_STATES = frozenset(
    QUALIFICATION_TRANSITIONS[QualificationState.PENDING]
) | {QualificationState.PENDING}


def _nonblank(value: str) -> bool:
    return bool(value and value.strip())


def _require_fingerprint(value: str, label: str) -> None:
    if not _SHA256_RE.match(value or ""):
        raise ValueError(f"{label} must be a 64-character lowercase sha256 hex digest")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")


@runtime_checkable
class TargetFingerprintProvider(Protocol):
    """The one interface EP-130 needs from EP-110 (§8.1)."""

    def fingerprint(self) -> str:
        """Return the current 64-hex canonical fingerprint of the exact target."""


@dataclass(frozen=True, slots=True)
class ExamIdentity:
    """EP-110 fingerprints the examinee; this fingerprints the exam (§3.3).

    Any change to the suite version, a grader version, a rubric version, a threshold, or
    the attempt policy produces a different digest, which makes every qualification
    issued under the old digest STALE (§8.2: "Grader/rubric/threshold → new
    qualification under the new exam version; historical results remain unchanged").
    """

    suite_id: str
    suite_version: str
    grader_versions: Mapping[str, str] = field(default_factory=dict)
    rubric_versions: Mapping[str, str] = field(default_factory=dict)
    thresholds: Mapping[str, Any] = field(default_factory=dict)
    attempt_policy_digest: str = ""

    def __post_init__(self) -> None:
        if not (_nonblank(self.suite_id) and _nonblank(self.suite_version)):
            raise ValueError("exam identity requires a non-blank suite id and version")
        object.__setattr__(self, "grader_versions", dict(self.grader_versions))
        object.__setattr__(self, "rubric_versions", dict(self.rubric_versions))
        object.__setattr__(self, "thresholds", dict(self.thresholds))

    def digest(self) -> str:
        return sha256_hex({
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "grader_versions": dict(self.grader_versions),
            "rubric_versions": dict(self.rubric_versions),
            "thresholds": dict(self.thresholds),
            "attempt_policy_digest": self.attempt_policy_digest,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id, "suite_version": self.suite_version,
            "grader_versions": dict(self.grader_versions),
            "rubric_versions": dict(self.rubric_versions),
            "thresholds": dict(self.thresholds),
            "attempt_policy_digest": self.attempt_policy_digest,
            "exam_digest": self.digest(),
        }


# --------------------------------------------------------------------- waivers

def waiver_requirement_ref(waiver: EvalWaiver) -> str:
    """The single requirement a waiver names, in `graders.requirement_ref` form."""
    return requirement_ref(
        waiver.failed_suite_id, waiver.failed_case_id, waiver.failed_grader_id)


@dataclass(frozen=True, slots=True)
class AppliedWaiver:
    waiver: EvalWaiver
    finding: BlockingFinding

    def to_dict(self) -> dict[str, Any]:
        return {
            "waiver_id": str(self.waiver.id),
            "requirement_ref": waiver_requirement_ref(self.waiver),
            "approver_identity": self.waiver.approver_identity,
            "founder_approval_id": str(self.waiver.founder_approval_id),
            "rationale": self.waiver.rationale,
            "evidence_ref": self.waiver.evidence_ref,
            "expires_at": (self.waiver.expires_at.astimezone(timezone.utc).isoformat()
                           if self.waiver.expires_at else None),
            "release_bound_to": self.waiver.release_bound_to,
            "finding": self.finding.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RejectedWaiver:
    waiver: EvalWaiver
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "waiver_id": str(self.waiver.id),
            "requirement_ref": waiver_requirement_ref(self.waiver),
            "approver_identity": self.waiver.approver_identity,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class WaiverApplication:
    remaining: tuple[BlockingFinding, ...]
    applied: tuple[AppliedWaiver, ...]
    rejected: tuple[RejectedWaiver, ...]


def _waiver_inactive_reason(
    waiver: EvalWaiver, *, now: datetime, release_id: str,
) -> str:
    """Empty string when the waiver is in force; otherwise why it is not."""
    if waiver.expires_at is not None:
        if now >= waiver.expires_at:
            return (f"expired at "
                    f"{waiver.expires_at.astimezone(timezone.utc).isoformat()}")
        return ""
    # domain.EvalWaiver.validate already refuses a waiver that is neither time-limited
    # nor release-limited, so reaching here means it is release-bound.
    if not _nonblank(release_id):
        return (f"release-bound to {waiver.release_bound_to!r} but this qualification "
                f"names no release")
    if waiver.release_bound_to != release_id:
        return (f"release-bound to {waiver.release_bound_to!r}, not the release under "
                f"qualification ({release_id!r})")
    return ""


def apply_waivers(
    findings: Sequence[BlockingFinding],
    waivers: Sequence[EvalWaiver],
    *,
    target_fingerprint: str,
    now: datetime,
    release_id: str = "",
) -> WaiverApplication:
    """Clear findings that an explicit, exact, in-force, authority-permitted waiver
    covers — and record every waiver that did not apply, with the reason it did not.

    A waiver never clears a finding marked `authority_forbidden`. That flag is set by
    rule R5 in `graders.aggregate` and by the effect-control layer (EP-120), and it is
    tested before any match can succeed, so there is no code path on which a waiver
    authorizes an effect the runtime authority system forbids (§6.8, final bullet).
    """
    _require_fingerprint(target_fingerprint, "target_fingerprint")
    _require_aware(now, "now")

    remaining: list[BlockingFinding] = []
    applied: list[AppliedWaiver] = []
    unused = {waiver.id: waiver for waiver in waivers}
    rejection_reasons: dict[Any, str] = {}

    for finding in findings:
        match: EvalWaiver | None = None
        for waiver in waivers:
            if waiver_requirement_ref(waiver) != finding.requirement_ref:
                continue
            if waiver.target_fingerprint != target_fingerprint:
                rejection_reasons.setdefault(
                    waiver.id,
                    f"bound to target {waiver.target_fingerprint!r} but this "
                    f"qualification is for {target_fingerprint!r}")
                continue
            inactive = _waiver_inactive_reason(waiver, now=now, release_id=release_id)
            if inactive:
                rejection_reasons.setdefault(waiver.id, inactive)
                continue
            if finding.authority_forbidden:
                rejection_reasons.setdefault(
                    waiver.id,
                    f"requirement {finding.requirement_ref!r} is forbidden by the "
                    f"runtime authority system — a waiver cannot authorize it")
                continue
            match = waiver
            break

        if match is None:
            remaining.append(finding)
        else:
            applied.append(AppliedWaiver(match, finding))
            unused.pop(match.id, None)

    rejected = tuple(
        RejectedWaiver(
            waiver,
            rejection_reasons.get(
                waiver_id,
                f"no blocking finding matched requirement "
                f"{waiver_requirement_ref(waiver)!r}"),
        )
        for waiver_id, waiver in unused.items()
    )
    return WaiverApplication(tuple(remaining), tuple(applied), rejected)


# --------------------------------------------------------------- baseline delta

def compare_to_baseline(
    current: AggregateOutcome,
    *,
    current_exam_digest: str,
    baseline: "Qualification | None",
) -> dict[str, Any]:
    """Material regression = a case that PASSed in the qualified baseline and no longer
    does, on the *same* exam.

    Across a different exam digest no comparison is made at all — §8.2 rules that a
    grader/rubric/threshold change starts a new exam version and leaves historical
    results unchanged, so a cross-exam delta would be comparing two different tests.
    """
    if baseline is None:
        return {"comparable": False, "reason": "no qualified baseline",
                "regressions": [], "improvements": [], "material_regression": False}
    if baseline.exam_digest != current_exam_digest:
        return {
            "comparable": False,
            "reason": "baseline was qualified under a different exam version",
            "baseline_qualification_id": baseline.qualification_id,
            "baseline_exam_digest": baseline.exam_digest,
            "regressions": [], "improvements": [], "material_regression": False,
        }

    regressions: list[dict[str, str]] = []
    improvements: list[dict[str, str]] = []
    for case in current.case_results:
        before = baseline.aggregate.case_state(case.case_id)
        if before is None:
            continue
        after = case.state
        if before is ResultState.PASS and after is not ResultState.PASS:
            regressions.append({
                "case_id": case.case_id, "baseline_state": before.value,
                "current_state": after.value, "criticality": case.criticality.value})
        elif before is not ResultState.PASS and after is ResultState.PASS:
            improvements.append({
                "case_id": case.case_id, "baseline_state": before.value,
                "current_state": after.value})
    return {
        "comparable": True,
        "baseline_qualification_id": baseline.qualification_id,
        "regressions": regressions, "improvements": improvements,
        "material_regression": bool(regressions),
    }


# ---------------------------------------------------------------- qualification

@dataclass(frozen=True, slots=True)
class Qualification:
    """Directive §6.7 — the release-facing result.

    A qualification may make a release eligible for its preexisting authority. It never
    grants new authority: nothing here is consulted by the runtime authority system,
    and the only field that could be mistaken for a grant (`applied_waivers`) is
    structurally incapable of covering an authority-forbidden finding.
    """

    qualification_id: str
    target_fingerprint: str
    exam: ExamIdentity
    exam_digest: str
    aggregate: AggregateOutcome
    gate_mode: GateMode
    state: QualificationState
    reviewer_identity: str
    reviewer_family: str
    subject_family: str
    issued_at: datetime
    expires_at: datetime
    release_id: str = ""
    remaining_findings: tuple[BlockingFinding, ...] = ()
    applied_waivers: tuple[AppliedWaiver, ...] = ()
    rejected_waivers: tuple[RejectedWaiver, ...] = ()
    baseline_comparison: Mapping[str, Any] = field(default_factory=dict)
    receipt_sha256: str = ""

    def __post_init__(self) -> None:
        if not _nonblank(self.qualification_id):
            raise ValueError("qualification requires an id")
        _require_fingerprint(self.target_fingerprint, "target_fingerprint")
        _require_fingerprint(self.exam_digest, "exam_digest")
        if not (_nonblank(self.reviewer_identity) and _nonblank(self.reviewer_family)
                and _nonblank(self.subject_family)):
            raise ValueError(
                "qualification requires a named reviewer, reviewer family, and "
                "subject family")
        # Mirror of the DDL 009 CHECK `verifier_family <> subject_family`. The database
        # constraint is the authority; this stops a non-independent qualification from
        # ever being constructed in process.
        if self.reviewer_family == self.subject_family:
            raise ValueError(
                f"reviewer family {self.reviewer_family!r} equals the subject family — "
                f"a same-family review is not independent verification")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("a qualification must expire after it is issued")
        object.__setattr__(self, "remaining_findings", tuple(self.remaining_findings))
        object.__setattr__(self, "applied_waivers", tuple(self.applied_waivers))
        object.__setattr__(self, "rejected_waivers", tuple(self.rejected_waivers))
        object.__setattr__(self, "baseline_comparison", dict(self.baseline_comparison))

    @property
    def qualifying_refusal(self) -> str:
        """Why the record layer would refuse a qualifying verdict here, or "".

        Computed from the aggregate rather than stored, so it cannot drift from the
        counters that are actually persisted.
        """
        return _qualifying_refusal(
            self.aggregate.domain_counters(),
            inconclusive_policy=self.aggregate.policy.inconclusive_policy,
            reviewer_identity=self.reviewer_identity)

    @property
    def would_block_under_enforcement(self) -> bool:
        """True whenever an enforcing gate would have refused this release.

        Advisory and warning stages (§16.1) may record a green that enforce mode would
        not. Following `stageb_payroll_run.py:gate_cmcv`'s labeling discipline, the
        caveat travels with the verdict, so an advisory green can never be read as an
        enforce-mode green. Applied waivers count: a waived release is one that would
        have blocked without the waiver, which is the whole reason the waiver exists.
        """
        return bool(
            self.remaining_findings or self.applied_waivers or self.qualifying_refusal)

    @property
    def material_regression(self) -> bool:
        return bool(self.baseline_comparison.get("material_regression"))

    def receipt_payload(self) -> dict[str, Any]:
        return {
            "qualification_id": self.qualification_id,
            "target_fingerprint": self.target_fingerprint,
            "exam": self.exam.to_dict(),
            "exam_digest": self.exam_digest,
            "aggregate": self.aggregate.to_dict(),
            "gate_mode": self.gate_mode.value,
            "state": self.state.value,
            "reviewer_identity": self.reviewer_identity,
            "reviewer_family": self.reviewer_family,
            "subject_family": self.subject_family,
            "release_id": self.release_id,
            "issued_at": self.issued_at.astimezone(timezone.utc).isoformat(),
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat(),
            "remaining_findings": [f.to_dict() for f in self.remaining_findings],
            # §6.8: waivers are visible in the qualification receipt, applied and
            # rejected alike, so a waiver can never do its work off the record.
            "applied_waivers": [a.to_dict() for a in self.applied_waivers],
            "rejected_waivers": [r.to_dict() for r in self.rejected_waivers],
            "baseline_comparison": dict(self.baseline_comparison),
            "would_block_under_enforcement": self.would_block_under_enforcement,
            "qualifying_refusal": self.qualifying_refusal,
            "blocking_counters": self.aggregate.domain_counters(),
        }

    def compute_receipt(self) -> str:
        return sha256_hex(canonical_json(self.receipt_payload()))


def _qualifying_refusal(
    counters: Mapping[str, int],
    *,
    inconclusive_policy: InconclusivePolicy,
    reviewer_identity: str,
) -> str:
    """Why EP-100 would refuse a qualifying verdict on this record, or "".

    A literal mirror of the guard in `domain.EvalQualification.transition` and of DDL
    042:231-232 — deliberately duplicated rather than inferred, because this module
    must decide a state *before* it has a row to transition. `to_domain_qualification`
    then drives the real transition, so the mirror is proved equal to the original on
    every state this module can issue rather than asserted equal.
    """
    if (counters["critical_failure_count"] or counters["error_count"]
            or counters["forbidden_effect_count"]):
        return "critical failure, error, or forbidden effect on record"
    if counters["inconclusive_count"] and not (
            inconclusive_policy is InconclusivePolicy.REVIEW
            and _nonblank(reviewer_identity)):
        return "unresolved inconclusive case"
    return ""


def _derive_state(
    *,
    gate_mode: GateMode,
    counters: Mapping[str, int],
    inconclusive_policy: InconclusivePolicy,
    reviewer_identity: str,
    remaining: Sequence[BlockingFinding],
    applied_waivers: Sequence[AppliedWaiver],
    requires_human_review: bool,
    human_review_completed: bool,
    material_regression: bool,
    regression_acknowledged: bool,
) -> QualificationState:
    """The issued state — always one of `ISSUABLE_STATES`.

    Issuance can never produce WAIVED. §7.2, `QUALIFICATION_TRANSITIONS`, and DDL
    042:361 all reach `waived` only from `rejected`, and the DDL additionally requires
    the waiver row to exist before the flip (its FK points back at the qualification).
    A run whose findings were all waived is therefore issued REJECTED and moved by
    `apply_waiver_to_rejected_qualification`, which keeps the waiver an explicit
    recorded act rather than a state issuance can mint on its own.
    """
    if remaining and gate_mode is GateMode.BLOCKING:
        return QualificationState.REJECTED
    if applied_waivers:
        return QualificationState.REJECTED
    if requires_human_review and not human_review_completed:
        return QualificationState.PENDING
    # §16.1 Stage 2: a material regression under WARNING requires explicit
    # acknowledgment or a versioned waiver; unacknowledged, it is not qualified.
    if material_regression and not regression_acknowledged \
            and gate_mode in (GateMode.WARNING, GateMode.BLOCKING):
        return QualificationState.REJECTED
    # A non-blocking gate records what it observed, but never a qualifying state that
    # secretly carries a critical failure, an unresolved error, or a forbidden effect.
    # Those record REJECTED with `would_block_under_enforcement` set, which is the
    # honest advisory verdict; a qualifying one here would be refused by both EP-100
    # and the DDL, so it could never be persisted anyway.
    if _qualifying_refusal(
            counters, inconclusive_policy=inconclusive_policy,
            reviewer_identity=reviewer_identity):
        return QualificationState.REJECTED
    return (QualificationState.QUALIFIED if gate_mode is GateMode.BLOCKING
            else QualificationState.ADVISORY_QUALIFIED)


def issue_qualification(
    *,
    qualification_id: str,
    target_fingerprint: str,
    exam: ExamIdentity,
    outcome: AggregateOutcome,
    gate_mode: GateMode,
    reviewer_identity: str,
    reviewer_family: str,
    subject_family: str,
    issued_at: datetime,
    expires_at: datetime,
    waivers: Sequence[EvalWaiver] = (),
    baseline: Qualification | None = None,
    release_id: str = "",
    human_review_completed: bool = False,
    regression_acknowledged: bool = False,
) -> Qualification:
    """Turn an aggregate outcome into a release-facing, content-bound qualification."""
    exam_digest = exam.digest()
    application = apply_waivers(
        outcome.findings, waivers, target_fingerprint=target_fingerprint,
        now=issued_at, release_id=release_id)
    comparison = compare_to_baseline(
        outcome, current_exam_digest=exam_digest, baseline=baseline)
    state = _derive_state(
        gate_mode=gate_mode,
        counters=outcome.domain_counters(),
        inconclusive_policy=outcome.policy.inconclusive_policy,
        reviewer_identity=reviewer_identity,
        remaining=application.remaining,
        applied_waivers=application.applied,
        requires_human_review=outcome.requires_human_review,
        human_review_completed=human_review_completed,
        material_regression=bool(comparison.get("material_regression")),
        regression_acknowledged=regression_acknowledged,
    )
    qualification = Qualification(
        qualification_id=qualification_id,
        target_fingerprint=target_fingerprint,
        exam=exam,
        exam_digest=exam_digest,
        aggregate=outcome,
        gate_mode=gate_mode,
        state=state,
        reviewer_identity=reviewer_identity,
        reviewer_family=reviewer_family,
        subject_family=subject_family,
        issued_at=issued_at,
        expires_at=expires_at,
        release_id=release_id,
        remaining_findings=application.remaining,
        applied_waivers=application.applied,
        rejected_waivers=application.rejected,
        baseline_comparison=comparison,
    )
    return replace(qualification, receipt_sha256=qualification.compute_receipt())


def apply_waiver_to_rejected_qualification(
    qualification: Qualification,
) -> Qualification:
    """§7.2 second step: REJECTED + an in-force applied waiver -> WAIVED.

    Separate from issuance because the lifecycle makes it separate. `waived` is
    reachable only from `rejected` in `QUALIFICATION_TRANSITIONS` and in the DDL's
    transition trigger, and the DDL additionally refuses a directly-inserted `waived`
    row because the waiver's foreign key points back at the qualification it excuses —
    the waiver must exist first. A waived release is never QUALIFIED: §7.2 gives WAIVED
    its own state precisely so a waiver cannot be laundered into a clean qualification.
    """
    if qualification.state is not QualificationState.REJECTED:
        raise ValueError(
            f"a waiver applies to a REJECTED qualification, not "
            f"{qualification.state.value}")
    if not qualification.applied_waivers:
        raise ValueError("waiving a qualification requires at least one applied waiver")
    if qualification.remaining_findings:
        raise ValueError(
            "unwaived findings remain — every blocking finding must be covered before "
            "the qualification can move to WAIVED")
    waived = replace(qualification, state=QualificationState.WAIVED)
    return replace(waived, receipt_sha256=waived.compute_receipt())


def to_domain_qualification(
    qualification: Qualification,
    *,
    tenant_id: str,
    run_id: uuid.UUID,
    snapshot_sha256: str,
    receipt_ref: str = "",
    baseline_qualification_id: uuid.UUID | None = None,
) -> EvalQualification:
    """Bridge the release-facing qualification onto EP-100's persisted Kernel row.

    The mirror of `graders.to_domain_result`, and the reason the two shapes cannot
    drift apart silently. The row is built PENDING and then walked to its state with
    `EvalQualification.transition`, so every state this module issues is proved legal
    by EP-100's own transition table and qualifying guards rather than by a parallel
    check here. WAIVED is walked through REJECTED, which is the only path the lifecycle
    and the DDL trigger both permit.

    `tenant_id` is required rather than defaulted: it is the RLS scope key on every
    kernel table, and a silent default would be a tenant-isolation hazard.
    """
    if not _nonblank(tenant_id):
        raise ValueError("persisting a qualification requires its tenant scope")
    _require_fingerprint(snapshot_sha256, "snapshot_sha256")

    outcome = qualification.aggregate
    payload = outcome.to_dict()
    row = EvalQualification(
        tenant_id=tenant_id,
        run_id=run_id,
        suite_id=outcome.suite_id,
        suite_version=outcome.suite_version,
        target_fingerprint=qualification.target_fingerprint,
        state=QualificationState.PENDING,
        gate_mode=qualification.gate_mode,
        inconclusive_policy=outcome.policy.inconclusive_policy,
        case_results={case["case_id"]: case for case in payload["cases"]},
        aggregate_metrics=payload["metrics"],
        **outcome.domain_counters(),
        baseline_qualification_id=baseline_qualification_id,
        reviewer_identity=qualification.reviewer_identity,
        reviewer_family=qualification.reviewer_family,
        subject_family=qualification.subject_family,
        receipt_ref=receipt_ref,
        receipt_sha256=qualification.receipt_sha256 or None,
        snapshot_sha256=snapshot_sha256,
        expires_at=qualification.expires_at,
        created_at=qualification.issued_at,
    )
    if qualification.state is QualificationState.WAIVED:
        row.transition(QualificationState.REJECTED, at=qualification.issued_at)
    if qualification.state is not QualificationState.PENDING:
        row.transition(qualification.state, at=qualification.issued_at)
    return row


def evaluate_staleness(
    qualification: Qualification,
    *,
    current_target_fingerprint: str,
    current_exam: ExamIdentity,
    now: datetime,
) -> QualificationState:
    """Directive §7.2/§8.2 — STALE is automatic, never a judgment call.

    Staleness is checked before expiry: a target or exam that changed is a stronger and
    more specific statement about why the qualification no longer applies than the
    clock running out. Either way this function only ever returns an invalidating state
    or the qualification's own recorded state — it never upgrades anything, so a changed
    threshold or grader version cannot pass through as a live qualification.
    """
    _require_fingerprint(current_target_fingerprint, "current_target_fingerprint")
    _require_aware(now, "now")

    if current_target_fingerprint != qualification.target_fingerprint:
        return QualificationState.STALE
    if current_exam.digest() != qualification.exam_digest:
        return QualificationState.STALE
    if now >= qualification.expires_at:
        return QualificationState.EXPIRED
    return qualification.state
