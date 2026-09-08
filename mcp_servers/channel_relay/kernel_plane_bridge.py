# SPDX-License-Identifier: MPL-2.0
"""Bridge channel_relay ↔ company message plane v1 cutover.

When a role's cutover state is dual_write or kernel_primary:
  * successful sends also land on the kernel plane (best-effort, never
    fails the file-bus receipt)
  * pending/drain also claims leased kernel messages into the same event
    shape the file queue uses

File bus remains authoritative for receipt-or-loud. Kernel is additive until
kernel_primary + daemon cutover is fully trusted; even then this bridge keeps
file writes for rollback (operator can set file_bus_only / rolled_back).
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any
from fleet_kernel.company_cutover_mirror import (
    read_cutover_mirror as _read_mirror,
    locked_cutover_mirror,
)

# Cutover states that enable kernel dual-write / claim
_KERNEL_ACTIVE = frozenset({"dual_write", "kernel_primary"})
_CACHE_TTL_SEC = 5.0
_cutover_cache: dict[str, tuple[float, str]] = {}

# Kernel kinds that map 1:1 to channel event types
_KERNEL_KINDS = frozenset({
    "agent_direct", "task_handoff", "audit_request",
    "reply_classification", "arbiter_ask",
})
_CC_ROLES = frozenset({
    "scribe", "cos", "ccto", "ccpo", "ccde", "ccro", "ccfo", "ccmo",
    "cclo", "ccgo", "ccco", "utility",
})


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_cutover_state(role: str) -> str:
    """Return cutover state for role; default file_bus_only."""
    if os.environ.get("CC_SUITE_ENABLE_KERNEL_BRIDGE") != "1":
        return "file_bus_only"
    if role not in _CC_ROLES:
        return "file_bus_only"
    now = time.monotonic()
    cached = _cutover_cache.get(role)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return cached[1]

    state = "file_bus_only"
    # Prefer DB when credentials exist
    try:
        import sys
        root = _repo_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from fleet_kernel.company_message_plane.client import CompanyMessagePlaneClient

        with locked_cutover_mirror() as write_mirror:
            state = CompanyMessagePlaneClient().get_cutover(role).state
            try:
                write_mirror(role, state)
            except Exception:
                # Projection failure does not override a successful canonical read.
                pass
    except Exception:
        state = _read_mirror().get(role, "file_bus_only")

    _cutover_cache[role] = (now, state)
    return state


def kernel_active_for(*roles: str) -> bool:
    return any(get_cutover_state(r) in _KERNEL_ACTIVE for r in roles if r)


def dual_write_send(
    *,
    sender_role: str,
    target_role: str,
    event_type: str,
    content: str,
    send_id: str,
) -> dict[str, Any]:
    """Best-effort kernel dual-write after a verified file-bus delivery.

    Never raises. Returns status dict for logging.
    """
    if not kernel_active_for(sender_role, target_role):
        return {"status": "skipped", "reason": "cutover_inactive"}
    if sender_role not in _CC_ROLES or target_role not in _CC_ROLES:
        return {"status": "skipped", "reason": "non_cc_role"}
    kind = event_type if event_type in _KERNEL_KINDS else "agent_direct"
    try:
        import sys
        root = _repo_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from fleet_kernel.company_message_plane.client import CompanyMessagePlaneClient

        client = CompanyMessagePlaneClient()
        result = client.send(
            sender_role=sender_role,
            recipient_role=target_role,
            message_kind=kind,
            payload=content,
            idempotency_key=f"channel:{send_id}",
            require_live_admission=False,
            provenance="native",
        )
        return {
            "status": result.status,
            "message_id": str(result.message_id),
            "sequence": result.recipient_sequence,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }


def claim_kernel_events(role: str, limit: int = 10) -> list[dict[str, Any]]:
    """Claim up to `limit` kernel messages for role; return channel-shaped events.

    Claims leave messages leased; consume is deferred until reply/ACK if we
    ever wire that. For v1 finish: auto-consume after materializing into the
    pending list so the agent sees the content once (same as draining a queue
    line). Idempotent: already-consumed is fine.
    """
    if role not in _CC_ROLES or get_cutover_state(role) not in _KERNEL_ACTIVE:
        return []
    events: list[dict[str, Any]] = []
    try:
        import sys
        root = _repo_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from fleet_kernel.company_message_plane.client import CompanyMessagePlaneClient

        client = CompanyMessagePlaneClient()
        for _ in range(max(1, min(limit, 20))):
            claimed = client.claim(
                recipient_role=role,
                require_live_admission=False,
                lease_seconds=300,
            )
            if claimed.status != "leased" or not claimed.message_id or not claimed.lease_id:
                break
            payload = claimed.payload or ""
            mid = str(claimed.message_id)
            # Auto-consume so the message is not re-leased forever.
            try:
                client.consume(
                    message_id=claimed.message_id,
                    recipient_role=role,
                    lease_id=claimed.lease_id,
                    payload=payload,
                    require_live_admission=False,
                )
            except Exception:
                pass
            send_id = f"kernel-{mid[:12]}"
            events.append({
                "event_id": send_id,
                "event_type": claimed.message_kind or "agent_direct",
                "content": payload,
                "meta": {
                    "sender_role": claimed.sender_role or "unknown",
                    "target_role": role,
                    "send_id": send_id,
                    "event_id": send_id,
                    "source_host": "kernel-message-plane-v1",
                    "kernel_message_id": mid,
                    "recipient_sequence": claimed.recipient_sequence,
                    "provenance": "kernel",
                },
            })
    except Exception:
        return events
    return events


def merge_pending(
    file_events: list[dict[str, Any]],
    role: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Merge file-queue events with freshly claimed kernel events."""
    kernel_events = claim_kernel_events(role, limit=limit)
    # Kernel first (newer plane), then file; cap total
    merged = kernel_events + list(file_events)
    # Dedupe by event_id / send_id
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for ev in merged:
        eid = str((ev.get("meta") or {}).get("send_id") or ev.get("event_id") or uuid.uuid4())
        if eid in seen:
            continue
        seen.add(eid)
        out.append(ev)
        if len(out) >= limit:
            break
    return out
