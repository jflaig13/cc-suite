"""Durable cursor-based inbox reader (Python port of channels/shared/durable_inbox.ts).

Replaces consume-and-delete drain for the shared role queue
(channels/shared/queue_{role}.jsonl). The Slack poller appends events to that
file; this module provides non-destructive reads so multiple consumers
(channel.ts subprocess, UserPromptSubmit hook drain) each maintain their own
cursor and see all events, regardless of order.

Cursor file: channels/shared/cursor_{role}_hook_{pid}.json
  {"line": N, "pid": PID, "role": ROLE}
  — tracks how many JSONL lines this consumer has already processed
  — one file per running consumer process
  — cleaned up for dead PIDs on next drain

The inbox file grows unbounded. At ~200 bytes/event and ~100 events/day per
role, growth is ~20KB/day — no rotation needed at fleet sizes <50 active
agents.

Composes with channels/shared/durable_inbox.ts (the bun/TS version, which
the channel.ts subprocess uses with cursor_{role}_bun_{pid}.json — kept
separate so the bun and hook drains don't fight over each other's cursors).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class Cursor:
    """Cursor state — how many JSONL lines this consumer has processed."""
    line: int
    pid: int
    role: str
    # G-e: content-hash of the last-processed line. A line-count cursor CANNOT detect a
    # truncate-then-regrow-PAST-cursor (start_line stays <= qlen but the content diverged);
    # the anchor catches it by CONTENT, so no events are silently dropped.
    anchor: str = ""


@dataclass
class DrainResult:
    """Outcome of a drainQueue() call."""
    processed: int
    held: bool  # True when an onEvent throw stopped processing early


def load_cursor(cursor_file: str | Path) -> Cursor:
    """Load cursor from disk. Returns Cursor(line=0, ...) on missing or corrupt."""
    try:
        raw = Path(cursor_file).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        return Cursor(
            line=int(parsed["line"]) if isinstance(parsed.get("line"), int) else 0,
            pid=int(parsed["pid"]) if isinstance(parsed.get("pid"), int) else 0,
            role=str(parsed["role"]) if isinstance(parsed.get("role"), str) else "",
            anchor=str(parsed["anchor"]) if isinstance(parsed.get("anchor"), str) else "",
        )
    except Exception:
        return Cursor(line=0, pid=0, role="")


def save_cursor(cursor_file: str | Path, line: int, pid: int, role: str, anchor: str = "") -> None:
    """Persist cursor to disk. Silently swallows write errors (best-effort). The anchor
    (G-e) is the content-hash of the last-processed line so a truncate-then-regrow that
    leaves line <= qlen is still caught by CONTENT divergence, not just line-count shrink."""
    try:
        Path(cursor_file).write_text(
            json.dumps({"line": line, "pid": pid, "role": role, "anchor": anchor}),
            encoding="utf-8",
        )
    except Exception:
        pass


def _complete_lines(inbox_file: str | Path) -> list[str]:
    """The torn-guarded list of complete (newline-terminated) non-blank lines — the shared
    basis for read_from_cursor + the drain's content-anchor check."""
    path = Path(inbox_file)
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    if raw.endswith("\n"):
        safe = raw
    else:
        last_nl = raw.rfind("\n")
        safe = raw[: last_nl + 1] if last_nl >= 0 else ""
    return [ln for ln in safe.split("\n") if ln.strip()]


def _anchor(line: str) -> str:
    """Short stable content-hash of a queue line (G-e cursor content anchor)."""
    import hashlib
    return hashlib.sha1(line.encode("utf-8", "replace")).hexdigest()[:16]


def cleanup_dead_cursors(shared_dir: str | Path, role: str, my_pid: int) -> int:
    """Remove cursor files for dead PIDs (process not alive).

    Equivalent to the TS cleanupDeadCursors. Uses os.kill(pid, 0) — sends
    signal 0 which is a no-op for alive PIDs and raises ProcessLookupError
    for dead PIDs. Returns the count of files removed (for observability).
    """
    removed = 0
    try:
        pattern = re.compile(rf"^cursor_{re.escape(role)}_[a-z]+_(\d+)\.json$")
        # Also support the older `cursor_{role}_{pid}.json` (no kind segment)
        legacy_pattern = re.compile(rf"^cursor_{re.escape(role)}_(\d+)\.json$")

        shared = Path(shared_dir)
        for f in shared.iterdir():
            if not f.is_file():
                continue
            match = pattern.match(f.name) or legacy_pattern.match(f.name)
            if not match:
                continue
            pid = int(match.group(1))
            if pid == my_pid:
                continue
            try:
                os.kill(pid, 0)
                # alive — leave the cursor alone
            except ProcessLookupError:
                try:
                    f.unlink()
                    removed += 1
                except Exception:
                    pass
            except PermissionError:
                # PID exists but owned by another user — treat as alive
                pass
    except Exception:
        pass
    return removed


