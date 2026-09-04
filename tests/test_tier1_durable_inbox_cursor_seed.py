"""Tier-1 D0 (F2-corrected): cold-start replay fix via the ROLE CHECKPOINT — no flood,
NO EVENT LOSS, Python matched to the TypeScript drain.

The founder-caught flood: a fresh window replayed the entire queue from line 0. The FIX
is the role high-water-mark checkpoint (NOT seed-to-tail, which the FIX-SPEC + Codex CMCV
flagged as dropping real gap-events): a fresh pid resumes from where the ROLE left off; a
genuinely-fresh role with no checkpoint processes from 0 (its events are real pending,
never dropped); the wall on busy queues is bounded by the checkpoint, not by skipping
events.

This is the PYTHON MIRROR of channels/shared/durable_inbox_coldstart.test.ts — the same
acceptance scenarios, an independent implementation (Verification Independence: does not
import or transpile the TS). If the two drains diverge, one of these suites goes red.

Fast (<1s), hermetic — tmp queue + tmp cursors, no network.
"""
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLE = "testrole"


def _load_durable_inbox():
    modname = "durable_inbox_d0"
    spec = importlib.util.spec_from_file_location(
        modname, REPO_ROOT / "mcp_servers" / "channel_relay" / "durable_inbox.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod  # register BEFORE exec so @dataclass resolves __module__
    spec.loader.exec_module(mod)
    return mod


DI = _load_durable_inbox()


def _q(dir_: Path) -> Path:
    return dir_ / f"queue_{ROLE}.jsonl"


def _c(dir_: Path, pid: int) -> Path:
    return dir_ / f"cursor_{ROLE}_{pid}.json"


def _cp(dir_: Path) -> Path:
    return DI.role_checkpoint_file(dir_, ROLE)


def _seed_queue(dir_: Path, n: int) -> None:
    lines = [json.dumps({"event_type": "slack_message", "content": f"e{i}", "meta": {}}) for i in range(n)]
    _q(dir_).write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def _append_queue(dir_: Path, contents) -> None:
    lines = [json.dumps({"event_type": "slack_message", "content": c, "meta": {}}) for c in contents]
    with _q(dir_).open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _collector():
    seen = []
    return seen, (lambda _t, content, _m: seen.append(content))


# ===========================================================================
# the coldstart acceptance — mirrored from durable_inbox_coldstart.test.ts
# ===========================================================================

def test_genuinely_fresh_role_processes_from_0_and_seeds_checkpoint(tmp_path):
    # A brand-new role (no cursor, no checkpoint) processes its queue — NO event
    # loss. The wall on EXISTING busy queues is prevented by the migration/checkpoint,
    # NOT by changing this default (which would lose events on a new queue).
    _seed_queue(tmp_path, 5)
    seen, on_event = _collector()
    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 101), 101, ROLE, on_event)
    assert r.processed == 5
    assert seen == ["e0", "e1", "e2", "e3", "e4"]
    assert DI.load_role_checkpoint(_cp(tmp_path)) == 5  # seeded for the next window


def test_migration_no_wall_checkpointed_busy_queue_replays_nothing(tmp_path):
    _seed_queue(tmp_path, 1700)
    _cp(tmp_path).write_text(json.dumps({"line": 1700}), encoding="utf-8")  # migration pre-seed
    seen, on_event = _collector()
    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 999), 999, ROLE, on_event)
    assert r.processed == 0 and seen == []  # zero replay — the flood fix the founder needs


def test_fresh_pid_resumes_from_role_checkpoint_not_line_0(tmp_path):
    _seed_queue(tmp_path, 1700)
    _cp(tmp_path).write_text(json.dumps({"line": 1690}), encoding="utf-8")  # a prior window got to 1690
    seen, on_event = _collector()
    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 202), 202, ROLE, on_event)
    assert r.processed == 10  # only the 10 new, not 1700
    assert seen == [f"e{i}" for i in range(1690, 1700)]


def test_no_loss_events_after_checkpoint_caught_by_next_fresh_window(tmp_path):
    _seed_queue(tmp_path, 50)
    _cp(tmp_path).write_text(json.dumps({"line": 50}), encoding="utf-8")
    _append_queue(tmp_path, ["g0", "g1", "g2", "g3", "g4"])  # arrived while no window was live
    seen, on_event = _collector()
    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 303), 303, ROLE, on_event)
    assert r.processed == 5 and seen == ["g0", "g1", "g2", "g3", "g4"]  # NO LOSS


