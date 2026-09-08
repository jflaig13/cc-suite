#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Run one strictly shadow-only M5 legacy-surface synchronization cycle."""
import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

KERNEL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KERNEL_ROOT))

from fleet_kernel.m5.dispatch_approval_migration import DispatchApprovalMigrationError  # noqa: E402
from fleet_kernel.m5.supervision import (  # noqa: E402
    LegacySurfacePaths,
    sync_authoritative_files,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dispatch-intents", type=Path, required=True)
    parser.add_argument("--fodl-audit", type=Path, required=True)
    parser.add_argument("--fodl-current", type=Path, required=True)
    parser.add_argument("--cycle-key", default="")
    parser.add_argument("--validate-config-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        database_url = os.environ.get("FLEET_KERNEL_DATABASE_URL", "").strip()
        tenant_id = os.environ.get("FLEET_KERNEL_TENANT_ID", "").strip()
        if not database_url or not tenant_id:
            raise DispatchApprovalMigrationError("database URL and tenant ID are required")
        if os.environ.get("MISE_TENANT", "").strip():
            raise DispatchApprovalMigrationError("M5 shadow is tenant-zero only before Phase 7")
        conninfo = conninfo_to_dict(database_url)
        if args.validate_config_only:
            print(json.dumps({"mode": "config-valid", "tenant_id": tenant_id}, sort_keys=True))
            return 0
        result = sync_authoritative_files(
            conninfo, tenant_id=tenant_id,
            paths=LegacySurfacePaths(
                args.dispatch_intents, args.fodl_audit, args.fodl_current,
            ),
            cycle_key=args.cycle_key,
        )
        print(json.dumps({
            "cycle_key": result.cycle_key,
            "dispatch_rows": result.dispatch_rows,
            "fodl_audit_rows": result.fodl_audit_rows,
            "changed_rows": result.changed_rows,
            "verdict": result.verdict,
            "dispatch_sha256": result.dispatch_sha256,
            "dispatch_render_sha256": result.dispatch_render_sha256,
            "fodl_audit_sha256": result.fodl_audit_sha256,
            "fodl_audit_render_sha256": result.fodl_audit_render_sha256,
            "fodl_current_sha256": result.fodl_current_sha256,
        }, sort_keys=True))
        return 0
    except (DispatchApprovalMigrationError, OSError, ValueError, psycopg.Error) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
