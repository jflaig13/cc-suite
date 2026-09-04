"""Persist and check qualifications derived from Evaluation Plane run history.

The qualification module owns the decision semantics. This module only harvests
append-only PostgreSQL evidence, constructs those domain inputs, persists the domain
row, and exposes a read-only checker.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import psycopg

from fleet_kernel.eval_plane.domain import (
    Criticality,
    GateMode,
    GraderKind,
    InconclusivePolicy,
    QUALIFYING_STATES,
    QualificationState,
    ResultState,
    SuitePolicy,
)
from fleet_kernel.eval_plane.graders import (
    AttemptAggregateRule,
    AttemptPolicy,
    CaseAttemptResult,
    CaseResult,
    GraderResult,
    aggregate,
    sha256_hex,
)
from fleet_kernel.eval_plane.qualification import (
    ExamIdentity,
    evaluate_staleness,
    issue_qualification,
    to_domain_qualification,
)


REQUIRED_CONSECUTIVE_GREENS = 2
QUALIFICATION_VALIDITY = timedelta(days=14)
REPO_ROOT = Path(__file__).resolve().parents[2]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class QualificationStoreError(RuntimeError):
    """Run history cannot support the requested qualification operation."""


@dataclass(frozen=True, slots=True)
class IssuanceResult:
    status: str
    reason: str
    qualification_id: str = ""
    run_id: str = ""
    suite_id: str = ""
    suite_version: str = ""
    target_fingerprint: str = ""
    state: str = ""

    @property
    def issued(self) -> bool:
        return self.status in {"issued", "already-issued"}

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "reason": self.reason,
            "qualification_id": self.qualification_id,
            "run_id": self.run_id,
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "target_fingerprint": self.target_fingerprint,
            "state": self.state,
        }


@dataclass(frozen=True, slots=True)
class CheckResult:
    verdict: str
    reason: str
    qualification_id: str = ""
    run_id: str = ""
    suite_id: str = ""
    suite_version: str = ""
    requested_suite_version: str = ""
    qualification_target_fingerprint: str = ""
    requested_target_fingerprint: str = ""
    state: str = ""
    scope: str = "fingerprint-only"

    def to_dict(self) -> dict[str, str]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "qualification_id": self.qualification_id,
            "run_id": self.run_id,
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "requested_suite_version": self.requested_suite_version,
            "qualification_target_fingerprint": self.qualification_target_fingerprint,
            "requested_target_fingerprint": self.requested_target_fingerprint,
            "state": self.state,
            "scope": self.scope,
        }


@dataclass(frozen=True, slots=True)
class _StalenessSubject:
    target_fingerprint: str
    exam_digest: str
    expires_at: datetime
    state: QualificationState


def _tenant_scope(conn: Any) -> str:
    row = conn.execute("SELECT kernel_current_tenant() AS tenant_id").fetchone()
    tenant_id = str(row["tenant_id"] or "")
    if not tenant_id.strip():
        raise QualificationStoreError(
            "the database session has no Evaluation Plane tenant scope"
        )
    return tenant_id


def assert_connection_identity(
    conn: Any, *, expected_principal: str, expected_database: str
) -> None:
    """Refuse a CLI connection whose PostgreSQL identity is not the named target."""
    if not expected_principal.strip() or not expected_database.strip():
        raise ValueError("expected principal and database may not be blank")
    row = conn.execute(
        """
        SELECT session_user::TEXT AS session_user,
               current_user::TEXT AS current_user,
               current_database()::TEXT AS database_name,
               role.rolsuper,
               role.rolreplication,
               role.rolbypassrls,
               current_setting('server_version_num')::INTEGER AS server_version_num
          FROM pg_catalog.pg_roles role
         WHERE role.rolname=session_user
        """
    ).fetchone()
    if row is None or row["session_user"] != expected_principal \
            or row["current_user"] != expected_principal:
        raise QualificationStoreError(
            "connected PostgreSQL principal does not match --expected-principal"
        )
    if row["database_name"] != expected_database:
        raise QualificationStoreError(
            "connected PostgreSQL database does not match --expected-database"
        )
    if row["rolsuper"] or row["rolreplication"] or row["rolbypassrls"]:
        raise QualificationStoreError(
            "connected PostgreSQL principal has unsafe elevated flags"
        )
    if not 160000 <= int(row["server_version_num"]) < 170000:
        raise QualificationStoreError("qualification CLIs require PostgreSQL 16")


def _candidate_runs(
    conn: Any,
    *,
    tenant_id: str,
    suite_id: str,
    target_fingerprint: str | None,
) -> list[Mapping[str, Any]]:
    where = "tenant_id=%s AND suite_id=%s"
    params: list[Any] = [tenant_id, suite_id]
    if target_fingerprint is not None:
        where += " AND target_fingerprint=%s"
        params.append(target_fingerprint)
    return list(
        conn.execute(
            f"""
            SELECT * FROM kernel_eval_runs
             WHERE {where}
             ORDER BY COALESCE(started_at, created_at) DESC,
                      created_at DESC,
                      id DESC
             LIMIT %s
            """,
            (*params, REQUIRED_CONSECUTIVE_GREENS + 1),
        ).fetchall()
    )


def _run_order_key(run: Mapping[str, Any]) -> tuple[datetime, datetime]:
    return (run["started_at"] or run["created_at"], run["created_at"])


def _ambiguous_streak_order(runs: Sequence[Mapping[str, Any]]) -> bool:
    relevant = runs[: REQUIRED_CONSECUTIVE_GREENS + 1]
    return any(
        _run_order_key(left) == _run_order_key(right)
        for left, right in zip(relevant, relevant[1:])
    )


def _subject_family(grader_rows: Sequence[Mapping[str, Any]]) -> str:
    families = {str(row["subject_family"]) for row in grader_rows}
    if len(families) != 1:
        raise QualificationStoreError(
            "the qualifying run must record exactly one subject_family"
        )
    return next(iter(families))


def _grader_result(row: Mapping[str, Any]) -> GraderResult:
    return GraderResult(
        grader_id=str(row["grader_id"]),
        grader_version=str(row["grader_version"]),
        kind=GraderKind(str(row["grader_kind"])),
        state=ResultState(str(row["result"])),
        reason=str(row["reason"]),
        criticality=Criticality(str(row["criticality"])),
        evidence_ref=str(row["evidence_ref"] or ""),
        evidence_sha256=str(row["evidence_sha256"] or ""),
        error_detail=str(row["error_detail"] or ""),
        grader_family=str(row["grader_family"]),
        judge_model_id=str(row["judge_model_id"] or ""),
        rubric_id=str(row["rubric_id"] or ""),
        rubric_version=str(row["rubric_version"] or ""),
        rubric_sha256=str(row["rubric_sha256"] or ""),
        adjudicator_identity=str(row["adjudicator_identity"] or ""),
        adjudicator_family=str(row["adjudicator_family"] or ""),
        adjudicated_at=row["adjudicated_at"],
    )


def _case_criticality(results: Sequence[GraderResult]) -> Criticality:
    if any(result.criticality is Criticality.CRITICAL for result in results):
        return Criticality.CRITICAL
    return Criticality.NONCRITICAL


def _harvest_outcome(
    conn: Any, *, tenant_id: str, run: Mapping[str, Any]
) -> tuple[Any, ExamIdentity, Mapping[str, Any]]:
    all_attempts = list(
        conn.execute(
            """
            SELECT * FROM kernel_eval_case_attempts
             WHERE tenant_id=%s AND run_id=%s
             ORDER BY case_id, attempt_number, id
            """,
            (tenant_id, run["id"]),
        ).fetchall()
    )
    attempts = [row for row in all_attempts if row["counted_in_aggregate"]]
    if not attempts:
        raise QualificationStoreError(
            f"run {run['id']} has no counted case attempts"
        )
    # Amendment 3 §2: attempt-pass and grader checks are scoped to COUNTED
    # attempts (DDL 042's exclusion model — a non-counted infra retry is
    # result='error' by schema and must not bar the run). Forbidden effects
    # stay checked across ALL attempts: a safety event that happened is
    # disqualifying even on an excluded retry.
    for attempt in attempts:
        if str(attempt["result"]) != "pass":
            raise QualificationStoreError(
                f"run {run['id']} attempt {attempt['id']} has result "
                f"{attempt['result']!r}"
            )
    for attempt in all_attempts:
        if int(attempt["forbidden_effect_attempts"]) > 0:
            raise QualificationStoreError(
                f"run {run['id']} attempt {attempt['id']} records forbidden effects"
            )
    all_grader_rows = list(
        conn.execute(
            """
            SELECT g.*
              FROM kernel_eval_grader_results g
              JOIN kernel_eval_case_attempts a
                ON a.id=g.attempt_id AND a.tenant_id=g.tenant_id
             WHERE a.tenant_id=%s AND a.run_id=%s
             ORDER BY a.case_id, a.attempt_number, g.grader_id, g.grader_version
            """,
            (tenant_id, run["id"]),
        ).fetchall()
    )
    counted_ids = {row["id"] for row in attempts}
    grader_rows = [row for row in all_grader_rows if row["attempt_id"] in counted_ids]
    if any(str(row["result"]) == "error" for row in grader_rows):
        raise QualificationStoreError(f"run {run['id']} has a grader error")
    # Amendment 3 §2, the restored §4 predicate applied to EVERY leg this
    # function harvests: a blocking-eligible (critical or noncritical)
    # grader verdict other than 'pass' makes the leg red — fail, error,
    # and inconclusive alike. Advisory verdicts never block.
    for row in grader_rows:
        if (
            str(row["criticality"]) in ("critical", "noncritical")
            and str(row["result"]) != "pass"
        ):
            raise QualificationStoreError(
                f"run {run['id']} has a blocking-eligible grader verdict "
                f"{row['result']!r} ({row['criticality']} grader "
                f"{row['grader_id']!r})"
            )
    if not grader_rows:
        raise QualificationStoreError(
            f"run {run['id']} has no grader results"
        )
    by_attempt: dict[Any, list[GraderResult]] = {}
    for row in grader_rows:
        by_attempt.setdefault(row["attempt_id"], []).append(_grader_result(row))

    for attempt in attempts:
        blocking = [
            result for result in by_attempt.get(attempt["id"], ())
            if result.criticality in {Criticality.CRITICAL, Criticality.NONCRITICAL}
        ]
        if not blocking:
            raise QualificationStoreError(
                f"run {run['id']} attempt {attempt['id']} has no blocking-eligible grader result"
            )

    by_case: dict[str, list[tuple[Mapping[str, Any], CaseAttemptResult]]] = {}
    for row in attempts:
        results = tuple(by_attempt.get(row["id"], ()))
        attempt = CaseAttemptResult(
            case_id=str(row["case_id"]),
            attempt_number=int(row["attempt_number"]),
            grader_results=results,
            forbidden_effect_attempted=int(row["forbidden_effect_attempts"]) > 0,
            forbidden_effect_detail=(
                f"persisted attempt recorded {row['forbidden_effect_attempts']} "
                "forbidden effect attempt(s)"
                if int(row["forbidden_effect_attempts"]) > 0
                else ""
            ),
            cost_usd=int(row["cost_microusd"]) / 1_000_000,
            latency_ms=int(row["latency_ms"]),
        )
        by_case.setdefault(str(row["case_id"]), []).append((row, attempt))

    cases: list[CaseResult] = []
    for case_id, entries in sorted(by_case.items()):
        attempt_results = tuple(item[1] for item in entries)
        all_graders = tuple(
            grader
            for attempt in attempt_results
            for grader in attempt.grader_results
        )
        cases.append(
            CaseResult(
                case_id=case_id,
                required=True,
                criticality=_case_criticality(all_graders),
                policy=AttemptPolicy(
                    attempts=max(attempt.attempt_number for attempt in attempt_results),
                    aggregate_rule=AttemptAggregateRule.ALL_MUST_PASS,
                ),
                attempts=attempt_results,
            )
        )

    policy = SuitePolicy(
        gate_mode=GateMode.ADVISORY,
        inconclusive_policy=InconclusivePolicy.BLOCK,
        noncritical_pass_threshold=1.0,
    )
    outcome = aggregate(
        cases,
        policy,
        suite_id=str(run["suite_id"]),
        suite_version=str(run["suite_version"]),
        subject_family=_subject_family(grader_rows),
    )

    grader_versions: dict[str, str] = {}
    rubric_versions: dict[str, str] = {}
    for row in grader_rows:
        grader_id = str(row["grader_id"])
        version = str(row["grader_version"])
        if grader_id in grader_versions and grader_versions[grader_id] != version:
            raise QualificationStoreError(
                f"grader {grader_id!r} has multiple versions in one run"
            )
        grader_versions[grader_id] = version
        if str(row["rubric_id"] or ""):
            rubric_versions[str(row["rubric_id"])] = str(row["rubric_version"])
    exam = ExamIdentity(
        suite_id=str(run["suite_id"]),
        suite_version=str(run["suite_version"]),
        grader_versions=grader_versions,
        rubric_versions=rubric_versions,
        thresholds={"noncritical_pass_threshold": 1.0},
        attempt_policy_digest=sha256_hex(run["attempt_policy"]),
    )
    outcome = replace(
        outcome,
        metrics={**dict(outcome.metrics), "qualification_exam": exam.to_dict()},
    )
    snapshot = {
        "run": {
            "id": str(run["id"]),
            "suite_id": str(run["suite_id"]),
            "suite_version": str(run["suite_version"]),
            "suite_manifest_sha256": str(run["suite_manifest_sha256"]),
            "target_fingerprint": str(run["target_fingerprint"]),
            "state": str(run["state"]),
            "attempt_policy": dict(run["attempt_policy"]),
            "evidence_sha256": str(run["evidence_sha256"] or ""),
        },
        "attempts": [
            {
                "id": str(row["id"]),
                "case_id": str(row["case_id"]),
                "attempt_number": int(row["attempt_number"]),
                "result": str(row["result"]),
                "counted_in_aggregate": bool(row["counted_in_aggregate"]),
                "forbidden_effect_attempts": int(row["forbidden_effect_attempts"]),
                "input_sha256": str(row["input_sha256"]),
                "fixture_sha256": str(row["fixture_sha256"]),
                "trajectory_sha256": str(row["trajectory_sha256"]),
            }
            for row in all_attempts
        ],
        "graders": [
            {
                "id": str(row["id"]),
                "attempt_id": str(row["attempt_id"]),
                "grader_id": str(row["grader_id"]),
                "grader_version": str(row["grader_version"]),
                "result": str(row["result"]),
                "criticality": str(row["criticality"]),
                "evidence_sha256": str(row["evidence_sha256"] or ""),
                "rubric_id": str(row["rubric_id"] or ""),
                "rubric_version": str(row["rubric_version"] or ""),
                "rubric_sha256": str(row["rubric_sha256"] or ""),
            }
            for row in all_grader_rows
        ],
    }
    return outcome, exam, snapshot


_QUALIFYING_STATE_VALUES = tuple(sorted(state.value for state in QUALIFYING_STATES))


def _live_equivalent(
    conn: Any,
    *,
    tenant_id: str,
    suite_id: str,
    suite_version: str,
    target_fingerprint: str,
) -> Mapping[str, Any] | None:
    return conn.execute(
        """
        SELECT * FROM kernel_eval_qualifications
         WHERE tenant_id=%s AND suite_id=%s AND suite_version=%s
           AND target_fingerprint=%s
           AND state = ANY(%s)
         ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (
            tenant_id, suite_id, suite_version, target_fingerprint,
            list(_QUALIFYING_STATE_VALUES),
        ),
    ).fetchone()


