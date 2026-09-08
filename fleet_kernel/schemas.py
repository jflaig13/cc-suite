# SPDX-License-Identifier: MPL-2.0
"""The 10 Phase-1 directive schemas (Objective, Run, Event, OwnershipClaim,
Approval, ToolInvocation, VerificationResult, Receipt, CostRecord, Incident)
as versioned code types.

Mapping to E4's nine overlap groups: see
cc_execs/memos/ccto/fleet_kernel_phase0_packet/02_state_surface_map.md's
table (kept current there; a doc-lint test in
tests/test_tier1_kernel_schema_doc_mapping.py fails if a group goes
unmapped).

Every row carries `tenant_id` (never nullable, never a shared default —
the Universal Tenant Isolation Invariant, docs/brain/070226__universal-
tenant-isolation-invariant.md) and `schema_version` (the "versioned" half
of the M1 acceptance bar — a schema evolves by adding a new version, never
by mutating a shipped one in place).

Each type exposes `to_row()` / `from_row()` for the DB round-trip
(psycopg, dict-row mapping) — the property M1's acceptance test exercises
(serialize -> store -> load -> equal).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from typing import Any, Optional


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


class ObjectiveState(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"
    REJECTED = "rejected"
    STALE = "stale"


class RunState(str, Enum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class VerificationVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class IncidentSeverity(str, Enum):
    TIER_S = "tier_s"
    TIER_A = "tier_a"
    TIER_B = "tier_b"
    TIER_C = "tier_c"


def _row_value(v: Any) -> Any:
    """Enum -> its value; everything else passes through unchanged. Used by
    to_row() so a stored row holds the plain enum value psycopg can bind."""
    return v.value if isinstance(v, Enum) else v


@dataclass
class _KernelRow:
    """Shared shape every Phase-1 schema carries. Not a table itself."""
    tenant_id: str
    id: uuid.UUID = field(default_factory=_new_id)
    schema_version: int = 1

    def to_row(self) -> dict:
        return {f.name: _row_value(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_row(cls, row: dict):
        kwargs = {f.name: row[f.name] for f in fields(cls) if f.name in row}
        return cls(**kwargs)


@dataclass
class Objective(_KernelRow):
    """Overlap groups #1, #2, #7 (handoff_ledger/handoff-md/announcements;
    OIL/announcements/FODL; TANDEM_BOARD) collapse into one row + lifecycle."""
    title: str = ""
    state: ObjectiveState = ObjectiveState.PROPOSED
    owner_role: str = ""
    strategic_objective_ref: str = ""  # traces to docs/brain/strategic-objectives.md
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    updated_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    deadline: Optional[datetime] = None
    parent_objective_id: Optional[uuid.UUID] = None

    def to_row(self) -> dict:
        row = super().to_row()
        row["state"] = _row_value(self.state)
        return row

    @classmethod
    def from_row(cls, row: dict):
        row = dict(row)
        if isinstance(row.get("state"), str):
            row["state"] = ObjectiveState(row["state"])
        return super(Objective, cls).from_row(row)


@dataclass
class Run(_KernelRow):
    """Overlap group #5 (dispatch_intents/selfwrite-guard/OIL) — a scheduled
    instance of an Objective."""
    objective_id: uuid.UUID = field(default_factory=_new_id)
    state: RunState = RunState.SCHEDULED
    worker_identity: str = ""
    attempt_number: int = 1
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    def to_row(self) -> dict:
        row = super().to_row()
        row["state"] = _row_value(self.state)
        return row

    @classmethod
    def from_row(cls, row: dict):
        row = dict(row)
        if isinstance(row.get("state"), str):
            row["state"] = RunState(row["state"])
        return super(Run, cls).from_row(row)


@dataclass
class Event(_KernelRow):
    """Overlap group #4 (audit_log.jsonl vs the dead audit_log.md) — the
    single authoritative append stream; stream_seq is the sequence-number
    boundary the dual-write/outbox discipline (D7) is built on."""
    stream_seq: int = 0
    event_type: str = ""
    payload: dict = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    objective_id: Optional[uuid.UUID] = None
    run_id: Optional[uuid.UUID] = None


@dataclass
class OwnershipClaim(_KernelRow):
    """Overlap group #6 (ready markers / codex heartbeats / watcher states)
    — liveness is an unexpired lease, never a marker file."""
    objective_id: uuid.UUID = field(default_factory=_new_id)
    owner_role: str = ""
    lease_token: str = ""
    acquired_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    expires_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    released_at: Optional[datetime] = None


@dataclass
class Approval(_KernelRow):
    """Overlap group #3 (FODL current.md / fodl audit log / emit events) —
    typed request -> decision -> consumption."""
    objective_id: uuid.UUID = field(default_factory=_new_id)
    requested_by: str = ""
    decision: ApprovalDecision = ApprovalDecision.PENDING
    decided_by: Optional[str] = None
    requested_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    decided_at: Optional[datetime] = None
    rationale: str = ""

    def to_row(self) -> dict:
        row = super().to_row()
        row["decision"] = _row_value(self.decision)
        return row

    @classmethod
    def from_row(cls, row: dict):
        row = dict(row)
        if isinstance(row.get("decision"), str):
            row["decision"] = ApprovalDecision(row["decision"])
        return super(Approval, cls).from_row(row)


@dataclass
class ToolInvocation(_KernelRow):
    """E3/E4's first missing schema — no per-action gate log exists today.
    Not an overlap-group replacement; a genuinely new capability."""
    run_id: uuid.UUID = field(default_factory=_new_id)
    tool_name: str = ""
    args_hash: str = ""
    idempotency_key: str = ""
    invoked_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    completed_at: Optional[datetime] = None
    result_code: Optional[int] = None


@dataclass
class VerificationResult(_KernelRow):
    """Overlap group #8 (verification_verdicts / cross_model_verdicts /
    obligations 0-byte ambiguity / cc_execs/verification)."""
    objective_id: uuid.UUID = field(default_factory=_new_id)
    verifier_identity: str = ""
    verdict: VerificationVerdict = VerificationVerdict.INCONCLUSIVE
    evidence_ref: str = ""
    verified_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    def to_row(self) -> dict:
        row = super().to_row()
        row["verdict"] = _row_value(self.verdict)
        return row

    @classmethod
    def from_row(cls, row: dict):
        row = dict(row)
        if isinstance(row.get("verdict"), str):
            row["verdict"] = VerificationVerdict(row["verdict"])
        return super(VerificationResult, cls).from_row(row)


@dataclass
class Receipt(_KernelRow):
    """Also overlap group #8's other half — a durable, corroborated artifact
    record backing a completion claim (Verification-Enforcement Standard)."""
    objective_id: uuid.UUID = field(default_factory=_new_id)
    artifact_ref: str = ""
    kind: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())


@dataclass
class CostRecord(_KernelRow):
    """E3/E4's second missing schema — no per-objective cost exists today."""
    objective_id: uuid.UUID = field(default_factory=_new_id)
    run_id: Optional[uuid.UUID] = None
    usd_amount: float = 0.0
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    recorded_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    @classmethod
    def from_row(cls, row: dict):
        # Postgres NUMERIC round-trips as decimal.Decimal via psycopg — cast
        # back to float so from_row(to_row(x)) == x (the round-trip contract).
        row = dict(row)
        if row.get("usd_amount") is not None:
            row["usd_amount"] = float(row["usd_amount"])
        return super(CostRecord, cls).from_row(row)


@dataclass
class Incident(_KernelRow):
    """Feeds Phase 8's incident-reconciliation + failure-eval generation."""
    objective_id: Optional[uuid.UUID] = None
    severity: IncidentSeverity = IncidentSeverity.TIER_C
    description: str = ""
    detected_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    resolved_at: Optional[datetime] = None
    root_cause_ref: str = ""

    def to_row(self) -> dict:
        row = super().to_row()
        row["severity"] = _row_value(self.severity)
        return row

    @classmethod
    def from_row(cls, row: dict):
        row = dict(row)
        if isinstance(row.get("severity"), str):
            row["severity"] = IncidentSeverity(row["severity"])
        return super(Incident, cls).from_row(row)


ALL_SCHEMAS = (
    Objective, Run, Event, OwnershipClaim, Approval,
    ToolInvocation, VerificationResult, Receipt, CostRecord, Incident,
)
