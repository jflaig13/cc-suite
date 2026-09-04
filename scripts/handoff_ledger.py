#!/usr/bin/env python3
"""H0 — the Handoff Obligation Ledger (Handoff Stall-Escalation Backstop v1).

Mirrors scripts/verification_ledger.py (validate / append / query), but for
FLEET HANDOFFS instead of verification verdicts. The substrate the stall sweep
(H3) reads to find handoffs that blew their deadline without being closed — so
a "held for the go / held for the result" mutual-wait deadlock gets escalated
onto a non-idle surface within one sweep interval, instead of sitting stale
until a human notices (the 2026-06-13 live instance the backstop exists for).

Two design rules from the canon (docs/brain/061326__handoff-stall-backstop-canon.md):
  - ATOMIC WORKING-TREE WRITE, NEVER MACHINE-COMMITTED. The writer rewrites the
    ledger via a temp file + os.replace under flock (so a concurrent sweep tick
    and an announcement-hook open can't corrupt it), and NEVER runs git — a
    machine commit would forge the founder's identity and trip the
    task-completion commit hook. The ledger rides into git on normal commits.
  - ERR LOUD, MUTABLE STATUS. Unlike the append-only verdict ledger, a handoff
    row's status transitions open -> closed (work resumed) or open -> escalated
    (sweep bit). Transitions are atomic compare-and-swap (CAS) so escalation is
    at-most-once: the first sweep tick past the deadline wins open->escalated;
    later ticks see `escalated` and skip.

Verification Independence: imports NOTHING from the pipelines/agents it tracks
handoffs for. Neutral substrate. The oil_auto_update hook (H2) opens/closes
rows; the sweep (H3) escalates; the canary (H4) proves the sweep bites.

Mechanism verified: "atomic temp+os.replace under flock; cas_status is
at-most-once; never git-commits" — confirmed against handoff_ledger.append_handoff
/ cas_status / _atomic_write_all / _flock (no subprocess/git import in this
module) + hermetic Tier-1 tests/test_tier1_handoff_ledger.py (2026-06-13).

Spec/canon: docs/brain/061326__handoff-stall-backstop-canon.md,
cc_execs/handoffs/handoff_stall_backstop/CCDE_build_brief.md.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(os.environ["CC_SUITE_WORKSPACE"])
LEDGER_PATH = REPO / "docs" / "ledger" / "handoff_ledger.jsonl"
# G-c: the bun send primitive (channels/agent/channel.ts) CANNOT safely write the
# flock-guarded ledger directly (it would race the sweep's atomic rewrite), so it appends
# an OPEN-REQUEST here and the flock-holding sweep drains it into owned rows (drain_open_requests).
OPEN_REQUESTS_PATH = Path(os.environ["CC_SUITE_CHANNEL_SHARED_DIR"]) / "handoff_open_requests.jsonl"

# The standalone sweep's launchd StartInterval (com.mise.handoff-sweep.plist).
# The deadline floor is 2x this so a handoff cannot expire before a sweep tick
# could ever see it open (a sub-one-interval deadline would escalate instantly).
SWEEP_INTERVAL_SECONDS = 180
DEADLINE_DEFAULT_SECONDS = 2 * 60 * 60           # 2h
DEADLINE_MIN_SECONDS = 2 * SWEEP_INTERVAL_SECONDS  # 360s — at least 2 sweep ticks
DEADLINE_MAX_SECONDS = 4 * 60 * 60               # 4h ceiling

STATUSES = {"open", "closed", "escalated"}

# A canary row's id carries this prefix; the sweep CASes it like any row but
# does NOT fire the founder-facing surfaces for it (H4 self-test, not real).
CANARY_PREFIX = "__canary__"

# Engine-hygiene #7 (CoS defect dispatch cos-send-1gukmr2lxdqe-0009, 2026-07-02):
# the legacy G-c path opened a 2h-deadline row for EVERY task_handoff send, with
# this PLACEHOLDER standing in for a never-declared artifact — so routine
# inter-agent messages to live, busy agents escalated as "stalls" (~10 spurious
# arbiter-asks/hr). The backstop canon's own discipline is that a genuine
# handoff DECLARES {owner, action, expected artifact, deadline}; open_from_send
# now enforces it: a send-row requires a sender-DECLARED expected_artifact.
# The placeholder is refused BY VALUE so un-relaunched bun daemons still
# appending legacy requests cannot reopen the noise class.
PLACEHOLDER_ARTIFACT = "handoff acknowledged + drained"

# Engine-hygiene #3: owner-attribution validation at row-open. A send-row's
# owner is a role (send targets are role-validated upstream) or a role-prefixed
# exec ID ({ROLE}-{NNN}). Mirrors the relay's VALID_ROLES set (12 Mise roles).
KNOWN_OWNER_ROLES = {
    "ccto", "ccpo", "ccro", "ccfo", "ccmo", "cclo", "ccgo", "ccco",
    "ccde", "cos", "scribe", "utility",
}


def is_known_owner(owner: str) -> bool:
    """True for a known role ('ccpo') or a role-prefixed exec ID
    ('CCPO-006'). Case-insensitive."""
    o = str(owner or "").strip().lower()
    if not o:
        return False
    if o in KNOWN_OWNER_ROLES:
        return True
    base, sep, tail = o.rpartition("-")
    return sep == "-" and base in KNOWN_OWNER_ROLES and tail.isdigit()

REQUIRED_FIELDS = (
    "handoff_id", "from_agent", "owner_agent", "action", "expected_artifact",
    "opened_ts_utc", "deadline_utc", "status", "closed_ts_utc", "evidence_ptr", "note",
)
# A subset must be non-empty; closed_ts_utc / evidence_ptr / note are empty on
# an open row (evidence only exists at CLOSE — open rows have no proof yet).
NON_EMPTY_FIELDS = (
    "handoff_id", "from_agent", "owner_agent", "action", "expected_artifact",
    "opened_ts_utc", "deadline_utc", "status",
)

# evidence_ptr, when present, must be durable — a /tmp pointer is ephemeral.
_EPHEMERAL_PREFIXES = ("/tmp", "/private/tmp", "/var/folders")


class LedgerError(ValueError):
    """A handoff entry that violates the ledger contract — rejected at write."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise LedgerError(msg)


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def clamp_deadline_seconds(seconds: float) -> float:
    """Clamp a handoff lifetime to [2x sweep interval, 4h] — the canon bound."""
    return max(DEADLINE_MIN_SECONDS, min(DEADLINE_MAX_SECONDS, float(seconds)))


