#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""CLI for bun channel dual-write (best-effort; exit 0 always)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mcp_servers.channel_relay.kernel_plane_bridge import dual_write_send  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sender", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--event-type", default="agent_direct")
    ap.add_argument("--send-id", required=True)
    ap.add_argument("--content-file", help="path to payload file; else stdin")
    args = ap.parse_args()
    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8")
    else:
        content = sys.stdin.read()
    result = dual_write_send(
        sender_role=args.sender,
        target_role=args.target,
        event_type=args.event_type,
        content=content,
        send_id=args.send_id,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
