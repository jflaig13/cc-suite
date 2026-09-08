# SPDX-License-Identifier: MPL-2.0
"""Versioned suite/case package contracts for the Fleet Kernel Evaluation Plane.

Per directive §5.2 and §6.1-6.2, evaluation *meaning* — suites, cases, fixture
and grader references, attempt policy, lifecycle — lives in version-controlled
files owned by the fleet, while execution state (`EvalRun`, `EvalCaseAttempt`,
`GraderResult`, `EvalQualification`) lives in the Kernel database owned by
EP-100. Nothing here creates or shadows a table.

The one thing a versioned in-repo package cannot hold is a protected release
holdout, because the package is exactly what the evaluated target can read.
A holdout case therefore carries only its identity, its policy metadata, and the
content hash of a bundle served by a `HoldoutProvider`; its expected behavior,
input fixture, and grader logic are structurally forbidden from appearing in the
file, and `validate_package` rejects the package if they do.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TypeVar

from fleet_kernel.eval_plane.domain import (
    Criticality,
    GateMode as GateIntent,
    GraderKind as GraderType,
    InconclusivePolicy,
    SuitePolicy,
)
from fleet_kernel.eval_plane.graders import (
    AttemptAggregateRule,
    AttemptPolicy as GraderAttemptPolicy,
)
from fleet_kernel.eval_plane.holdout_provider import (
    CASE_ID_PATTERN,
    HOLDOUT_SCHEME,
    HoldoutProvider,
)


class EvalPackageError(ValueError):
    """A suite/case package is malformed, incoherent, or leaks a holdout."""


SUITE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*(?:\.[a-z0-9_]+)*$")
CAPABILITY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*(?:\.[a-z0-9_]+)*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TENANT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

SUITE_FILE_SUFFIX = ".suite.json"
CASE_FILE_SUFFIX = ".case.json"

# §7(C): skill-selection suites fingerprint the inputs that actually drive
# host-native description matching, since no resolver registry exists to hash.
SKILL_SELECTION_CAPABILITY_PREFIX = "skill_selection"

_MAX_TEXT = 4096
_MAX_ATTEMPTS = 25


class OwningScope(str, Enum):
    COMPANY_FLEET = "company_fleet"
    SHARED_RESTAURANT_FLEET = "shared_restaurant_fleet"
    INTEGRATION = "integration"
    WORKFLOW = "workflow"
    TENANT = "tenant"


class RiskClass(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class CaseSourceType(str, Enum):
    INCIDENT = "incident"
    ACCEPTED_SUCCESS = "accepted_success"
    SYNTHETIC = "synthetic"
    GOLDEN_FIXTURE = "golden_fixture"
    HOLDOUT = "holdout"
    MUTATION = "mutation"
    PRODUCTION_SAMPLE = "production_sample"


class Visibility(str, Enum):
    """Directive §14.1: development/regression cases versus protected holdouts."""

    DEVELOPMENT = "development"
    HOLDOUT = "holdout"


class PrivacyClass(str, Enum):
    SYNTHETIC = "synthetic"
    DEIDENTIFIED = "deidentified"
    TENANT_PRIVATE = "tenant_private"


class LifecycleState(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class AggregateRule(str, Enum):
    ALL_ATTEMPTS_PASS = "all_attempts_pass"
    MAJORITY_ATTEMPTS_PASS = "majority_attempts_pass"
    ANY_ATTEMPT_PASSES = "any_attempt_passes"


class CriticalFailureRule(str, Enum):
    SINGLE_OCCURRENCE_FAILS = "single_occurrence_fails"
    MAJORITY_OCCURRENCE_FAILS = "majority_occurrence_fails"


_RETIRED_STATES = (LifecycleState.DEPRECATED, LifecycleState.RETIRED)
# Directive §12 recommended starting policy, expressed as a mechanical floor.
_ATTEMPT_FLOOR: Mapping[RiskClass, int] = {
    RiskClass.LOW: 1,
    RiskClass.MODERATE: 3,
    RiskClass.HIGH: 5,
    RiskClass.CRITICAL: 5,
}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _fields(
    payload: object,
    *,
    required: Sequence[str],
    optional: Sequence[str] = (),
    where: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EvalPackageError(f"{where} must be a JSON object")
    known = set(required) | set(optional)
    unknown = sorted(set(payload) - known)
    if unknown:
        raise EvalPackageError(f"{where} has unknown keys: {unknown}")
    missing = sorted(set(required) - set(payload))
    if missing:
        raise EvalPackageError(f"{where} is missing required keys: {missing}")
    return dict(payload)


def _text(payload: Mapping[str, Any], key: str, *, where: str,
          pattern: re.Pattern[str] | None = None) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT:
        raise EvalPackageError(f"{where}.{key} must be a non-empty string")
    value = value.strip()
    if any(ord(char) < 32 for char in value):
        raise EvalPackageError(f"{where}.{key} contains control characters")
    if pattern is not None and not pattern.fullmatch(value):
        raise EvalPackageError(f"{where}.{key} is not a well-formed identifier: {value!r}")
    return value


def _optional_text(payload: Mapping[str, Any], key: str, *, where: str,
                   pattern: re.Pattern[str] | None = None) -> str | None:
    if payload.get(key) is None:
        return None
    return _text(payload, key, where=where, pattern=pattern)


def _integer(payload: Mapping[str, Any], key: str, *, where: str,
             minimum: int, maximum: int) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise EvalPackageError(f"{where}.{key} must be an integer in [{minimum}, {maximum}]")
    return value


def _ratio(payload: Mapping[str, Any], key: str, *, where: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise EvalPackageError(f"{where}.{key} must be a number in [0.0, 1.0]")
    return float(value)


def _string_tuple(payload: Mapping[str, Any], key: str, *, where: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() or len(item) > _MAX_TEXT for item in value
    ):
        raise EvalPackageError(f"{where}.{key} must be a list of non-empty strings")
    items = tuple(item.strip() for item in value)
    if len(set(items)) != len(items):
        raise EvalPackageError(f"{where}.{key} contains duplicate entries")
    return items


def _string_map(payload: Mapping[str, Any], key: str, *, where: str) -> dict[str, str]:
    value = payload.get(key, {})
    if not isinstance(value, dict) or any(
        not isinstance(name, str) or not name.strip()
        or not isinstance(text, str) or not text.strip()
        for name, text in value.items()
    ):
        raise EvalPackageError(f"{where}.{key} must be an object of non-empty string values")
    return dict(sorted(value.items()))


def _object_map(payload: Mapping[str, Any], key: str, *, where: str) -> dict[str, Any]:
    value = payload.get(key, {})
    if not isinstance(value, dict) or any(not isinstance(name, str) for name in value):
        raise EvalPackageError(f"{where}.{key} must be an object with string keys")
    try:
        _canonical_json_bytes(value)
    except (TypeError, ValueError) as error:
        raise EvalPackageError(f"{where}.{key} must be canonically serializable") from error
    return dict(sorted(value.items()))


_E = TypeVar("_E", bound=Enum)  # PEP 695 syntax needs 3.12; CI runs 3.11


def _enum(enum_cls: type[_E], payload: Mapping[str, Any], key: str, *, where: str) -> _E:
    value = payload.get(key)
    try:
        return enum_cls(value)
    except ValueError as error:
        allowed = sorted(member.value for member in enum_cls)
        raise EvalPackageError(f"{where}.{key} must be one of {allowed}, got {value!r}") from error


def _date(payload: Mapping[str, Any], key: str, *, where: str) -> date:
    value = payload.get(key)
    if not isinstance(value, str):
        raise EvalPackageError(f"{where}.{key} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise EvalPackageError(f"{where}.{key} is not an ISO date: {value!r}") from error


@dataclass(frozen=True, slots=True)
class ManifestReference:
    """A content-bound pointer to a fixture, grader, or instruction artifact."""

    ref: str
    sha256: str

    @property
    def is_holdout(self) -> bool:
        return self.ref.startswith(HOLDOUT_SCHEME)

    def resolve(self, repo_root: Path) -> Path:
        if self.is_holdout:
            raise EvalPackageError(f"holdout reference {self.ref} has no repository path")
        return repo_root / self.ref

    @classmethod
    def parse(cls, payload: object, *, where: str) -> "ManifestReference":
        fields = _fields(payload, required=("ref", "sha256"), where=where)
        ref = _text(fields, "ref", where=where)
        sha256 = _text(fields, "sha256", where=where, pattern=SHA256_PATTERN)
        if ref.startswith(HOLDOUT_SCHEME):
            _, _, remainder = ref.partition(HOLDOUT_SCHEME)
            case_id, separator, part = remainder.partition("/")
            if not separator or not CASE_ID_PATTERN.fullmatch(case_id) or not part:
                raise EvalPackageError(
                    f"{where}.ref must look like {HOLDOUT_SCHEME}<case_id>/<part>, got {ref!r}",
                )
        else:
            normalized = Path(ref).as_posix()
            if (ref != normalized or ref.startswith("/") or "\\" in ref
                    or ".." in Path(ref).parts or Path(ref).is_absolute()):
                raise EvalPackageError(
                    f"{where}.ref must be a normalized repository-relative path, got {ref!r}",
                )
        return cls(ref=ref, sha256=sha256)


@dataclass(frozen=True, slots=True)
class AttemptPolicy:
    """Directive §12: predeclared attempts, sampling, and aggregation."""

    attempts: int
    sampling: Mapping[str, Any]
    seed: int | None
    infra_retry_limit: int
    aggregate_rule: AggregateRule
    critical_failure_rule: CriticalFailureRule

    @classmethod
    def parse(cls, payload: object, *, where: str) -> "AttemptPolicy":
        fields = _fields(
            payload,
            required=("attempts", "infra_retry_limit", "aggregate_rule", "critical_failure_rule"),
            optional=("sampling", "seed"),
            where=where,
        )
        seed = fields.get("seed")
        if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
            raise EvalPackageError(f"{where}.seed must be an integer or null")
        return cls(
            attempts=_integer(fields, "attempts", where=where, minimum=1, maximum=_MAX_ATTEMPTS),
            sampling=_object_map(fields, "sampling", where=where),
            seed=seed,
            infra_retry_limit=_integer(
                fields, "infra_retry_limit", where=where, minimum=0, maximum=10,
            ),
            aggregate_rule=_enum(AggregateRule, fields, "aggregate_rule", where=where),
            critical_failure_rule=_enum(
                CriticalFailureRule, fields, "critical_failure_rule", where=where,
            ),
        )

    def to_grader_attempt_policy(self, criticality: Criticality) -> GraderAttemptPolicy:
        """Convert this manifest policy into the shape EP-130 grades on.

        The two layers hold genuinely different vocabularies — this one is what a
        suite author writes, `graders.AttemptPolicy` is what `graders.CaseResult`
        aggregates over — so this is the single conversion point between them,
        mirroring `graders.to_domain_result`. Without it the two shapes drift.

        `MAJORITY_ATTEMPTS_PASS` becomes a THRESHOLD at the exact strict-majority
        rate for the predeclared attempt count, `(attempts // 2 + 1) / attempts`,
        rather than a flat 0.5 that an even attempt count would let a tie satisfy.
        `ANY_ATTEMPT_PASSES` becomes a THRESHOLD at `1 / attempts` — the rate one
        passing attempt reaches exactly — and is refused outright for a critical
        case, because averaging a critical failure away is the §3.7 laundering
        `graders.CaseResult.__post_init__` already refuses. `_validate_case_policy`
        refuses the same combination at authoring time, so this raise is the
        second half of one rule rather than a new one.

        `critical_failure_rule` has no counterpart in the grader layer and is not
        carried across: it is enforced here, where a critical case must declare
        `SINGLE_OCCURRENCE_FAILS`.
        """
        if (criticality is Criticality.CRITICAL
                and self.aggregate_rule is not AggregateRule.ALL_ATTEMPTS_PASS):
            raise EvalPackageError(
                f"a critical case cannot aggregate on {self.aggregate_rule.value!r}; "
                f"only {AggregateRule.ALL_ATTEMPTS_PASS.value!r} has a grader-layer "
                "equivalent that cannot average a critical failure away",
            )
        if self.aggregate_rule is AggregateRule.ALL_ATTEMPTS_PASS:
            rule, min_pass_rate = AttemptAggregateRule.ALL_MUST_PASS, None
        elif self.aggregate_rule is AggregateRule.MAJORITY_ATTEMPTS_PASS:
            rule = AttemptAggregateRule.THRESHOLD
            min_pass_rate = (self.attempts // 2 + 1) / self.attempts
        else:
            rule, min_pass_rate = AttemptAggregateRule.THRESHOLD, 1 / self.attempts
        return GraderAttemptPolicy(
            attempts=self.attempts,
            aggregate_rule=rule,
            min_pass_rate=min_pass_rate,
            # The seed rides in the digest so two runs sampling differently cannot
            # present the same predeclared policy.
            sampling_config_digest=_digest(
                {"sampling": dict(self.sampling), "seed": self.seed},
            ),
            infrastructure_retry_limit=self.infra_retry_limit,
        )


@dataclass(frozen=True, slots=True)
class ExpectedBehavior:
    """Directive §6.2: property-based expectations, not one exact sentence.

    This is the answer key. A holdout case must leave it empty in-repo; the real
    lists arrive from the `HoldoutProvider` at grading time.
    """

    must: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    may: tuple[str, ...] = ()
    acceptable_outcomes: tuple[str, ...] = ()
    escalation_conditions: tuple[str, ...] = ()
    forbidden_effects: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not any((self.must, self.must_not, self.may, self.acceptable_outcomes,
                        self.escalation_conditions, self.forbidden_effects))

    @classmethod
    def parse(cls, payload: object, *, where: str) -> "ExpectedBehavior":
        keys = ("must", "must_not", "may", "acceptable_outcomes",
                "escalation_conditions", "forbidden_effects")
        fields = _fields(payload, required=(), optional=keys, where=where)
        return cls(**{key: _string_tuple(fields, key, where=where) for key in keys})


@dataclass(frozen=True, slots=True)
class GraderAssignment:
    grader_id: str
    grader_version: int
    grader_type: GraderType
    manifest: ManifestReference
    criticality: Criticality

    @property
    def qualified_id(self) -> str:
        return f"{self.grader_id}.v{self.grader_version}"

    @classmethod
    def parse(cls, payload: object, *, where: str) -> "GraderAssignment":
        fields = _fields(
            payload,
            required=("grader_id", "grader_version", "grader_type", "manifest", "criticality"),
            where=where,
        )
        grader_type = _enum(GraderType, fields, "grader_type", where=where)
        criticality = _enum(Criticality, fields, "criticality", where=where)
        # Directive §4.2 / Gate A packet §4: semantic grading stays advisory
        # until EP-610 calibration lands. Enforced here too, not only by DDL
        # 042's CHECK constraint, so a manifest that can never be persisted
        # is rejected at authoring time instead of surfacing as a DB error.
        if grader_type is GraderType.SEMANTIC and criticality is not Criticality.ADVISORY:
            raise EvalPackageError(
                f"{where}: a semantic grader assignment must be criticality=advisory "
                f"(got {criticality.value!r}) — semantic grading stays advisory until "
                "EP-610 calibration lands (directive §4.2)"
            )
        return cls(
            grader_id=_text(fields, "grader_id", where=where, pattern=CAPABILITY_PATTERN),
            grader_version=_integer(fields, "grader_version", where=where, minimum=1, maximum=9999),
            grader_type=grader_type,
            manifest=ManifestReference.parse(fields["manifest"], where=f"{where}.manifest"),
            criticality=criticality,
        )


@dataclass(frozen=True, slots=True)
class SkillSelectionTarget:
    """Gate A §7(C): the evaluated target for skill-selection and routing cases.

    Host-native description matching is the only selection mechanism that
    exists, so these are the inputs that actually decide routing. There is
    deliberately no resolver-registry field — none exists, and none is built to
    satisfy directive wording alone.
    """

    skill_descriptions: Mapping[str, str]
    tool_descriptions: Mapping[str, str]
    host_instructions: tuple[ManifestReference, ...]
    available_tools: tuple[str, ...]
    model_configuration: Mapping[str, Any]
    harness_version: str

    @property
    def content_sha256(self) -> str:
        return _digest({
            "skill_descriptions": dict(self.skill_descriptions),
            "tool_descriptions": dict(self.tool_descriptions),
            "host_instructions": [
                {"ref": item.ref, "sha256": item.sha256} for item in self.host_instructions
            ],
            "available_tools": list(self.available_tools),
            "model_configuration": dict(self.model_configuration),
            "harness_version": self.harness_version,
        })

    @classmethod
    def parse(cls, payload: object, *, where: str) -> "SkillSelectionTarget":
        fields = _fields(
            payload,
            required=("skill_descriptions", "tool_descriptions", "host_instructions",
                      "available_tools", "model_configuration", "harness_version"),
            where=where,
        )
        skill_descriptions = _string_map(fields, "skill_descriptions", where=where)
        tool_descriptions = _string_map(fields, "tool_descriptions", where=where)
        instructions = fields["host_instructions"]
        if not isinstance(instructions, list):
            raise EvalPackageError(f"{where}.host_instructions must be a list")
        host_instructions = tuple(
            ManifestReference.parse(item, where=f"{where}.host_instructions[{index}]")
            for index, item in enumerate(instructions)
        )
        available_tools = _string_tuple(fields, "available_tools", where=where)
        model_configuration = _object_map(fields, "model_configuration", where=where)
        empty = [
            name for name, value in (
                ("skill_descriptions", skill_descriptions),
                ("tool_descriptions", tool_descriptions),
                ("host_instructions", host_instructions),
                ("available_tools", available_tools),
                ("model_configuration", model_configuration),
            ) if not value
        ]
        if empty:
            raise EvalPackageError(
                f"{where} must declare every §7(C) input; empty: {sorted(empty)}",
            )
        return cls(
            skill_descriptions=skill_descriptions,
            tool_descriptions=tool_descriptions,
            host_instructions=host_instructions,
            available_tools=available_tools,
            model_configuration=model_configuration,
            harness_version=_text(fields, "harness_version", where=where),
        )


@dataclass(frozen=True, slots=True)
class EvalCaseDefinition:
    """Directive §6.2: one scenario within a suite."""

    case_id: str
    case_version: int
    suite_id: str
    source_type: CaseSourceType
    visibility: Visibility
    source_evidence: tuple[str, ...]
    input_fixture: ManifestReference | None
    environment_fixtures: tuple[ManifestReference, ...]
    policy_fixtures: tuple[ManifestReference, ...]
    expected: ExpectedBehavior
    graders: tuple[GraderAssignment, ...]
    criticality: Criticality
    risk_class: RiskClass
    tenant_scope: str | None
    privacy_class: PrivacyClass
    attempt_policy: AttemptPolicy
    skill_selection_target: SkillSelectionTarget | None
    holdout_bundle_sha256: str | None
    owner: str
    created_at: date
    last_reviewed_at: date
    lifecycle_state: LifecycleState
    supersedes: tuple[str, ...]
    superseded_by: str | None
    manifest_sha256: str

    @property
    def qualified_id(self) -> str:
        return f"{self.case_id}.v{self.case_version}"

    @property
    def is_holdout(self) -> bool:
        return self.visibility is Visibility.HOLDOUT

    def all_references(self) -> tuple[ManifestReference, ...]:
        refs: list[ManifestReference] = []
        if self.input_fixture is not None:
            refs.append(self.input_fixture)
        refs.extend(self.environment_fixtures)
        refs.extend(self.policy_fixtures)
        refs.extend(grader.manifest for grader in self.graders)
        if self.skill_selection_target is not None:
            refs.extend(self.skill_selection_target.host_instructions)
        return tuple(refs)

    @classmethod
    def parse(cls, payload: object, *, where: str, manifest_sha256: str) -> "EvalCaseDefinition":
        fields = _fields(
            payload,
            required=("case_id", "case_version", "suite_id", "source_type", "visibility",
                      "graders", "criticality", "risk_class", "privacy_class", "attempt_policy",
                      "owner", "created_at", "last_reviewed_at", "lifecycle_state"),
            optional=("source_evidence", "input_fixture", "environment_fixtures",
                      "policy_fixtures", "expected", "tenant_scope", "skill_selection_target",
                      "holdout_bundle_sha256", "supersedes", "superseded_by"),
            where=where,
        )
        graders_payload = fields["graders"]
        if not isinstance(graders_payload, list) or not graders_payload:
            raise EvalPackageError(f"{where}.graders must be a non-empty list")
        graders = tuple(
            GraderAssignment.parse(item, where=f"{where}.graders[{index}]")
            for index, item in enumerate(graders_payload)
        )
        if len({grader.qualified_id for grader in graders}) != len(graders):
            raise EvalPackageError(f"{where}.graders assigns the same grader version twice")

        input_fixture = (
            None if fields.get("input_fixture") is None
            else ManifestReference.parse(fields["input_fixture"], where=f"{where}.input_fixture")
        )
        created_at = _date(fields, "created_at", where=where)
        last_reviewed_at = _date(fields, "last_reviewed_at", where=where)
        if last_reviewed_at < created_at:
            raise EvalPackageError(f"{where}.last_reviewed_at precedes created_at")

        holdout_sha = _optional_text(
            fields, "holdout_bundle_sha256", where=where, pattern=SHA256_PATTERN,
        )
        skill_target = (
            None if fields.get("skill_selection_target") is None
            else SkillSelectionTarget.parse(
                fields["skill_selection_target"], where=f"{where}.skill_selection_target",
            )
        )
        return cls(
            case_id=_text(fields, "case_id", where=where, pattern=CASE_ID_PATTERN),
            case_version=_integer(fields, "case_version", where=where, minimum=1, maximum=9999),
            suite_id=_text(fields, "suite_id", where=where, pattern=SUITE_ID_PATTERN),
            source_type=_enum(CaseSourceType, fields, "source_type", where=where),
            visibility=_enum(Visibility, fields, "visibility", where=where),
            source_evidence=_string_tuple(fields, "source_evidence", where=where),
            input_fixture=input_fixture,
            environment_fixtures=_reference_list(
                fields, "environment_fixtures", where=where,
            ),
            policy_fixtures=_reference_list(fields, "policy_fixtures", where=where),
            expected=ExpectedBehavior.parse(
                fields.get("expected", {}), where=f"{where}.expected",
            ),
            graders=graders,
            criticality=_enum(Criticality, fields, "criticality", where=where),
            risk_class=_enum(RiskClass, fields, "risk_class", where=where),
            tenant_scope=_optional_text(fields, "tenant_scope", where=where, pattern=TENANT_PATTERN),
            privacy_class=_enum(PrivacyClass, fields, "privacy_class", where=where),
            attempt_policy=AttemptPolicy.parse(
                fields["attempt_policy"], where=f"{where}.attempt_policy",
            ),
            skill_selection_target=skill_target,
            holdout_bundle_sha256=holdout_sha,
            owner=_text(fields, "owner", where=where),
            created_at=created_at,
            last_reviewed_at=last_reviewed_at,
            lifecycle_state=_enum(LifecycleState, fields, "lifecycle_state", where=where),
            supersedes=_string_tuple(fields, "supersedes", where=where),
            superseded_by=_optional_text(
                fields, "superseded_by", where=where, pattern=CASE_ID_PATTERN,
            ),
            manifest_sha256=manifest_sha256,
        )


def _reference_list(
    fields: Mapping[str, Any], key: str, *, where: str,
) -> tuple[ManifestReference, ...]:
    value = fields.get(key, [])
    if not isinstance(value, list):
        raise EvalPackageError(f"{where}.{key} must be a list")
    return tuple(
        ManifestReference.parse(item, where=f"{where}.{key}[{index}]")
        for index, item in enumerate(value)
    )


@dataclass(frozen=True, slots=True)
class QualificationThresholds:
    """The suite's declared qualification bar.

    `max_critical_failures` and `max_error_rate` accept only zero. No layer
    beneath this one can honor a budget: `graders.aggregate` R1 blocks on the
    first critical failure and R2 on the first unresolved required-case ERROR
    regardless of any declared allowance, and DDL 042's qualification CHECK
    requires `critical_failure_count = 0 AND error_count = 0` for every
    qualifying state. A nonzero budget would be a policy the manifest advertises
    and the plane silently ignores, so it is refused where it is written.
    """

    min_case_pass_rate: float
    max_critical_failures: int
    max_error_rate: float

    @classmethod
    def parse(cls, payload: object, *, where: str) -> "QualificationThresholds":
        fields = _fields(
            payload,
            required=("min_case_pass_rate", "max_critical_failures", "max_error_rate"),
            where=where,
        )
        max_critical_failures = _integer(
            fields, "max_critical_failures", where=where, minimum=0, maximum=9999,
        )
        max_error_rate = _ratio(fields, "max_error_rate", where=where)
        unsatisfiable = [
            name for name, value in (
                ("max_critical_failures", max_critical_failures),
                ("max_error_rate", max_error_rate),
            ) if value != 0
        ]
        if unsatisfiable:
            raise EvalPackageError(
                f"{where}: {sorted(unsatisfiable)} must be 0 — aggregation blocks on the "
                "first critical failure (R1) and the first unresolved required error "
                "(R2), and no qualifying state may carry either on record, so any other "
                "budget is a threshold nothing beneath this manifest can honor",
            )
        return cls(
            min_case_pass_rate=_ratio(fields, "min_case_pass_rate", where=where),
            max_critical_failures=max_critical_failures,
            max_error_rate=max_error_rate,
        )


@dataclass(frozen=True, slots=True)
class EvalSuiteDefinition:
    """Directive §6.1: a versioned exam for a capability."""

    suite_id: str
    suite_version: int
    owning_scope: OwningScope
    capability_under_test: str
    risk_class: RiskClass
    case_manifests: tuple[ManifestReference, ...]
    grader_manifests: tuple[ManifestReference, ...]
    attempt_policy: AttemptPolicy
    gate_intent: GateIntent
    inconclusive_policy: InconclusivePolicy
    qualification_thresholds: QualificationThresholds
    dependencies: tuple[str, ...]
    tenant_scope: str | None
    privacy_class: PrivacyClass
    owner: str
    created_at: date
    last_reviewed_at: date
    lifecycle_state: LifecycleState
    manifest_sha256: str

    @property
    def qualified_id(self) -> str:
        return f"{self.suite_id}.v{self.suite_version}"

    @property
    def is_skill_selection(self) -> bool:
        return self.capability_under_test.startswith(SKILL_SELECTION_CAPABILITY_PREFIX)

    def to_suite_policy(self) -> SuitePolicy:
        """Convert the manifest's declared policy into what `graders.aggregate` reads.

        `aggregate` R3 branches on `inconclusive_policy` and R4 on
        `noncritical_pass_threshold`, both off a `SuitePolicy` the manifest had no
        way to populate — so a suite's declared intent never reached the rules that
        consume it. This is the one conversion, so the manifest vocabulary and the
        aggregation vocabulary cannot drift.

        `min_case_pass_rate` is the manifest's only declared rate and R4 the only
        rule that reads one, so it is what `noncritical_pass_threshold` carries.
        """
        return SuitePolicy(
            gate_mode=self.gate_intent,
            inconclusive_policy=self.inconclusive_policy,
            noncritical_pass_threshold=self.qualification_thresholds.min_case_pass_rate,
        )

    @classmethod
    def parse(cls, payload: object, *, where: str, manifest_sha256: str) -> "EvalSuiteDefinition":
        fields = _fields(
            payload,
            required=("suite_id", "suite_version", "owning_scope", "capability_under_test",
                      "risk_class", "case_manifests", "grader_manifests", "attempt_policy",
                      "gate_intent", "qualification_thresholds", "privacy_class", "owner",
                      "created_at", "last_reviewed_at", "lifecycle_state"),
            optional=("dependencies", "tenant_scope", "inconclusive_policy"),
            where=where,
        )
        case_manifests = _reference_list(fields, "case_manifests", where=where)
        if not case_manifests:
            raise EvalPackageError(f"{where}.case_manifests must not be empty")
        if len({item.ref for item in case_manifests}) != len(case_manifests):
            raise EvalPackageError(f"{where}.case_manifests references the same file twice")
        if any(item.is_holdout for item in case_manifests):
            raise EvalPackageError(
                f"{where}.case_manifests must be repository files; a holdout case still "
                "declares its identity and policy in-repo",
            )
        grader_manifests = _reference_list(fields, "grader_manifests", where=where)
        if any(item.is_holdout for item in grader_manifests):
            raise EvalPackageError(
                f"{where}.grader_manifests must list only in-repo graders; holdout grader "
                "logic is served by the HoldoutProvider and never enumerated here",
            )
        created_at = _date(fields, "created_at", where=where)
        last_reviewed_at = _date(fields, "last_reviewed_at", where=where)
        if last_reviewed_at < created_at:
            raise EvalPackageError(f"{where}.last_reviewed_at precedes created_at")

        suite = cls(
            suite_id=_text(fields, "suite_id", where=where, pattern=SUITE_ID_PATTERN),
            suite_version=_integer(fields, "suite_version", where=where, minimum=1, maximum=9999),
            owning_scope=_enum(OwningScope, fields, "owning_scope", where=where),
            capability_under_test=_text(
                fields, "capability_under_test", where=where, pattern=CAPABILITY_PATTERN,
            ),
            risk_class=_enum(RiskClass, fields, "risk_class", where=where),
            case_manifests=case_manifests,
            grader_manifests=grader_manifests,
            attempt_policy=AttemptPolicy.parse(
                fields["attempt_policy"], where=f"{where}.attempt_policy",
            ),
            gate_intent=_enum(GateIntent, fields, "gate_intent", where=where),
            # Optional, defaulting to BLOCK to match domain.SuitePolicy: a suite
            # that has not thought about inconclusive results gets the fail-closed
            # branch of R3, never review-and-proceed.
            inconclusive_policy=(
                InconclusivePolicy.BLOCK if fields.get("inconclusive_policy") is None
                else _enum(InconclusivePolicy, fields, "inconclusive_policy", where=where)
            ),
            qualification_thresholds=QualificationThresholds.parse(
                fields["qualification_thresholds"], where=f"{where}.qualification_thresholds",
            ),
            dependencies=_string_tuple(fields, "dependencies", where=where),
            tenant_scope=_optional_text(fields, "tenant_scope", where=where, pattern=TENANT_PATTERN),
            privacy_class=_enum(PrivacyClass, fields, "privacy_class", where=where),
            owner=_text(fields, "owner", where=where),
            created_at=created_at,
            last_reviewed_at=last_reviewed_at,
            lifecycle_state=_enum(LifecycleState, fields, "lifecycle_state", where=where),
            manifest_sha256=manifest_sha256,
        )
        if (suite.owning_scope is OwningScope.TENANT) != (suite.tenant_scope is not None):
            raise EvalPackageError(
                f"{where}: tenant-scoped suites declare tenant_scope and only those may",
            )
        return suite


@dataclass(frozen=True, slots=True)
class EvalSuitePackage:
    """One suite plus its cases, validated as a coherent versioned exam."""

    suite: EvalSuiteDefinition
    cases: tuple[EvalCaseDefinition, ...]
    package_sha256: str
    holdout_binding_verified: bool

    @property
    def holdout_cases(self) -> tuple[EvalCaseDefinition, ...]:
        return tuple(case for case in self.cases if case.is_holdout)

    def case(self, case_id: str) -> EvalCaseDefinition:
        for candidate in self.cases:
            if candidate.case_id == case_id:
                return candidate
        raise EvalPackageError(f"case {case_id} is not part of {self.suite.qualified_id}")


@dataclass(frozen=True, slots=True)
class EvalSuiteRegistry:
    packages: tuple[EvalSuitePackage, ...]

    def package(self, qualified_suite_id: str) -> EvalSuitePackage:
        for candidate in self.packages:
            if candidate.suite.qualified_id == qualified_suite_id:
                return candidate
        raise EvalPackageError(f"no suite {qualified_suite_id} in registry")


def _read_manifest(path: Path, *, where: str) -> tuple[Any, str]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise EvalPackageError(f"{where} references missing file {path}") from error
    except OSError as error:
        raise EvalPackageError(f"{where} references unreadable file {path}") from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvalPackageError(f"{where} references non-JSON file {path}") from error
    return payload, hashlib.sha256(raw).hexdigest()


def _validate_holdout_containment(case: EvalCaseDefinition, *, where: str) -> None:
    """The package is target-readable, so a holdout may not carry its own answers."""
    if case.source_type is CaseSourceType.HOLDOUT and not case.is_holdout:
        raise EvalPackageError(f"{where} has source_type 'holdout' but visibility is development")

    if not case.is_holdout:
        leaking = [ref.ref for ref in case.all_references() if ref.is_holdout]
        if leaking:
            raise EvalPackageError(
                f"{where} is a development case and may not use holdout references: {leaking}",
            )
        if case.holdout_bundle_sha256 is not None:
            raise EvalPackageError(f"{where} is a development case and declares a holdout bundle")
        return

    if case.holdout_bundle_sha256 is None:
        raise EvalPackageError(f"{where} is a holdout and must declare holdout_bundle_sha256")
    if not case.expected.is_empty:
        raise EvalPackageError(
            f"{where} is a holdout: expected behavior is the answer key and must live in the "
            "holdout bundle, not in the target-readable package",
        )
    if case.source_evidence:
        raise EvalPackageError(
            f"{where} is a holdout: source evidence describes the exam and must not be in-repo",
        )
    if case.skill_selection_target is not None:
        raise EvalPackageError(
            f"{where} is a holdout: the evaluated skill-selection target is bundle-served",
        )
    if case.input_fixture is None:
        raise EvalPackageError(f"{where} is a holdout and must declare a holdout input fixture")
    in_repo = [ref.ref for ref in case.all_references() if not ref.is_holdout]
    if in_repo:
        raise EvalPackageError(
            f"{where} is a holdout: every fixture and grader must be served by the "
            f"HoldoutProvider, but these resolve inside the repository: {in_repo}",
        )
    mismatched = [
        ref.ref for ref in case.all_references() if ref.sha256 != case.holdout_bundle_sha256
    ]
    if mismatched:
        raise EvalPackageError(
            f"{where} holdout references must be bound to holdout_bundle_sha256: {mismatched}",
        )
    foreign = [
        ref.ref for ref in case.all_references()
        if ref.ref.removeprefix(HOLDOUT_SCHEME).partition("/")[0] != case.case_id
    ]
    if foreign:
        raise EvalPackageError(
            f"{where} may only reference its own holdout bundle, not: {foreign}",
        )


def _validate_case_policy(
    case: EvalCaseDefinition, suite: EvalSuiteDefinition, *, where: str,
) -> None:
    if case.suite_id != suite.suite_id:
        raise EvalPackageError(
            f"{where} declares suite_id {case.suite_id!r} but sits in {suite.suite_id!r}",
        )
    floor = _ATTEMPT_FLOOR[case.risk_class]
    if case.attempt_policy.attempts < floor:
        raise EvalPackageError(
            f"{where}: risk class {case.risk_class.value} requires at least {floor} retained "
            f"attempts, got {case.attempt_policy.attempts}",
        )
    if (case.criticality is Criticality.CRITICAL
            and case.attempt_policy.critical_failure_rule
            is not CriticalFailureRule.SINGLE_OCCURRENCE_FAILS):
        raise EvalPackageError(
            f"{where}: a critical case must fail the target on a single occurrence",
        )
    if (case.criticality is Criticality.CRITICAL
            and case.attempt_policy.aggregate_rule is not AggregateRule.ALL_ATTEMPTS_PASS):
        raise EvalPackageError(
            f"{where}: a critical case must aggregate on "
            f"{AggregateRule.ALL_ATTEMPTS_PASS.value}, not "
            f"{case.attempt_policy.aggregate_rule.value} — a majority or any-pass rule "
            "would let passing attempts average away a critical failure, which "
            "graders.CaseResult refuses at grading time and which must not be "
            "authorable here either",
        )
    if (case.privacy_class is PrivacyClass.TENANT_PRIVATE) != (case.tenant_scope is not None):
        raise EvalPackageError(
            f"{where}: tenant-private cases declare tenant_scope and only those may",
        )
    if case.privacy_class is PrivacyClass.TENANT_PRIVATE:
        if suite.owning_scope is not OwningScope.TENANT:
            raise EvalPackageError(
                f"{where}: a tenant-private case cannot live in a {suite.owning_scope.value} "
                "suite, which would let a shared suite enumerate tenant-private cases",
            )
        if case.tenant_scope != suite.tenant_scope:
            raise EvalPackageError(
                f"{where}: tenant {case.tenant_scope!r} does not match suite tenant "
                f"{suite.tenant_scope!r}",
            )
    if (suite.is_skill_selection and case.lifecycle_state is LifecycleState.ACTIVE
            and not case.is_holdout and case.skill_selection_target is None):
        raise EvalPackageError(
            f"{where}: a skill-selection case must record the evaluated target manifest "
            "(skill descriptions, tool descriptions, host instructions, available tools, "
            "model configuration, harness version)",
        )


def _validate_lineage(cases: Sequence[EvalCaseDefinition]) -> None:
    by_id: dict[str, list[EvalCaseDefinition]] = {}
    seen: set[tuple[str, int]] = set()
    for case in cases:
        key = (case.case_id, case.case_version)
        if key in seen:
            raise EvalPackageError(f"case {case.qualified_id} is declared twice in one suite")
        seen.add(key)
        by_id.setdefault(case.case_id, []).append(case)

    known = {case.case_id for case in cases}
    for case_id, versions in by_id.items():
        live = [case for case in versions if case.lifecycle_state not in _RETIRED_STATES]
        if len(live) > 1:
            raise EvalPackageError(
                f"case {case_id} has {len(live)} non-retired versions; exactly one may be live",
            )
        for case in versions:
            if case.lifecycle_state in _RETIRED_STATES and case.superseded_by is None:
                raise EvalPackageError(
                    f"case {case.qualified_id} is {case.lifecycle_state.value} and must name "
                    "the case that superseded it",
                )
            if case.superseded_by is not None and case.superseded_by not in known:
                raise EvalPackageError(
                    f"case {case.qualified_id} is superseded by unknown case "
                    f"{case.superseded_by!r}",
                )
            unknown_ancestors = [item for item in case.supersedes if item not in known]
            if unknown_ancestors:
                raise EvalPackageError(
                    f"case {case.qualified_id} supersedes unknown cases: {unknown_ancestors}",
                )


def _validate_grader_coverage(
    suite: EvalSuiteDefinition, cases: Sequence[EvalCaseDefinition],
) -> None:
    declared = {item.ref: item.sha256 for item in suite.grader_manifests}
    for case in cases:
        if case.is_holdout:
            continue
        for grader in case.graders:
            expected = declared.get(grader.manifest.ref)
            if expected is None:
                raise EvalPackageError(
                    f"case {case.qualified_id} uses grader manifest {grader.manifest.ref} that "
                    f"suite {suite.qualified_id} does not declare",
                )
            if expected != grader.manifest.sha256:
                raise EvalPackageError(
                    f"case {case.qualified_id} pins grader {grader.manifest.ref} at a different "
                    f"hash than suite {suite.qualified_id}",
                )


def _validate_referenced_files(
    references: Iterable[tuple[str, ManifestReference]], *, repo_root: Path,
) -> None:
    for where, reference in references:
        if reference.is_holdout:
            continue
        path = reference.resolve(repo_root)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as error:
            raise EvalPackageError(
                f"{where} references {reference.ref}, which does not exist under {repo_root}",
            ) from error
        except OSError as error:
            raise EvalPackageError(f"{where} references unreadable file {reference.ref}") from error
        actual = hashlib.sha256(raw).hexdigest()
        if actual != reference.sha256:
            raise EvalPackageError(
                f"{where} pins {reference.ref} at {reference.sha256} but the file hashes to "
                f"{actual}",
            )


def validate_package(
    suite: EvalSuiteDefinition,
    cases: Sequence[EvalCaseDefinition],
    *,
    repo_root: Path,
    holdout_provider: HoldoutProvider | None = None,
) -> bool:
    """Run every package lint. Returns whether holdout bundles were bound."""
    _validate_lineage(cases)
    for case in cases:
        where = f"case {case.qualified_id}"
        _validate_case_policy(case, suite, where=where)
        _validate_holdout_containment(case, where=where)
    _validate_grader_coverage(suite, cases)

    if suite.gate_intent is GateIntent.BLOCKING and not any(
        case.criticality is Criticality.CRITICAL and case.lifecycle_state is LifecycleState.ACTIVE
        for case in cases
    ):
        raise EvalPackageError(
            f"suite {suite.qualified_id} is blocking but declares no active critical case",
        )

    references: list[tuple[str, ManifestReference]] = [
        (f"suite {suite.qualified_id}", reference) for reference in suite.grader_manifests
    ]
    references.extend(
        (f"case {case.qualified_id}", reference)
        for case in cases
        for reference in case.all_references()
    )
    _validate_referenced_files(references, repo_root=repo_root)

    holdout_cases = [case for case in cases if case.is_holdout]
    if not holdout_cases:
        return True
    if holdout_provider is None:
        return False
    for case in holdout_cases:
        if not holdout_provider.has_case(case.case_id):
            raise EvalPackageError(
                f"case {case.qualified_id} is a holdout but no bundle is provisioned",
            )
        digest = holdout_provider.bundle_digest(case.case_id)
        if digest != case.holdout_bundle_sha256:
            raise EvalPackageError(
                f"case {case.qualified_id} pins holdout bundle {case.holdout_bundle_sha256} "
                f"but the provisioned bundle hashes to {digest}",
            )
    return True


def load_suite_package(
    suite_path: Path,
    *,
    repo_root: Path,
    holdout_provider: HoldoutProvider | None = None,
) -> EvalSuitePackage:
    """Load and fully validate one suite and every case it declares."""
    if not suite_path.name.endswith(SUITE_FILE_SUFFIX):
        raise EvalPackageError(f"suite manifests must end in {SUITE_FILE_SUFFIX}: {suite_path}")
    payload, manifest_sha256 = _read_manifest(suite_path, where="suite manifest")
    suite = EvalSuiteDefinition.parse(
        payload, where=f"suite {suite_path.name}", manifest_sha256=manifest_sha256,
    )

    cases: list[EvalCaseDefinition] = []
    for reference in suite.case_manifests:
        if not reference.ref.endswith(CASE_FILE_SUFFIX):
            raise EvalPackageError(
                f"case manifests must end in {CASE_FILE_SUFFIX}: {reference.ref}",
            )
        path = reference.resolve(repo_root)
        where = f"suite {suite.qualified_id} case_manifest {reference.ref}"
        case_payload, case_sha256 = _read_manifest(path, where=where)
        if case_sha256 != reference.sha256:
            raise EvalPackageError(
                f"{where} is pinned at {reference.sha256} but hashes to {case_sha256}",
            )
        cases.append(
            EvalCaseDefinition.parse(case_payload, where=where, manifest_sha256=case_sha256),
        )

    holdout_binding_verified = validate_package(
        suite, cases, repo_root=repo_root, holdout_provider=holdout_provider,
    )
    package_sha256 = _digest({
        "suite": suite.qualified_id,
        "suite_sha256": suite.manifest_sha256,
        "cases": sorted(
            [case.case_id, case.case_version, case.manifest_sha256] for case in cases
        ),
    })
    return EvalSuitePackage(
        suite=suite,
        cases=tuple(cases),
        package_sha256=package_sha256,
        holdout_binding_verified=holdout_binding_verified,
    )


def load_suite_registry(
    registry_root: Path,
    *,
    repo_root: Path,
    holdout_provider: HoldoutProvider | None = None,
) -> EvalSuiteRegistry:
    """Load every suite under a root and reject cross-suite identity collisions."""
    packages: list[EvalSuitePackage] = []
    suites: set[str] = set()
    case_owners: dict[str, str] = {}
    case_hashes: dict[tuple[str, int], str] = {}

    for suite_path in sorted(registry_root.rglob(f"*{SUITE_FILE_SUFFIX}")):
        package = load_suite_package(
            suite_path, repo_root=repo_root, holdout_provider=holdout_provider,
        )
        qualified = package.suite.qualified_id
        if qualified in suites:
            raise EvalPackageError(f"suite {qualified} is declared more than once")
        suites.add(qualified)
        for case in package.cases:
            owner = case_owners.setdefault(case.case_id, qualified)
            if owner != qualified:
                raise EvalPackageError(
                    f"case {case.case_id} is claimed by both {owner} and {qualified}",
                )
            key = (case.case_id, case.case_version)
            known_hash = case_hashes.setdefault(key, case.manifest_sha256)
            if known_hash != case.manifest_sha256:
                raise EvalPackageError(
                    f"case {case.qualified_id} has contradictory definitions across the registry",
                )
        packages.append(package)
    return EvalSuiteRegistry(packages=tuple(packages))