def is_canary(entry: Dict[str, Any]) -> bool:
    return str(entry.get("handoff_id", "")).startswith(CANARY_PREFIX)


# Founder identity set — a founder-owned intent is a pending founder DECISION,
# not a fleet handoff, so it must never enter the stall-escalation set (OIL-137).
# Escalating the founder's own pending action (approve/send/review) back at the
# founder/CoS is a manufactured nag (VALUES_CORE; Line Check Rule 11) — founder
# decisions ride FODL/OIL, not the stall ledger. Case-insensitive exact match
# against the identities the autonomy dispatch leg writes as owner_agent.
FOUNDER_IDENTITIES = {"founder", *(value.strip().lower() for value in os.environ.get("CC_SUITE_FOUNDER_IDENTITIES", "").split(",") if value.strip())}


def is_founder_owned(entry_or_owner: Any) -> bool:
    """True iff the row/owner is founder-owned. Accepts a row dict (reads
    owner_agent) or a bare owner string. Pure stdlib predicate — imports nothing
    from tracked pipelines (Verification Independence preserved); mirrors
    is_canary as a carve-out at the single query chokepoint."""
    owner = (entry_or_owner.get("owner_agent", "")
             if isinstance(entry_or_owner, dict) else str(entry_or_owner or ""))
    return owner.strip().lower() in FOUNDER_IDENTITIES


