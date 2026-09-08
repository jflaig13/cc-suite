# SPDX-License-Identifier: MPL-2.0
"""Tier 1 tests for mcp_servers/channel_relay/durable_inbox.py.

Tests the cursor primitive in isolation (no Claude Code, no MCP, no actual
queue files in production paths). Uses tmp_path for all filesystem operations.

This module is the Python port of channels/shared/durable_inbox.ts. The
tests cover the same scenarios as the TS test suite (durable_inbox.test.ts)
plus Python-specific edge cases (PermissionError handling, dataclass shapes).

Verification independence: this test file does NOT import from
channels/shared/durable_inbox.ts or any compiled/transpiled version of it.
Tests verify the Python implementation against its own spec.
"""

from __future__ import annotations

import json
import os
import pytest
from pathlib import Path

from mcp_servers.channel_relay.durable_inbox import (
    Cursor,
    DrainResult,
    cleanup_dead_cursors,
    cleanup_stale_session_cursors,
    cursor_filename,
    drain_queue,
    load_cursor,
    read_from_cursor,
    save_cursor,
    session_cursor_filename,
)


# ---------- load_cursor / save_cursor ------------------------------------


class TestCursorLoadSave:
    def test_save_then_load_round_trip(self, tmp_path: Path):
        cursor_file = tmp_path / "c.json"
        save_cursor(cursor_file, line=42, pid=12345, role="ccto")
        loaded = load_cursor(cursor_file)
        assert loaded.line == 42
        assert loaded.pid == 12345
        assert loaded.role == "ccto"

    def test_load_missing_file_returns_zero_cursor(self, tmp_path: Path):
        cursor_file = tmp_path / "does-not-exist.json"
        loaded = load_cursor(cursor_file)
        assert loaded.line == 0
        assert loaded.pid == 0
        assert loaded.role == ""

    def test_load_corrupt_json_returns_zero_cursor(self, tmp_path: Path):
        cursor_file = tmp_path / "garbage.json"
        cursor_file.write_text("not-json-at-all{{{", encoding="utf-8")
        loaded = load_cursor(cursor_file)
        assert loaded.line == 0

    def test_load_missing_keys_returns_defaults(self, tmp_path: Path):
        cursor_file = tmp_path / "partial.json"
        cursor_file.write_text('{"line": 7}', encoding="utf-8")
        loaded = load_cursor(cursor_file)
        assert loaded.line == 7
        assert loaded.pid == 0
        assert loaded.role == ""

    def test_save_swallows_disk_error(self, tmp_path: Path):
        # Target a path inside a non-existent directory — save should not raise
        bad_path = tmp_path / "no-such-dir" / "c.json"
        # Should NOT raise — best-effort silent failure per the spec.
        save_cursor(bad_path, line=1, pid=2, role="ccto")
        # Subsequent load should return zero cursor (file wasn't written)
        loaded = load_cursor(bad_path)
        assert loaded.line == 0


# ---------- read_from_cursor ---------------------------------------------


