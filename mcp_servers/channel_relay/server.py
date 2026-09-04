"""
Channel relay MCP server (Python).

Provides the full channel MCP tool surface (reply, send, arbiter_ask, emit_fodl)
to any Claude Code session by HTTP-proxying to the per-role bun channel daemon
at localhost:RELAY_PORT.

This is the fork-friendly variant — works in any session (top-level, agent view,
Task-tool sub-agent) without requiring --dangerously-load-development-channels
or --mcp-config role-X.mcp.json. The bun daemon (channels/agent/channel.ts or
channels/scribe/webhook.ts) is the persistent backing store; the relay is a
thin shim that exposes its HTTP endpoints as MCP tools.

This export provides explicit pending/drain tools for relay sessions.
Project prompt hooks are a separate adopter integration and are not installed.
Native push sessions use the admitted Bun channel server.

Per FDRL Components 3+4+5 (canon 2026-05-26):
- send → POST /send (OIL-012 primitive)
- arbiter_ask → POST /arbiter_ask (FDRL C3)
- emit_fodl → POST /emit_fodl (FDRL C4)
- reply → POST /reply (existing)
"""

import os
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp_servers.channel_relay.durable_inbox import (
    cursor_filename,
    drain_queue,
    load_cursor,
    read_from_cursor,
)
from mcp_servers.channel_relay.kernel_plane_bridge import (
    dual_write_send,
    merge_pending,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))
from portable_host import authorize, configured_path
RELAY_ROLE = os.environ.get("RELAY_ROLE", "")
if RELAY_ROLE == "scribe":
    raise SystemExit("REFUSED: Scribe uses its admitted brokered channel")
authorize(RELAY_ROLE, "channel-relay")
RELAY_PORT = int(os.environ["RELAY_PORT"])
BUN_BASE_URL = f"http://127.0.0.1:{RELAY_PORT}"

VALID_ROLES = [
    "ccto", "ccpo", "ccro", "ccfo", "ccmo", "cclo", "ccgo", "ccco",
    "ccde", "cos", "scribe", "utility",
]
VALID_EVENT_TYPES = [
    "agent_direct",
    "task_handoff",
    "audit_request",
    "reply_classification",
    "arbiter_ask",
]

mcp = FastMCP(
    f"{RELAY_ROLE}-channel",
    instructions=(
        f"Events from {RELAY_ROLE}-channel are pushed to you in real-time via "
        f"the bun channel server when this session has the dev-channel "
        f"capability. Relay-only sessions use pending to drain events; project "
        f"prompt hooks require separate adopter configuration. Acknowledge every event with the reply tool. "
        f"Send to peer agents with send. Dispatch CoS arbitration with "
        f"arbiter_ask. Emit founder-decision items with emit_fodl. "
        f"All tools HTTP-proxy to the bun daemon at port {RELAY_PORT}."
    ),
)


def _repo_root() -> Path:
    return configured_path("CC_SUITE_WORKSPACE")

def _shared_dir() -> Path:
    return configured_path("CC_SUITE_CHANNEL_SHARED_DIR")

def _state_dir() -> Path:
    path = configured_path("CC_SUITE_CHANNEL_STATE_DIR")
    expected = configured_path("CC_SUITE_STATE_ROOT") / f"company-{RELAY_ROLE}" / "channel"
    if path != expected:
        raise ValueError("channel state must belong to the requested role and configured state root")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path