def validate_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Validate (and CLAMP the deadline) against the H0 contract. Returns the
    entry on success (deadline_utc possibly rewritten to the clamped value),
    raises LedgerError otherwise. The clamp lives HERE so it is the single
    enforcement point no caller can bypass (brief: 'clamp in validate_entry')."""
    _require(isinstance(entry, dict), "handoff entry must be a dict")
    for field in REQUIRED_FIELDS:
        _require(field in entry, f"handoff entry missing required field {field!r}")
    for field in NON_EMPTY_FIELDS:
        val = entry[field]
        _require(isinstance(val, str) and val.strip() != "",
                 f"field {field!r} must be a non-empty string")

    _require(entry["status"] in STATUSES,
             f"unknown status {entry['status']!r} — one of {sorted(STATUSES)}")

    # evidence_ptr durability: empty is fine (open rows have no proof yet); a
    # non-empty ephemeral path is not durable evidence.
    ptr = str(entry.get("evidence_ptr") or "")
    if ptr.strip():
        _require(not any(ptr.startswith(p) for p in _EPHEMERAL_PREFIXES),
                 f"evidence_ptr {ptr!r} is in an ephemeral path — point at a "
                 "committed dir or commit hash (surviving-evidence rule).")

    # Deadline clamp: recompute deadline_utc = opened + clamp(deadline - opened).
    opened = _parse_ts(entry["opened_ts_utc"])
    deadline = _parse_ts(entry["deadline_utc"])
    delta = (deadline - opened).total_seconds()
    _require(delta > 0, "deadline_utc must be after opened_ts_utc")
    clamped = clamp_deadline_seconds(delta)
    entry["deadline_utc"] = _iso(opened + timedelta(seconds=clamped))

    # closed_ts_utc, when present, must parse.
    if str(entry.get("closed_ts_utc") or "").strip():
        try:
            _parse_ts(entry["closed_ts_utc"])
        except ValueError as e:
            raise LedgerError(f"closed_ts_utc not ISO-8601: {entry['closed_ts_utc']!r} ({e})")

    return entry


def new_handoff(
    handoff_id: str,
    from_agent: str,
    owner_agent: str,
    action: str,
    expected_artifact: str,
    opened_ts_utc: str,
    deadline_seconds: float = DEADLINE_DEFAULT_SECONDS,
    note: str = "",
) -> Dict[str, Any]:
    """Build + validate an OPEN handoff row. deadline_seconds is clamped by
    validate_entry; opened_ts_utc is passed in (this module stamps nothing —
    deterministic, hermetically testable)."""
    opened = _parse_ts(opened_ts_utc)
    deadline = opened + timedelta(seconds=clamp_deadline_seconds(deadline_seconds))
    entry = {
        "handoff_id": handoff_id,
        "from_agent": from_agent,
        "owner_agent": owner_agent,
        "action": action,
        "expected_artifact": expected_artifact,
        "opened_ts_utc": opened_ts_utc,
        "deadline_utc": _iso(deadline),
        "status": "open",
        "closed_ts_utc": "",
        "evidence_ptr": "",
        "note": note,
    }
    return validate_entry(entry)


# --- atomic, flock-guarded, never-committed persistence ------------------

@contextmanager
def _flock(path: Path):
    """Exclusive lock on a sidecar .lock file for the duration of a
    read-modify-write. Serializes the sweep tick, the open-hook, and the
    close-hook so none sees or writes a half-written ledger."""
    lock = path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_all(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # err-loud elsewhere; a corrupt line never blocks a sweep
    return out


def _kernel_pointer_config(path: Path) -> Optional[tuple[Dict[str, Any], str]]:
    """Return explicit kernel connection config for the canonical ledger only."""
    mode = os.environ.get("FLEET_KERNEL_HANDOFF_READ_MODE", "legacy").strip().lower()
    if mode not in {"legacy", "kernel"}:
        raise LedgerError("FLEET_KERNEL_HANDOFF_READ_MODE must be legacy or kernel")
    if mode == "legacy" or path.resolve() != LEDGER_PATH.resolve():
        return None
    if os.environ.get("MISE_TENANT", "").strip():
        raise LedgerError("kernel handoff reads are tenant-zero only before Phase 7")
    database_url = os.environ.get("FLEET_KERNEL_DATABASE_URL", "").strip()
    tenant_id = os.environ.get("FLEET_KERNEL_TENANT_ID", "").strip()
    if not database_url or not tenant_id:
        raise LedgerError("kernel handoff reads require database URL and tenant ID")
    try:
        from psycopg.conninfo import conninfo_to_dict

        return conninfo_to_dict(database_url), tenant_id
    except Exception as exc:
        raise LedgerError(f"kernel handoff database configuration is invalid: {exc}") from exc


def _mirror_kernel_after_legacy_write(
    path: Path, config: Optional[tuple[Dict[str, Any], str]],
) -> None:
    if config is None:
        return
    conninfo, tenant_id = config
    try:
        from fleet_kernel.m4.supervision import sync_authoritative_file

        result = sync_authoritative_file(
            conninfo, tenant_id=tenant_id, legacy_path=path,
        )
        if result.verdict != "clean":
            raise RuntimeError("kernel mirror parity diverged")
    except Exception as exc:
        raise LedgerError(
            f"legacy handoff write is committed but synchronous kernel mirror failed: {exc}"
        ) from exc


def _read_control_rows(path: Path) -> List[Dict[str, Any]]:
    """Read the kernel projection only under an explicit canonical-path pointer.

    Writers and CAS operations still commit JSONL first, then synchronously
    mirror it while this pointer is active. That preserves read-your-write,
    keeps rollback immediate, and reports any crash-between-writes gap loudly.
    A requested kernel read never silently falls back to the file.
    """
    config = _kernel_pointer_config(path)
    if config is None:
        return _read_all(path)
    conninfo, tenant_id = config
    try:
        from fleet_kernel.m4.read_backend import read_verified_kernel_rows

        return read_verified_kernel_rows(
            conninfo, tenant_id=tenant_id, legacy_path=path,
        )
    except Exception as exc:
        if isinstance(exc, LedgerError):
            raise
        raise LedgerError(f"kernel handoff read refused: {exc}") from exc


def _atomic_write_all(path: Path, rows: List[Dict[str, Any]]) -> None:
    """Write the whole ledger via temp file + os.replace (atomic on POSIX same
    filesystem). NEVER git-commits — durability rides normal commits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".handoff_ledger.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_handoff(entry: Dict[str, Any], path: Path = LEDGER_PATH) -> Dict[str, Any]:
    """Validate then add one OPEN row. Refuses a duplicate handoff_id (open is
    at-most-once — a re-open would race the sweep)."""
    validate_entry(entry)
    config = _kernel_pointer_config(path)
    with _flock(path):
        rows = _read_all(path)
        duplicate = any(r.get("handoff_id") == entry["handoff_id"] for r in rows)
        if not duplicate:
            rows.append(entry)
            _atomic_write_all(path, rows)
    # Even a duplicate attempt repairs a crash-between-writes gap before the
    # idempotent caller advances its cursor.
    _mirror_kernel_after_legacy_write(path, config)
    _require(not duplicate,
             f"handoff_id {entry['handoff_id']!r} already exists — open is at-most-once")
    return entry