def read_from_cursor(
    inbox_file: str | Path,
    cursor_line: int,
) -> tuple[list[str], int]:
    """Read lines from inbox starting at cursor_line. Non-destructive.

    Returns (new_lines, total_complete_lines). The inbox file is NEVER
    modified or deleted by this function.

    Torn-line guard (F-B2 fix): if the file does not end with '\\n', the final
    entry is a partial write in progress; it's excluded. The poller always
    appends complete newline-terminated lines; a missing trailing '\\n' means
    a write is still in flight. The partial line will be readable on the next
    drain cycle once the write completes.
    """
    all_lines = _complete_lines(inbox_file)
    return all_lines[cursor_line:], len(all_lines)


# --- role high-water-mark checkpoint (cold-start replay fix, D0 — matched with
#     channels/shared/durable_inbox.ts's roleCheckpoint*) ----------------------
# A role-scoped floor so a fresh pid resumes where the ROLE left off instead of
# replaying the entire queue from line 0 — WITHOUT dropping events (the seed-to-tail
# "simpler fallback" in the FIX-SPEC drops gap-events; the role checkpoint does not).


def role_checkpoint_file(shared_dir: str | Path, role: str) -> Path:
    """Role high-water mark: cursor_{role}_role.json — the highest queue line ANY
    window of the role has processed. A fresh pid (relaunched window) with no own
    cursor seeds from THIS instead of line 0, so it resumes where the role left off
    (catching gap events) rather than replaying history. Per-pid cursors are
    unchanged; this is a seed floor for NEW pids only, never a cap on a live pid."""
    return Path(shared_dir) / f"cursor_{role}_role.json"


def load_role_checkpoint(file: str | Path) -> int:
    """Load the role high-water-mark line. Returns 0 if missing or corrupt."""
    try:
        parsed = json.loads(Path(file).read_text(encoding="utf-8"))
        return int(parsed["line"]) if isinstance(parsed.get("line"), int) else 0
    except Exception:
        return 0


def save_role_checkpoint(file: str | Path, line: int, force: bool = False) -> None:
    """Advance the role high-water mark — MONOTONIC by default (never moves backward, so a
    slow window cannot rewind a fast peer's progress). force=True bypasses the monotonic
    guard to RESET the mark after a truncation/rotation (CF1 desync recovery — the old
    high-water mark is INVALID once the queue is truncated below it). Best-effort."""
    try:
        if force or line > load_role_checkpoint(file):
            Path(file).write_text(json.dumps({"line": line}), encoding="utf-8")
    except Exception:
        pass


def _log_desync(role: str, cursor_line: int, qlen: int) -> None:
    """LOUD (stderr) log of a cursor-desync recovery — never silent (CF1). Hooks capture
    stderr; the drain hook's stdout JSON contract is untouched."""
    try:
        import sys as _sys
        print(f"[durable_inbox] CURSOR DESYNC role={role}: cursor {cursor_line} > qlen {qlen} "
              f"— queue truncated/rotated under the cursor; resyncing from 0 so pending events deliver",
              file=_sys.stderr, flush=True)
    except Exception:
        pass


# Callback type for processing a single decoded queue event. The callback
# receives event_type, content, meta. Raising from the callback causes
# drain_queue to hold the cursor at the failed event (retry next cycle).
EventHandler = Callable[[str, str, dict], None]