def _expire_lapsed(conn: Any, row: Mapping[str, Any], *, now: datetime) -> bool:
    """Transition a clock-lapsed live certificate to EXPIRED (Amendment 3 §2).

    This is the store's single legal mutation: the domain state machine and
    the DDL qualification guard both permit qualified/advisory_qualified ->
    expired. Clearing the live slot is what makes renewal possible at all —
    the partial unique index only covers qualifying states. Returns True when
    the row was expired by this call.
    """
    if row["expires_at"] is None or row["expires_at"] > now:
        return False
    updated = conn.execute(
        """
        UPDATE kernel_eval_qualifications
           SET state='expired', invalidated_at=%s,
               invalidation_reason='expired by clock; live slot released for renewal'
         WHERE id=%s AND tenant_id=%s AND state = ANY(%s)
        """,
        (now, row["id"], row["tenant_id"], list(_QUALIFYING_STATE_VALUES)),
    )
    return updated.rowcount == 1


def _already_issued(row: Mapping[str, Any]) -> IssuanceResult:
    return IssuanceResult(
        status="already-issued",
        reason="an equivalent live qualification already exists",
        qualification_id=str(row["id"]),
        run_id=str(row["run_id"]),
        suite_id=str(row["suite_id"]),
        suite_version=str(row["suite_version"]),
        target_fingerprint=str(row["target_fingerprint"]),
        state=str(row["state"]),
    )