class TestReadFromCursor:
    def test_missing_file_returns_empty(self, tmp_path: Path):
        lines, total = read_from_cursor(tmp_path / "no-queue.jsonl", 0)
        assert lines == []
        assert total == 0

    def test_returns_all_lines_when_cursor_zero(self, tmp_path: Path):
        q = tmp_path / "q.jsonl"
        q.write_text('{"a":1}\n{"b":2}\n{"c":3}\n', encoding="utf-8")
        lines, total = read_from_cursor(q, 0)
        assert lines == ['{"a":1}', '{"b":2}', '{"c":3}']
        assert total == 3

    def test_returns_lines_from_cursor_offset(self, tmp_path: Path):
        q = tmp_path / "q.jsonl"
        q.write_text('{"a":1}\n{"b":2}\n{"c":3}\n', encoding="utf-8")
        lines, total = read_from_cursor(q, 1)
        assert lines == ['{"b":2}', '{"c":3}']
        assert total == 3

    def test_cursor_at_end_returns_empty(self, tmp_path: Path):
        q = tmp_path / "q.jsonl"
        q.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
        lines, total = read_from_cursor(q, 2)
        assert lines == []
        assert total == 2

    def test_torn_line_guard_excludes_unterminated_final(self, tmp_path: Path):
        """F-B2 fix: a final line without trailing '\\n' is in-flight."""
        q = tmp_path / "q.jsonl"
        q.write_text('{"a":1}\n{"b":2}\n{"in-progress', encoding="utf-8")
        lines, total = read_from_cursor(q, 0)
        # The third line is partial — excluded
        assert lines == ['{"a":1}', '{"b":2}']
        assert total == 2

    def test_torn_only_partial_line_returns_empty(self, tmp_path: Path):
        """Single partial line with no '\\n' anywhere → empty result."""
        q = tmp_path / "q.jsonl"
        q.write_text('{"partial', encoding="utf-8")
        lines, total = read_from_cursor(q, 0)
        assert lines == []
        assert total == 0

    def test_skips_blank_lines(self, tmp_path: Path):
        q = tmp_path / "q.jsonl"
        q.write_text('{"a":1}\n\n{"b":2}\n', encoding="utf-8")
        lines, total = read_from_cursor(q, 0)
        assert lines == ['{"a":1}', '{"b":2}']
        assert total == 2


# ---------- cleanup_dead_cursors -----------------------------------------


class TestCleanupDeadCursors:
    def test_removes_cursor_for_dead_pid(self, tmp_path: Path):
        # PID 999_999_999 is virtually guaranteed to not exist on macOS.
        dead = tmp_path / "cursor_ccto_hook_999999999.json"
        dead.write_text('{"line":1,"pid":999999999,"role":"ccto"}', encoding="utf-8")
        removed = cleanup_dead_cursors(tmp_path, role="ccto", my_pid=os.getpid())
        assert removed >= 1
        assert not dead.exists()

    def test_preserves_cursor_for_live_pid(self, tmp_path: Path):
        live_pid = os.getpid()
        alive = tmp_path / f"cursor_ccto_hook_{live_pid}.json"
        alive.write_text('{"line":1}', encoding="utf-8")
        cleanup_dead_cursors(tmp_path, role="ccto", my_pid=live_pid)
        # The function skips own pid too — alive should remain regardless
        assert alive.exists()

    def test_preserves_cursor_for_other_role(self, tmp_path: Path):
        # Dead PID but different role — should NOT be touched
        other = tmp_path / "cursor_ccpo_hook_999999999.json"
        other.write_text('{"line":1}', encoding="utf-8")
        cleanup_dead_cursors(tmp_path, role="ccto", my_pid=os.getpid())
        assert other.exists()

    def test_supports_legacy_filename_pattern(self, tmp_path: Path):
        """Old `cursor_{role}_{pid}.json` (no 'kind' segment) still cleaned up
        for dead pids."""
        legacy_dead = tmp_path / "cursor_ccto_999999999.json"
        legacy_dead.write_text('{"line":1}', encoding="utf-8")
        cleanup_dead_cursors(tmp_path, role="ccto", my_pid=os.getpid())
        assert not legacy_dead.exists()

    def test_ignores_non_matching_files(self, tmp_path: Path):
        unrelated = tmp_path / "some_other_file.txt"
        unrelated.write_text("hello", encoding="utf-8")
        cleanup_dead_cursors(tmp_path, role="ccto", my_pid=os.getpid())
        assert unrelated.exists()

    def test_missing_shared_dir_does_not_crash(self, tmp_path: Path):
        # Pass a non-existent dir — should silently return 0
        removed = cleanup_dead_cursors(
            tmp_path / "no-such-dir",
            role="ccto",
            my_pid=os.getpid(),
        )
        assert removed == 0


# ---------- drain_queue --------------------------------------------------