def drain_queue(
    inbox_file: str | Path,
    cursor_file: str | Path,
    pid: int,
    role: str,
    on_event: EventHandler,
) -> DrainResult:
    """Drain the inbox from the current cursor, calling on_event per event.

    Advances the cursor only for events that were successfully handled.

    F-B1 fix (catch blocks intentionally separated):
      - JSON.parse failure: unrecoverable bad data — advance past, continue.
      - on_event throw: transient handler failure — hold cursor here, stop
        processing. The caller receives held=True and can log appropriately.
        On the next drain cycle, the same event will be retried.

    Extracted from channels/shared/durable_inbox.ts:drainQueue. Independent
    implementation for verification-independence per the canon — does not
    import from the TS module.

    Cold-start replay fix (D0, behaviorally matched with durable_inbox.ts): a
    relaunched window is a NEW pid with no own cursor. The old load_cursor line-0
    fallback replayed the ENTIRE unbounded queue on every init (the founder-caught
    flood). Seed by PRECEDENCE — WITHOUT dropping events:
      1. own per-pid cursor exists  -> resume from this pid's position
      2. role checkpoint exists     -> resume from where the ROLE left off
                                       (catches gap events, no history replay)
      3. neither (genuinely fresh)  -> process from line 0 (a brand-new queue: the
                                       events are REAL pending, never dropped)
    The wall on EXISTING busy queues is bounded by the role checkpoint being seeded
    (advanced by every drain, or a one-time migration), NOT by seeding to tail —
    which would drop the gap-events the FIX-SPEC forbids losing.
    """
    cursor_path = Path(cursor_file)
    role_cp = role_checkpoint_file(cursor_path.parent, role)
    own_cursor = load_cursor(cursor_file) if cursor_path.exists() else None
    if own_cursor is not None:
        start_line = own_cursor.line
    elif role_cp.exists():
        start_line = load_role_checkpoint(role_cp)
    else:
        start_line = 0

    all_lines = _complete_lines(inbox_file)
    total = len(all_lines)

    # DESYNC detection — the queue was truncated/rotated under the cursor:
    #   (CF1) start_line > total -> cursor beyond the queue (truncate-to-smaller); OR
    #   (G-e) the own cursor's CONTENT ANCHOR no longer matches all_lines[start_line-1] ->
    #         truncate-then-regrow-PAST-cursor (start_line <= total, but the content diverged;
    #         a line-count cursor cannot see this and would SILENTLY DROP the new [0..start_line)).
    # On either: log loud + resync from 0 (DELIVER the pending events, not 0) + FORCE-reset the
    # stale role checkpoint (the old high-water mark is invalid after a truncation).
    desync = start_line > total
    if not desync and own_cursor is not None and own_cursor.anchor and start_line > 0:
        prev = all_lines[start_line - 1] if start_line - 1 < total else None
        if prev is None or _anchor(prev) != own_cursor.anchor:
            desync = True
    if desync:
        _log_desync(role, start_line, total)
        start_line = 0
        save_role_checkpoint(role_cp, 0, force=True)

    new_lines = all_lines[start_line:]
    if not new_lines:
        # Persist position (+ anchor + seed the role checkpoint) even with nothing to do, so a
        # peer fresh window resumes here, not line 0.
        anchor = _anchor(all_lines[start_line - 1]) if start_line > 0 and start_line - 1 < total else ""
        save_cursor(cursor_file, start_line, pid, role, anchor)
        save_role_checkpoint(role_cp, start_line)
        return DrainResult(processed=0, held=False)

    processed = 0
    held = False
    for line in new_lines:
        event: Optional[dict]
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                event = None
        except json.JSONDecodeError:
            processed += 1  # advance past malformed JSON (unrecoverable)
            continue

        if event is None:
            processed += 1
            continue

        try:
            on_event(
                event.get("event_type") or "slack_message",
                event.get("content") or "",
                event.get("meta") or {},
            )
            processed += 1
        except Exception:
            held = True  # handler failed — hold cursor, retry next cycle
            break

    new_line = start_line + processed
    new_anchor = _anchor(all_lines[new_line - 1]) if new_line > 0 and new_line - 1 < len(all_lines) else ""
    save_cursor(cursor_file, new_line, pid, role, new_anchor)  # + G-e content anchor
    save_role_checkpoint(role_cp, new_line)  # advance the role high-water mark
    return DrainResult(processed=processed, held=held)


def cursor_filename(role: str, pid: int, kind: str) -> str:
    """Canonical cursor filename for a given role + pid + consumer kind.

    Kind is the consumer type (e.g., "hook", "bun", "relay") so multiple
    consumer types can drain the same role queue without cursor collision.
    Returns the bare filename — caller composes with the shared dir path.
    """
    if not re.match(r"^[a-z]+$", kind):
        raise ValueError(f"cursor kind must be lowercase letters only, got: {kind!r}")
    return f"cursor_{role}_{kind}_{pid}.json"


def session_cursor_filename(role: str, session_id: str) -> str:
    """Cursor filename keyed on the stable per-session id.

    Unlike cursor_filename (keyed on an ephemeral hook-subprocess pid), this is
    stable across every hook invocation in a session, so ALL drain consumers in
    one session (the UserPromptSubmit hook drain + the FileChanged hook drain)
    share a single cursor and never double-deliver an event. The session_id
    comes from the Claude Code hook stdin payload ("session_id" field).

    session_id is sanitized to [a-z0-9]; empty/missing -> "unknown" (callers
    should fall back to the pid-keyed cursor when there is no session_id).
    """
    sid = re.sub(r"[^a-z0-9]", "", (session_id or "").lower()) or "unknown"
    return f"cursor_{role}_session_{sid}.json"


def cleanup_stale_session_cursors(
    shared_dir: str | Path, role: str, max_age_seconds: int = 30 * 24 * 3600
) -> int:
    """Remove session-keyed cursor files older than max_age (by mtime).

    Session cursors are not tied to a live PID (cleanup_dead_cursors can't
    reap them), so they're cleaned by age. The 30-day default is far longer
    than any realistic continuously-idle session, so an active window's cursor
    is never deleted out from under it. Best-effort; never raises. Returns the
    count removed (for observability).
    """
    removed = 0
    try:
        pattern = re.compile(rf"^cursor_{re.escape(role)}_session_[a-z0-9]+\.json$")
        now = time.time()
        for f in Path(shared_dir).iterdir():
            if not f.is_file() or not pattern.match(f.name):
                continue
            try:
                if now - f.stat().st_mtime > max_age_seconds:
                    f.unlink()
                    removed += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed
