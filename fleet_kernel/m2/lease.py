"""The kernel lease module — heartbeat leases + monotonic DB-issued fencing
tokens (D4's decision: built as a first-class kernel component ON the same
Postgres DBOS uses, not hand-rolled outside it).

Contention is resolved by ONE atomic UPSERT per acquire/steal attempt —
Postgres's own row-level locking on the (tenant_id, resource_key) unique
constraint makes the race safe with no application-level mutex. A lease
can only be stolen once its expires_at has passed (the WHERE clause on the
UPSERT's DO UPDATE); a live, unexpired lease refuses every other claimant.

Fencing: every acquire/steal issues a token from ONE shared sequence
(kernel_fencing_seq) — globally monotonic, so any two tokens from any two
resources are comparable. `fenced_write` is the write-side half: a write
to a fenced resource is accepted only if its token is STRICTLY greater
than the resource's own last-recorded token — this is what refuses a
paused writer that resumes after losing its lease (D4's Codex-round
addition; ownership-at-acquisition alone is not enough).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class LeaseHandle:
    tenant_id: str
    resource_key: str
    owner_token: str
    fencing_token: int


class LeaseNotAcquired(Exception):
    """Raised when acquire() finds the resource held by another live owner."""


class LeaseLost(Exception):
    """Raised when renew()/release() finds the caller no longer holds the
    lease it thinks it holds (someone else already stole it)."""


def acquire(conn, tenant_id: str, resource_key: str, owner_token: str, ttl_seconds: float) -> LeaseHandle:
    row = conn.execute(
        """
        INSERT INTO kernel_leases
            (id, tenant_id, resource_key, owner_token, fencing_token, acquired_at, expires_at)
        VALUES
            (%(id)s, %(tenant_id)s, %(resource_key)s, %(owner_token)s,
             nextval('kernel_fencing_seq'), now(), now() + make_interval(secs => %(ttl)s))
        ON CONFLICT (tenant_id, resource_key) DO UPDATE
            SET owner_token   = EXCLUDED.owner_token,
                fencing_token = nextval('kernel_fencing_seq'),
                acquired_at   = now(),
                expires_at    = now() + make_interval(secs => %(ttl)s)
            WHERE kernel_leases.expires_at < now()
        RETURNING fencing_token
        """,
        {
            "id": uuid.uuid4(), "tenant_id": tenant_id, "resource_key": resource_key,
            "owner_token": owner_token, "ttl": ttl_seconds,
        },
    ).fetchone()
    if row is None:
        raise LeaseNotAcquired(f"{resource_key!r} (tenant {tenant_id!r}) is held by another live owner")
    return LeaseHandle(tenant_id, resource_key, owner_token, row["fencing_token"])


def renew(conn, handle: LeaseHandle, ttl_seconds: float) -> LeaseHandle:
    """Extend the SAME lease (same fencing token — this is not a transfer,
    just proof-of-life). Fails if the lease was stolen out from under the
    caller (owner_token or fencing_token no longer match) OR if it already
    expired — even if nobody has stolen it YET, a lapsed lease is lost the
    moment its TTL passes ("use it or lose it"; a caller can't retroactively
    extend a lease it already failed to renew in time, since a partition
    that outlasts the TTL means another owner may take over at any moment)."""
    row = conn.execute(
        """
        UPDATE kernel_leases SET expires_at = now() + make_interval(secs => %(ttl)s)
        WHERE tenant_id = %(tenant_id)s AND resource_key = %(resource_key)s
          AND owner_token = %(owner_token)s AND fencing_token = %(fencing_token)s
          AND expires_at > now()
        RETURNING fencing_token
        """,
        {
            "tenant_id": handle.tenant_id, "resource_key": handle.resource_key,
            "owner_token": handle.owner_token, "fencing_token": handle.fencing_token,
            "ttl": ttl_seconds,
        },
    ).fetchone()
    if row is None:
        raise LeaseLost(f"{handle.resource_key!r} no longer held by {handle.owner_token!r}")
    return handle


def release(conn, handle: LeaseHandle) -> None:
    conn.execute(
        """
        DELETE FROM kernel_leases
        WHERE tenant_id = %(tenant_id)s AND resource_key = %(resource_key)s
          AND owner_token = %(owner_token)s AND fencing_token = %(fencing_token)s
        """,
        {
            "tenant_id": handle.tenant_id, "resource_key": handle.resource_key,
            "owner_token": handle.owner_token, "fencing_token": handle.fencing_token,
        },
    )


def current_holder(conn, tenant_id: str, resource_key: str) -> Optional[dict]:
    return conn.execute(
        "SELECT owner_token, fencing_token, expires_at FROM kernel_leases "
        "WHERE tenant_id = %(t)s AND resource_key = %(r)s",
        {"t": tenant_id, "r": resource_key},
    ).fetchone()


def fenced_write(
    conn, table: str, id_col: str, id_val: Any, token: int, set_sql: str, params: dict
) -> bool:
    """Apply a write to a fenced resource ONLY if `token` is strictly
    greater than the resource's own last-recorded fencing token. Returns
    True if applied, False if refused (a stale/paused writer's token).

    `set_sql` is a caller-supplied SQL fragment (e.g. "balance_cents =
    %(new_balance)s") — never string-interpolated user input; callers in
    this codebase pass fixed literals, never external strings.
    """
    row = conn.execute(
        f"UPDATE {table} SET {set_sql}, last_fencing_token = %(token)s "
        f"WHERE {id_col} = %(id_val)s AND last_fencing_token < %(token)s",
        {**params, "id_val": id_val, "token": token},
    )
    return row.rowcount > 0