class TestDrainQueue:
    def test_drain_empty_queue_returns_zero(self, tmp_path: Path):
        q = tmp_path / "q.jsonl"
        c = tmp_path / "c.json"
        events = []
        result = drain_queue(q, c, pid=123, role="ccto", on_event=lambda *a: events.append(a))
        assert result.processed == 0
        assert result.held is False
        assert events == []

    def test_drain_advances_cursor_on_success(self, tmp_path: Path):
        q = tmp_path / "q.jsonl"
        c = tmp_path / "c.json"
        q.write_text(
            '{"event_type":"slack_message","content":"hi","meta":{"k":"v"}}\n'
            '{"event_type":"slack_message","content":"hi2","meta":{}}\n'
            '{"event_type":"slack_message","content":"hi3","meta":{}}\n',
            encoding="utf-8",
        )
        events = []
        result = drain_queue(q, c, pid=42, role="ccto", on_event=lambda *a: events.append(a))
        assert result.processed == 3
        assert result.held is False
        assert len(events) == 3
        # Cursor should now be at 3
        cursor = load_cursor(c)
        assert cursor.line == 3
        assert cursor.pid == 42
        assert cursor.role == "ccto"

    def test_drain_holds_cursor_on_handler_throw(self, tmp_path: Path):
        """F-B1 fix: on_event throw stops processing AND holds cursor."""
        q = tmp_path / "q.jsonl"
        c = tmp_path / "c.json"
        q.write_text(
            '{"event_type":"a","content":"1","meta":{}}\n'
            '{"event_type":"a","content":"2","meta":{}}\n'
            '{"event_type":"a","content":"3","meta":{}}\n',
            encoding="utf-8",
        )

        calls = []

        def throwing_handler(event_type, content, meta):
            calls.append(content)
            if content == "2":
                raise RuntimeError("simulated handler failure")

        result = drain_queue(q, c, pid=1, role="ccto", on_event=throwing_handler)
        assert result.processed == 1  # only "1" advanced; "2" failed; "3" untouched
        assert result.held is True
        assert calls == ["1", "2"]  # handler was called for 1 + 2, threw on 2
        # Next drain should retry event "2"
        cursor = load_cursor(c)
        assert cursor.line == 1

    def test_drain_skips_malformed_json(self, tmp_path: Path):
        """F-B1 fix: JSON.parse failure advances cursor past the bad line."""
        q = tmp_path / "q.jsonl"
        c = tmp_path / "c.json"
        q.write_text(
            '{"event_type":"a","content":"good1","meta":{}}\n'
            'NOT JSON\n'
            '{"event_type":"a","content":"good2","meta":{}}\n',
            encoding="utf-8",
        )
        events = []
        result = drain_queue(q, c, pid=1, role="ccto", on_event=lambda *a: events.append(a[1]))
        assert result.processed == 3  # advanced past bad line
        assert result.held is False
        assert events == ["good1", "good2"]

    def test_drain_resumes_from_cursor(self, tmp_path: Path):
        """Sequential drain calls advance cumulatively."""
        q = tmp_path / "q.jsonl"
        c = tmp_path / "c.json"
        q.write_text(
            '{"event_type":"a","content":"1","meta":{}}\n'
            '{"event_type":"a","content":"2","meta":{}}\n',
            encoding="utf-8",
        )
        # First drain
        events1 = []
        r1 = drain_queue(q, c, pid=1, role="ccto", on_event=lambda *a: events1.append(a[1]))
        assert r1.processed == 2

        # Append more events
        with q.open("a", encoding="utf-8") as f:
            f.write('{"event_type":"a","content":"3","meta":{}}\n')
            f.write('{"event_type":"a","content":"4","meta":{}}\n')

        # Second drain — should only see new events
        events2 = []
        r2 = drain_queue(q, c, pid=1, role="ccto", on_event=lambda *a: events2.append(a[1]))
        assert r2.processed == 2
        assert events2 == ["3", "4"]

    def test_drain_torn_line_does_not_advance_past_partial(self, tmp_path: Path):
        """If queue file ends mid-line, drain processes complete lines only."""
        q = tmp_path / "q.jsonl"
        c = tmp_path / "c.json"
        q.write_text(
            '{"event_type":"a","content":"1","meta":{}}\n'
            '{"event_type":"a","content":"partial-write-in-progress',
            encoding="utf-8",
        )
        events = []
        result = drain_queue(q, c, pid=1, role="ccto", on_event=lambda *a: events.append(a[1]))
        assert result.processed == 1
        assert events == ["1"]
        # Cursor at 1 — next drain will pick up the (then-completed) partial line