def _suite_path_from_input(
    current_suite: str | Path, *, repo_root: Path
) -> Path:
    text = str(current_suite)
    candidate = Path(text)
    if candidate.is_file():
        return candidate.resolve()
    if not _SHA256_RE.fullmatch(text):
        raise QualificationStoreError(
            "current-suite must be a suite package path or a 64-hex manifest sha"
        )
    matches: list[Path] = []
    from fleet_kernel.eval_plane.suite_package import load_suite_package

    for path in sorted((repo_root / "eval/suites").rglob("*.suite.json")):
        package = load_suite_package(path, repo_root=repo_root)
        if package.suite.manifest_sha256 == text:
            matches.append(path)
    if len(matches) != 1:
        raise QualificationStoreError(
            f"current-suite manifest sha resolved to {len(matches)} suite packages"
        )
    return matches[0]


def exam_identity_from_suite(
    current_suite: str | Path, *, repo_root: Path = REPO_ROOT
) -> tuple[Any, ExamIdentity]:
    """Load the versioned suite package and derive its exact current exam identity."""
    from fleet_kernel.eval_plane.suite_package import load_suite_package

    path = _suite_path_from_input(current_suite, repo_root=repo_root)
    package = load_suite_package(path, repo_root=repo_root)
    grader_versions: dict[str, str] = {}
    for case in package.cases:
        for grader in case.graders:
            version = str(grader.grader_version)
            previous = grader_versions.setdefault(grader.grader_id, version)
            if previous != version:
                raise QualificationStoreError(
                    f"suite assigns grader {grader.grader_id!r} multiple versions"
                )
    rubric_versions: dict[str, str] = {}
    for reference in package.suite.grader_manifests:
        payload = json.loads(reference.resolve(repo_root).read_text(encoding="utf-8"))
        rubric_id = str(payload.get("rubric_id") or "")
        if rubric_id:
            rubric_versions[rubric_id] = str(payload.get("rubric_version") or "")
    suite = package.suite
    policy = suite.attempt_policy
    exam = ExamIdentity(
        suite_id=suite.suite_id,
        suite_version=str(suite.suite_version),
        grader_versions=grader_versions,
        rubric_versions=rubric_versions,
        thresholds={
            "min_case_pass_rate": suite.qualification_thresholds.min_case_pass_rate,
            "max_critical_failures": suite.qualification_thresholds.max_critical_failures,
            "max_error_rate": suite.qualification_thresholds.max_error_rate,
            "gate_intent": suite.gate_intent.value,
            "inconclusive_policy": suite.inconclusive_policy.value,
        },
        attempt_policy_digest=sha256_hex({
            "attempts": policy.attempts,
            "sampling": dict(policy.sampling),
            "seed": policy.seed,
            "infra_retry_limit": policy.infra_retry_limit,
            "aggregate_rule": policy.aggregate_rule.value,
            "critical_failure_rule": policy.critical_failure_rule.value,
        }),
    )
    return package, exam


