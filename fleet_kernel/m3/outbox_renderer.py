"""Crash-idempotent file renderer for M3's transactional outbox.

The database is authoritative. Each render rebuilds the destination from an
immutable pre-cutover baseline plus the complete ordered kernel projection,
then atomically replaces the file. A crash after replace but before the DB
status update is harmless: retry produces identical bytes.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row


@dataclass(frozen=True, slots=True)
class RenderTarget:
    destination: str
    path: Path
    baseline_path: Path | None = None
    projection: str = "append_jsonl"


@dataclass(frozen=True, slots=True)
class RenderResult:
    """Projection receipt.

    ``rows`` counts all historical rows for the destination. ``content_rows``
    counts rows that contribute bytes to this render (one for replace_text).
    ``global_sequence_gaps`` is tenant-global because stream_seq is global.
    ``sequence_gaps`` remains as a compatibility alias for existing monitors.
    """

    rows: int
    content_rows: int
    first_stream_seq: int | None
    last_stream_seq: int | None
    bytes_written: int
    global_sequence_gaps: tuple[int, ...]

    @property
    def sequence_gaps(self) -> tuple[int, ...]:
        return self.global_sequence_gaps


def sequence_gaps(stream_seqs: Sequence[int]) -> list[int]:
    if not stream_seqs:
        return []
    ordered = sorted(stream_seqs)
    if len(set(ordered)) != len(ordered):
        raise ValueError("duplicate stream sequence in outbox projection")
    return [value for value in range(ordered[0], ordered[-1] + 1) if value not in set(ordered)]


def _line(payload: dict[str, Any]) -> bytes:
    if set(payload) == {"line_text"}:
        value = payload["line_text"]
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise ValueError("line_text must be one newline-free string")
        return value.encode("utf-8") + b"\n"
    if set(payload) == {"line"} and isinstance(payload["line"], dict):
        return (
            json.dumps(payload["line"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
    raise ValueError("outbox payload must contain exactly line or line_text")


def _replacement(payload: dict[str, Any]) -> bytes:
    if set(payload) != {"replace_text"} or not isinstance(payload["replace_text"], str):
        raise ValueError("replacement outbox payload must contain exactly replace_text")
    return payload["replace_text"].encode("utf-8")


def _baseline_bytes(target: RenderTarget) -> bytes:
    if target.baseline_path is None:
        return b""
    baseline = target.baseline_path.resolve()
    if not baseline.is_file() or baseline.is_symlink():
        raise FileNotFoundError(f"immutable render baseline missing: {baseline}")
    data = baseline.read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError("render baseline must end with newline")
    return data


def _atomic_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


def render_destination(
    conninfo: dict, *, tenant_id: str, target: RenderTarget, require_contiguous: bool = True,
) -> RenderResult:
    if not tenant_id.strip() or not target.destination.strip():
        raise ValueError("tenant_id and destination must be non-empty")
    lock_key = f"render:{tenant_id}:{target.destination}"
    # The session lock is acquired and committed before the render transaction
    # starts, so a waiter takes its MVCC snapshot only after the prior renderer's
    # status writes are visible. A crashed session releases this lock automatically.
    with psycopg.connect(**conninfo, row_factory=dict_row, autocommit=True) as lock_conn:
        lock_conn.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
            (lock_key,),
        )
        try:
            return _render_destination_snapshot(
                conninfo, tenant_id=tenant_id, target=target,
                require_contiguous=require_contiguous,
            )
        finally:
            try:
                lock_conn.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                    (lock_key,),
                )
            except psycopg.Error:
                pass


def _render_destination_snapshot(
    conninfo: dict, *, tenant_id: str, target: RenderTarget, require_contiguous: bool,
) -> RenderResult:
    with psycopg.connect(**conninfo, row_factory=dict_row, autocommit=False) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        # The entire verification + projection uses one snapshot. Event-head
        # allocation and event+outbox insertion commit atomically, so an append
        # after this snapshot is wholly invisible and belongs to the next render.
        head = conn.execute(
            "SELECT outbox_start_seq, next_seq FROM event_stream_heads WHERE tenant_id = %s",
            (tenant_id,),
        ).fetchone()
        if head is None or head["outbox_start_seq"] is None:
            governed_rows = int(conn.execute(
                "SELECT count(*) AS n FROM kernel_outbox "
                "WHERE tenant_id = %s AND event_id IS NOT NULL",
                (tenant_id,),
            ).fetchone()["n"])
            if governed_rows:
                raise RuntimeError(
                    "LOUD outbox sequence anchor missing while governed rows exist"
                )
            start = stop = None
            gaps: list[int] = []
        else:
            start = int(head["outbox_start_seq"])
            stop = int(head["next_seq"])
            if start >= stop:
                raise RuntimeError(
                    f"LOUD invalid outbox sequence anchor range: start={start}, stop={stop}"
                )
            outside_rows = int(conn.execute(
                "SELECT count(*) AS n FROM kernel_outbox "
                "WHERE tenant_id = %s AND event_id IS NOT NULL "
                "AND (stream_seq < %s OR stream_seq >= %s)",
                (tenant_id, start, stop),
            ).fetchone()["n"])
            if outside_rows:
                raise RuntimeError(
                    "LOUD outbox sequence anchor excludes governed rows"
                )
            observed_count = int(conn.execute(
                "SELECT count(DISTINCT stream_seq) AS n FROM kernel_outbox "
                "WHERE tenant_id = %s AND event_id IS NOT NULL "
                "AND stream_seq >= %s AND stream_seq < %s",
                (tenant_id, start, stop),
            ).fetchone()["n"])
            if observed_count == stop - start:
                gaps = []
            else:
                gaps = [int(row["missing_seq"]) for row in conn.execute(
                    "SELECT expected.seq AS missing_seq "
                    "FROM generate_series(%s::bigint, %s::bigint) AS expected(seq) "
                    "LEFT JOIN kernel_outbox o ON o.tenant_id = %s "
                    "AND o.event_id IS NOT NULL AND o.stream_seq = expected.seq "
                    "WHERE o.id IS NULL ORDER BY expected.seq",
                    (start, stop - 1, tenant_id),
                ).fetchall()]
        if require_contiguous and gaps:
            raise RuntimeError(f"LOUD outbox sequence gap(s): {gaps}")
        if start is None or stop is None:
            rows = []
        else:
            rows = conn.execute(
                "SELECT id, stream_seq, payload FROM kernel_outbox "
                "WHERE tenant_id = %s AND destination = %s AND event_id IS NOT NULL "
                "AND stream_seq >= %s AND stream_seq < %s "
                "ORDER BY stream_seq, id FOR UPDATE",
                (tenant_id, target.destination, start, stop),
            ).fetchall()
        seqs = [int(row["stream_seq"]) for row in rows]
        if target.projection == "append_jsonl":
            data = _baseline_bytes(target) + b"".join(
                _line(dict(row["payload"])) for row in rows
            )
            content_rows = len(rows)
        elif target.projection == "replace_text":
            if target.baseline_path is not None:
                raise ValueError("replace_text projection cannot use a baseline")
            if not rows:
                raise RuntimeError(
                    f"replacement destination {target.destination!r} has no authoritative row"
                )
            data = _replacement(dict(rows[-1]["payload"]))
            content_rows = 1
        else:
            raise ValueError(f"unknown render projection: {target.projection!r}")
        _atomic_replace(target.path.resolve(), data)
        if rows:
            rendered_ids = [row["id"] for row in rows]
            conn.execute(
                "UPDATE kernel_outbox SET status = 'rendered', rendered_at = now(), "
                "attempts = attempts + 1, last_error = '' "
                "WHERE tenant_id = %s AND destination = %s AND id = ANY(%s)",
                (tenant_id, target.destination, rendered_ids),
            )
        conn.commit()
    return RenderResult(
        rows=len(rows),
        content_rows=content_rows,
        first_stream_seq=seqs[0] if seqs else None,
        last_stream_seq=seqs[-1] if seqs else None,
        bytes_written=len(data),
        global_sequence_gaps=tuple(gaps),
    )