# ---------- cursor_filename ----------------------------------------------


class TestCursorFilename:
    def test_basic_format(self):
        assert cursor_filename("ccto", 1234, "hook") == "cursor_ccto_hook_1234.json"

    def test_different_roles(self):
        assert cursor_filename("scribe", 9999, "bun") == "cursor_scribe_bun_9999.json"

    def test_rejects_non_lowercase_kind(self):
        with pytest.raises(ValueError, match="lowercase letters only"):
            cursor_filename("ccto", 1, "Hook")

    def test_rejects_kind_with_digits(self):
        with pytest.raises(ValueError, match="lowercase letters only"):
            cursor_filename("ccto", 1, "hook2")

    def test_rejects_kind_with_separator(self):
        with pytest.raises(ValueError, match="lowercase letters only"):
            cursor_filename("ccto", 1, "hook_v2")


# ---------- DrainResult / Cursor dataclass shape -------------------------


class TestDataclassShape:
    def test_cursor_fields(self):
        c = Cursor(line=5, pid=100, role="ccto")
        assert c.line == 5
        assert c.pid == 100
        assert c.role == "ccto"

    def test_drain_result_fields(self):
        r = DrainResult(processed=3, held=False)
        assert r.processed == 3
        assert r.held is False


# ---------- session_cursor_filename --------------------------------------


class TestSessionCursorFilename:
    def test_basic_shape(self):
        assert session_cursor_filename("ccde", "abc123") == "cursor_ccde_session_abc123.json"

    def test_sanitizes_uuid_hyphens_and_case(self):
        # A real session_id is a hyphenated UUID; hyphens stripped, lowercased.
        out = session_cursor_filename("ccpo", "E32B6A6F-AEB0-475B-A04A-5845C0BCC3D7")
        assert out == "cursor_ccpo_session_e32b6a6faeb0475ba04a5845c0bcc3d7.json"

    def test_empty_session_id_uses_unknown(self):
        assert session_cursor_filename("scribe", "") == "cursor_scribe_session_unknown.json"

    def test_none_session_id_uses_unknown(self):
        assert session_cursor_filename("cos", None) == "cursor_cos_session_unknown.json"

    def test_stable_across_calls(self):
        # The whole point: same session_id -> same filename every invocation.
        a = session_cursor_filename("ccto", "sess-1")
        b = session_cursor_filename("ccto", "sess-1")
        assert a == b


# ---------- cleanup_stale_session_cursors --------------------------------


class TestCleanupStaleSessionCursors:
    def test_removes_old_session_cursor(self, tmp_path: Path):
        old = tmp_path / "cursor_ccde_session_aaa.json"
        old.write_text('{"line":1,"pid":0,"role":"ccde"}')
        old_time = old.stat().st_mtime - (40 * 24 * 3600)  # 40 days old
        os.utime(old, (old_time, old_time))
        removed = cleanup_stale_session_cursors(tmp_path, "ccde")
        assert removed == 1
        assert not old.exists()

    def test_keeps_recent_session_cursor(self, tmp_path: Path):
        fresh = tmp_path / "cursor_ccde_session_bbb.json"
        fresh.write_text('{"line":1,"pid":0,"role":"ccde"}')
        removed = cleanup_stale_session_cursors(tmp_path, "ccde")
        assert removed == 0
        assert fresh.exists()

    def test_ignores_other_roles_and_pid_cursors(self, tmp_path: Path):
        other = tmp_path / "cursor_ccpo_session_ccc.json"
        pid_cursor = tmp_path / "cursor_ccde_hook_123.json"
        for f in (other, pid_cursor):
            f.write_text("{}")
            old_time = f.stat().st_mtime - (40 * 24 * 3600)
            os.utime(f, (old_time, old_time))
        removed = cleanup_stale_session_cursors(tmp_path, "ccde")
        # Only ccde session cursors are eligible; other role + pid cursor untouched.
        assert removed == 0
        assert other.exists()
        assert pid_cursor.exists()

    def test_missing_dir_does_not_raise(self, tmp_path: Path):
        assert cleanup_stale_session_cursors(tmp_path / "nope", "ccde") == 0