# --- G-c: the SEND path opens an OWNED handoff row (so the stall-backstop can bite) ------

def open_from_send(
    send_id: str,
    from_agent: str,
    owner_agent: str,
    action: str,
    opened_ts_utc: str,
    expected_artifact: str = "",
    deadline_seconds: float = DEADLINE_DEFAULT_SECONDS,
    path: Path = LEDGER_PATH,
) -> Optional[Dict[str, Any]]:
    """Open an OWNED handoff row for a task_handoff SEND, IDEMPOTENT on send_id. Returns the new
    row; returns None if a row already exists for this send_id (at-most-once dup — a safe no-op).
    RAISES on malformed request content (new_handoff ValueError/LedgerError) or a transient ledger
    error (append OSError) — so a caller can distinguish "bad data / already-done" (advance) from
    "retry later" (hold). Callers that must not break a send wrap this best-effort (server.py's
    _open_handoff_row_safe); drain_open_requests uses the distinction to skip bad data but HOLD on a
    transient error. owner_agent = the target of the send, so the stall sweep re-dispatches there.

    ENGINE-HYGIENE CONTRACT (2026-07-02, #7 + #3 — THE chokepoint all three send
    writers converge on: the bun daemon via drain_open_requests, the relay
    server + relay daemon via _open_handoff_row_safe):
      #7 A stall-tracked row requires a sender-DECLARED expected_artifact — a
         bare task_handoff send is a message, not a deadline-bound obligation.
         Blank AND the legacy PLACEHOLDER_ARTIFACT are refused (LedgerError),
         so un-relaunched daemons appending legacy requests cannot reopen the
         spurious-escalation class. deadline_seconds may also be declared
         (clamped to the canon bound by validate_entry, as always).
      #3 owner_agent must be a known role / role-prefixed exec ID
         (is_known_owner) — phantom owners are refused at row-open, not
         discovered at dispatch time."""
    art = str(expected_artifact or "").strip()
    if not art or art.lower() == PLACEHOLDER_ARTIFACT:
        raise LedgerError(
            f"undeclared obligation for send {send_id!r}: a stall-tracked handoff row "
            f"requires a sender-DECLARED expected_artifact (engine-hygiene #7 — a bare "
            f"task_handoff send is a message, not a deadline-bound obligation; declare "
            f"meta.expected_artifact [+ optional meta.deadline_seconds] on the send)")
    if not is_known_owner(owner_agent):
        raise LedgerError(
            f"unknown owner_agent {owner_agent!r} for send {send_id!r}: stall-tracked rows "
            f"must be owned by a known role or exec ID (engine-hygiene #3 owner-attribution "
            f"validation at row-open)")
    handoff_id = f"send-{send_id}"
    entry = new_handoff(  # ValueError/LedgerError on malformed content -> the caller skips it
        handoff_id=handoff_id, from_agent=from_agent, owner_agent=owner_agent,
        action=action, expected_artifact=art, opened_ts_utc=opened_ts_utc,
        deadline_seconds=deadline_seconds,
    )
    try:
        return append_handoff(entry, path=path)
    except LedgerError as e:
        if "already exists" in str(e):
            return None  # idempotent dup — already opened for this send_id
        raise  # unexpected ledger error -> propagate (the drain HOLDS + retries)


