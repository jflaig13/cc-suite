#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Refuse legacy role launch while file-backed Kernel lineage can act/recover."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


ROLE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


def _read_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"kernel lineage artifact is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"kernel lineage artifact is not an object: {path}")
    return value


def _lineage_reasons(role: str, state_dir: Path) -> list[str]:
    reasons: list[str] = []
    active = state_dir / "ACTIVE.json"
    if active.exists():
        value = _read_object(active)
        if value.get("role_type") != role:
            raise ValueError("ACTIVE.json role does not match the requested role")
        reasons.append("ACTIVE.json")

    request = state_dir / "restart-request.json"
    if request.exists():
        value = _read_object(request)
        if (
            value.get("role_type") != role
            or value.get("state") != "awaiting-external-launch"
        ):
            raise ValueError("restart-request.json is not an exact recoverable role request")
        reasons.append("restart-request.json")

    for intent in sorted(state_dir.glob("restart-intent-epoch-*.json")):
        value = _read_object(intent)
        if value.get("role_type") != role:
            raise ValueError(f"restart intent role mismatch: {intent}")
        reasons.append(intent.name)
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--state-dir", type=Path)
    args = parser.parse_args()
    if not ROLE.fullmatch(args.role) or args.role == "scribe":
        print("REFUSED: probe requires one non-Scribe Company role", file=sys.stderr)
        return 78
    state_dir = args.state_dir or (
        Path(os.environ["CC_SUITE_STATE_ROOT"]) / f"company-{args.role}" / "state"
    )
    try:
        reasons = _lineage_reasons(args.role, state_dir) if state_dir.exists() else []
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 78
    if reasons:
        print(
            "REFUSED: legacy role launch is fenced by an existing/eligible "
            f"Company Kernel {args.role} lineage ({','.join(reasons)}); the "
            "verified role-scoped rollback receipt is the only legacy re-entry gate",
            file=sys.stderr,
        )
        return 78
    print(f"CLEAR: no file-backed Company Kernel lineage for role {args.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