def test_own_pid_cursor_beats_role_checkpoint(tmp_path):
    _seed_queue(tmp_path, 100)
    DI.save_cursor(_c(tmp_path, 404), 30, 404, ROLE)
    _cp(tmp_path).write_text(json.dumps({"line": 90}), encoding="utf-8")
    seen, on_event = _collector()
    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 404), 404, ROLE, on_event)
    assert r.processed == 70 and seen[0] == "e30"  # from its own 30, not the role's 90


def test_role_checkpoint_advances_monotonically_to_max(tmp_path):
    _seed_queue(tmp_path, 100)
    _cp(tmp_path).write_text(json.dumps({"line": 80}), encoding="utf-8")
    DI.drain_queue(_q(tmp_path), _c(tmp_path, 505), 505, ROLE, lambda *a: None)
    assert DI.load_role_checkpoint(_cp(tmp_path)) == 100
    # a stale lower write must NOT rewind it
    DI.save_cursor(_c(tmp_path, 506), 10, 506, ROLE)
    DI.drain_queue(_q(tmp_path), _c(tmp_path, 506), 506, ROLE, lambda *a: None)
    assert DI.load_role_checkpoint(_cp(tmp_path)) == 100


def test_fb1_onevent_throw_holds_cursor_at_failed_event(tmp_path):
    _seed_queue(tmp_path, 5)
    _cp(tmp_path).write_text(json.dumps({"line": 0}), encoding="utf-8")  # process from the start
    state = {"count": 0}

    def throwing(_t, _c, _m):
        state["count"] += 1
        if state["count"] == 3:
            raise RuntimeError("push fail")

    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 606), 606, ROLE, throwing)
    assert r.held is True and r.processed == 2  # e0,e1 ok; held at e2
    assert DI.load_cursor(_c(tmp_path, 606)).line == 2


def test_malformed_json_skipped_not_held(tmp_path):
    _q(tmp_path).write_text(
        "not-json\n" + json.dumps({"event_type": "slack_message", "content": "ok", "meta": {}}) + "\n",
        encoding="utf-8",
    )
    _cp(tmp_path).write_text(json.dumps({"line": 0}), encoding="utf-8")
    seen, on_event = _collector()
    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 707), 707, ROLE, on_event)
    assert r.held is False and r.processed == 2 and seen == ["ok"]


# ===========================================================================
# py/ts MATCH proof — the flood is bounded AND no events are dropped
# ===========================================================================

def test_no_flood_but_no_drop_1803_backlog_with_checkpoint(tmp_path):
    """The founder's 1,803-event flood: WITH the role checkpoint at tail, a fresh
    window replays ZERO (bounded); a new event after is still delivered (no drop)."""
    _seed_queue(tmp_path, 1803)
    _cp(tmp_path).write_text(json.dumps({"line": 1803}), encoding="utf-8")
    seen, on_event = _collector()
    assert DI.drain_queue(_q(tmp_path), _c(tmp_path, 1), 1, ROLE, on_event).processed == 0
    _append_queue(tmp_path, ["LIVE"])
    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 1), 1, ROLE, on_event)
    assert r.processed == 1 and seen == ["LIVE"]


# ===========================================================================
# CF1 — CURSOR DESYNC recovery: a stale-HIGH cursor (queue truncated under it) resyncs
# and DELIVERS the pending events (does NOT return 0) — the CCPO directed-handoff recovery.
# ===========================================================================

def test_cursor_desync_own_cursor_resyncs_and_delivers(tmp_path):
    _seed_queue(tmp_path, 12)  # queue truncated to 12
    DI.save_cursor(_c(tmp_path, 1), 1723, 1, ROLE)  # stale-HIGH cursor (was 1723)
    seen, on_event = _collector()
    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 1), 1, ROLE, on_event)
    assert r.processed == 12 and len(seen) == 12, "desync must DELIVER the pending events, not return 0"
    assert DI.load_cursor(_c(tmp_path, 1)).line == 12  # resynced to the current tail


