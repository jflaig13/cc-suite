# SPDX-License-Identifier: MPL-2.0
"""Byte-honest M6 shadow of legacy budget reservations.

``budget_usd`` is a ceiling, not actual spend. Actual provider usage remains in
``cost_records`` and is never inferred from the legacy envelope ledger.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row


_NAMESPACE = uuid.UUID("67ec0ad9-985d-4d20-834f-93a6c3c09d4a")
_BUDGET_FIELDS = frozenset({"ts", "run_type", "rc", "budget_usd"})
_BUDGET_TENANT_FIELDS = _BUDGET_FIELDS | {"tenant"}
_IDEMPOTENCY_FIELDS = frozenset({"ts", "run_type", "rc", "idempotency_key"})


class SpendMigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LegacySpendRow:
    source_key: str
    ordinal: int
    line: str
    sha256: str
    value: dict[str, Any]
    recorded_at: datetime
    budget_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class SpendSnapshot:
    snapshot_id: uuid.UUID
    tenant_id: str
    cycle_key: str
    legacy_sha256: str
    kernel_render_sha256: str
    row_count: int
    attributed_rows: int
    unattributed_rows: int
    changed_rows: int
    verdict: str


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, "\x1f".join(parts))


def _aware(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SpendMigrationError("spend ts must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpendMigrationError("spend ts must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SpendMigrationError("spend ts must be timezone-aware")
    return parsed


def parse_spend_bytes(data: bytes) -> tuple[LegacySpendRow, ...]:
    if data and not data.endswith(b"\n"):
        raise SpendMigrationError("spend ledger has a torn trailing line")
    raw_lines = data[:-1].split(b"\n") if data else []
    occurrences: dict[str, int] = {}
    rows: list[LegacySpendRow] = []
    for ordinal, raw in enumerate(raw_lines):
        try:
            line = raw.decode("utf-8", errors="strict")
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SpendMigrationError(f"spend row {ordinal + 1} is invalid JSON/UTF-8") from exc
        if not isinstance(value, dict) or set(value) not in {
            _BUDGET_FIELDS, _BUDGET_TENANT_FIELDS, _IDEMPOTENCY_FIELDS,
        }:
            raise SpendMigrationError(f"spend row {ordinal + 1} schema is not exact")
        if not isinstance(value.get("run_type"), str) or not value["run_type"].strip():
            raise SpendMigrationError(f"spend row {ordinal + 1} run_type is invalid")
        if isinstance(value.get("rc"), bool) or not isinstance(value.get("rc"), int):
            raise SpendMigrationError(f"spend row {ordinal + 1} rc is invalid")
        if "tenant" in value and value["tenant"] is not None and (
            not isinstance(value["tenant"], str) or not value["tenant"].strip()
        ):
            raise SpendMigrationError(f"spend row {ordinal + 1} tenant is invalid")
        budget: Decimal | None = None
        if "budget_usd" in value:
            try:
                raw_budget = Decimal(str(value["budget_usd"]))
            except (InvalidOperation, ValueError) as exc:
                raise SpendMigrationError(f"spend row {ordinal + 1} budget is invalid") from exc
            if (
                not raw_budget.is_finite() or raw_budget < 0
                or raw_budget.as_tuple().exponent < -6
                or raw_budget > Decimal("999999.999999")
            ):
                raise SpendMigrationError(f"spend row {ordinal + 1} budget is invalid")
            budget = raw_budget.quantize(Decimal("0.000001"))
        elif not isinstance(value.get("idempotency_key"), str) or not value[
            "idempotency_key"
        ].strip():
            raise SpendMigrationError(f"spend row {ordinal + 1} idempotency key is invalid")
        digest = hashlib.sha256(line.encode()).hexdigest()
        occurrences[digest] = occurrences.get(digest, 0) + 1
        rows.append(LegacySpendRow(
            f"{digest}::occurrence:{occurrences[digest]}", ordinal, line, digest,
            dict(value), _aware(value.get("ts")), budget,
        ))
    return tuple(rows)


def _render(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return (("\n".join(str(row["legacy_line"]) for row in rows) + "\n").encode()
            if rows else b"")


def _attribution_from_m3(conn: psycopg.Connection, tenant_id: str) -> dict[str, tuple]:
    events = conn.execute(
        "SELECT e.objective_id, e.run_id, o.payload FROM events e JOIN kernel_outbox o "
        "ON o.tenant_id = e.tenant_id AND o.event_id = e.id "
        "WHERE e.tenant_id = %s AND e.event_type = 'legacy.surface_snapshot.spend_ledger' "
        "ORDER BY e.stream_seq",
        (tenant_id,),
    ).fetchall()
    previous: tuple[LegacySpendRow, ...] = ()
    attributed: dict[str, tuple] = {}
    for index, event in enumerate(events):
        payload = event["payload"]
        if not isinstance(payload, dict) or set(payload) != {"replace_text"} or not isinstance(
            payload["replace_text"], str
        ):
            raise SpendMigrationError("M3 spend snapshot outbox payload is invalid")
        current = parse_spend_bytes(payload["replace_text"].encode())
        if index and [row.line for row in current[:len(previous)]] != [row.line for row in previous]:
            raise SpendMigrationError("M3 spend snapshot history is not append-only")
        if index:
            if event["objective_id"] is None or event["run_id"] is None:
                raise SpendMigrationError("M3 spend snapshot lacks objective/run attribution")
            for row in current[len(previous):]:
                attributed[row.source_key] = (event["objective_id"], event["run_id"])
        previous = current
    return attributed


def sync_spend_bytes(
    conninfo: dict, *, tenant_id: str, cycle_key: str, data: bytes,
    compared_at: datetime | None = None,
) -> SpendSnapshot:
    if not tenant_id.strip() or not cycle_key.strip():
        raise SpendMigrationError("tenant_id and cycle_key are required")
    rows = parse_spend_bytes(data)
    if any(row.value.get("tenant") is not None for row in rows):
        raise SpendMigrationError("M6 tenant-zero ledger contains a foreign tenant stamp")
    now = compared_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise SpendMigrationError("compared_at must be timezone-aware")
    digest = hashlib.sha256(data).hexdigest()
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"m6-spend:{tenant_id}",),
            )
            prior = conn.execute(
                "SELECT * FROM m6_spend_snapshots WHERE tenant_id = %s AND cycle_key = %s",
                (tenant_id, cycle_key),
            ).fetchone()
            if prior is not None:
                if prior["legacy_sha256"] != digest:
                    raise SpendMigrationError("cycle_key replay carries different spend bytes")
                return SpendSnapshot(
                    prior["id"], tenant_id, cycle_key, prior["legacy_sha256"],
                    prior["kernel_render_sha256"], prior["row_count"],
                    prior["attributed_rows"], prior["unattributed_rows"],
                    prior["changed_rows"], prior["verdict"],
                )
            existing_rows = conn.execute(
                "SELECT source_key, legacy_sha256, legacy_ordinal, legacy_line, objective_id, "
                "run_id, attribution_status, present FROM m6_spend_bindings "
                "WHERE tenant_id = %s ORDER BY legacy_ordinal FOR UPDATE", (tenant_id,),
            ).fetchall()
            existing = {row["source_key"]: row for row in existing_rows}
            if set(existing) - {row.source_key for row in rows}:
                raise SpendMigrationError("legacy spend ledger is not append-only")
            if [row.line for row in rows[:len(existing_rows)]] != [
                row["legacy_line"] for row in existing_rows
            ]:
                raise SpendMigrationError("legacy spend ledger is not append-only")
            attribution = _attribution_from_m3(conn, tenant_id)
            changed = 0
            attributed_count = 0
            for row in rows:
                previous = existing.get(row.source_key)
                if previous is not None and previous["legacy_sha256"] != row.sha256:
                    raise SpendMigrationError("legacy spend identity changed bytes")
                ids = attribution.get(row.source_key)
                status = "attributed" if ids else "unattributed_legacy"
                if previous is not None and previous["attribution_status"] == "attributed" and (
                    ids is None or previous["objective_id"] != ids[0]
                    or previous["run_id"] != ids[1]
                ):
                    raise SpendMigrationError("M6 financial attribution cannot be downgraded or rebound")
                changed += int(
                    previous is None or previous["legacy_ordinal"] != row.ordinal
                    or previous["objective_id"] != (ids[0] if ids else None)
                    or previous["run_id"] != (ids[1] if ids else None)
                    or previous["attribution_status"] != status
                )
                attributed_count += int(ids is not None)
                conn.execute(
                    "INSERT INTO m6_spend_bindings (tenant_id, source_key, legacy_ordinal, "
                    "legacy_line, legacy_sha256, recorded_at, run_type, return_code, budget_usd, "
                    "ledger_tenant, idempotency_key, objective_id, run_id, attribution_status, "
                    "present, synced_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s) "
                    "ON CONFLICT (tenant_id, source_key) DO UPDATE SET legacy_ordinal=EXCLUDED.legacy_ordinal, "
                    "objective_id=EXCLUDED.objective_id, run_id=EXCLUDED.run_id, "
                    "attribution_status=EXCLUDED.attribution_status, present=TRUE, synced_at=EXCLUDED.synced_at",
                    (
                        tenant_id, row.source_key, row.ordinal, row.line, row.sha256,
                        row.recorded_at, row.value["run_type"], row.value["rc"], row.budget_usd,
                        row.value.get("tenant"), row.value.get("idempotency_key"),
                        ids[0] if ids else None, ids[1] if ids else None,
                        status, now,
                    ),
                )
            rendered = _render(conn.execute(
                "SELECT legacy_line FROM m6_spend_bindings WHERE tenant_id=%s AND present IS TRUE "
                "ORDER BY legacy_ordinal, source_key", (tenant_id,),
            ).fetchall())
            if rendered != data:
                raise SpendMigrationError("M6 spend parity divergence; transaction rolled back")
            snapshot_id = _id(tenant_id, cycle_key, "snapshot")
            conn.execute(
                "INSERT INTO m6_spend_snapshots (id,tenant_id,cycle_key,legacy_sha256,"
                "kernel_render_sha256,row_count,attributed_rows,unattributed_rows,changed_rows,"
                "verdict,compared_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'clean',%s)",
                (snapshot_id, tenant_id, cycle_key, digest, digest, len(rows), attributed_count,
                 len(rows)-attributed_count, changed, now),
            )
    return SpendSnapshot(
        snapshot_id, tenant_id, cycle_key, digest, digest, len(rows), attributed_count,
        len(rows)-attributed_count, changed, "clean",
    )


def render_spend_bytes(conninfo: dict, *, tenant_id: str) -> bytes:
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        return _render(conn.execute(
            "SELECT legacy_line FROM m6_spend_bindings WHERE tenant_id=%s AND present IS TRUE "
            "ORDER BY legacy_ordinal, source_key", (tenant_id,),
        ).fetchall())


def parity_gate_status(
    conninfo: dict, *, tenant_id: str, required_cycles: int = 50,
) -> dict[str, Any]:
    if required_cycles < 1:
        raise ValueError("required_cycles must be positive")
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT cycle_key,verdict,legacy_sha256,kernel_render_sha256,"
            "unattributed_rows,compared_at "
            "FROM m6_spend_snapshots WHERE tenant_id=%s "
            "ORDER BY compared_at DESC,cycle_key DESC",
            (tenant_id,),
        ).fetchall()
    consecutive = 0
    seen_digests: set[str] = set()
    baseline_unattributed: int | None = None
    for row in rows:
        if row["verdict"] != "clean" or row["legacy_sha256"] != row["kernel_render_sha256"]:
            break
        if baseline_unattributed is None:
            baseline_unattributed = row["unattributed_rows"]
        elif row["unattributed_rows"] != baseline_unattributed:
            break
        if row["legacy_sha256"] in seen_digests:
            continue
        seen_digests.add(row["legacy_sha256"])
        consecutive += 1
        if consecutive >= required_cycles:
            break
    return {
        "ready": consecutive >= required_cycles,
        "required_cycles": required_cycles,
        "consecutive_clean": consecutive,
        "historical_unattributed": baseline_unattributed or 0,
        "latest_cycle_key": str(rows[0]["cycle_key"]) if rows else "",
    }
