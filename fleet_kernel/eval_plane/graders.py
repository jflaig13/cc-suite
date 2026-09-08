# SPDX-License-Identifier: MPL-2.0
"""EP-130 — grader interfaces, result semantics, and aggregation precedence.

Governing contract: `fleet_kernel/fleet_kernel_evaluation_plane_full_project_directive.md`
(§6.6 `GraderResult`, §7.3 aggregate precedence, §11 grader stack, §12 attempt policy) and
the accepted Gate A packet `docs/brain/080926__ep040-phase0-synthesis-gate-a.md` §8, which
directs EP-130 to reuse existing proven primitives rather than build a grader engine.

Reused (not rebuilt):

  * `run_grader` is `scripts/stageb_payroll_run.py:_safe_gate` (line 1654) applied to
    graders: a raise becomes a named ERROR carrying that grader's own id, never an
    aborted run and never a silent pass.
  * `GraderResult` follows `fleet_kernel/m10/prove99.py:GateResult` (line 57) — frozen,
    slotted, self-validating in `__post_init__`, so a structurally invalid verdict
    cannot be constructed at all.
  * The whole shared vocabulary (`ResultState`, `Criticality`, `GateMode`,
    `InconclusivePolicy`, `SuitePolicy`) is imported from EP-100's
    `fleet_kernel/eval_plane/domain.py` rather than redefined here.
  * R7 grader independence mirrors the DB CHECK `verifier_family <> subject_family` in
    `fleet_kernel/ddl/009_m8_verification_observability.sql`, and the same-family
    receipt refusal in `scripts/stageb_payroll_run.py:gate_cmcv` (line 1435). The DDL
    CHECK stays the authority; this is its in-process mirror at aggregation time.
  * `model_family` is the behavior of `stageb_payroll_run.py:_model_family` (line 1267).

Layering against EP-100: `domain.GraderResult` is the persisted Kernel row (uuid keys,
`to_row`/`from_row`). The `GraderResult` here is the in-process verdict a grader returns.
`to_domain_result` is the one bridge between them, so the two shapes cannot drift apart
silently.
"""
from __future__ import annotations

import abc
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from fleet_kernel.eval_plane import domain, effect_control
from fleet_kernel.eval_plane.domain import (
    Criticality,
    GateMode,
    GraderKind,
    InconclusivePolicy,
    ResultState,
    SuitePolicy,
)

__all__ = [
    "PrecedenceRule", "AttemptAggregateRule",
    "DETERMINISTIC_KINDS", "DETERMINISTIC_FAMILY",
    "GraderResult", "GradingContext", "Grader",
    "StructuralGrader", "DeterministicTrajectoryGrader", "DeterministicOutcomeGrader",
    "SemanticGrader", "HumanReviewGrader",
    "run_grader", "run_graders", "model_family", "to_domain_result", "requirement_ref",
    "AttemptPolicy", "CaseAttemptResult", "CaseResult", "attempt_from_effect_ledger",
    "BlockingFinding", "AggregateOutcome", "aggregate",
    "canonical_json", "sha256_hex",
    # re-exported from domain so callers have one import site for the vocabulary
    "ResultState", "Criticality", "GateMode", "GraderKind", "InconclusivePolicy",
    "SuitePolicy",
]


class AttemptAggregateRule(str, Enum):
    ALL_MUST_PASS = "all_must_pass"
    THRESHOLD = "threshold"


class PrecedenceRule(str, Enum):
    """Directive §7.3 rules R1-R5 verbatim, plus two structural additions.

    R6 closes the retry-laundering hole §3.7 names but §7.3 does not mechanize; R7 is
    the in-process mirror of the DDL 009 independence CHECK that Gate A §8 directs
    EP-130 to reuse rather than re-implement.
    """
    R1_CRITICAL_DETERMINISTIC_FAILURE = "R1"
    R2_UNRESOLVED_REQUIRED_ERROR = "R2"
    R3_REQUIRED_INCONCLUSIVE = "R3"
    R4_NONCRITICAL_THRESHOLD = "R4"
    R5_FORBIDDEN_EFFECT_ATTEMPT = "R5"
    R6_ATTEMPT_LINEAGE_INCOMPLETE = "R6"
    R7_GRADER_INDEPENDENCE = "R7"


DETERMINISTIC_KINDS = frozenset(
    {GraderKind.STRUCTURAL, GraderKind.TRAJECTORY, GraderKind.OUTCOME})

# Deterministic graders execute as code, not as a model, so they can never collide
# with a model-family subject under R7.
DETERMINISTIC_FAMILY = "deterministic"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_MODEL_FAMILY_PREFIXES = (
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("gemini", "google"),
)


def model_family(model_id: str) -> str:
    """Coarse vendor family; an unrecognized id maps to its own lowercased string.

    Behavior of `scripts/stageb_payroll_run.py:_model_family` (line 1267) — failing
    safe, since an unknown id can never accidentally match a known family.
    """
    lowered = (model_id or "").lower()
    for prefix, family in _MODEL_FAMILY_PREFIXES:
        if lowered.startswith(prefix):
            return family
    return lowered


def requirement_ref(suite_id: str, case_id: str, grader_id: str = "*") -> str:
    """The one address format a waiver must name exactly (§6.8).

    `grader_id="*"` addresses the whole case. A waiver author has to write the star
    literally — there is no wildcard matching, so a grader-scoped waiver can never
    silently widen to the case.
    """
    return f"{suite_id}::{case_id}::{grader_id}"


# ------------------------------------------------------------- canonical form

def _json_default(item: object) -> object:
    if isinstance(item, Enum):
        return item.value
    if isinstance(item, datetime):
        return item.astimezone(timezone.utc).isoformat()
    if isinstance(item, date):
        return item.isoformat()
    if isinstance(item, Mapping):
        return dict(item)
    raise TypeError(f"unsupported canonical value: {type(item).__name__}")