def _insert_qualification(conn: Any, row: Any) -> None:
    values = row.to_row()
    conn.execute(
        """
        INSERT INTO kernel_eval_qualifications (
          id, tenant_id, run_id, schema_version, suite_id, suite_version,
          target_fingerprint, state, gate_mode, inconclusive_policy,
          case_results, aggregate_metrics, critical_failure_count, error_count,
          inconclusive_count, forbidden_effect_count, baseline_qualification_id,
          reviewer_identity, reviewer_family, subject_family, receipt_ref,
          receipt_sha256, snapshot_sha256, expires_at, invalidated_at,
          invalidation_reason, created_at, decided_at
        ) VALUES (
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,
          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
        )
        """,
        (
            values["id"], values["tenant_id"], values["run_id"],
            values["schema_version"], values["suite_id"], values["suite_version"],
            values["target_fingerprint"], values["state"], values["gate_mode"],
            values["inconclusive_policy"], json.dumps(values["case_results"]),
            json.dumps(values["aggregate_metrics"]),
            values["critical_failure_count"], values["error_count"],
            values["inconclusive_count"], values["forbidden_effect_count"],
            values["baseline_qualification_id"], values["reviewer_identity"],
            values["reviewer_family"], values["subject_family"],
            values["receipt_ref"], values["receipt_sha256"],
            values["snapshot_sha256"], values["expires_at"],
            values["invalidated_at"], values["invalidation_reason"],
            values["created_at"], values["decided_at"],
        ),
    )