def test_cursor_desync_stale_role_checkpoint_force_resets(tmp_path):
    """A stale-HIGH ROLE CHECKPOINT (no per-pid cursor) also triggers recovery + FORCE-resets
    the checkpoint (bypassing the monotonic guard)."""
    _seed_queue(tmp_path, 5)
    _cp(tmp_path).write_text(json.dumps({"line": 999}), encoding="utf-8")  # stale-high checkpoint
    seen, on_event = _collector()
    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 2), 2, ROLE, on_event)
    assert r.processed == 5 and len(seen) == 5
    assert DI.load_role_checkpoint(_cp(tmp_path)) == 5  # 999 -> 5 (force-reset)


def test_save_role_checkpoint_force_bypasses_monotonic(tmp_path):
    f = _cp(tmp_path)
    f.write_text(json.dumps({"line": 999}), encoding="utf-8")
    DI.save_role_checkpoint(f, 5)               # monotonic: 5 < 999 -> NO write
    assert DI.load_role_checkpoint(f) == 999
    DI.save_role_checkpoint(f, 5, force=True)   # force -> reset
    assert DI.load_role_checkpoint(f) == 5


def test_cursor_at_exact_tail_is_not_desync(tmp_path):
    """Guard against false-positives: cursor == qlen (caught up, normal) is NOT a desync."""
    _seed_queue(tmp_path, 5)
    DI.save_cursor(_c(tmp_path, 3), 5, 3, ROLE)  # exactly at tail
    seen, on_event = _collector()
    r = DI.drain_queue(_q(tmp_path), _c(tmp_path, 3), 3, ROLE, on_event)
    assert r.processed == 0 and seen == []  # nothing new, no spurious resync-from-0


# ===========================================================================
# G-e — CONTENT ANCHOR: truncate-then-regrow-PAST-cursor (line-count blind) is caught.
# ===========================================================================

def test_ge_truncate_then_regrow_past_cursor_caught_by_content_anchor(tmp_path):
    """The residual in CF1: a queue truncated then REGROWN past the stale cursor leaves
    start_line <= qlen (line-count sees NO desync) but the content diverged -> the content
    anchor catches it and DELIVERS all events (no silent drop)."""
    inbox, cursor = _q(tmp_path), _c(tmp_path, 1)
    _seed_queue(tmp_path, 20)  # e0..e19
    seen, on_event = _collector()
    DI.drain_queue(inbox, cursor, 1, ROLE, on_event)  # cursor.line=20 + anchor(e19)
    assert len(seen) == 20
    # regrow to 25 DIFFERENT lines: cursor.line=20 <= qlen=25 (line-count blind), content diverged.
    lines = [json.dumps({"event_type": "slack_message", "content": f"NEW{i}", "meta": {}}) for i in range(25)]
    inbox.write_text("\n".join(lines) + "\n", encoding="utf-8")
    seen2, on_event2 = _collector()
    r = DI.drain_queue(inbox, cursor, 1, ROLE, on_event2)
    assert r.processed == 25 and seen2 == [f"NEW{i}" for i in range(25)], \
        "content anchor must catch truncate-then-regrow-past-cursor + deliver ALL"


def test_ge_no_false_desync_on_plain_append(tmp_path):
    """No false-positive: appending (content at cursor-1 unchanged) resumes normally."""
    inbox, cursor = _q(tmp_path), _c(tmp_path, 1)
    _seed_queue(tmp_path, 10)
    seen, on_event = _collector()
    DI.drain_queue(inbox, cursor, 1, ROLE, on_event)  # cursor=10, anchor(e9)
    _append_queue(tmp_path, ["a", "b"])               # e0..e9 unchanged, +2
    seen2, on_event2 = _collector()
    r = DI.drain_queue(inbox, cursor, 1, ROLE, on_event2)
    assert r.processed == 2 and seen2 == ["a", "b"]   # resumes from 10, no false desync


def test_ge_anchor_persisted_and_loaded(tmp_path):
    inbox, cursor = _q(tmp_path), _c(tmp_path, 1)
    _seed_queue(tmp_path, 3)
    DI.drain_queue(inbox, cursor, 1, ROLE, lambda *a: None)
    c = DI.load_cursor(cursor)
    last_line = inbox.read_text(encoding="utf-8").strip().split("\n")[-1]
    assert c.line == 3 and c.anchor == DI._anchor(last_line) and c.anchor != ""
