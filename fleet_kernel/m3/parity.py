# SPDX-License-Identifier: MPL-2.0
"""Per-cycle M3 parity comparison and the mechanical 50-cycle gate."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


REQUIRED_CONTROL_TAGS = frozenset({"quota_park_resume", "america_chicago_midnight_reset"})


def _cents(value: Any) -> int:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid money value: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"invalid money value: {value!r}")
    return int((amount * 100).quantize(Decimal("1")))


def _intent_set(values: Iterable[dict[str, Any]]) -> set[str]:
    return {json.dumps(value, sort_keys=True, separators=(",", ":")) for value in values}


@dataclass(frozen=True, slots=True)
class ParityVerdict:
    verdict: str
    differences: tuple[dict[str, Any], ...]


def compare_cycle(
    *,
    legacy_run_seen: bool,
    kernel_run_seen: bool,
    legacy_spend_usd: Any,
    kernel_spend_usd: Any,
    legacy_intents: Iterable[dict[str, Any]],
    kernel_intents: Iterable[dict[str, Any]],
    sequence_gap_values: Iterable[int] = (),
) -> ParityVerdict:
    differences: list[dict[str, Any]] = []
    if legacy_run_seen != kernel_run_seen:
        differences.append({"field": "run_seen", "legacy": legacy_run_seen, "kernel": kernel_run_seen})
    legacy_cents, kernel_cents = _cents(legacy_spend_usd), _cents(kernel_spend_usd)
    if legacy_cents != kernel_cents:
        differences.append({"field": "spend_cents", "legacy": legacy_cents, "kernel": kernel_cents})
    legacy_set, kernel_set = _intent_set(legacy_intents), _intent_set(kernel_intents)
    if legacy_set != kernel_set:
        differences.append({
            "field": "intent_set",
            "legacy_only": sorted(legacy_set - kernel_set),
            "kernel_only": sorted(kernel_set - legacy_set),
        })
    gaps = sorted(set(int(value) for value in sequence_gap_values))
    if gaps:
        differences.append({"field": "sequence_gaps", "values": gaps})
    return ParityVerdict("clean" if not differences else "diverged", tuple(differences))


def record_parity_result(
    conninfo: dict,
    *,
    tenant_id: str,
    run_id: uuid.UUID,
    cycle_key: str,
    input_manifest_sha256: str,
    verdict: ParityVerdict,
    control_tags: Iterable[str] = (),
) -> uuid.UUID:
    result_id = uuid.uuid5(uuid.NAMESPACE_URL, f"mise:m3:parity:{tenant_id}:{cycle_key}")
    tags = sorted(set(control_tags))
    with psycopg.connect(**conninfo) as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"parity:{tenant_id}:{cycle_key}",),
        )
        existing = conn.execute(
            "SELECT id, run_id, input_manifest_sha256, verdict, differences, control_tags "
            "FROM m3_parity_results WHERE tenant_id = %s AND cycle_key = %s",
            (tenant_id, cycle_key),
        ).fetchone()
        expected = {
            "id": result_id,
            "run_id": run_id,
            "input_manifest_sha256": input_manifest_sha256,
            "verdict": verdict.verdict,
            "differences": list(verdict.differences),
            "control_tags": tags,
        }
        if existing is not None:
            actual = {
                "id": existing[0],
                "run_id": existing[1],
                "input_manifest_sha256": existing[2],
                "verdict": existing[3],
                "differences": existing[4],
                "control_tags": existing[5],
            }
            if actual != expected:
                raise RuntimeError("parity result is immutable once recorded")
            return result_id
        conn.execute(
            "INSERT INTO m3_parity_results "
            "(id, tenant_id, run_id, cycle_key, input_manifest_sha256, verdict, differences, control_tags) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                result_id, tenant_id, run_id, cycle_key, input_manifest_sha256,
                verdict.verdict, Jsonb(list(verdict.differences)), Jsonb(tags),
            ),
        )
    return result_id


def parity_gate_status(conninfo: dict, *, tenant_id: str, required_cycles: int = 50) -> dict[str, Any]:
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT verdict, control_tags FROM m3_parity_results WHERE tenant_id = %s "
            "ORDER BY compared_at DESC, id DESC",
            (tenant_id,),
        ).fetchall()
    consecutive = []
    for row in rows:
        if row["verdict"] != "clean":
            break
        consecutive.append(row)
    window = consecutive[:required_cycles]
    tags = {tag for row in window for tag in row["control_tags"]}
    missing = sorted(REQUIRED_CONTROL_TAGS - tags)
    return {
        "ready": len(window) >= required_cycles and not missing,
        "consecutive_clean": len(consecutive),
        "required_cycles": required_cycles,
        "missing_controls": missing,
    }


def kernel_run_seen(conninfo: dict, *, tenant_id: str, run_id: uuid.UUID) -> bool:
    with psycopg.connect(**conninfo) as conn:
        row = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM runs WHERE tenant_id = %s AND id = %s) AS seen",
            (tenant_id, run_id),
        ).fetchone()
    return bool(row[0])
