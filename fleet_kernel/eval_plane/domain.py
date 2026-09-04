"""Evaluation Plane domain contracts (EP-100).

Mirrors fleet_kernel/ddl/042_eval_plane_domain.sql one dataclass per table, in
the fleet_kernel/schemas.py convention (str Enums, `_KernelRow` base,
to_row()/from_row() round-trip).

The state vocabularies come from the directive §7.1 (run lifecycle) and §7.2
(qualification lifecycle); the aggregation rules from §7.3. The DDL enforces the
same invariants in Postgres — this module is the in-process half so a caller
cannot assemble an illegal record and only discover it at INSERT time.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Optional

from fleet_kernel.schemas import _KernelRow, _new_id, _row_value

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EvalRunState(str, Enum):
    CREATED = "created"
    TARGET_FROZEN = "target_frozen"
    READY = "ready"
    RUNNING = "running"
    GRADING = "grading"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"
    BLOCKED = "blocked"


class ExecutionMode(str, Enum):
    DENY_EFFECTS = "deny_effects"
    SIMULATED = "simulated"
    TEST_ACCOUNT = "test_account"
    SHADOW = "shadow"
    SUPERVISED_LIVE = "supervised_live"


class ResultState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    INCONCLUSIVE = "inconclusive"


class GraderKind(str, Enum):
    """Directive §11.1-11.4, plus human adjudication."""
    STRUCTURAL = "structural"
    TRAJECTORY = "trajectory"
    OUTCOME = "outcome"
    SEMANTIC = "semantic"
    HUMAN = "human"


class Criticality(str, Enum):
    """ADVISORY is distinct from NONCRITICAL and load-bearing.

    A noncritical result still counts toward the suite's pass-rate threshold, so
    it can block a release. An advisory result never can — it is excluded from
    aggregation entirely. Directive §4.2 keeps semantic grading advisory until
    it has calibration, disagreement behavior, injection testing, versioned
    judge configuration, and an adjudication path; collapsing the two would let
    a V1 judge model fail a release.
    """
    CRITICAL = "critical"
    NONCRITICAL = "noncritical"
    ADVISORY = "advisory"


class QualificationState(str, Enum):
    PENDING = "pending"
    ADVISORY_QUALIFIED = "advisory_qualified"
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    STALE = "stale"
    EXPIRED = "expired"
    WAIVED = "waived"
    ERROR = "error"


class GateMode(str, Enum):
    ADVISORY = "advisory"
    WARNING = "warning"
    BLOCKING = "blocking"


class InconclusivePolicy(str, Enum):
    BLOCK = "block"
    REVIEW = "review"


RUN_TERMINAL_STATES = frozenset(
    {EvalRunState.COMPLETED, EvalRunState.CANCELLED, EvalRunState.ERROR}
)

QUALIFYING_STATES = frozenset(
    {QualificationState.QUALIFIED, QualificationState.ADVISORY_QUALIFIED}
)

QUALIFICATION_TERMINAL_STATES = frozenset(
    {QualificationState.STALE, QualificationState.EXPIRED, QualificationState.ERROR}
)

# Abandoning a run mid-flight is always legal; the reverse never is.
_RUN_ABORTS = frozenset({EvalRunState.CANCELLED, EvalRunState.ERROR})

RUN_TRANSITIONS: dict[EvalRunState, frozenset[EvalRunState]] = {
    EvalRunState.CREATED: frozenset({EvalRunState.TARGET_FROZEN, EvalRunState.BLOCKED}) | _RUN_ABORTS,
    EvalRunState.TARGET_FROZEN: frozenset({EvalRunState.READY, EvalRunState.BLOCKED}) | _RUN_ABORTS,
    EvalRunState.READY: frozenset({EvalRunState.RUNNING, EvalRunState.BLOCKED}) | _RUN_ABORTS,
    EvalRunState.RUNNING: frozenset({EvalRunState.GRADING, EvalRunState.BLOCKED}) | _RUN_ABORTS,
    EvalRunState.GRADING: frozenset(
        {EvalRunState.REVIEW_REQUIRED, EvalRunState.COMPLETED, EvalRunState.BLOCKED}
    ) | _RUN_ABORTS,
    EvalRunState.REVIEW_REQUIRED: frozenset(
        {EvalRunState.COMPLETED, EvalRunState.BLOCKED}
    ) | _RUN_ABORTS,
    EvalRunState.BLOCKED: frozenset({EvalRunState.READY}) | _RUN_ABORTS,
    EvalRunState.COMPLETED: frozenset(),
    EvalRunState.CANCELLED: frozenset(),
    EvalRunState.ERROR: frozenset(),
}

QUALIFICATION_TRANSITIONS: dict[QualificationState, frozenset[QualificationState]] = {
    QualificationState.PENDING: frozenset({
        QualificationState.QUALIFIED,
        QualificationState.ADVISORY_QUALIFIED,
        QualificationState.REJECTED,
        QualificationState.ERROR,
    }),
    QualificationState.QUALIFIED: frozenset(
        {QualificationState.STALE, QualificationState.EXPIRED}
    ),
    QualificationState.ADVISORY_QUALIFIED: frozenset(
        {QualificationState.STALE, QualificationState.EXPIRED}
    ),
    QualificationState.REJECTED: frozenset(
        {QualificationState.WAIVED, QualificationState.STALE}
    ),
    QualificationState.WAIVED: frozenset(
        {QualificationState.STALE, QualificationState.EXPIRED}
    ),
    QualificationState.STALE: frozenset(),
    QualificationState.EXPIRED: frozenset(),
    QualificationState.ERROR: frozenset(),
}


def _require_tenant(tenant_id: str) -> None:
    """A blank tenant is missing data, and the Universal Tenant Isolation
    Invariant says missing data fails loudly rather than landing in a shared
    scope. The DDL carries the same CHECK on all five tables."""
    if not tenant_id.strip():
        raise ValueError("tenant_id is required and may not be blank")


class IllegalTransition(ValueError):
    """A state edge that the lifecycle does not permit."""


class DiscardedFailure(ValueError):
    """An attempt whose failure would vanish from the aggregate."""


def assert_run_transition(current: EvalRunState, target: EvalRunState) -> None:
    if current == target:
        return
    if target not in RUN_TRANSITIONS[current]:
        raise IllegalTransition(f"illegal eval run transition {current.value} -> {target.value}")


def assert_qualification_transition(
    current: QualificationState, target: QualificationState
) -> None:
    if current == target:
        return
    if target not in QUALIFICATION_TRANSITIONS[current]:
        raise IllegalTransition(
            f"illegal qualification transition {current.value} -> {target.value}"
        )


@dataclass
class EvalRun(_KernelRow):
    """A durable execution of one suite version against one exact target."""
    suite_id: str = ""
    suite_version: str = ""
    suite_manifest_sha256: str = ""
    target_fingerprint: str = ""
    environment: str = ""
    execution_mode: ExecutionMode = ExecutionMode.DENY_EFFECTS
    state: EvalRunState = EvalRunState.CREATED
    actor_identity: str = ""
    actor_family: str = ""
    attempt_policy: dict = field(default_factory=dict)
    recovered_from_run_id: Optional[uuid.UUID] = None
    spend_microusd: int = 0
    latency_ms: int = 0
    provider_error_count: int = 0
    tool_error_count: int = 0
    evidence_ref: str = ""
    evidence_sha256: Optional[str] = None
    created_at: datetime = field(default_factory=_now)
    started_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require_tenant(self.tenant_id)

    def transition(self, target: EvalRunState, *, at: Optional[datetime] = None) -> "EvalRun":
        assert_run_transition(self.state, target)
        moment = at or _now()
        self.state = target
        if target == EvalRunState.RUNNING and self.started_at is None:
            self.started_at = moment
        if target in RUN_TERMINAL_STATES:
            self.ended_at = moment
        return self

    def to_row(self) -> dict:
        row = super().to_row()
        row["state"] = _row_value(self.state)
        row["execution_mode"] = _row_value(self.execution_mode)
        return row

    @classmethod
    def from_row(cls, row: dict):
        row = dict(row)
        if isinstance(row.get("state"), str):
            row["state"] = EvalRunState(row["state"])
        if isinstance(row.get("execution_mode"), str):
            row["execution_mode"] = ExecutionMode(row["execution_mode"])
        return super(EvalRun, cls).from_row(row)


@dataclass
class EvalCaseAttempt(_KernelRow):
    """One retained execution of one case. Retries append; they never replace.

    `counted_in_aggregate=False` is legal only for an infrastructure ERROR that
    states its exclusion_reason — a FAIL can never be excluded (directive §6.5,
    "No failed behavioral attempt may be deleted or silently excluded").
    """
    run_id: uuid.UUID = field(default_factory=_new_id)
    case_id: str = ""
    case_version: str = ""
    case_manifest_sha256: str = ""
    attempt_number: int = 1
    sampling_config: dict = field(default_factory=dict)
    input_sha256: str = ""
    fixture_sha256: str = ""
    trajectory_ref: str = ""
    trajectory_sha256: str = ""
    final_output_ref: str = ""
    forbidden_effect_attempts: int = 0
    result: ResultState = ResultState.INCONCLUSIVE
    counted_in_aggregate: bool = True
    exclusion_reason: str = ""
    retry_of_attempt_id: Optional[uuid.UUID] = None
    cost_microusd: int = 0
    latency_ms: int = 0
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime = field(default_factory=_now)
    recorded_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "EvalCaseAttempt":
        _require_tenant(self.tenant_id)
        if self.attempt_number < 1:
            raise ValueError("attempt_number starts at 1")
        if self.attempt_number > 1 and self.retry_of_attempt_id is None:
            raise ValueError("a retry attempt must name its predecessor")
        if not self.counted_in_aggregate:
            if self.result is not ResultState.ERROR:
                raise DiscardedFailure(
                    f"a {self.result.value} attempt cannot be excluded from the aggregate"
                )
            if not self.exclusion_reason.strip():
                raise DiscardedFailure("an excluded ERROR attempt must state its reason")
        elif self.exclusion_reason.strip():
            raise ValueError("a counted attempt cannot carry an exclusion reason")
        return self

    def to_row(self) -> dict:
        row = super().to_row()
        row["result"] = _row_value(self.result)
        return row

    @classmethod
    def from_row(cls, row: dict):
        row = dict(row)
        if isinstance(row.get("result"), str):
            row["result"] = ResultState(row["result"])
        return super(EvalCaseAttempt, cls).from_row(row)


@dataclass
class GraderResult(_KernelRow):
    """One grader's verdict on one attempt.

    `grader_family != subject_family` mirrors the DB CHECK on
    kernel_verification_attestations (ddl/009:41) — independence is structural.
    """
    attempt_id: uuid.UUID = field(default_factory=_new_id)
    grader_id: str = ""
    grader_version: str = ""
    grader_kind: GraderKind = GraderKind.STRUCTURAL
    grader_family: str = ""
    subject_family: str = ""
    result: ResultState = ResultState.INCONCLUSIVE
    criticality: Criticality = Criticality.NONCRITICAL
    reason: str = ""
    evidence_ref: str = ""
    evidence_sha256: Optional[str] = None
    error_detail: str = ""
    judge_model_id: str = ""
    rubric_id: str = ""
    rubric_version: str = ""
    rubric_sha256: Optional[str] = None
    adjudicator_identity: str = ""
    adjudicator_family: str = ""
    adjudicated_at: Optional[datetime] = None
    graded_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "GraderResult":
        _require_tenant(self.tenant_id)
        # Gate B pass 2 finding P2-N5, narrowed by pass 3 finding P3-N1:
        # independence is only meaningful where a verdict can affect
        # qualification. A semantic grader's family is derived from its judge
        # model, so judging a Claude target with a Claude judge — advisory by
        # construction (§4.2) — is the common case this exemption exists for.
        # Scoped to `grader_kind is SEMANTIC` specifically, not to
        # `criticality is ADVISORY` alone: semantic ⟹ advisory is enforced
        # below, but advisory does not imply semantic, so a bare criticality
        # check would let any grader kind dodge independence by declaring
        # itself advisory — it would still fail closed elsewhere (an
        # advisory-only attempt is never qualifiable), but for the wrong
        # reason, not because independence held.
        if (self.grader_family and self.grader_family == self.subject_family
                and self.grader_kind is not GraderKind.SEMANTIC):
            raise ValueError("grader family must differ from subject family")
        if self.adjudicator_identity and self.adjudicator_family == self.subject_family:
            raise ValueError("adjudicator family must differ from subject family")
        if self.grader_kind is GraderKind.SEMANTIC:
            if not (self.judge_model_id.strip() and self.rubric_id.strip()
                    and self.rubric_version.strip() and self.rubric_sha256):
                raise ValueError(
                    "a semantic grade requires judge model and versioned rubric identity"
                )
            if self.criticality is not Criticality.ADVISORY:
                raise ValueError(
                    "semantic grading stays advisory until EP-610 calibration lands (§4.2)"
                )
        if self.result is ResultState.ERROR and not self.error_detail.strip():
            raise ValueError("an ERROR grade requires error detail")
        # Gate B pass 3 finding P3-N2: mirrors the same check in graders.py so
        # an untyped adjudicated_at is caught at whichever layer constructs it.
        if self.adjudicated_at is not None and not isinstance(self.adjudicated_at, datetime):
            raise ValueError(
                f"adjudicated_at must be a datetime, not {type(self.adjudicated_at).__name__}")
        # Mirrors ddl/042_eval_plane_domain.sql's biconditional CHECK on
        # kernel_eval_grader_results: adjudicator identity and its timestamp travel
        # together, so a caller learns here rather than at INSERT, far from the cause.
        adjudicator_named = bool(self.adjudicator_identity.strip())
        if adjudicator_named and self.adjudicated_at is None:
            raise ValueError("an adjudicator identity requires adjudicated_at")
        if not adjudicator_named and self.adjudicated_at is not None:
            raise ValueError("adjudicated_at requires a named adjudicator_identity")
        # Mirrors the DDL's evidence CHECKs so a caller learns here rather than
        # at INSERT, where the cause is far from the symptom.
        if not self.reason.strip():
            raise ValueError("a grade requires a human-readable reason")
        graded = self.result in (ResultState.PASS, ResultState.FAIL)
        has_ref = bool(self.evidence_ref.strip())
        # Gate B pass 3 finding P3-B1: `bool(self.evidence_sha256)` treated `""`
        # and `None` as equally absent, but the DDL tests `IS NULL` — a blank
        # string is neither absent nor a valid digest, and this layer silently
        # accepted what the database would refuse. `is not None` routes `""`
        # into the format check below, which correctly rejects it.
        has_digest = self.evidence_sha256 is not None
        if graded and not (has_ref and has_digest):
            raise ValueError(
                "a PASS or FAIL requires a sha256-bound evidence reference; "
                "ERROR and INCONCLUSIVE carry error_detail and reason instead"
            )
        if has_ref != has_digest:
            raise ValueError(
                "evidence must be wholly absent or wholly hash-bound, never a "
                "reference without a digest"
            )
        if has_digest and not _SHA256.fullmatch(self.evidence_sha256):
            raise ValueError("evidence_sha256 must be a 64-character sha256 hex digest")
        return self

    def to_row(self) -> dict:
        row = super().to_row()
        row["grader_kind"] = _row_value(self.grader_kind)
        row["result"] = _row_value(self.result)
        row["criticality"] = _row_value(self.criticality)
        return row

    @classmethod
    def from_row(cls, row: dict):
        row = dict(row)
        for key, enum_type in (
            ("grader_kind", GraderKind),
            ("result", ResultState),
            ("criticality", Criticality),
        ):
            if isinstance(row.get(key), str):
                row[key] = enum_type(row[key])
        return super(GraderResult, cls).from_row(row)


@dataclass
class EvalQualification(_KernelRow):
    """The release-facing result. Case-level detail is never compressed away."""
    run_id: uuid.UUID = field(default_factory=_new_id)
    suite_id: str = ""
    suite_version: str = ""
    target_fingerprint: str = ""
    state: QualificationState = QualificationState.PENDING
    gate_mode: GateMode = GateMode.BLOCKING
    inconclusive_policy: InconclusivePolicy = InconclusivePolicy.BLOCK
    case_results: dict = field(default_factory=dict)
    aggregate_metrics: dict = field(default_factory=dict)
    critical_failure_count: int = 0
    error_count: int = 0
    inconclusive_count: int = 0
    forbidden_effect_count: int = 0
    baseline_qualification_id: Optional[uuid.UUID] = None
    reviewer_identity: str = ""
    reviewer_family: str = ""
    subject_family: str = ""
    receipt_ref: str = ""
    receipt_sha256: Optional[str] = None
    snapshot_sha256: str = ""
    expires_at: Optional[datetime] = None
    invalidated_at: Optional[datetime] = None
    invalidation_reason: str = ""
    created_at: datetime = field(default_factory=_now)
    decided_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require_tenant(self.tenant_id)

    def transition(
        self,
        target: QualificationState,
        *,
        at: Optional[datetime] = None,
        reason: str = "",
    ) -> "EvalQualification":
        assert_qualification_transition(self.state, target)
        moment = at or _now()
        if target in QUALIFYING_STATES:
            if self.critical_failure_count or self.error_count or self.forbidden_effect_count:
                raise IllegalTransition(
                    "qualification refused: critical failure, error, or forbidden effect on record"
                )
            if self.inconclusive_count and not (
                self.inconclusive_policy is InconclusivePolicy.REVIEW
                and self.reviewer_identity
            ):
                raise IllegalTransition(
                    "qualification refused: unresolved inconclusive case"
                )
            if target is QualificationState.QUALIFIED and self.gate_mode is GateMode.ADVISORY:
                raise IllegalTransition("an advisory gate cannot produce a QUALIFIED verdict")
        if target in (QualificationState.STALE, QualificationState.EXPIRED):
            if not reason.strip():
                raise ValueError("invalidation requires a recorded reason")
            self.invalidated_at = moment
            self.invalidation_reason = reason
        self.state = target
        if target is not QualificationState.PENDING:
            self.decided_at = moment
        return self

    def to_row(self) -> dict:
        row = super().to_row()
        row["state"] = _row_value(self.state)
        row["gate_mode"] = _row_value(self.gate_mode)
        row["inconclusive_policy"] = _row_value(self.inconclusive_policy)
        return row

    @classmethod
    def from_row(cls, row: dict):
        row = dict(row)
        for key, enum_type in (
            ("state", QualificationState),
            ("gate_mode", GateMode),
            ("inconclusive_policy", InconclusivePolicy),
        ):
            if isinstance(row.get(key), str):
                row[key] = enum_type(row[key])
        return super(EvalQualification, cls).from_row(row)


@dataclass
class EvalWaiver(_KernelRow):
    """One named authority excusing one named failed requirement, time-bounded."""
    qualification_id: uuid.UUID = field(default_factory=_new_id)
    target_fingerprint: str = ""
    failed_suite_id: str = ""
    failed_case_id: str = ""
    failed_grader_id: str = ""
    founder_approval_id: uuid.UUID = field(default_factory=_new_id)
    approver_identity: str = ""
    rationale: str = ""
    evidence_ref: str = ""
    evidence_sha256: str = ""
    expires_at: Optional[datetime] = None
    release_bound_to: str = ""
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "EvalWaiver":
        _require_tenant(self.tenant_id)
        if not (self.failed_suite_id and self.failed_case_id and self.failed_grader_id):
            raise ValueError("a waiver names exactly one failed suite/case/grader requirement")
        if not (self.approver_identity.strip() and self.rationale.strip()
                and self.evidence_ref.strip()):
            raise ValueError("a waiver requires a named approver, rationale, and evidence")
        if self.expires_at is None and not self.release_bound_to.strip():
            raise ValueError("a waiver must be time-limited or release-limited")
        return self


@dataclass(frozen=True)
class SuitePolicy:
    """The suite's declared aggregation policy (EP-140 owns the manifest)."""
    gate_mode: GateMode = GateMode.BLOCKING
    inconclusive_policy: InconclusivePolicy = InconclusivePolicy.BLOCK
    noncritical_pass_threshold: float = 1.0



# The §7.3 precedence *decision* lives in graders.aggregate(), not here. This
# module is the record layer that graders.py builds on, so it cannot import
# upward without a cycle — and a second implementation of the release gate would
# be free to drift from the first. What stays here is enforcement, not decision:
# EvalQualification.transition() refuses counters that do not support a verdict,
# and the DDL trigger refuses one the grader rows do not support. Neither
# computes a verdict, so neither can disagree with aggregate() about one.
def assert_no_discarded_failures(attempts: Iterable[EvalCaseAttempt]) -> None:
    """Every retained attempt must be either counted or an explained ERROR."""
    for attempt in attempts:
        attempt.validate()


ALL_EVAL_SCHEMAS = (
    EvalRun, EvalCaseAttempt, GraderResult, EvalQualification, EvalWaiver,
)