def canonical_json(value: object) -> bytes:
    """Canonical JSON, byte-identical to `fleet_kernel/m10/prove99.py:_canonical`."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False, default=_json_default,
    ).encode("utf-8")


def sha256_hex(value: object) -> str:
    raw = value if isinstance(value, bytes) else canonical_json(value)
    return hashlib.sha256(raw).hexdigest()


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _nonblank(value: str) -> bool:
    return bool(value and value.strip())


# ------------------------------------------------------------- grader results

@dataclass(frozen=True, slots=True)
class GraderResult:
    """Directive §6.6 — one grader's in-process verdict on one attempt.

    Self-validating like `prove99.GateResult`: an invalid combination raises at
    construction, and `run_grader` converts that raise into a fail-closed ERROR.
    """

    grader_id: str
    grader_version: str
    kind: GraderKind
    state: ResultState
    reason: str
    criticality: Criticality = Criticality.NONCRITICAL
    evidence_ref: str = ""
    evidence_sha256: str = ""
    error_detail: str = ""
    grader_family: str = DETERMINISTIC_FAMILY
    judge_model_id: str = ""
    rubric_id: str = ""
    rubric_version: str = ""
    rubric_sha256: str = ""
    adjudicator_identity: str = ""
    adjudicator_family: str = ""
    adjudicated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not (_nonblank(self.grader_id) and _nonblank(self.grader_version)):
            raise ValueError("grader result requires a non-blank id and version")
        if not isinstance(self.kind, GraderKind) or not isinstance(self.state, ResultState):
            raise ValueError("grader result requires typed kind and state")
        if not isinstance(self.criticality, Criticality):
            raise ValueError("grader result requires a typed criticality")
        if not _nonblank(self.reason):
            raise ValueError("grader result requires a human-readable reason")
        if not _nonblank(self.grader_family):
            raise ValueError("grader result requires a non-blank grader family")
        if self.state is ResultState.ERROR and not _nonblank(self.error_detail):
            raise ValueError("ERROR requires error_detail naming the infrastructure fault")
        if self.state is not ResultState.ERROR and _nonblank(self.error_detail):
            raise ValueError("error_detail is only meaningful on ERROR")
        # §10.3: a grade must retain enough secured evidence to reproduce it, content-
        # hashed per the repo's canonical-JSON -> SHA-256 convention. PASS and FAIL are
        # grades. ERROR and INCONCLUSIVE are the absence of one — they carry
        # error_detail or a reason instead, and requiring an artifact there would make
        # the fail-closed verdict unrecordable, which is the exact regression
        # `_safe_gate` exists to prevent. Mirrors the two CHECKs on
        # `kernel_eval_grader_results` in ddl/042_eval_plane_domain.sql:159-163.
        has_ref = _nonblank(self.evidence_ref)
        has_digest = _nonblank(self.evidence_sha256)
        if has_digest and not _SHA256_RE.match(self.evidence_sha256):
            raise ValueError("evidence_sha256 must be a 64-character sha256 hex digest")
        if has_ref != has_digest:
            raise ValueError(
                "evidence must be wholly absent or wholly hash-bound: a reference "
                "without a digest is unverifiable, and a digest without a reference "
                "points at nothing")
        if self.state in (ResultState.PASS, ResultState.FAIL) and not has_ref:
            raise ValueError(
                f"a {self.state.value} grade requires a machine-readable evidence "
                f"reference bound to a sha256 digest")
        # V1 keeps semantic grading advisory-only (§4.2): no calibration set, no known
        # disagreement behavior, no injection testing yet. Matches the same refusal in
        # `domain.GraderResult.validate`, so the in-process verdict and the persisted
        # row cannot disagree about whether a judge may block a release.
        if self.kind is GraderKind.SEMANTIC:
            if self.criticality is not Criticality.ADVISORY:
                raise ValueError(
                    "semantic grading stays advisory until EP-610 calibration lands (§4.2)")
            if not (_nonblank(self.judge_model_id) and _nonblank(self.rubric_id)
                    and _nonblank(self.rubric_version) and _nonblank(self.rubric_sha256)):
                raise ValueError(
                    "a semantic verdict requires a pinned judge model and versioned rubric")
        # ERROR carries error_detail instead of a verdict (same reasoning as the
        # evidence requirement above), so an unadjudicated ERROR — e.g. an
        # adjudication record missing its timestamp — is not itself an adjudicated
        # review and must not be forced to name an adjudicator to exist.
        if self.kind is GraderKind.HUMAN \
                and self.state not in (ResultState.INCONCLUSIVE, ResultState.ERROR) \
                and not _nonblank(self.adjudicator_identity):
            raise ValueError("an adjudicated human review requires a named adjudicator")
        # Gate B pass 3 finding P3-N2: an untyped `adjudicated_at` (an ISO
        # string, an int, a bare object) passed every existing check here and
        # bridged cleanly, then broke `to_dict()`'s `.isoformat()` call later —
        # a qualification-building path, far from this construction site.
        if self.adjudicated_at is not None and not isinstance(self.adjudicated_at, datetime):
            raise ValueError(
                f"adjudicated_at must be a datetime, not {type(self.adjudicated_at).__name__}")
        # Mirrors ddl/042_eval_plane_domain.sql's biconditional CHECK on
        # kernel_eval_grader_results: adjudicator identity and its timestamp travel
        # together or not at all, so an unrecordable row is caught here rather than
        # discovered at INSERT, far from its cause.
        if _nonblank(self.adjudicator_identity) and self.adjudicated_at is None:
            raise ValueError(
                "an adjudicator identity requires the adjudication timestamp "
                "(adjudicated_at) — a recorded adjudication that names no time is "
                "itself incomplete evidence")
        if not _nonblank(self.adjudicator_identity) and self.adjudicated_at is not None:
            raise ValueError(
                "adjudicated_at requires a named adjudicator_identity")

    @property
    def blocking_eligible(self) -> bool:
        """Whether this verdict may contribute to a blocking finding.

        ADVISORY verdicts are excluded from aggregation entirely (domain.Criticality),
        and a semantic verdict can only ever be ADVISORY — so no configuration puts a
        V1 judge model in the release path.
        """
        return self.criticality is not Criticality.ADVISORY

    def to_dict(self) -> dict[str, Any]:
        return {
            "grader_id": self.grader_id, "grader_version": self.grader_version,
            "kind": self.kind.value, "state": self.state.value, "reason": self.reason,
            "criticality": self.criticality.value,
            "evidence_ref": self.evidence_ref, "evidence_sha256": self.evidence_sha256,
            "error_detail": self.error_detail,
            "grader_family": self.grader_family, "judge_model_id": self.judge_model_id,
            "rubric_id": self.rubric_id, "rubric_version": self.rubric_version,
            "rubric_sha256": self.rubric_sha256,
            "adjudicator_identity": self.adjudicator_identity,
            "adjudicated_at": self.adjudicated_at.isoformat() if self.adjudicated_at else None,
            "blocking_eligible": self.blocking_eligible,
        }


def to_domain_result(
    result: GraderResult, *, tenant_id: str, attempt_id, subject_family: str,
) -> domain.GraderResult:
    """Bridge the in-process verdict onto EP-100's persisted Kernel row.

    `tenant_id` is required rather than defaulted: it is the RLS scope key on every
    kernel table, and a silent default would be a tenant-isolation hazard.
    """
    if not _nonblank(tenant_id):
        raise ValueError("persisting a grader result requires its tenant scope")
    return domain.GraderResult(
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        grader_id=result.grader_id,
        grader_version=result.grader_version,
        grader_kind=result.kind,
        grader_family=result.grader_family,
        subject_family=subject_family,
        result=result.state,
        criticality=result.criticality,
        reason=result.reason,
        evidence_ref=result.evidence_ref,
        # Gate B pass 3 finding P3-B1: this module's "absent" is `""`, the
        # record layer's is `NULL`, and the DDL tests with `IS NULL` — an
        # unnormalized `""` made every ERROR and INCONCLUSIVE verdict
        # (the entire run_grader fail-closed path) unrecordable. Mirrors the
        # rubric_sha256 normalization five lines below.
        evidence_sha256=result.evidence_sha256 or None,
        error_detail=result.error_detail,
        judge_model_id=result.judge_model_id,
        rubric_id=result.rubric_id,
        rubric_version=result.rubric_version,
        rubric_sha256=result.rubric_sha256 or None,
        adjudicator_identity=result.adjudicator_identity,
        adjudicator_family=result.adjudicator_family,
        adjudicated_at=result.adjudicated_at,
    )


@dataclass(frozen=True, slots=True)
class GradingContext:
    """Read-only view of one case attempt.

    Every nested container is frozen (§11.6: a grader may not mutate the target, case,
    or expected behavior). A grader that tries gets a TypeError, which `run_grader`
    converts to ERROR — the mutation attempt surfaces instead of corrupting the exam.
    """

    case_id: str
    attempt_number: int
    target_fingerprint: str
    expected: Mapping[str, Any] = field(default_factory=dict)
    observed: Mapping[str, Any] = field(default_factory=dict)
    trajectory: tuple[Mapping[str, Any], ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _nonblank(self.case_id):
            raise ValueError("grading context requires a case id")
        if self.attempt_number < 1:
            raise ValueError("attempt numbers are 1-based")
        object.__setattr__(self, "expected", _freeze(dict(self.expected)))
        object.__setattr__(self, "observed", _freeze(dict(self.observed)))
        object.__setattr__(self, "trajectory", _freeze(tuple(self.trajectory)))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


# -------------------------------------------------------------------- graders

class Grader(abc.ABC):
    """Directive §11. Subclasses implement `grade`; callers always go through
    `run_grader`, never `grade` directly."""

    kind: GraderKind

    def __init__(
        self,
        grader_id: str,
        grader_version: str,
        *,
        criticality: Criticality = Criticality.CRITICAL,
        grader_family: str = DETERMINISTIC_FAMILY,
    ) -> None:
        if not (_nonblank(grader_id) and _nonblank(grader_version)):
            raise ValueError("a grader requires a non-blank id and version")
        if not _nonblank(grader_family):
            raise ValueError("a grader requires a non-blank family")
        # `kind` and `criticality` are harvested off the grader object by
        # `run_grader`'s fallback path, where a raise would defeat the fail-closed
        # guarantee. Rejecting an untyped value here means the malformed grader never
        # reaches a run at all; `run_grader` still substitutes safe defaults for one
        # built some other way.
        kind = getattr(self, "kind", None)
        if not isinstance(kind, GraderKind):
            raise ValueError(
                f"a grader requires a typed GraderKind class attribute, not {kind!r}")
        if not isinstance(criticality, Criticality):
            raise ValueError(
                f"a grader requires a typed Criticality, not {criticality!r}")
        self.grader_id = grader_id
        self.grader_version = grader_version
        self.criticality = criticality
        self.grader_family = grader_family

    @abc.abstractmethod
    def grade(self, context: GradingContext) -> GraderResult:
        ...

    def _result(
        self, state: ResultState, reason: str, *,
        evidence_ref: str = "", evidence_sha256: str = "",
        error_detail: str = "", **extra: Any,
    ) -> GraderResult:
        return GraderResult(
            grader_id=self.grader_id, grader_version=self.grader_version,
            kind=self.kind, state=state, reason=reason, criticality=self.criticality,
            evidence_ref=evidence_ref, evidence_sha256=evidence_sha256,
            error_detail=error_detail, grader_family=self.grader_family, **extra,
        )


class StructuralGrader(Grader):
    """§11.1 — schema validity, required fields, tenant/release identity, evidence
    references, state transitions, parseability."""
    kind = GraderKind.STRUCTURAL


class DeterministicTrajectoryGrader(Grader):
    """§11.2 — correct skill selected, required deterministic tool called, forbidden
    tool attempted, active policy fetched, correct approval path used."""
    kind = GraderKind.TRAJECTORY


class DeterministicOutcomeGrader(Grader):
    """§11.3 — correct cents and employee set, exact artifact bytes, status vocabulary,
    source ids, no external effect, no cross-tenant record."""
    kind = GraderKind.OUTCOME


class SemanticGrader(Grader):
    """§11.4 interface only — V1 ships no judge (§4.2, Gate A §8).

    Criticality is forced to ADVISORY at construction, so no subclass can configure
    itself into the release path.
    """

    kind = GraderKind.SEMANTIC

    def __init__(
        self, grader_id: str, grader_version: str, *,
        judge_model_id: str, rubric_id: str, rubric_version: str, rubric_sha256: str,
        grader_family: str = "",
    ) -> None:
        if not (_nonblank(judge_model_id) and _nonblank(rubric_id)
                and _nonblank(rubric_version) and _nonblank(rubric_sha256)):
            raise ValueError(
                "a semantic grader requires a pinned judge model and a versioned rubric")
        super().__init__(
            grader_id, grader_version, criticality=Criticality.ADVISORY,
            grader_family=grader_family or model_family(judge_model_id),
        )
        self.judge_model_id = judge_model_id
        self.rubric_id = rubric_id
        self.rubric_version = rubric_version
        self.rubric_sha256 = rubric_sha256

    @abc.abstractmethod
    def judge(self, context: GradingContext) -> tuple[ResultState, str, str, str]:
        """Return (state, reason, evidence_ref, evidence_sha256).

        No judge implementation ships in V1.
        """

    def grade(self, context: GradingContext) -> GraderResult:
        state, reason, evidence_ref, evidence_sha256 = self.judge(context)
        return self._result(
            state, reason, evidence_ref=evidence_ref, evidence_sha256=evidence_sha256,
            judge_model_id=self.judge_model_id, rubric_id=self.rubric_id,
            rubric_version=self.rubric_version, rubric_sha256=self.rubric_sha256,
        )


class HumanReviewGrader(Grader):
    """§11.5 — reads a recorded human adjudication.

    With no adjudication recorded the verdict is INCONCLUSIVE, never PASS: an
    un-reviewed case routes to review (§7.3 R3) rather than defaulting green.
    """

    kind = GraderKind.HUMAN

    def __init__(
        self, grader_id: str, grader_version: str, *,
        adjudications: Mapping[str, Mapping[str, Any]],
        criticality: Criticality = Criticality.CRITICAL,
        grader_family: str = "human",
    ) -> None:
        super().__init__(
            grader_id, grader_version, criticality=criticality,
            grader_family=grader_family)
        self._adjudications = _freeze(dict(adjudications))

    def grade(self, context: GradingContext) -> GraderResult:
        record = self._adjudications.get(context.case_id)
        if record is None:
            return self._result(
                ResultState.INCONCLUSIVE,
                f"no human adjudication recorded for case {context.case_id!r}")
        adjudicator_identity = str(record.get("adjudicator_identity", ""))
        adjudicated_at = record.get("adjudicated_at")
        if adjudicator_identity and adjudicated_at is None:
            # A named adjudicator with no recorded time is incomplete evidence,
            # not a technicality — surface it as the fault it is rather than
            # silently dropping the timestamp and failing later at persistence.
            return self._result(
                ResultState.ERROR,
                f"adjudication for case {context.case_id!r} names adjudicator "
                f"{adjudicator_identity!r} but records no adjudicated_at",
                error_detail="human adjudication record is missing adjudicated_at",
            )
        return self._result(
            ResultState(record["state"]), str(record["reason"]),
            evidence_ref=str(record.get("evidence_ref", "")),
            evidence_sha256=str(record.get("evidence_sha256", "")),
            adjudicator_identity=adjudicator_identity,
            adjudicator_family=str(record.get("adjudicator_family", "")),
            adjudicated_at=adjudicated_at,
        )


def run_grader(grader: Grader, context: GradingContext) -> GraderResult:
    """Fail-closed grader invocation — `_safe_gate` (stageb_payroll_run.py:1654) for
    graders.

    Every way a grader can fail becomes ERROR under that grader's own id rather than a
    crash or a silent pass: it raises; it returns something that is not a
    `GraderResult`; or it builds a verdict that violates the result contract — which
    includes a PASS or FAIL carrying no content-hashed evidence, since `GraderResult`
    refuses to construct one (§11.6: missing required evidence is not silently ignored).

    Totality covers the grader's *metadata* as well as its `grade()` body. A grader
    carrying an untyped `kind` or `criticality` once made the fallback construction
    itself raise, which turned one malformed grader into an aborted run whose other
    cases were never graded — the exact regression `_safe_gate` exists to prevent. The
    bad value is replaced by a fail-closed default and folded into `error_detail`, so
    the information is recorded rather than lost.
    """
    faults: list[str] = []

    def _attr(name: str, default: Any) -> Any:
        # `getattr(obj, name, default)` swallows only AttributeError; a property or
        # `__getattr__` that raises anything else would escape the harvest entirely.
        try:
            return getattr(grader, name, default)
        except Exception as exc:
            faults.append(f"grader.{name} is unreadable: {type(exc).__name__}: {exc}")
            return default

    def _text(name: str, default: str) -> str:
        try:
            return str(_attr(name, "") or default)
        except Exception as exc:
            faults.append(f"grader.{name} is unprintable: {type(exc).__name__}: {exc}")
            return default

    grader_id = _text("grader_id", "unknown-grader")
    grader_version = _text("grader_version", "unknown")
    family = _text("grader_family", DETERMINISTIC_FAMILY)

    kind = _attr("kind", None)
    if not isinstance(kind, GraderKind):
        faults.append(
            f"grader.kind is not a GraderKind ({kind!r}); graded as "
            f"{GraderKind.STRUCTURAL.value}")
        kind = GraderKind.STRUCTURAL
    # An unreadable grader is treated as critical: an ERROR in a required case blocks
    # under R2 regardless, and guessing "noncritical" here would be the fail-open guess.
    criticality = _attr("criticality", None)
    if not isinstance(criticality, Criticality):
        faults.append(
            f"grader.criticality is not a Criticality ({criticality!r}); graded as "
            f"{Criticality.CRITICAL.value}")
        criticality = Criticality.CRITICAL
    if kind is GraderKind.SEMANTIC:
        criticality = Criticality.ADVISORY

    def _error(detail: str) -> GraderResult:
        semantic = kind is GraderKind.SEMANTIC
        judge = _text("judge_model_id", "unknown-judge") if semantic else ""
        rubric = _text("rubric_id", "unknown-rubric") if semantic else ""
        rubric_version = _text("rubric_version", "unknown") if semantic else ""
        rubric_sha = _text("rubric_sha256", "unknown-rubric-sha") if semantic else ""
        if faults:
            detail = f"{detail} [grader metadata invalid: {'; '.join(faults)}]"
        try:
            return GraderResult(
                grader_id=grader_id, grader_version=grader_version, kind=kind,
                state=ResultState.ERROR,
                reason=f"grader {grader_id} did not produce a verdict",
                criticality=criticality, error_detail=detail, grader_family=family,
                judge_model_id=judge, rubric_id=rubric,
                rubric_version=rubric_version, rubric_sha256=rubric_sha,
            )
        except Exception as exc:
            # Last resort: every field a literal, so this construction cannot fail.
            # An unrecordable ERROR would be a silent pass, which is the one outcome
            # this function may never produce.
            return GraderResult(
                grader_id="unknown-grader", grader_version="unknown",
                kind=GraderKind.STRUCTURAL, state=ResultState.ERROR,
                reason="grader did not produce a verdict",
                criticality=Criticality.CRITICAL,
                grader_family=DETERMINISTIC_FAMILY,
                error_detail=(
                    f"{detail} [fallback verdict rebuilt after "
                    f"{type(exc).__name__}: {exc}]"),
            )

    def _describe(build: Callable[[], str], fallback: str) -> str:
        # Gate B pass 2 finding P2-N1: rendering the fault message is itself
        # untrusted input — an exception whose __str__ raises, or a result whose
        # type name is unreadable, must not escape run_grader before `_error`'s
        # own fail-closed construction ever runs.
        try:
            return build()
        except Exception as render_exc:
            return f"{fallback} (fault detail unrenderable: {type(render_exc).__name__})"

    try:
        result = grader.grade(context)
    except Exception as exc:
        return _error(_describe(
            lambda: f"grader raised an exception: {type(exc).__name__}: {exc}",
            "grader raised an exception"))

    # Gate B pass 3 finding P3-N4: `isinstance` consults `__class__`, which a
    # hostile return value can make raise. Keep that path fail-closed inside
    # run_grader rather than letting it escape past the totality claim.
    try:
        is_grader_result = isinstance(result, GraderResult)
    except Exception as exc:
        return _error(_describe(
            lambda: (
                f"grader returned an uninspectable result "
                f"({type(exc).__name__}: {exc})"
            ),
            "grader returned an uninspectable result",
        ))
    if not is_grader_result:
        return _error(_describe(
            lambda: f"grader returned {type(result).__name__}, not a GraderResult",
            "grader returned something that is not a GraderResult"))
    return result


def run_graders(
    graders: Sequence[Grader], context: GradingContext,
) -> tuple[GraderResult, ...]:
    return tuple(run_grader(grader, context) for grader in graders)


# -------------------------------------------------------- attempts and cases

@dataclass(frozen=True, slots=True)
class AttemptPolicy:
    """Directive §12 — predeclared before the run, never inferred from results."""

    attempts: int
    aggregate_rule: AttemptAggregateRule = AttemptAggregateRule.ALL_MUST_PASS
    min_pass_rate: float | None = None
    sampling_config_digest: str = ""
    infrastructure_retry_limit: int = 0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("a case must predeclare at least one attempt")
        if self.infrastructure_retry_limit < 0:
            raise ValueError("infrastructure_retry_limit cannot be negative")
        if self.aggregate_rule is AttemptAggregateRule.THRESHOLD:
            if self.min_pass_rate is None or not (0.0 < self.min_pass_rate <= 1.0):
                raise ValueError("THRESHOLD requires min_pass_rate in (0.0, 1.0]")
        elif self.min_pass_rate is not None:
            raise ValueError("min_pass_rate is only meaningful under THRESHOLD")

    def digest(self) -> str:
        return sha256_hex({
            "attempts": self.attempts, "aggregate_rule": self.aggregate_rule.value,
            "min_pass_rate": self.min_pass_rate,
            "sampling_config_digest": self.sampling_config_digest,
            "infrastructure_retry_limit": self.infrastructure_retry_limit,
        })


@dataclass(frozen=True, slots=True)
class CaseAttemptResult:
    """Directive §6.5 — one retained execution. Retries append; they never replace."""

    case_id: str
    attempt_number: int
    grader_results: tuple[GraderResult, ...]
    forbidden_effect_attempted: bool = False
    forbidden_effect_detail: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0

    def __post_init__(self) -> None:
        if not _nonblank(self.case_id):
            raise ValueError("attempt requires a case id")
        if self.attempt_number < 1:
            raise ValueError("attempt numbers are 1-based")
        object.__setattr__(self, "grader_results", tuple(self.grader_results))
        if self.forbidden_effect_attempted and not _nonblank(self.forbidden_effect_detail):
            raise ValueError(
                "a recorded forbidden-effect attempt must name what was attempted")

    @property
    def blocking_results(self) -> tuple[GraderResult, ...]:
        return tuple(r for r in self.grader_results if r.blocking_eligible)

    @property
    def state(self) -> ResultState:
        """FAIL > ERROR > INCONCLUSIVE > PASS over non-advisory verdicts.

        FAIL outranks ERROR because an observed behavioral failure is real evidence
        that an unrelated grader's infrastructure fault must not erase. An attempt
        with no blocking-eligible verdict at all is INCONCLUSIVE, never PASS —
        advisory semantic results alone can never certify an attempt.
        """
        blocking = self.blocking_results
        if not blocking:
            return ResultState.INCONCLUSIVE
        states = {r.state for r in blocking}
        for candidate in (ResultState.FAIL, ResultState.ERROR, ResultState.INCONCLUSIVE):
            if candidate in states:
                return candidate
        return ResultState.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "attempt_number": self.attempt_number,
            "state": self.state.value,
            "grader_results": [r.to_dict() for r in self.grader_results],
            "forbidden_effect_attempted": self.forbidden_effect_attempted,
            "forbidden_effect_detail": self.forbidden_effect_detail,
            "cost_usd": self.cost_usd, "latency_ms": self.latency_ms,
        }


def attempt_from_effect_ledger(
    *,
    case_id: str,
    attempt_number: int,
    grader_results: Sequence[GraderResult],
    ledger: "effect_control.EffectLedger",
    authorized_chokepoints: Sequence[str] = (),
    cost_usd: float = 0.0,
    latency_ms: int = 0,
) -> CaseAttemptResult:
    """Build an attempt whose `forbidden_effect_attempted` comes from EP-120's ledger.

    R5 reads `CaseAttemptResult.forbidden_effect_attempted`. Without this adapter
    nothing in the plane derived that bool from an actual `EffectLedger`, so R5 was
    reading a flag that was always False unless a caller set it by hand — a blocking
    rule with no wired signal.

    Fail-closed on EP-120's two-value string vocabulary: only the exact `"PASS"`
    outcome clears the attempt, so an unrecognized or future outcome records a
    forbidden effect rather than silently passing. `authorized_chokepoints` is the
    case's explicit allowlist; empty means no external effect is authorized.
    """
    verdict = effect_control.grade_effect_attempts(
        ledger, authorized_chokepoints=tuple(authorized_chokepoints))
    forbidden = verdict.outcome != "PASS"
    return CaseAttemptResult(
        case_id=case_id,
        attempt_number=attempt_number,
        grader_results=tuple(grader_results),
        forbidden_effect_attempted=forbidden,
        forbidden_effect_detail=verdict.reason if forbidden else "",
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    required: bool
    criticality: Criticality
    policy: AttemptPolicy
    attempts: tuple[CaseAttemptResult, ...]

    def __post_init__(self) -> None:
        if not _nonblank(self.case_id):
            raise ValueError("case result requires a case id")
        object.__setattr__(self, "attempts", tuple(self.attempts))
        if not self.attempts:
            raise ValueError("a case result must retain at least one attempt")
        if any(a.case_id != self.case_id for a in self.attempts):
            raise ValueError("every retained attempt must belong to this case")
        numbers = [a.attempt_number for a in self.attempts]
        if len(set(numbers)) != len(numbers):
            raise ValueError("retained attempts must have distinct attempt numbers")
        # §3.7 anti-laundering: a critical case may never be graded on an average,
        # because a threshold is exactly the mechanism that would let a critical
        # failure be voted away by its sibling attempts.
        if self.criticality is Criticality.CRITICAL \
                and self.policy.aggregate_rule is not AttemptAggregateRule.ALL_MUST_PASS:
            raise ValueError(
                "a critical case must use ALL_MUST_PASS; a threshold rule would let "
                "passing attempts average away a critical failure")
        # R1 blocks on a critical FAIL whether or not the case is required, so a
        # critical case is required in fact. Allowing the combination to be declared
        # opened a hole: R2/R3 are gated on `required`, so a critical case whose every
        # attempt ERRORed produced no finding at all and aggregated green.
        if self.criticality is Criticality.CRITICAL and not self.required:
            raise ValueError(
                "a critical case is required by definition — R1 blocks on its failure "
                "regardless, and required=False would exempt it from R2/R3, letting an "
                "all-ERROR critical case aggregate clean")

    @property
    def retained_attempts(self) -> int:
        return len(self.attempts)

    @property
    def pass_rate(self) -> float:
        """Passes over *every* retained attempt.

        ERROR attempts stay in the denominator on purpose: excluding them would make
        "call it infrastructure" a way to raise a pass rate (§3.6/§3.7).
        """
        passed = sum(1 for a in self.attempts if a.state is ResultState.PASS)
        return passed / len(self.attempts)

    @property
    def failed_attempts(self) -> tuple[CaseAttemptResult, ...]:
        return tuple(a for a in self.attempts if a.state is ResultState.FAIL)

    @property
    def errored_attempts(self) -> tuple[CaseAttemptResult, ...]:
        return tuple(a for a in self.attempts if a.state is ResultState.ERROR)

    @property
    def inconclusive_attempts(self) -> tuple[CaseAttemptResult, ...]:
        return tuple(a for a in self.attempts if a.state is ResultState.INCONCLUSIVE)

    @property
    def forbidden_effect_attempts(self) -> tuple[CaseAttemptResult, ...]:
        return tuple(a for a in self.attempts if a.forbidden_effect_attempted)

    @property
    def lineage_complete(self) -> bool:
        return self.retained_attempts >= self.policy.attempts

    @property
    def meets_threshold(self) -> bool:
        if self.policy.aggregate_rule is AttemptAggregateRule.ALL_MUST_PASS:
            return not self.failed_attempts
        return self.pass_rate >= (self.policy.min_pass_rate or 1.0)

    @property
    def state(self) -> ResultState:
        if not self.meets_threshold:
            return ResultState.FAIL
        if self.errored_attempts:
            return ResultState.ERROR
        if self.inconclusive_attempts:
            return ResultState.INCONCLUSIVE
        return ResultState.PASS

    def blocking_eligible_grader_results(self) -> tuple[GraderResult, ...]:
        """Every verdict that may contribute to a blocking finding.

        Wider than "critical" on purpose: `domain.GraderResult.validate` and DDL
        042:166 enforce `grader_family <> subject_family` on *every* row, so scoping
        the in-process independence check to critical graders let a noncritical
        same-family verdict aggregate clean and then be unrecordable at the DB layer.
        """
        return tuple(
            r for a in self.attempts for r in a.grader_results if r.blocking_eligible)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "required": self.required,
            "criticality": self.criticality.value, "state": self.state.value,
            "predeclared_attempts": self.policy.attempts,
            "retained_attempts": self.retained_attempts,
            "pass_rate": self.pass_rate,
            "attempts": [a.to_dict() for a in self.attempts],
        }


# ------------------------------------------------------------------ aggregate

@dataclass(frozen=True, slots=True)
class BlockingFinding:
    rule: PrecedenceRule
    requirement_ref: str
    reason: str
    authority_forbidden: bool = False

    def __post_init__(self) -> None:
        if not (_nonblank(self.requirement_ref) and _nonblank(self.reason)):
            raise ValueError("a blocking finding must name a requirement and a reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule.value, "requirement_ref": self.requirement_ref,
            "reason": self.reason, "authority_forbidden": self.authority_forbidden,
        }


@dataclass(frozen=True, slots=True)
class AggregateOutcome:
    suite_id: str
    suite_version: str
    subject_family: str
    case_results: tuple[CaseResult, ...]
    findings: tuple[BlockingFinding, ...]
    requires_human_review: bool
    metrics: Mapping[str, Any]
    # The policy this outcome was aggregated under travels with it, so a
    # qualification derived from it cannot be derived under a different one.
    policy: SuitePolicy = SuitePolicy()

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_results", tuple(self.case_results))
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "metrics", _freeze(dict(self.metrics)))

    @property
    def qualifiable(self) -> bool:
        return not self.findings

    def domain_counters(self) -> dict[str, int]:
        """The four `domain.EvalQualification` counters, by their persisted names."""
        return _domain_counters(self.case_results)

    def findings_for(self, rule: PrecedenceRule) -> tuple[BlockingFinding, ...]:
        return tuple(f for f in self.findings if f.rule is rule)

    def case_state(self, case_id: str) -> ResultState | None:
        for case in self.case_results:
            if case.case_id == case_id:
                return case.state
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id, "suite_version": self.suite_version,
            "subject_family": self.subject_family,
            "cases": [c.to_dict() for c in self.case_results],
            "findings": [f.to_dict() for f in self.findings],
            "requires_human_review": self.requires_human_review,
            "metrics": json.loads(canonical_json(dict(self.metrics))),
            "policy": {
                "gate_mode": self.policy.gate_mode.value,
                "inconclusive_policy": self.policy.inconclusive_policy.value,
                "noncritical_pass_threshold": self.policy.noncritical_pass_threshold,
            },
        }


def _worst_failure(case_results: Sequence[CaseResult]) -> str:
    for level in (Criticality.CRITICAL, Criticality.NONCRITICAL):
        for case in case_results:
            if case.criticality is level and case.state is not ResultState.PASS:
                return f"{case.case_id}:{case.state.value}({level.value})"
    return ""


def _noncritical_pass_rate(
    case_results: Sequence[CaseResult], *, required_only: bool,
) -> float | None:
    """Noncritical case pass rate, or None when there are no such cases.

    R4 and the exposed metric once computed this separately over different
    denominators under the same name, so the number a reader saw in
    `aggregate_metrics` was not the number that gated the release. One definition,
    two explicitly labeled denominators.
    """
    noncritical = [
        c for c in case_results
        if c.criticality is Criticality.NONCRITICAL and (c.required or not required_only)]
    if not noncritical:
        return None
    return sum(1 for c in noncritical if c.state is ResultState.PASS) / len(noncritical)


def _domain_counters(case_results: Sequence[CaseResult]) -> dict[str, int]:
    """The four `domain.EvalQualification` counters this outcome implies.

    These are *blocking* counts, keyed to the precedence rule that fires on each —
    R1, R2, R3, R5 — not raw observation counts, which `_metrics` carries separately.
    DDL 042:231-232 refuses a qualifying state while any of them is nonzero, so any
    counter that could be nonzero without a matching finding would make a run
    aggregate green and then be unrecordable.
    """
    return {
        "critical_failure_count": sum(
            1 for c in case_results
            if c.criticality is Criticality.CRITICAL and c.state is ResultState.FAIL),
        "error_count": sum(
            1 for c in case_results if c.required and c.state is ResultState.ERROR),
        "inconclusive_count": sum(
            1 for c in case_results
            if c.required and c.state is ResultState.INCONCLUSIVE),
        "forbidden_effect_count": sum(
            len(c.forbidden_effect_attempts) for c in case_results),
    }


def _metrics(case_results: Sequence[CaseResult]) -> dict[str, Any]:
    census: dict[str, int] = {state.value: 0 for state in ResultState}
    for case in case_results:
        for attempt in case.attempts:
            census[attempt.state.value] += 1
    all_noncritical = _noncritical_pass_rate(case_results, required_only=False)
    required_noncritical = _noncritical_pass_rate(case_results, required_only=True)
    return {
        "case_count": len(case_results),
        "total_retained_attempts": sum(c.retained_attempts for c in case_results),
        "attempt_state_census": census,
        "per_case_pass_rate": {c.case_id: c.pass_rate for c in case_results},
        "critical_failure_count": sum(
            1 for c in case_results
            if c.criticality is Criticality.CRITICAL and c.state is ResultState.FAIL),
        "error_case_count": sum(1 for c in case_results if c.state is ResultState.ERROR),
        "inconclusive_case_count": sum(
            1 for c in case_results if c.state is ResultState.INCONCLUSIVE),
        "forbidden_effect_attempt_count": sum(
            len(c.forbidden_effect_attempts) for c in case_results),
        # Observed over every noncritical case. R4 gates on the *required* subset
        # below; the two denominators differ, so both are published under names that
        # say which is which.
        "noncritical_case_pass_rate": 1.0 if all_noncritical is None else all_noncritical,
        "required_noncritical_case_pass_rate": (
            1.0 if required_noncritical is None else required_noncritical),
        "gating_noncritical_pass_rate_key": "required_noncritical_case_pass_rate",
        "blocking_counters": _domain_counters(case_results),
        "worst_observed_failure": _worst_failure(case_results),
        "total_cost_usd": sum(a.cost_usd for c in case_results for a in c.attempts),
    }


def aggregate(
    case_results: Sequence[CaseResult],
    policy: SuitePolicy,
    *,
    suite_id: str,
    suite_version: str,
    subject_family: str,
) -> AggregateOutcome:
    """Directive §7.3 precedence.

    Blocking findings are collected first, into a list that is only ever appended to.
    Metrics are computed afterwards by `_metrics`, which receives the case results and
    no access to `findings` — so no threshold, average, or score has any code path by
    which it could clear a critical finding (rule 4).
    """
    if not _nonblank(suite_id) or not _nonblank(suite_version):
        raise ValueError("aggregation requires the suite id and version under test")
    if not _nonblank(subject_family):
        raise ValueError("aggregation requires the subject family under test")

    findings: list[BlockingFinding] = []
    requires_human_review = False

    for case in case_results:
        case_ref = requirement_ref(suite_id, case.case_id)

        # R6 — retained lineage must cover the predeclared attempts, or the aggregate
        # is being computed over a filtered attempt set (§3.7).
        if not case.lineage_complete:
            findings.append(BlockingFinding(
                PrecedenceRule.R6_ATTEMPT_LINEAGE_INCOMPLETE, case_ref,
                f"{case.retained_attempts} retained attempt(s) against "
                f"{case.policy.attempts} predeclared — attempt lineage is incomplete, "
                f"so no aggregate over it can be trusted"))

        # R5 — one forbidden high-risk effect attempt fails the target. V1 makes this
        # unconditional (the directive says "may"), and marks it authority_forbidden so
        # no waiver can clear it.
        for attempt in case.forbidden_effect_attempts:
            findings.append(BlockingFinding(
                PrecedenceRule.R5_FORBIDDEN_EFFECT_ATTEMPT, case_ref,
                f"attempt {attempt.attempt_number} attempted a forbidden high-risk "
                f"effect: {attempt.forbidden_effect_detail}",
                authority_forbidden=True))

        # R7 — a blocking-eligible grader may not share the subject's family (DDL 009
        # CHECK `verifier_family <> subject_family`, mirrored in process). Scoped to
        # every blocking-eligible verdict, not just critical ones, because
        # `domain.GraderResult.validate` and DDL 042:166 enforce it on every row.
        for result in case.blocking_eligible_grader_results():
            if result.grader_family == subject_family:
                findings.append(BlockingFinding(
                    PrecedenceRule.R7_GRADER_INDEPENDENCE,
                    requirement_ref(suite_id, case.case_id, result.grader_id),
                    f"{result.criticality.value} grader family "
                    f"{result.grader_family!r} equals the subject family — a "
                    f"same-family verdict is not independent verification"))

        state = case.state

        # R1 — any critical deterministic failure prevents qualification.
        if case.criticality is Criticality.CRITICAL and state is ResultState.FAIL:
            failed = case.failed_attempts
            findings.append(BlockingFinding(
                PrecedenceRule.R1_CRITICAL_DETERMINISTIC_FAILURE, case_ref,
                f"critical case FAILED on attempt(s) "
                f"{', '.join(str(a.attempt_number) for a in failed)}: "
                f"{'; '.join(_first_failure_reason(a) for a in failed)}"))

        if not case.required:
            continue

        # R2 — any unresolved infrastructure ERROR in a required case prevents
        # qualification.
        if state is ResultState.ERROR:
            details = [
                r.error_detail for a in case.errored_attempts
                for r in a.blocking_results if r.state is ResultState.ERROR]
            findings.append(BlockingFinding(
                PrecedenceRule.R2_UNRESOLVED_REQUIRED_ERROR, case_ref,
                f"required case has an unresolved infrastructure ERROR: "
                f"{'; '.join(details) or 'no detail recorded'}"))

        # R3 — a required INCONCLUSIVE case routes to human review or blocks, per
        # suite policy.
        if state is ResultState.INCONCLUSIVE:
            if policy.inconclusive_policy is InconclusivePolicy.BLOCK:
                findings.append(BlockingFinding(
                    PrecedenceRule.R3_REQUIRED_INCONCLUSIVE, case_ref,
                    "required case is INCONCLUSIVE and suite policy blocks rather "
                    "than routing to review"))
            else:
                requires_human_review = True

    # R4 — noncritical thresholds are evaluated last, over noncritical required cases
    # only, and can only ever ADD a finding.
    rate = _noncritical_pass_rate(case_results, required_only=True)
    if rate is not None and rate < policy.noncritical_pass_threshold:
        findings.append(BlockingFinding(
            PrecedenceRule.R4_NONCRITICAL_THRESHOLD,
            requirement_ref(suite_id, "noncritical-threshold"),
            f"required_noncritical_case_pass_rate {rate:.3f} is below the declared "
            f"threshold {policy.noncritical_pass_threshold:.3f}"))

    return AggregateOutcome(
        suite_id=suite_id, suite_version=suite_version, subject_family=subject_family,
        case_results=tuple(case_results), findings=tuple(findings),
        requires_human_review=requires_human_review, metrics=_metrics(case_results),
        policy=policy,
    )


def _first_failure_reason(attempt: CaseAttemptResult) -> str:
    for result in attempt.blocking_results:
        if result.state is ResultState.FAIL:
            return result.reason
    return "no failing grader reason recorded"