def _request_lines(p: Path) -> List[str]:
    """Torn-guarded complete (newline-terminated) non-blank lines of the request log — a partial
    trailing append (no '\\n') is excluded and picked up next tick once complete."""
    raw = p.read_text(encoding="utf-8")
    if raw.endswith("\n"):
        safe = raw
    else:
        nl = raw.rfind("\n")
        safe = raw[: nl + 1] if nl >= 0 else ""
    return [ln for ln in safe.split("\n") if ln.strip()]


def _request_anchor(line: str) -> str:
    """Short stable content-hash of a request-log line (F3-a, 2026-07-01 CMCV batch-2:
    mirrors mcp_servers/channel_relay/durable_inbox.py's G-e cursor content anchor — an
    INDEPENDENT implementation, not an import, per this module's Verification Independence
    contract (imports nothing from tracked pipelines). A line-NUMBER cursor alone cannot
    detect a truncate-then-regrow-PAST-cursor (start <= new total, but the content at and
    below the cursor diverged) — Codex's falsifier: drain N rows (cursor=N) -> rewrite the
    log to N NEW rows (same or greater count) -> a line-count-only cursor sees start<=total
    and skips ALL of them, PERMANENTLY. The anchor catches this by CONTENT, not count."""
    return hashlib.sha1(line.encode("utf-8", "replace")).hexdigest()[:16]


def _load_request_cursor(cp: Path) -> Dict[str, Any]:
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        return {"line": int(data.get("line", 0)), "anchor": str(data.get("anchor") or "")}
    except Exception:
        return {"line": 0, "anchor": ""}


def _save_request_cursor(cp: Path, line: int, anchor: str = "") -> None:
    try:
        cp.write_text(json.dumps({"line": line, "anchor": anchor}), encoding="utf-8")
    except Exception:
        pass