def _event_id_for(role: str, content: str, meta: dict[str, Any]) -> str:
    explicit = meta.get("event_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    seed = "|".join(
        str(part)
        for part in (
            role,
            meta.get("slack_ts", ""),
            meta.get("send_id", ""),
            meta.get("sent_at", ""),
            content,
        )
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"{role}-codex-{digest}"


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


# --- OIL-211 receipt-or-loud (send seam) --------------------------------------
# The bite: a send that APPEARS to succeed while delivering nothing (daemon
# claimed ok but the canonical queue never got the row — e.g. a daemon launched
# from a worktree appending to the WRONG TREE, or the direct fallback's bare
# append silently failing). Contract: `send` returns ok ONLY on a read-back of
# the canonical queue file. This is a LOCAL mirror of
# scripts/receipt_or_loud.py::append_row_with_receipt — mirrors, does NOT
# import (the handoff_ledger mirrors-not-imports precedent: mcp_servers must
# not couple to scripts/, and a defect in one implementation must stay visible
# to the other's tests).
#
# Honest bound: the relay verifies against ITS OWN canonical view of the repo
# (CCSUITE_REPO_ROOT or the module's location). If the relay itself runs from
# a non-canonical tree, its receipt attests arrival in THAT tree; the
# send-delivery sweep (scripts/send_delivery_sweep.py) is the structural
# backstop that catches cross-tree residuals.

# D3 canonical-check scan bound: when the queue is larger than this, only the
# trailing max(bound, row-size + slack) bytes are parsed. The daemon's row just
# landed, so it lives at the tail; the bound only caps pathological files.
_CANONICAL_SCAN_FULL_READ_MAX = 32 * 1024 * 1024


def _append_row_durable(path: Path, row: dict[str, Any]) -> tuple[bool, str]:
    """flock'd offset-anchored append: repair-if-torn + append + flush + fsync
    + read-back of EXACTLY the appended row at its recorded offset, all inside
    one critical section (the 2026-07-08 refinement ruling + the CCPO/CCTO
    concurrency constraint: the lock spans record-size → repair → append →
    read-at-offset; a torn tail is repaired only while the lock proves it
    quiescent). Kills D1 (torn-tail glue false receipt — the repair makes glue
    impossible, and the receipt is byte-equality + JSON-parse, never substring)
    and D2 (no scan window → no row too big to verify). (ok, reason); never
    raises. Mirrors scripts/receipt_or_loud.py::append_row_with_receipt —
    mirrors, does NOT import."""
    import fcntl

    try:
        line = json.dumps(row, sort_keys=True)
    except (TypeError, ValueError) as e:
        return False, f"event not serializable: {e}"
    payload = line.encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                prefix = b""
                if size > 0:
                    f.seek(size - 1)
                    if f.read(1) != b"\n":
                        prefix = b"\n"
                offset = size + len(prefix)
                f.write(prefix + payload + b"\n")
                f.flush()
                os.fsync(f.fileno())
                f.seek(offset)
                got = f.read(len(payload) + 1)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError as e:
        return False, f"append failed: {type(e).__name__}: {e}"
    if got != payload + b"\n":
        return False, "read-back MISSING: appended line not found at its offset on disk"
    try:
        json.loads(got[:-1].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return False, f"read-back row not parseable ({type(e).__name__}) — receipt refused"
    return True, "receipt: fsync + offset-anchored read-back verified"


def _queue_has_delivery_row(path: Path, send_id: str, content: str) -> bool:
    """D3 build-back: the canonical-queue verification of a daemon's send claim.

    The old check was bare `"{send_id}"` substring presence in a tail window —
    a STALE row's id verified a void as delivered (CCPO reproduced it). Per
    the refinement ruling, delivery evidence is the expected ROW: a complete,
    parseable queue line whose meta.send_id equals the claimed id AND whose
    content equals what was sent. A stale row can share an id but not both.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False
    need = max(_CANONICAL_SCAN_FULL_READ_MAX, len(content.encode("utf-8")) * 6 + 1024 * 1024)
    try:
        with path.open("rb") as f:
            if size > need:
                f.seek(size - need)
                raw = f.read()
                # Drop the leading partial line the seek may have cut into.
                nl = raw.find(b"\n")
                raw = raw[nl + 1:] if nl >= 0 else b""
            else:
                raw = f.read()
    except OSError:
        return False
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or f'"{send_id}"' not in line:
            continue  # cheap pre-filter; the verdict below is parse-based
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        meta = row.get("meta")
        # OIL-211 build-back (CCPO re-verify 33fdc3c, MEDIUM 1): `meta or {}`
        # only substitutes on a FALSY meta — a truthy non-dict (e.g. a bare
        # string or list some foreign/corrupt row carries) survived that guard
        # and meta.get() below raised AttributeError, escaping send()'s two
        # contractual terminal states (ok-with-receipt / loud SEND FAILED) as
        # an uncaught THIRD state (CCTO ruling: any escaping exception here is
        # a contract violation regardless of trigger). Per the R2/D4 fail-
        # closed-parse principle applied to this seam's read side: a row whose
        # shape we cannot trust is skipped, not crashed on.
        if not isinstance(meta, dict):
            continue
        if meta.get("send_id") == send_id and row.get("content") == content:
            return True
    return False


def _record_reply(event_id: str, text: str, source: str) -> None:
    _append_jsonl(
        _shared_dir() / "audit_replies.jsonl",
        {
            "ts": _now_iso(),
            "host": "Codex",
            "role": RELAY_ROLE,
            "event_id": event_id,
            "verdict": text,
            "source": source,
            "tool": f"mcp__{RELAY_ROLE}-channel__reply",
        },
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_send_matrix(sender_role: str, target_role: str, event_type: str) -> str | None:
    """The ONE send-permission matrix — shared by the per-role MCP relay
    (sender = RELAY_ROLE) and the Workloop relay daemon (sender = the port's
    bound role). Keep every rule here so the two surfaces cannot drift.

    Rules:
    - Scribe: UNRESTRICTED across VALID_ROLES — Scribe is the fleet COMMS HUB
      ("everything goes through the Scribe", both directions). It relays
      founder dispositions + handoffs to every Mise exec lane.
    - Mise exec roles: unrestricted across VALID_ROLES (their lane authority).
    """
    if target_role not in VALID_ROLES:
        return f'Unknown target_role "{target_role}". Valid: {", ".join(VALID_ROLES)}'
    if event_type not in VALID_EVENT_TYPES:
        return f'Unknown event_type "{event_type}". Valid: {", ".join(VALID_EVENT_TYPES)}'
    if sender_role == "scribe":
        # Scribe is the fleet comms hub — unrestricted across VALID_ROLES
        # (founder-ratified 2026-07-02). Same authority as a Mise exec lane.
        return None
    return None


def _validate_send(target_role: str, event_type: str) -> str | None:
    return validate_send_matrix(RELAY_ROLE, target_role, event_type)


# B3 (CCTO amendment A3, OIL-221 north-star trial): the Plan-First advisor protocol
# (cc_execs/memos/ccde/CCTO_design__20260708__plan-first-mechanism.md §3) sends an
# advisor consult as event_type=agent_direct with SUBJECT prefix "ADVISOR:" — never
# task_handoff, since it's a pull (a question) not a push (a directive). That meant it
# never got the stall-backstop's protection: CCDE's own branch-reachability advisor ask
# reached CCTO's queue durably but never surfaced into a live-but-busy window (receipt-
# to-queue != receipt-to-attention). Mirrors channels/shared/advisor_ask_detect.ts —
# same regex, same case-sensitivity, kept in parity by each mirror's own tests.
_ADVISOR_SUBJECT_RE = re.compile(r"^SUBJECT:\s*ADVISOR:", re.MULTILINE)


def _is_advisor_ask(event_type: str, content: str) -> bool:
    return event_type == "agent_direct" and bool(_ADVISOR_SUBJECT_RE.search(content or ""))


def _open_handoff_row_safe(send_id: str, from_agent: str, target_role: str,
                           content: str, sent_at: str,
                           meta: dict[str, Any] | None = None) -> None:
    """G-c: on a task_handoff send OR an ADVISOR ask (B3), open an OWNED handoff-ledger
    row (owner = the target) so the stall-backstop can bite. BEST-EFFORT — a ledger
    failure must NEVER break a send. Idempotent on send_id (open_from_send is a no-op
    on a dup). Action = the first content line.

    ENGINE-HYGIENE #7 (2026-07-02): a row opens ONLY for a DECLARED obligation —
    meta.expected_artifact (+ optional meta.deadline_seconds). A bare send is
    a message, not a deadline-bound obligation: skip silently (no row was intended). A
    DECLARED row that still fails is logged loud (that IS an intended obligation being
    dropped) — the send itself is never broken either way."""
    m = meta or {}
    art = str(m.get("expected_artifact") or "").strip()
    if not art:
        return  # bare send — a message, not an obligation (engine-hygiene #7)
    try:
        from scripts import handoff_ledger as _hl
        action = ((content or "").strip().splitlines() or ["task handoff"])[0][:200] or "task handoff"
        try:
            dl = float(m.get("deadline_seconds") or _hl.DEADLINE_DEFAULT_SECONDS)
        except (TypeError, ValueError):
            dl = _hl.DEADLINE_DEFAULT_SECONDS
        _hl.open_from_send(send_id=send_id, from_agent=from_agent, owner_agent=target_role,
                           action=action, opened_ts_utc=sent_at,
                           expected_artifact=art, deadline_seconds=dl)
    except Exception as e:
        print(f"[channel_relay] DECLARED handoff row-open failed for {send_id}: "
              f"{type(e).__name__}: {e} (send delivered; obligation NOT tracked)",
              file=sys.stderr)


def _direct_send(
    target_role: str,
    content: str,
    event_type: str,
    meta: dict[str, Any] | None,
) -> dict[str, Any]:
    validation = _validate_send(target_role, event_type)
    if validation:
        return {"status": "error", "error": validation}

    sent_at = _now_iso()
    seed = f"{RELAY_ROLE}|{target_role}|{event_type}|{content}|{sent_at}"
    send_id = f"{RELAY_ROLE}-codex-send-{hashlib.sha1(seed.encode()).hexdigest()[:10]}"
    event = {
        "event_type": event_type,
        "content": content,
        "meta": {
            **(meta or {}),
            "sender_role": RELAY_ROLE,
            "target_role": target_role,
            "sent_at": sent_at,
            "send_id": send_id,
            "event_id": send_id,
            "source_host": "Codex",
        },
    }
    # OIL-211: the fallback queue append is DURABLE (flock+fsync+read-back) —
    # the old bare append could fail or land nowhere while the caller got ok.
    ok, reason = _append_row_durable(_shared_dir() / f"queue_{target_role}.jsonl", event)
    if not ok:
        return {"status": "error", "error": f"direct fallback NOT delivered: {reason}"}
    _append_jsonl(
        _shared_dir() / "audit_sends.jsonl",
        {
            "ts": sent_at,
            "send_id": send_id,
            "sender": RELAY_ROLE,
            "target": target_role,
            "event_type": event_type,
            "content_length": len(content),
            # OIL-211: privacy-preserving content fingerprint (never the body).
            "content_sha1": hashlib.sha1(content.encode("utf-8")).hexdigest()[:12],
            "source": "codex-direct-fallback",
        },
    )
    if event_type == "task_handoff" or _is_advisor_ask(event_type, content):
        _open_handoff_row_safe(send_id, RELAY_ROLE, target_role, content, sent_at, meta=meta)
    # PATH C1 finish: dual-write to company message plane when cutover active.
    kernel = dual_write_send(
        sender_role=RELAY_ROLE,
        target_role=target_role,
        event_type=event_type,
        content=content,
        send_id=send_id,
    )
    return {
        "status": "ok",
        "send_id": send_id,
        "source": "codex-direct-fallback",
        "receipt": reason,
        "kernel": kernel,
    }


def _format_pending_event(event_type: str, content: str, meta: dict[str, Any]) -> dict[str, Any]:
    meta = dict(meta or {})
    event_id = _event_id_for(RELAY_ROLE, content, meta)
    meta.setdefault("event_id", event_id)
    return {
        "event_id": event_id,
        "event_type": event_type,
        "content": content,
        "meta": meta,
    }


def _read_pending_without_advancing(limit: int) -> list[dict[str, Any]]:
    inbox = _shared_dir() / f"queue_{RELAY_ROLE}.jsonl"
    cursor = _shared_dir() / cursor_filename(RELAY_ROLE, os.getpid(), "relay")
    current = load_cursor(cursor)
    lines, _ = read_from_cursor(inbox, current.line)
    events: list[dict[str, Any]] = []
    for line in lines[:limit]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        events.append(
            _format_pending_event(
                row.get("event_type") or "slack_message",
                row.get("content") or "",
                row.get("meta") or {},
            )
        )
    return events


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any] | str:
    """POST helper. Returns parsed JSON on success, error string on failure."""
    try:
        response = httpx.post(
            f"{BUN_BASE_URL}{path}",
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
            err = body.get("error") or body.get("message") or str(e)
        except Exception:
            err = str(e)
        return f"ERROR — {err}"
    except httpx.RequestError as e:
        return (
            f"ERROR — bun channel daemon at port {RELAY_PORT} unreachable "
            f"({type(e).__name__}). Is the {RELAY_ROLE}-channel server running?"
        )


@mcp.tool()
def reply(event_id: str, text: str) -> str:
    """Acknowledge a channel event by event_id."""
    result = _post("/reply", {"event_id": event_id, "text": text})
    if isinstance(result, str):
        _record_reply(event_id, text, "codex-direct-fallback")
        return f"acknowledged via Codex direct fallback; relay note: {result}"
    _record_reply(event_id, text, "http-relay")
    return result.get("message", "acknowledged")


def _send_failed(target_role: str, detail: str) -> str:
    """The loud terminal failure — no path returns ok without a canonical
    read-back receipt, and this string is the mandated caller contract:
    fall back to a durable memo file + surface the failure."""
    return (
        f"SEND FAILED — NOT delivered to queue_{target_role}. {detail} "
        f"Write a durable memo file (cc_execs/memos/{target_role}/) and "
        f"surface this delivery failure loudly in your own output."
    )


@mcp.tool()
def send(
    target_role: str,
    content: str,
    event_type: str = "agent_direct",
    meta: dict[str, Any] | None = None,
) -> str:
    """Send a direct message to a peer agent's channel queue (OIL-012 primitive,
    OIL-211 receipt-or-loud: ok ONLY on canonical-queue read-back).

    target_role: one of ccto, ccpo, ccro, ccfo, ccmo, cclo, ccgo, ccco, ccde, cos, scribe, utility
    event_type: agent_direct (default), task_handoff, audit_request, reply_classification, arbiter_ask
    """
    payload: dict[str, Any] = {
        "target_role": target_role,
        "content": content,
        "event_type": event_type,
    }
    if meta is not None:
        payload["meta"] = meta
    result = _post("/send", payload)

    if isinstance(result, str):
        # Daemon unreachable/errored → durable direct fallback (flock+fsync+
        # read-back). ok only on its receipt.
        fallback = _direct_send(target_role, content, event_type, meta)
        if fallback.get("status") == "ok":
            return (
                f"sent: {fallback['send_id']} (Codex direct fallback, "
                f"{fallback.get('receipt', 'receipt verified')}; relay note: {result})"
            )
        return _send_failed(target_role, f"daemon: {result}; fallback: {fallback.get('error')}.")

    send_id = result.get("send_id")
    if not send_id:
        # A daemon "ok" without a send_id is unverifiable — treat as NOT
        # delivered and recover through the durable fallback.
        fallback = _direct_send(target_role, content, event_type, meta)
        if fallback.get("status") == "ok":
            return (
                f"sent: {fallback['send_id']} (daemon ok carried no send_id — "
                f"unverifiable, recovered via direct fallback, {fallback.get('receipt')})"
            )
        return _send_failed(target_role, f"daemon ok without send_id; fallback: {fallback.get('error')}.")

    # OIL-211 receipt: the daemon's claim is verified against the CANONICAL
    # queue file. A daemon launched from the wrong tree appends elsewhere and
    # still returns ok — the leading silent-void mechanism (bite #2). D3
    # build-back: the check is the expected ROW (parsed meta.send_id + exact
    # content), never bare id presence — a stale row's id no longer verifies
    # a void as delivered.
    if _queue_has_delivery_row(_shared_dir() / f"queue_{target_role}.jsonl", send_id, content):
        kernel = dual_write_send(
            sender_role=RELAY_ROLE,
            target_role=target_role,
            event_type=event_type,
            content=content,
            send_id=send_id,
        )
        knote = ""
        if kernel.get("status") in {"accepted", "duplicate"}:
            knote = f"; kernel {kernel.get('status')} {kernel.get('message_id', '')[:13]}"
        elif kernel.get("status") == "error":
            knote = f"; kernel dual-write soft-fail: {kernel.get('error', '')[:80]}"
        return f"sent: {send_id} (receipt: canonical read-back verified{knote})"

    fallback = _direct_send(target_role, content, event_type, meta)
    if fallback.get("status") == "ok":
        return (
            f"sent: {fallback['send_id']} (daemon claimed {send_id} but the canonical "
            f"queue has no such row — wrong-tree void recovered via direct fallback, "
            f"{fallback.get('receipt')})"
        )
    return _send_failed(
        target_role,
        f"daemon claimed {send_id} but the canonical queue has no such row, "
        f"and the fallback failed: {fallback.get('error')}.",
    )


@mcp.tool()
def pending(limit: int = 10, mark_seen: bool = False) -> str:
    """Read pending events for this role from channels/shared/queue_{role}.jsonl.

    mark_seen=false peeks without advancing the relay cursor. mark_seen=true
    advances only this relay consumer's cursor; hook and wake-loop cursors are
    independent.
    """
    if limit < 1:
        limit = 1
    if limit > 50:
        limit = 50

    if not mark_seen:
        file_events = _read_pending_without_advancing(limit)
        events = merge_pending(file_events, RELAY_ROLE, limit)
        return json.dumps({"events": events}, indent=2)

    events: list[dict[str, Any]] = []

    def _handler(event_type: str, content: str, meta: dict[str, Any]) -> None:
        if len(events) < limit:
            events.append(_format_pending_event(event_type, content, meta))

    result = drain_queue(
        inbox_file=_shared_dir() / f"queue_{RELAY_ROLE}.jsonl",
        cursor_file=_shared_dir() / cursor_filename(RELAY_ROLE, os.getpid(), "relay"),
        pid=os.getpid(),
        role=RELAY_ROLE,
        on_event=_handler,
    )
    events = merge_pending(events, RELAY_ROLE, limit)
    return json.dumps(
        {"processed": result.processed, "held": result.held, "events": events},
        indent=2,
    )


@mcp.tool()
def dual_ack(
    event_id: str,
    text: str,
    slack_channel_id: str | None = None,
    slack_channel_name: str | None = None,
    slack_ts: str | None = None,
    event_content: str | None = None,
    thread_ts: str | None = None,
) -> str:
    """Perform CC-Suite Dual-ACK for a Slack-sourced event.

    Always performs the internal channel reply. Posts the external Slack ACK
    only when slack_ts is present and the ACK-of-ACK/self-echo exemptions do
    not apply.
    """
    internal = reply(event_id, text)
    if not slack_ts:
        return f"internal-only ACK complete (hook/no-slack_ts exemption). {internal}"
    if event_content and "Both ACK'd internally via" in event_content:
        return f"internal-only ACK complete (ACK-of-ACK exemption). {internal}"

    if os.environ.get("CC_SUITE_ENABLE_SLACK_BRIDGE") != "1":
        return f"internal ACK complete; external Slack adapter is not configured. {internal}"
    try:
        authorize(RELAY_ROLE, "slack-ack")
        from mcp_servers.slack_bridge.client import (
            channel_name_for_id,
            is_recent_self_post,
            post_message,
        )
    except Exception as exc:
        return f"internal ACK complete; Slack bridge unavailable: {type(exc).__name__}: {exc}"

    if is_recent_self_post(slack_ts, _state_dir()):
        return f"internal-only ACK complete (self-echo exemption for ts {slack_ts}). {internal}"
    if not slack_channel_id:
        return "internal ACK complete; Slack external ACK skipped because slack_channel_id was not provided"

    channel_label = slack_channel_name or channel_name_for_id(slack_channel_id) or slack_channel_id
    detail = f" {text.strip()}" if text.strip() else ""
    external_text = (
        f"Posted to #{channel_label} (ts {slack_ts}). Both ACK'd internally via "
        f"`mcp__{RELAY_ROLE}-channel__reply` and confirmed externally in Slack "
        f"so you can see it. Channel event {event_id} closed.{detail}"
    )
    posted = post_message(
        channel_id=slack_channel_id,
        text=external_text,
        thread_ts=thread_ts,
        state_dir=_state_dir(),
    )
    if posted.ok:
        return f"dual-ACK complete: internal={internal}; slack_ts={posted.ts}"
    return f"internal ACK complete; Slack external ACK failed: {posted.error}"


@mcp.tool()
def arbiter_ask(
    tier: str,
    edg: int,
    confidence: str,
    question: str,
    options: list[str],
    recommended_default: str,
    subject_slug: str,
    confidence_pct: int | None = None,
    peer_consulted: str | None = None,
    files_searched: list[str] | None = None,
    precedent: str | None = None,
    deadline: str | None = None,
) -> str:
    """Dispatch a structured arbiter-ask to CoS-001 (FDRL Component 3).

    tier: S | A | B | C
    edg: 0-4
    confidence: VF | MN | FDP | U
    options: numbered list of options with brief tradeoffs
    subject_slug: kebab-case
    """
    payload: dict[str, Any] = {
        "tier": tier,
        "edg": edg,
        "confidence": confidence,
        "question": question,
        "options": options,
        "recommended_default": recommended_default,
        "subject_slug": subject_slug,
    }
    if confidence_pct is not None:
        payload["confidence_pct"] = confidence_pct
    if peer_consulted is not None:
        payload["peer_consulted"] = peer_consulted
    if files_searched is not None:
        payload["files_searched"] = files_searched
    if precedent is not None:
        payload["precedent"] = precedent
    if deadline is not None:
        payload["deadline"] = deadline
    result = _post("/arbiter_ask", payload)
    if isinstance(result, str):
        return result
    send_id = result.get("send_id")
    return f"arbiter-ask dispatched: {send_id}" if send_id else f"dispatched: {result}"


@mcp.tool()
def emit_fodl(
    title: str,
    problem_statement: str,
    why_it_matters: str,
    options: list[dict[str, str]],
    recommended_default: str,
    if_no_decision_by: str,
    time_sensitivity: str,
    tier: str,
    edg: int,
    originating_agent: str,
    arbiter_consulted: str | None = None,
    precedent: str | None = None,
) -> str:
    """Auto-emit a Founder Open-Decision List item (FDRL Component 4).

    options: list of {label, text} dicts
    time_sensitivity: emergency | normal | low
    tier: S | A | B | C
    edg: 0-4
    originating_agent: agent-ID like "CCDE-001"
    """
    payload: dict[str, Any] = {
        "title": title,
        "problem_statement": problem_statement,
        "why_it_matters": why_it_matters,
        "options": options,
        "recommended_default": recommended_default,
        "if_no_decision_by": if_no_decision_by,
        "time_sensitivity": time_sensitivity,
        "tier": tier,
        "edg": edg,
        "originating_agent": originating_agent,
    }
    if arbiter_consulted is not None:
        payload["arbiter_consulted"] = arbiter_consulted
    if precedent is not None:
        payload["precedent"] = precedent
    result = _post("/emit_fodl", payload)
    if isinstance(result, str):
        return result
    fodl_id = result.get("fodl_id")
    return f"FODL emitted: {fodl_id}" if fodl_id else f"emitted: {result}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