def issue_from_history(
    conn: Any,
    *,
    suite_id: str,
    target_fingerprint: str | None = None,
    current_suite: str | Path | ExamIdentity | None = None,
    repo_root: Path = REPO_ROOT,
    now: datetime | None = None,
) -> IssuanceResult:
    """Issue one qualification from the latest two run outcomes.

    Amendment 3 §2: `current_suite` is REQUIRED — every certificate persists
    the suite-derived exam identity so full-scope staleness checks are
    coherent. Pass a suite package path or manifest sha (the CLI path, which
    also binds the manifest to the newest run), or a ready ExamIdentity for
    in-process callers, whose suite_id/suite_version are still verified.
    """
    if not suite_id.strip():
        raise ValueError("suite_id may not be blank")
    if current_suite is None:
        raise QualificationStoreError(
            "current_suite is required at issuance: a certificate must "
            "persist the suite-derived exam identity (Amendment 3)"
        )
    issued_at = now or datetime.now(timezone.utc)
    tenant_id = _tenant_scope(conn)
    runs = _candidate_runs(
        conn,
        tenant_id=tenant_id,
        suite_id=suite_id,
        target_fingerprint=target_fingerprint,
    )
    if runs:
        newest = runs[0]
        existing = _live_equivalent(
            conn,
            tenant_id=tenant_id,
            suite_id=suite_id,
            suite_version=str(newest["suite_version"]),
            target_fingerprint=str(newest["target_fingerprint"]),
        )
        if existing is not None and not _expire_lapsed(
            conn, existing, now=issued_at
        ):
            # Codex R3 H1: the transition may have been lost to a concurrent
            # renewer, in which case `existing` is a stale pre-update image.
            # Reread and answer from the CURRENT live row; if the slot is now
            # free, fall through and issue.
            fresh = _live_equivalent(
                conn,
                tenant_id=tenant_id,
                suite_id=suite_id,
                suite_version=str(newest["suite_version"]),
                target_fingerprint=str(newest["target_fingerprint"]),
            )
            if fresh is not None:
                return _already_issued(fresh)
    if len(runs) < REQUIRED_CONSECUTIVE_GREENS:
        return IssuanceResult(
            status="refused",
            reason=(
                f"requires {REQUIRED_CONSECUTIVE_GREENS} consecutive green "
                f"runs; found {len(runs)}"
            ),
            suite_id=suite_id,
            target_fingerprint=target_fingerprint or "",
        )
    newest = runs[0]
    if _ambiguous_streak_order(runs):
        return IssuanceResult(
            status="refused",
            reason="run ordering is ambiguous at the qualifying-streak boundary",
            suite_id=suite_id,
            suite_version=str(newest["suite_version"]),
            target_fingerprint=str(newest["target_fingerprint"]),
        )
    selected = runs[:REQUIRED_CONSECUTIVE_GREENS]
    if any(str(run["state"]) != "completed" for run in selected):
        blocking = next(run for run in selected if str(run["state"]) != "completed")
        return IssuanceResult(
            status="refused",
            reason=(
                f"run {blocking['id']} in the qualifying streak has state "
                f"{blocking['state']!r}"
            ),
            run_id=str(blocking["id"]),
            suite_id=suite_id,
            suite_version=str(blocking["suite_version"]),
            target_fingerprint=str(blocking["target_fingerprint"]),
        )
    if len({str(run["target_fingerprint"]) for run in selected}) != 1:
        return IssuanceResult(
            status="refused",
            reason="the latest two completed runs have different target fingerprints",
            suite_id=suite_id,
            target_fingerprint=str(newest["target_fingerprint"]),
        )
    if len({str(run["suite_version"]) for run in selected}) != 1:
        return IssuanceResult(
            status="refused",
            reason="the latest two runs have different suite versions",
            suite_id=suite_id,
            suite_version=str(newest["suite_version"]),
            target_fingerprint=str(newest["target_fingerprint"]),
        )
    harvests: list[tuple[Any, ExamIdentity, Mapping[str, Any]]] = []
    for run in reversed(selected):
        try:
            harvests.append(_harvest_outcome(conn, tenant_id=tenant_id, run=run))
        except (QualificationStoreError, ValueError) as exc:
            return IssuanceResult(
                status="refused",
                reason=f"run {run['id']} is red under qualification harvesting: {exc}",
                run_id=str(run["id"]),
                suite_id=suite_id,
                suite_version=str(run["suite_version"]),
                target_fingerprint=str(run["target_fingerprint"]),
            )
    if len({harvest[1].digest() for harvest in harvests}) != 1:
        return IssuanceResult(
            status="refused",
            reason="the qualifying streak was evaluated under different exam identities",
            suite_id=suite_id,
            suite_version=str(newest["suite_version"]),
            target_fingerprint=str(newest["target_fingerprint"]),
        )
    outcome = harvests[-1][0]
    harvested_exam = harvests[-1][1]
    if isinstance(current_suite, ExamIdentity):
        exam = current_suite
        if exam.suite_id != suite_id \
                or str(exam.suite_version) != str(newest["suite_version"]):
            raise QualificationStoreError(
                "current exam identity does not match the newest qualifying run"
            )
        # Codex R3 C1: an in-process identity must agree with what the runs
        # actually recorded — grader and rubric versions are the objectively
        # harvested parts, so they must match exactly before persistence.
        # (Thresholds and the attempt-policy digest are suite-policy claims
        # the run evidence cannot confirm; the CLI path binds those via the
        # suite manifest instead.)
        if exam.grader_versions != harvested_exam.grader_versions \
                or exam.rubric_versions != harvested_exam.rubric_versions:
            raise QualificationStoreError(
                "current exam identity disagrees with the harvested runs: "
                f"graders {exam.grader_versions!r} vs recorded "
                f"{harvested_exam.grader_versions!r}"
            )
    else:
        package, exam = exam_identity_from_suite(current_suite, repo_root=repo_root)
        if package.suite.suite_id != suite_id \
                or str(package.suite.suite_version) != str(newest["suite_version"]):
            raise QualificationStoreError(
                "current suite identity does not match the newest qualifying run"
            )
        if package.suite.manifest_sha256 != str(newest["suite_manifest_sha256"]):
            raise QualificationStoreError(
                "current suite manifest does not match the newest qualifying run"
            )
    outcome = replace(
        outcome,
        metrics={**dict(outcome.metrics), "qualification_exam": exam.to_dict()},
    )
    qualification = issue_qualification(
        qualification_id=str(uuid.uuid4()),
        target_fingerprint=str(newest["target_fingerprint"]),
        exam=exam,
        outcome=outcome,
        gate_mode=GateMode.ADVISORY,
        reviewer_identity="eval-plane-qualification-issuer",
        reviewer_family="qualification-control",
        subject_family=outcome.subject_family,
        issued_at=issued_at,
        expires_at=issued_at + QUALIFICATION_VALIDITY,
    )
    if qualification.state not in QUALIFYING_STATES:
        return IssuanceResult(
            status="refused",
            reason=(
                "the Evaluation Plane domain refused a qualifying state: "
                f"{qualification.state.value}"
            ),
            run_id=str(newest["id"]),
            suite_id=suite_id,
            suite_version=str(newest["suite_version"]),
            target_fingerprint=str(newest["target_fingerprint"]),
            state=qualification.state.value,
        )
    persisted = to_domain_qualification(
        qualification,
        tenant_id=tenant_id,
        run_id=newest["id"],
        snapshot_sha256=sha256_hex({"harvested_runs": [item[2] for item in harvests]}),
        receipt_ref=f"eval-qualification://{qualification.qualification_id}",
    )
    try:
        with conn.transaction():
            _insert_qualification(conn, persisted)
    except psycopg.errors.UniqueViolation:
        existing = _live_equivalent(
            conn,
            tenant_id=tenant_id,
            suite_id=suite_id,
            suite_version=str(newest["suite_version"]),
            target_fingerprint=str(newest["target_fingerprint"]),
        )
        if existing is not None:
            return _already_issued(existing)
        raise
    return IssuanceResult(
        status="issued",
        reason="two consecutive green runs share the same target fingerprint",
        qualification_id=str(persisted.id),
        run_id=str(newest["id"]),
        suite_id=suite_id,
        suite_version=str(newest["suite_version"]),
        target_fingerprint=str(newest["target_fingerprint"]),
        state=persisted.state.value,
    )


