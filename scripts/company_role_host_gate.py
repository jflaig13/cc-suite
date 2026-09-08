#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Source-pinned Company host/model gate; adopter authorization is required separately.

The reusable host policy admits Claude seats and Codex review and refuses the
retired Grok seat host. Role models come from role_model_map.sh. Refusal records
use only explicitly configured Company state. This file is not a deployment mandate.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fleet_kernel.identity import COMPANY_ROLE_TYPES  # noqa: E402

HOST_REFUSAL_SCHEMA = "mise.company-role-host-refusal.v1"
OFFICIAL_HOST = "claude"
OFFICIAL_FAMILY = "fable"
REVIEWER_HOST = "codex"
ROLE_MODEL_MAP = REPO_ROOT / "scripts" / "role_model_map.sh"


def official_model(role: str) -> str:
    """The seat's model per the single source (role_model_map.sh)."""
    result = subprocess.run(
        [
            "bash", "-c",
            'source "$1" && get_model_for_role "$2"', '--', str(ROLE_MODEL_MAP), role,
        ],
        capture_output=True, text=True, check=False,
    )
    model = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if result.returncode != 0 or not model:
        raise RuntimeError(
            f"role_model_map.sh resolved no model for {role}: {result.stderr.strip()}"
        )
    return model


def _record_refusal(role: str, host: str, model: str) -> None:
    state_dir = Path(os.environ["CC_SUITE_STATE_ROOT"]) / f"company-{role}" / "state"
    row = {
        "schema": HOST_REFUSAL_SCHEMA,
        "refused_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "role_type": role,
        "official_model": model,
        "official_family": OFFICIAL_FAMILY,
        "attempted_host": host,
        "surface": os.environ.get(
            "MISE_HOST_GATE_SURFACE", "company_role_host_gate",
        ),
        "pid": os.getpid(),
    }
    try:
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (state_dir / "host-refusals.jsonl").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument(
        "--host", required=True, choices=("claude", "grok", "codex"),
    )
    args = parser.parse_args()
    role = args.role.strip()
    if role not in COMPANY_ROLE_TYPES:
        print(f"CLEAR: '{role}' is outside the Company host gate scope")
        return 0
    if args.host == REVIEWER_HOST:
        print(f"CLEAR: codex host is admitted for every Company seat as the CMCV reviewer ({role})")
        return 0
    try:
        model = official_model(role)
    except RuntimeError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 78
    if args.host == OFFICIAL_HOST:
        print(f"CLEAR: {role} is officially {OFFICIAL_FAMILY} ({model}); {args.host} host admitted")
        return 0
    _record_refusal(role, args.host, model)
    print(
        f"REFUSED: Company role '{role}' is officially {OFFICIAL_FAMILY} "
        f"({model}); the {args.host} host is retired for seats and is refused. "
        f"This seat clocks in through scripts/launch_claude.sh {role}",
        file=sys.stderr,
    )
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
