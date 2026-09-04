"""Python client for kernel company message plane v1 RPCs."""
from __future__ import annotations
from fleet_kernel.configuration import company_id, scribe_deployment_id, authority_issuer, deployment_binding

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from fleet_kernel.company_cutover_mirror import (
    CutoverMirrorError, locked_cutover_mirror,
)

DEFAULT_CONNINFO_FILE = None
COMPANY_ID = company_id()
ROLES = frozenset({
    "scribe", "cos", "ccto", "ccpo", "ccde", "ccro", "ccfo", "ccmo",
    "cclo", "ccgo", "ccco", "utility",
})
MESSAGE_KINDS = frozenset({
    "agent_direct", "task_handoff", "audit_request",
    "reply_classification", "arbiter_ask",
})


class MessagePlaneError(RuntimeError):
    pass


@dataclass(frozen=True)
class SendResult:
    message_id: UUID
    recipient_sequence: int
    message_sha256: str
    accepted_at: Any
    status: str


@dataclass(frozen=True)
class ClaimResult:
    status: str
    message_id: Optional[UUID] = None
    sender_role: Optional[str] = None
    message_kind: Optional[str] = None
    payload: Optional[str] = None
    message_sha256: Optional[str] = None
    recipient_sequence: Optional[int] = None
    lease_id: Optional[UUID] = None
    lease_expires_at: Any = None


@dataclass(frozen=True)
class ConsumeResult:
    message_id: UUID
    consumed_at: Any
    status: str


@dataclass(frozen=True)
class CutoverState:
    role_type: str
    state: str
    notes: str


def _conninfo() -> str:
    env = os.environ.get("FLEET_KERNEL_DDL_DATABASE_URL")
    if env:
        return env.strip()
    configured = os.environ.get("FLEET_KERNEL_DDL_DATABASE_URL_FILE")
    if not configured:
        raise MessagePlaneError("An explicit database URL or owner-only URL file is required")
    path = Path(configured)
    if not path.is_file():
        raise MessagePlaneError(f"conninfo missing: {path}")
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise MessagePlaneError("conninfo must be owner-only (mode 600)")
    return path.read_text(encoding="utf-8").strip()


class CompanyMessagePlaneClient:
    """Thin wrapper around SECURITY DEFINER RPCs."""

    def __init__(self, conninfo: str | None = None) -> None:
        self._conninfo = conninfo or _conninfo()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self._conninfo,
            connect_timeout=15,
            application_name="mise-company-message-plane-v1",
            row_factory=dict_row,
        )

    def send(
        self,
        *,
        sender_role: str,
        recipient_role: str,
        message_kind: str,
        payload: str,
        idempotency_key: str,
        sender_admission_id: UUID | None = None,
        require_live_admission: bool = False,
        provenance: str = "native",
    ) -> SendResult:
        if sender_role not in ROLES or recipient_role not in ROLES:
            raise MessagePlaneError("invalid role")
        if message_kind not in MESSAGE_KINDS:
            raise MessagePlaneError("invalid message_kind")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM kernel_send_company_message_v1(
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    sender_role, recipient_role, message_kind, payload,
                    idempotency_key, sender_admission_id, require_live_admission,
                    provenance,
                ),
            ).fetchone()
            conn.commit()
        if row is None:
            raise MessagePlaneError("send returned no row")
        return SendResult(
            message_id=row["message_id"],
            recipient_sequence=int(row["recipient_sequence"]),
            message_sha256=row["message_sha256"],
            accepted_at=row["accepted_at"],
            status=row["status"],
        )

    def claim(
        self,
        *,
        recipient_role: str,
        consuming_admission_id: UUID | None = None,
        require_live_admission: bool = False,
        lease_seconds: int = 120,
    ) -> ClaimResult:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM kernel_claim_company_message_v1(%s, %s, %s, %s)
                """,
                (
                    recipient_role, consuming_admission_id,
                    require_live_admission, lease_seconds,
                ),
            ).fetchone()
            conn.commit()
        if row is None:
            return ClaimResult(status="empty")
        if row["status"] == "empty":
            return ClaimResult(status="empty")
        return ClaimResult(
            status=row["status"],
            message_id=row["message_id"],
            sender_role=row["sender_role"],
            message_kind=row["message_kind"],
            payload=row["payload"],
            message_sha256=row["message_sha256"],
            recipient_sequence=int(row["recipient_sequence"]) if row["recipient_sequence"] is not None else None,
            lease_id=row["lease_id"],
            lease_expires_at=row["lease_expires_at"],
        )

    def consume(
        self,
        *,
        message_id: UUID,
        recipient_role: str,
        lease_id: UUID,
        payload: str,
        consuming_admission_id: UUID | None = None,
        require_live_admission: bool = False,
    ) -> ConsumeResult:
        proof = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM kernel_consume_company_message_v1(
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    message_id, recipient_role, lease_id, proof,
                    consuming_admission_id, require_live_admission,
                ),
            ).fetchone()
            conn.commit()
        if row is None:
            raise MessagePlaneError("consume returned no row")
        return ConsumeResult(
            message_id=row["message_id"],
            consumed_at=row["consumed_at"],
            status=row["status"],
        )

    def receipt(self, message_id: UUID) -> Mapping[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM kernel_company_message_receipt_v1(%s)",
                (message_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_cutover(self, role_type: str) -> CutoverState:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT role_type, state, notes
                  FROM kernel_company_message_cutover_registry
                 WHERE company_id = %s AND role_type = %s
                """,
                (COMPANY_ID, role_type),
            ).fetchone()
        if row is None:
            return CutoverState(role_type=role_type, state="file_bus_only", notes="missing")
        return CutoverState(
            role_type=row["role_type"], state=row["state"], notes=row["notes"] or "",
        )

    def set_cutover(self, role_type: str, state: str, notes: str = "") -> CutoverState:
        allowed = {"file_bus_only", "dual_write", "kernel_primary", "rolled_back"}
        if state not in allowed:
            raise MessagePlaneError(f"invalid cutover state {state}")
        if role_type not in ROLES:
            raise MessagePlaneError("invalid role")
        # Lock before the DB change so a delayed reader/setter cannot overwrite
        # the projection of a later rollback. Configuration failures precede writes.
        try:
            with locked_cutover_mirror() as write_mirror:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO kernel_company_message_cutover_registry
                            (company_id, role_type, state, updated_at, updated_by, notes)
                        VALUES (%s, %s, %s, clock_timestamp(), 'mission-control', %s)
                        ON CONFLICT (company_id, role_type) DO UPDATE
                            SET state = EXCLUDED.state,
                                updated_at = clock_timestamp(),
                                updated_by = 'mission-control',
                                notes = EXCLUDED.notes
                        """,
                        (COMPANY_ID, role_type, state, notes),
                    )
                    conn.commit()
                # The DB is canonical; report any postcommit projection failure.
                try:
                    write_mirror(role_type, state)
                except (OSError, CutoverMirrorError) as exc:
                    raise MessagePlaneError(
                        "Cutover committed in database but local mirror update failed; "
                        "retry set_cutover before relying on relay fallback"
                    ) from exc
        except CutoverMirrorError as exc:
            raise MessagePlaneError(str(exc)) from exc
        return CutoverState(role_type=role_type, state=state, notes=notes)

    def handoff_roundtrip(
        self,
        *,
        sender_role: str,
        recipient_role: str,
        content: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """End-to-end task_handoff: send → claim → consume."""
        sent = self.send(
            sender_role=sender_role,
            recipient_role=recipient_role,
            message_kind="task_handoff",
            payload=content,
            idempotency_key=idempotency_key,
            require_live_admission=False,
        )
        claimed = self.claim(recipient_role=recipient_role, require_live_admission=False)
        if claimed.status != "leased" or claimed.message_id != sent.message_id:
            raise MessagePlaneError(
                f"claim failed: {claimed.status} mid={claimed.message_id} expected={sent.message_id}"
            )
        assert claimed.payload is not None and claimed.lease_id is not None
        consumed = self.consume(
            message_id=claimed.message_id,
            recipient_role=recipient_role,
            lease_id=claimed.lease_id,
            payload=claimed.payload,
            require_live_admission=False,
        )
        return {
            "send": sent,
            "claim": claimed,
            "consume": consumed,
            "ok": consumed.status in {"consumed", "already_consumed"},
        }
