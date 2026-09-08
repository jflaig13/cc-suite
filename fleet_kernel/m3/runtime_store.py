# SPDX-License-Identifier: MPL-2.0
"""Transactional M3 runtime state for the autonomy migration.

The kernel event is the authoritative append boundary. Event sequence
allocation and outbox enqueue commit together; legacy files are rendered only
from the outbox. Every externally-retried operation carries a stable
idempotency key so a crash after the application commit but before a DBOS step
checkpoint replays to the existing row instead of duplicating an effect.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


_ID_NAMESPACE = uuid.UUID("e5f35cb7-a7bd-47ce-b7f3-f3dc82afbfb3")


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_ID_NAMESPACE, "\x1f".join(parts))


def _connect(conninfo: dict):
    return psycopg.connect(**conninfo, row_factory=dict_row)


def _require_identity(tenant_id: str, key: str) -> None:
    if not tenant_id.strip():
        raise ValueError("tenant_id must be non-empty")
    if not key.strip():
        raise ValueError("idempotency/cycle key must be non-empty")


@dataclass(frozen=True, slots=True)
class CycleIdentity:
    objective_id: uuid.UUID
    run_id: uuid.UUID
    cycle_key: str


@dataclass(frozen=True, slots=True)
class AppendedEvent:
    event_id: uuid.UUID
    outbox_id: uuid.UUID
    stream_seq: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class ProviderCost:
    cost_record_id: uuid.UUID
    usd_amount: Decimal
    tokens_in: int
    tokens_out: int
    replayed: bool


def ensure_cycle(
    conninfo: dict,
    *,
    tenant_id: str,
    cycle_key: str,
    run_type: str,
    worker_identity: str,
) -> CycleIdentity:
    """Create Objective+Run atomically and return the same pair on retry."""
    _require_identity(tenant_id, cycle_key)
    objective_id = _id(tenant_id, cycle_key, "objective")
    run_id = _id(tenant_id, cycle_key, "run")
    now = datetime.now(timezone.utc)
    with _connect(conninfo) as conn:
        with conn.transaction():
            conn.execute(
                "INSERT INTO objectives "
                "(id, tenant_id, schema_version, title, state, owner_role, strategic_objective_ref, "
                "created_at, updated_at) VALUES (%s, %s, 1, %s, 'active', 'scribe', "
                "'SO-autonomy-engine', %s, %s) ON CONFLICT (id) DO NOTHING",
                (objective_id, tenant_id, f"autonomy-engine-{run_type}", now, now),
            )
            conn.execute(
                "INSERT INTO runs "
                "(id, tenant_id, schema_version, objective_id, state, worker_identity, "
                "attempt_number, started_at, cycle_key) "
                "VALUES (%s, %s, 1, %s, 'running', %s, 1, %s, %s) "
                "ON CONFLICT (tenant_id, cycle_key) WHERE cycle_key IS NOT NULL DO NOTHING",
                (run_id, tenant_id, objective_id, worker_identity, now, cycle_key),
            )
            row = conn.execute(
                "SELECT id, objective_id, cycle_key FROM runs "
                "WHERE tenant_id = %s AND cycle_key = %s",
                (tenant_id, cycle_key),
            ).fetchone()
            if row is None:
                raise RuntimeError("cycle binding was not created")
    return CycleIdentity(row["objective_id"], row["id"], row["cycle_key"])


def finish_cycle(
    conninfo: dict,
    *,
    tenant_id: str,
    run_id: uuid.UUID,
    objective_id: uuid.UUID,
    state: str,
) -> None:
    """Set terminal Run/Objective state with tenant-scoped row checks."""
    if state not in {"succeeded", "failed", "timed_out", "cancelled"}:
        raise ValueError(f"nonterminal run state refused: {state}")
    objective_state = "done" if state == "succeeded" else "blocked"
    with _connect(conninfo) as conn:
        with conn.transaction():
            run_result = conn.execute(
                "UPDATE runs SET state = %s, ended_at = now() "
                "WHERE tenant_id = %s AND id = %s AND objective_id = %s",
                (state, tenant_id, run_id, objective_id),
            )
            objective_result = conn.execute(
                "UPDATE objectives SET state = %s, updated_at = now() "
                "WHERE tenant_id = %s AND id = %s",
                (objective_state, tenant_id, objective_id),
            )
            if run_result.rowcount != 1 or objective_result.rowcount != 1:
                raise RuntimeError("tenant-scoped cycle rows not found")


def append_event_with_outbox(
    conninfo: dict,
    *,
    tenant_id: str,
    objective_id: uuid.UUID,
    run_id: uuid.UUID,
    event_type: str,
    event_payload: dict[str, Any],
    destination: str,
    outbox_payload: dict[str, Any],
    idempotency_key: str,
) -> AppendedEvent:
    """Allocate sequence + append event + enqueue render in one transaction."""
    _require_identity(tenant_id, idempotency_key)
    event_id = _id(tenant_id, idempotency_key, "event")
    outbox_id = _id(tenant_id, idempotency_key, "outbox")
    with _connect(conninfo) as conn:
        with conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"event:{tenant_id}:{idempotency_key}",),
            )
            existing = conn.execute(
                "SELECT o.event_id, o.id, o.stream_seq, o.destination, "
                "e.id AS authoritative_event_id, e.objective_id, e.run_id, e.event_type, "
                "e.payload = %s AS event_payload_matches, "
                "o.payload = %s AS outbox_payload_matches "
                "FROM kernel_outbox o LEFT JOIN events e ON e.id = o.event_id "
                "AND e.tenant_id = o.tenant_id AND e.stream_seq = o.stream_seq "
                "WHERE o.tenant_id = %s AND o.idempotency_key = %s",
                (Jsonb(event_payload), Jsonb(outbox_payload), tenant_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["authoritative_event_id"] is None:
                    raise RuntimeError(
                        "LOUD outbox row exists but authoritative event linkage is missing"
                    )
                replay_matches = (
                    existing["objective_id"] == objective_id
                    and existing["run_id"] == run_id
                    and existing["event_type"] == event_type
                    and existing["destination"] == destination
                    and existing["event_payload_matches"] is True
                    and existing["outbox_payload_matches"] is True
                )
                if not replay_matches:
                    raise RuntimeError(
                        "idempotency collision: existing event/outbox content differs"
                    )
                return AppendedEvent(
                    existing["event_id"], existing["id"], existing["stream_seq"], True,
                )
            seq_row = conn.execute(
                "INSERT INTO event_stream_heads (tenant_id, next_seq, outbox_start_seq) "
                "VALUES (%s, 2, 1) "
                "ON CONFLICT (tenant_id) DO UPDATE "
                "SET outbox_start_seq = COALESCE(event_stream_heads.outbox_start_seq, "
                "event_stream_heads.next_seq), "
                "next_seq = event_stream_heads.next_seq + 1 "
                "RETURNING next_seq - 1 AS stream_seq",
                (tenant_id,),
            ).fetchone()
            stream_seq = int(seq_row["stream_seq"])
            conn.execute(
                "INSERT INTO events "
                "(id, tenant_id, schema_version, stream_seq, event_type, payload, occurred_at, "
                "objective_id, run_id) VALUES (%s, %s, 1, %s, %s, %s, now(), %s, %s)",
                (
                    event_id, tenant_id, stream_seq, event_type, Jsonb(event_payload),
                    objective_id, run_id,
                ),
            )
            conn.execute(
                "INSERT INTO kernel_outbox "
                "(id, tenant_id, event_id, stream_seq, destination, adapter, payload, "
                "idempotency_key) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    outbox_id, tenant_id, event_id, stream_seq, destination, destination,
                    Jsonb(outbox_payload), idempotency_key,
                ),
            )
    return AppendedEvent(event_id, outbox_id, stream_seq, False)


def record_provider_cost(
    conninfo: dict,
    *,
    tenant_id: str,
    objective_id: uuid.UUID,
    run_id: uuid.UUID,
    provider_request_id: str,
    usd_amount: str | Decimal,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> ProviderCost:
    """Record actual provider usage once, keyed by the provider request."""
    _require_identity(tenant_id, provider_request_id)
    amount = Decimal(str(usd_amount)).quantize(Decimal("0.000001"))
    if amount < 0 or tokens_in < 0 or tokens_out < 0:
        raise ValueError("provider usage cannot be negative")
    cost_id = _id(tenant_id, provider_request_id, "cost")
    with _connect(conninfo) as conn:
        with conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"cost:{tenant_id}:{provider_request_id}",),
            )
            existing = conn.execute(
                "SELECT c.id, c.usd_amount, c.tokens_in, c.tokens_out "
                "FROM m3_provider_cost_dedup d JOIN cost_records c ON c.id = d.cost_record_id "
                "WHERE d.tenant_id = %s AND d.provider_request_id = %s",
                (tenant_id, provider_request_id),
            ).fetchone()
            if existing is not None:
                return ProviderCost(
                    existing["id"], Decimal(existing["usd_amount"]),
                    existing["tokens_in"], existing["tokens_out"], True,
                )
            conn.execute(
                "INSERT INTO cost_records "
                "(id, tenant_id, schema_version, objective_id, run_id, usd_amount, model, "
                "tokens_in, tokens_out, recorded_at) "
                "VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, now())",
                (
                    cost_id, tenant_id, objective_id, run_id, amount, model,
                    tokens_in, tokens_out,
                ),
            )
            conn.execute(
                "INSERT INTO m3_provider_cost_dedup "
                "(tenant_id, provider_request_id, cost_record_id) VALUES (%s, %s, %s)",
                (tenant_id, provider_request_id, cost_id),
            )
    return ProviderCost(cost_id, amount, tokens_in, tokens_out, False)


def park_quota(
    conninfo: dict,
    *,
    tenant_id: str,
    run_id: uuid.UUID,
    reason: str,
    resume_at: datetime,
) -> None:
    if resume_at.tzinfo is None:
        raise ValueError("resume_at must be timezone-aware")
    with _connect(conninfo) as conn:
        with conn.transaction():
            conn.execute(
                "INSERT INTO m3_quota_parks (tenant_id, run_id, reason, resume_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id, run_id) DO UPDATE "
                "SET reason = EXCLUDED.reason, resume_at = EXCLUDED.resume_at, "
                "state = 'parked', resumed_at = NULL",
                (tenant_id, run_id, reason, resume_at),
            )


def resume_quota(conninfo: dict, *, tenant_id: str, run_id: uuid.UUID) -> None:
    with _connect(conninfo) as conn:
        with conn.transaction():
            result = conn.execute(
                "UPDATE m3_quota_parks SET state = 'resumed', resumed_at = now() "
                "WHERE tenant_id = %s AND run_id = %s AND state = 'parked'",
                (tenant_id, run_id),
            )
            if result.rowcount != 1:
                raise RuntimeError("parked quota row not found")