def _exam_from_metrics(row: Mapping[str, Any]) -> ExamIdentity:
    metrics = row["aggregate_metrics"]
    raw = metrics.get("qualification_exam") if isinstance(metrics, Mapping) else None
    if not isinstance(raw, Mapping):
        raise QualificationStoreError(
            "qualification has no persisted exam identity for staleness evaluation"
        )
    return ExamIdentity(
        suite_id=str(raw["suite_id"]),
        suite_version=str(raw["suite_version"]),
        grader_versions=dict(raw.get("grader_versions", {})),
        rubric_versions=dict(raw.get("rubric_versions", {})),
        thresholds=dict(raw.get("thresholds", {})),
        attempt_policy_digest=str(raw.get("attempt_policy_digest", "")),
    )


def check_qualification(
    conn: Any,
    *,
    suite_id: str,
    suite_version: str,
    target_fingerprint: str,
    current_suite: str | Path | None = None,
    repo_root: Path = REPO_ROOT,
    now: datetime | None = None,
) -> CheckResult:
    """Return a fail-closed certificate verdict without mutating the database."""
    if not suite_id.strip() or not suite_version.strip():
        raise ValueError("suite_id and suite_version may not be blank")
    scope = "full" if current_suite is not None else "fingerprint-only"
    tenant_id = _tenant_scope(conn)
    row = conn.execute(
        """
        SELECT * FROM kernel_eval_qualifications
         WHERE tenant_id=%s AND suite_id=%s
         ORDER BY (suite_version=%s) DESC,
                  (target_fingerprint=%s) DESC,
                  created_at DESC, id DESC
         LIMIT 1
        """,
        (tenant_id, suite_id, suite_version, target_fingerprint),
    ).fetchone()
    if not row:
        return CheckResult(
            verdict="ABSENT",
            reason="no qualification exists for this suite",
            suite_id=suite_id,
            requested_suite_version=suite_version,
            requested_target_fingerprint=target_fingerprint,
            scope=scope,
        )
    base = {
        "qualification_id": str(row["id"]),
        "run_id": str(row["run_id"]),
        "suite_id": str(row["suite_id"]),
        "suite_version": str(row["suite_version"]),
        "requested_suite_version": suite_version,
        "qualification_target_fingerprint": str(row["target_fingerprint"]),
        "requested_target_fingerprint": target_fingerprint,
        "state": str(row["state"]),
        "scope": scope,
    }
    state = QualificationState(str(row["state"]))
    if state not in QUALIFYING_STATES:
        return CheckResult(
            verdict=state.value.upper(),
            reason=f"qualification state {state.value!r} is not live",
            **base,
        )
    try:
        exam = _exam_from_metrics(row)
    except (QualificationStoreError, KeyError, TypeError, ValueError) as exc:
        return CheckResult(
            verdict="ERROR",
            reason=f"qualification exam identity is unavailable: {exc}",
            **base,
        )
    current_exam = exam
    if current_suite is not None:
        package, current_exam = exam_identity_from_suite(
            current_suite, repo_root=repo_root
        )
        if package.suite.suite_id != suite_id \
                or str(package.suite.suite_version) != suite_version:
            raise QualificationStoreError(
                "current suite identity does not match the requested suite and version"
            )
    if str(row["suite_version"]) != suite_version:
        return CheckResult(
            verdict="STALE",
            reason="the requested suite version differs from the certificate",
            **base,
        )
    evaluated = evaluate_staleness(
        _StalenessSubject(
            target_fingerprint=str(row["target_fingerprint"]),
            exam_digest=exam.digest(),
            expires_at=row["expires_at"],
            state=state,
        ),
        current_target_fingerprint=target_fingerprint,
        current_exam=current_exam,
        now=now or datetime.now(timezone.utc),
    )
    if evaluated is QualificationState.STALE:
        return CheckResult(
            verdict="STALE",
            reason="the requested target fingerprint or current exam identity differs from the certificate",
            **base,
        )
    if evaluated in QUALIFYING_STATES:
        return CheckResult(
            verdict="VALID",
            reason="the certificate is live and fingerprint-bound to this target",
            **base,
        )
    return CheckResult(
        verdict=evaluated.value.upper(),
        reason=f"staleness evaluation returned {evaluated.value!r}",
        **base,
    )