def drain_open_requests(
    requests_path: Path = OPEN_REQUESTS_PATH,
    ledger_path: Path = LEDGER_PATH,
    cursor_path: Optional[Path] = None,
) -> int:
    """Drain task_handoff OPEN-REQUESTS (appended by the bun send primitive, which cannot safely
    write the flock-guarded ledger) into OWNED handoff rows. Returns the count of NEW rows opened.

    HIGH-WATER-MARK CURSOR (mirrors durable_inbox): each tick processes only requests PAST the
    cursor, so the cost is O(new x ledger), not O(history x ledger) — the dark-factory scaling fix
    (ccde-63/CCPO). Single-consumer (the sweep tick), so the cursor has no concurrency. Bad request
    data (unparseable / missing fields / a malformed handoff that new_handoff rejects) is SKIPPED
    (advance past, logged LOUD to stderr per G-b — F3-b, 2026-07-01: previously silent, so a real
    drop was invisible). A TRANSIENT ledger error HOLDS the cursor at that line (retry next tick —
    no loss). open_from_send stays idempotent, so any resync (truncation OR regrow-past-cursor) is
    safe — a re-scan double-processes nothing.

    F3-a (2026-07-01 CMCV batch-2, Codex-falsified): DESYNC detection is line-count OR content-
    anchor — start_line > total (shrink/truncate) OR the anchor of the line just before the cursor
    no longer matches what's on disk (truncate-then-regrow-PAST-cursor, same or greater line count
    but different content). Either case resyncs from 0."""
    p = Path(requests_path)
    if not p.exists():
        return 0
    cp = Path(cursor_path) if cursor_path is not None else Path(f"{p}.cursor")
    lines = _request_lines(p)
    total = len(lines)
    cursor = _load_request_cursor(cp)
    start = cursor["line"]

    desync = start > total  # shrink/truncate-below-cursor
    if not desync and cursor["anchor"] and start > 0:
        prev = lines[start - 1] if start - 1 < total else None
        if prev is None or _request_anchor(prev) != cursor["anchor"]:
            desync = True  # F3-a: content diverged even though start_line <= total (regrow-past-cursor)
    if desync:
        print(f"[handoff_ledger] DESYNC: open-requests log at {p} diverged under the cursor "
              f"(cursor_line={start}, total={total}) — resyncing from 0 (open_from_send is "
              f"idempotent, safe).", file=sys.stderr, flush=True)
        start = 0

    opened = 0
    processed = 0
    for line in lines[start:]:
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(f"[handoff_ledger] BAD REQUEST ROW skipped (invalid JSON) at line "
                  f"{start + processed + 1}: {line[:200]!r}", file=sys.stderr, flush=True)
            processed += 1  # bad JSON -> skip (advance)
            continue
        if not isinstance(req, dict) or not req.get("send_id") or not req.get("opened_ts_utc"):
            print(f"[handoff_ledger] BAD REQUEST ROW skipped (missing send_id/opened_ts_utc) at "
                  f"line {start + processed + 1}: {line[:200]!r}", file=sys.stderr, flush=True)
            processed += 1  # missing required fields -> skip (advance)
            continue
        try:
            dl = float(req.get("deadline_seconds") or DEADLINE_DEFAULT_SECONDS)
        except (TypeError, ValueError):
            dl = DEADLINE_DEFAULT_SECONDS
        try:
            row = open_from_send(
                send_id=str(req["send_id"]),
                from_agent=str(req.get("from_agent") or "unknown"),
                owner_agent=str(req.get("owner_agent") or "unknown"),
                action=str(req.get("action") or "task handoff"),
                opened_ts_utc=str(req["opened_ts_utc"]),
                # engine-hygiene #7: NO placeholder default — an undeclared artifact is
                # refused by open_from_send (LedgerError -> the skip-loud branch below),
                # which is how legacy requests from un-relaunched daemons are retired.
                expected_artifact=str(req.get("expected_artifact") or ""),
                deadline_seconds=dl,
                path=ledger_path,
            )
        except (LedgerError, ValueError) as e:
            print(f"[handoff_ledger] BAD REQUEST ROW skipped (rejected: {e}) at line "
                  f"{start + processed + 1}, send_id={req.get('send_id')!r}",
                  file=sys.stderr, flush=True)
            processed += 1  # malformed request DATA (new_handoff rejected it) -> skip (advance)
            continue
        except Exception:
            break  # TRANSIENT ledger error -> HOLD the cursor here, retry this line next tick
        if row is not None:
            opened += 1
        processed += 1  # opened OR a dup -> advance past it

    new_line = start + processed
    new_anchor = _request_anchor(lines[new_line - 1]) if 0 < new_line <= total else ""
    _save_request_cursor(cp, new_line, new_anchor)
    return opened


