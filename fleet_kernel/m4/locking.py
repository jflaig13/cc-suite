# SPDX-License-Identifier: MPL-2.0
"""Shared-reader/exclusive-writer advisory locking for M4 handoff state."""
from __future__ import annotations

from contextlib import contextmanager

import psycopg

from fleet_kernel.m4.handoff_migration import HandoffMigrationError, advisory_lock_identity


@contextmanager
def handoff_advisory_lock(conninfo: dict, *, tenant_id: str, shared: bool):
    """Acquire a nonblocking DB-wide lock without masking primary failures."""
    identity = advisory_lock_identity(tenant_id)
    acquire = "pg_try_advisory_lock_shared" if shared else "pg_try_advisory_lock"
    release = "pg_advisory_unlock_shared" if shared else "pg_advisory_unlock"
    kind = "read" if shared else "sync"
    with psycopg.connect(**conninfo, autocommit=True) as lock_conn:
        acquired = lock_conn.execute(
            f"SELECT {acquire}(hashtextextended(%s, 0))", (identity,),
        ).fetchone()[0]
        if not acquired:
            raise HandoffMigrationError(f"M4 handoff {kind} lock is busy")
        primary_error: BaseException | None = None
        try:
            yield
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                unlocked = lock_conn.execute(
                    f"SELECT {release}(hashtextextended(%s, 0))", (identity,),
                ).fetchone()[0]
                if not unlocked:
                    raise HandoffMigrationError(f"M4 handoff {kind} lock ownership was lost")
            except Exception as unlock_error:
                if primary_error is not None:
                    primary_error.add_note(
                        f"Additionally, M4 handoff {kind} unlock failed: {unlock_error}"
                    )
                else:
                    raise