def cas_status(
    handoff_id: str,
    expect: str,
    to: str,
    path: Path = LEDGER_PATH,
    now_utc: Optional[str] = None,
    note: Optional[str] = None,
) -> bool:
    """Atomic compare-and-swap of a row's status: if the row's current status
    == expect, set it to `to` and return True; else return False (no row, or
    already in another state). At-most-once by construction — the basis for
    open->escalated (sweep bites once) and open->closed (work resumed). Sets
    closed_ts_utc on any transition OUT of open (the time the obligation left
    the open state)."""
    _require(to in STATUSES, f"target status {to!r} not one of {sorted(STATUSES)}")
    config = _kernel_pointer_config(path)
    with _flock(path):
        rows = _read_all(path)
        changed = False
        for r in rows:
            if r.get("handoff_id") == handoff_id and r.get("status") == expect:
                r["status"] = to
                # OIL-195-follow-up (2026-07-10): stamp closed_ts_utc on the
                # transition that actually LEAVES open OR ARRIVES at closed —
                # open->escalated (existing) AND escalated->closed (new). The
                # latter was previously silent (only expect=="open" stamped),
                # so a row closed from escalated kept its ESCALATION
                # timestamp in a field literally named closed_ts_utc — wrong
                # semantics, and the reason the re-fire class looked closed
                # in the ledger's own history but stayed "escalated" live.
                if now_utc and (
                    (expect == "open" and to in ("closed", "escalated"))
                    or (expect == "escalated" and to == "closed")
                ):
                    r["closed_ts_utc"] = now_utc
                if note:
                    r["note"] = note
                changed = True
                break
        if changed:
            _atomic_write_all(path, rows)
    _mirror_kernel_after_legacy_write(path, config)
    return changed


def remove_handoff(handoff_id: str, path: Path = LEDGER_PATH) -> bool:
    """Delete a row by id (atomic, flock-guarded). Used by the H4 canary to
    clean up its synthetic __canary__ sentinels so they don't accumulate —
    NOT for real handoffs (closed/escalated real rows are retained as an
    auditable record). Returns True if a row was removed."""
    config = _kernel_pointer_config(path)
    with _flock(path):
        rows = _read_all(path)
        kept = [r for r in rows if r.get("handoff_id") != handoff_id]
        changed = len(kept) != len(rows)
        if changed:
            _atomic_write_all(path, kept)
    _mirror_kernel_after_legacy_write(path, config)
    return changed


def read_handoff(handoff_id: str, path: Path = LEDGER_PATH) -> Optional[Dict[str, Any]]:
    """Return the row for handoff_id from the working tree, or None."""
    for r in _read_control_rows(path):
        if r.get("handoff_id") == handoff_id:
            return r
    return None


def query_open(
    path: Path = LEDGER_PATH,
    now_utc: Optional[str] = None,
    past_deadline_only: bool = False,
) -> List[Dict[str, Any]]:
    """Open rows from the WORKING TREE (the sweep reads live state, not a git
    snapshot — a just-opened handoff must be visible immediately). With
    past_deadline_only + now_utc, returns only the rows the sweep should
    escalate (now > deadline AND status == open)."""
    rows = [r for r in _read_control_rows(path) if r.get("status") == "open"]
    if past_deadline_only:
        _require(now_utc is not None, "past_deadline_only needs now_utc")
        now = _parse_ts(now_utc)
        rows = [r for r in rows if now > _parse_ts(r["deadline_utc"])]
        # OIL-137: founder-owned rows are pending founder DECISIONS, not fleet
        # handoffs — exclude them from the escalation set at this single
        # chokepoint every writer/sweep reads through (the load-bearing invariant
        # no future writer can re-break). A plain query_open (no past_deadline)
        # still returns them — they ARE open, just never stall-escalable.
        rows = [r for r in rows if not is_founder_owned(r)]
    return rows


def query_escalated(path: Path = LEDGER_PATH) -> List[Dict[str, Any]]:
    """Escalated rows from the WORKING TREE (OIL-195 follow-up, 2026-07-10).
    An escalated row is by definition already past-deadline (the sweep only
    escalates on that condition), so no separate past_deadline filter exists
    here — unlike query_open, which serves both "all open" and "stall
    candidates" callers. Founder-owned rows are excluded (mirrors query_open's
    OIL-137 exclusion) — a founder DECISION is never auto-closed by an
    announcement, escalated or not."""
    return [r for r in _read_all(path)
            if r.get("status") == "escalated" and not is_founder_owned(r)]
