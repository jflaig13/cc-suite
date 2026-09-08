# SPDX-License-Identifier: MPL-2.0
"""Internal daily driver for the Mise Company Fleet.

The Company Workspace is a separate operating surface from every restaurant
fleet and from the customer-facing web application.  Its database identity is
not a tenant identity, its objective history is append-only, and completion is
database-gated on an exact-subject CLEAN review by the active CCPO Fable worker.
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Iterator, Mapping

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from fleet_kernel.company_scribe_admission import (
    CompanyScribeAdmissionError,
    ScribeAdmissionBinding,
    bind_transaction as bind_scribe_transaction,
)
from fleet_kernel.identity import COMPANY_ROLE_TYPES, COMPANY_TENANT_ID, FABLE_REVIEW_MODELS, company_role_spec
from fleet_kernel.m8.db_isolation import (
    assert_company_review_attestor_principal,
    assert_company_review_mac_signer_principal,
    assert_company_reviewer_principal,
    assert_company_serving_principal,
    assert_company_worker_principal,
    company_principal_for,
    company_review_attestor_principal_for,
    company_review_mac_signer_principal_for,
    company_reviewer_principal_for,
    company_worker_principal_for,
)


class CompanyWorkspaceError(RuntimeError):
    pass


class CompanyWorkspaceConflict(CompanyWorkspaceError):
    pass


CALLER_EVENT_KINDS = frozenset({
    "schedule", "handoff", "status", "decision", "performance",
})
EVENT_KINDS = CALLER_EVENT_KINDS | {"receipt", "legacy_import"}
ACTOR_ROLES = frozenset({"founder", *COMPANY_ROLE_TYPES})
STATES = frozenset({
    "queued", "active", "needs_input", "ready_for_review", "held",
    "completed", "cancelled",
})


@dataclass(frozen=True, slots=True)
class CompanyObjective:
    id: uuid.UUID
    company_id: str
    title: str
    description: str
    owner_role: str
    priority: int
    state: str
    work_subject_sha256: str | None
    due_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["id"] = str(self.id)
        for key in ("due_at", "created_at", "updated_at", "completed_at"):
            item = value[key]
            value[key] = None if item is None else item.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class CompanyReviewReceipt:
    id: uuid.UUID
    objective_id: uuid.UUID
    subject_sha256: str
    verdict: str
    result_uuid: uuid.UUID
    session_id: uuid.UUID
    issued_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id), "objective_id": str(self.objective_id),
            "subject_sha256": self.subject_sha256, "verdict": self.verdict,
            "result_uuid": str(self.result_uuid), "session_id": str(self.session_id),
            "issued_at": self.issued_at.isoformat(),
        }


def _objective(row: Mapping[str, Any]) -> CompanyObjective:
    return CompanyObjective(**{field: row[field] for field in CompanyObjective.__dataclass_fields__})


def review_signature_payload(
    *, objective_id: uuid.UUID, reviewer_worker_id: uuid.UUID,
    subject_sha256: str, verdict: str, effort: str, result_uuid: uuid.UUID,
    session_id: uuid.UUID, review_output_sha256: str, model: str | None = None,
) -> bytes:
    """Canonical exact-subject payload signed by the registered CCPO worker."""
    model = model or company_role_spec("ccpo").default_model
    if model not in FABLE_REVIEW_MODELS:
        raise CompanyWorkspaceError("unsupported formal review model")
    return json.dumps(
        {
            "effort": effort,
            "model": model,
            "objective_id": str(objective_id),
            "result_uuid": str(result_uuid),
            "review_output_sha256": review_output_sha256,
            "reviewer_role": "ccpo",
            "reviewer_worker_id": str(reviewer_worker_id),
            "session_id": str(session_id),
            "subject_sha256": subject_sha256,
            "verdict": verdict,
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


class CompanyWorkspace:
    """No-pool service: each operation uses the company-only DB principal."""

    def __init__(
        self, conninfo: Mapping[str, Any],
        credential_lookup: Callable[[], Mapping[str, str]] | None = None,
        reviewer_credential_lookup: Callable[[], Mapping[str, str]] | None = None,
        reviewer_conninfo: Mapping[str, Any] | None = None,
        attestor_credential_lookup: Callable[[], Mapping[str, str]] | None = None,
        attestor_conninfo: Mapping[str, Any] | None = None,
        mac_signer_credential_lookup: Callable[[], Mapping[str, str]] | None = None,
        mac_signer_conninfo: Mapping[str, Any] | None = None,
        worker_credential_lookup: Callable[[uuid.UUID], Mapping[str, str]] | None = None,
        worker_conninfo: Mapping[str, Any] | None = None,
        scribe_admission_lookup: Callable[
            [uuid.UUID], ScribeAdmissionBinding | Mapping[str, Any]
        ] | None = None,
    ) -> None:
        self._conninfo = dict(conninfo)
        self._reviewer_conninfo = dict(reviewer_conninfo or conninfo)
        self._attestor_conninfo = dict(attestor_conninfo or conninfo)
        self._mac_signer_conninfo = dict(mac_signer_conninfo or conninfo)
        self._worker_conninfo = dict(worker_conninfo or conninfo)
        self._credential_lookup = credential_lookup or (lambda: {})
        self._reviewer_credential_lookup = reviewer_credential_lookup or (lambda: {})
        self._attestor_credential_lookup = attestor_credential_lookup or (lambda: {})
        self._mac_signer_credential_lookup = mac_signer_credential_lookup or (lambda: {})
        self._worker_credential_lookup = worker_credential_lookup or (lambda _: {})
        self._scribe_admission_lookup = scribe_admission_lookup

    @staticmethod
    def _credentials(
        lookup: Callable[[], Mapping[str, str]], *, route: str,
    ) -> dict[str, str]:
        credentials = dict(lookup())
        forbidden = set(credentials) - {"password", "sslcert", "sslkey", "sslrootcert"}
        if forbidden:
            raise CompanyWorkspaceError(
                f"credential lookup attempted to override {route} routing: {sorted(forbidden)}"
            )
        return credentials

    @staticmethod
    def _has_authentication_material(route: Mapping[str, Any]) -> bool:
        return bool(route.get("password")) or bool(
            route.get("sslcert") and route.get("sslkey")
        )

    @contextmanager
    def _checkout(self) -> Iterator[psycopg.Connection]:
        credentials = self._credentials(self._credential_lookup, route="company")
        if not self._has_authentication_material({**self._conninfo, **credentials}):
            raise CompanyWorkspaceError(
                "Company Workspace requires explicit company credentials"
            )
        conn = psycopg.connect(
            **{
                **self._conninfo, **credentials,
                "user": company_principal_for().db_role,
            },
            row_factory=dict_row,
            autocommit=True,
        )
        try:
            assert_company_serving_principal(conn)
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _review_checkout(self) -> Iterator[psycopg.Connection]:
        credentials = self._credentials(
            self._reviewer_credential_lookup, route="company reviewer",
        )
        if not self._has_authentication_material({**self._reviewer_conninfo, **credentials}):
            raise CompanyWorkspaceError(
                "formal review requires explicit reviewer credentials"
            )
        conn = psycopg.connect(
            **{
                **self._reviewer_conninfo, **credentials,
                "user": company_reviewer_principal_for().db_role,
            },
            row_factory=dict_row,
            autocommit=True,
        )
        try:
            assert_company_reviewer_principal(conn)
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _attestor_checkout(self) -> Iterator[psycopg.Connection]:
        credentials = self._credentials(
            self._attestor_credential_lookup, route="company review attestor",
        )
        if not self._has_authentication_material({**self._attestor_conninfo, **credentials}):
            raise CompanyWorkspaceError(
                "formal review persistence requires explicit attestor credentials"
            )
        conn = psycopg.connect(
            **{
                **self._attestor_conninfo, **credentials,
                "user": company_review_attestor_principal_for().db_role,
            },
            row_factory=dict_row,
            autocommit=True,
        )
        try:
            assert_company_review_attestor_principal(conn)
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _mac_signer_checkout(self) -> Iterator[psycopg.Connection]:
        credentials = self._credentials(
            self._mac_signer_credential_lookup, route="company review MAC signer",
        )
        if not self._has_authentication_material(
            {**self._mac_signer_conninfo, **credentials}
        ):
            raise CompanyWorkspaceError(
                "formal review requires explicit MAC signer credentials"
            )
        conn = psycopg.connect(
            **{
                **self._mac_signer_conninfo, **credentials,
                "user": company_review_mac_signer_principal_for().db_role,
            },
            row_factory=dict_row,
            autocommit=True,
        )
        try:
            assert_company_review_mac_signer_principal(conn)
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _actor_checkout(
        self, *, actor_role: str, actor_worker_id: uuid.UUID | None,
    ) -> Iterator[psycopg.Connection]:
        if actor_role == "founder":
            if actor_worker_id is not None:
                raise CompanyWorkspaceError("founder events cannot claim a worker")
            with self._checkout() as conn:
                yield conn
            return
        if actor_role not in COMPANY_ROLE_TYPES or actor_worker_id is None:
            raise CompanyWorkspaceError(
                "Company Fleet agent actions require their registered worker identity"
            )
        if actor_role == "ccpo":
            raise CompanyWorkspaceError(
                "CCPO authority is limited to the dedicated formal-review path"
            )
        credentials = self._credentials(
            lambda: self._worker_credential_lookup(actor_worker_id),
            route="company worker",
        )
        if not self._has_authentication_material({**self._worker_conninfo, **credentials}):
            raise CompanyWorkspaceError(
                "Company agent action requires explicit worker credentials"
            )
        principal = company_worker_principal_for(actor_worker_id, actor_role)
        try:
            conn = psycopg.connect(
                **{
                    **self._worker_conninfo, **credentials, "user": principal.db_role,
                },
                row_factory=dict_row,
                autocommit=True,
            )
        except psycopg.OperationalError as exc:
            raise CompanyWorkspaceError(
                "registered Company worker principal is unavailable"
            ) from exc
        try:
            assert_company_worker_principal(
                conn, worker_id=actor_worker_id, role_type=actor_role,
            )
            yield conn
        finally:
            conn.close()

    def _bind_actor_transaction(
        self,
        conn: psycopg.Connection,
        *,
        actor_role: str,
        actor_worker_id: uuid.UUID | None,
    ) -> None:
        if actor_role != "scribe":
            return
        if actor_worker_id is None or self._scribe_admission_lookup is None:
            raise CompanyWorkspaceError(
                "Scribe write requires an exact live admission"
            )
        try:
            candidate = self._scribe_admission_lookup(actor_worker_id)
            binding = (
                candidate
                if isinstance(candidate, ScribeAdmissionBinding)
                else ScribeAdmissionBinding.from_mapping(candidate)
            )
            if binding.worker_id != actor_worker_id:
                raise CompanyScribeAdmissionError(
                    "Scribe admission belongs to another worker"
                )
            bind_scribe_transaction(conn, binding)
        except (CompanyScribeAdmissionError, psycopg.Error) as exc:
            raise CompanyWorkspaceError(
                "Scribe write admission is missing, stale, or invalid"
            ) from exc

    @staticmethod
    def _append_event(
        conn: psycopg.Connection, *, objective_id: uuid.UUID,
        event_kind: str, actor_role: str, payload: Mapping[str, Any],
        actor_worker_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        if event_kind not in EVENT_KINDS:
            raise CompanyWorkspaceError("unsupported company event kind")
        if actor_role not in ACTOR_ROLES:
            raise CompanyWorkspaceError("unsupported Company Fleet actor")
        if (actor_role == "founder") != (actor_worker_id is None):
            raise CompanyWorkspaceError(
                "Company Fleet agent events require their registered worker identity"
            )
        row = conn.execute(
            "INSERT INTO kernel_company_objective_events "
            "(id,company_id,objective_id,event_seq,event_kind,actor_role,actor_worker_id,payload) "
            "VALUES (%s,%s,%s,1,%s,%s,%s,%s) "
            "RETURNING id,event_seq,event_kind,actor_role,actor_worker_id,payload,recorded_at",
            (
                uuid.uuid4(), COMPANY_TENANT_ID, objective_id, event_kind,
                actor_role, actor_worker_id, Jsonb(dict(payload)),
            ),
        ).fetchone()
        return dict(row)

    def create_objective(
        self, *, title: str, owner_role: str, description: str = "",
        priority: int = 50, due_at: datetime | None = None,
        actor_role: str = "founder", actor_worker_id: uuid.UUID | None = None,
    ) -> CompanyObjective:
        if owner_role not in COMPANY_ROLE_TYPES:
            raise CompanyWorkspaceError("objective owner must be a canonical Company Fleet role")
        if actor_role == "kernel":
            raise CompanyWorkspaceError(
                "Kernel objective writes require a dedicated Kernel principal"
            )
        objective_id = uuid.uuid4()
        with self._actor_checkout(
            actor_role=actor_role, actor_worker_id=actor_worker_id,
        ) as conn, conn.transaction():
            self._bind_actor_transaction(
                conn, actor_role=actor_role, actor_worker_id=actor_worker_id,
            )
            row = conn.execute(
                "INSERT INTO kernel_company_objectives "
                "(id,company_id,title,description,owner_role,priority,due_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (objective_id, COMPANY_TENANT_ID, title, description, owner_role, priority, due_at),
            ).fetchone()
        return _objective(row)

    def list_objectives(self, *, state: str | None = None) -> tuple[CompanyObjective, ...]:
        if state is not None and state not in STATES:
            raise CompanyWorkspaceError("unsupported objective state")
        with self._checkout() as conn:
            where = "company_id=%s" if state is None else "company_id=%s AND state=%s"
            params: tuple[object, ...] = (
                (COMPANY_TENANT_ID,) if state is None else (COMPANY_TENANT_ID, state)
            )
            rows = conn.execute(
                f"SELECT * FROM kernel_company_objectives WHERE {where} "
                "ORDER BY CASE WHEN state IN ('completed','cancelled') THEN 1 ELSE 0 END,"
                "priority DESC,updated_at DESC,id",
                params,
            ).fetchall()
        return tuple(_objective(row) for row in rows)

    def get_objective(self, objective_id: uuid.UUID) -> CompanyObjective:
        with self._checkout() as conn:
            row = conn.execute(
                "SELECT * FROM kernel_company_objectives WHERE company_id=%s AND id=%s",
                (COMPANY_TENANT_ID, objective_id),
            ).fetchone()
        if row is None:
            raise CompanyWorkspaceError("company objective not found")
        return _objective(row)

    def objective_detail(self, objective_id: uuid.UUID) -> dict[str, Any]:
        with self._checkout() as conn:
            row = conn.execute(
                "SELECT * FROM kernel_company_objectives WHERE company_id=%s AND id=%s",
                (COMPANY_TENANT_ID, objective_id),
            ).fetchone()
            if row is None:
                raise CompanyWorkspaceError("company objective not found")
            events = conn.execute(
                "SELECT id,event_seq,event_kind,actor_role,actor_worker_id,payload,recorded_at "
                "FROM kernel_company_objective_events WHERE company_id=%s AND objective_id=%s "
                "ORDER BY event_seq,id",
                (COMPANY_TENANT_ID, objective_id),
            ).fetchall()
            reviews = conn.execute(
                "SELECT id,subject_sha256,verdict,result_uuid,session_id,issued_at "
                "FROM kernel_company_review_receipts WHERE company_id=%s AND objective_id=%s "
                "ORDER BY issued_at,id",
                (COMPANY_TENANT_ID, objective_id),
            ).fetchall()
        return {
            "objective": _objective(row).to_dict(),
            "events": [dict(item) for item in events],
            "reviews": [dict(item) for item in reviews],
        }

    def transition(
        self, objective_id: uuid.UUID, *, to_state: str, expected_version: int,
        actor_role: str, actor_worker_id: uuid.UUID | None = None,
        work_subject_sha256: str | None = None,
    ) -> CompanyObjective:
        if to_state not in STATES:
            raise CompanyWorkspaceError("unsupported objective state")
        if actor_role not in ACTOR_ROLES:
            raise CompanyWorkspaceError("unsupported Company Fleet actor")
        if actor_role == "kernel":
            raise CompanyWorkspaceError(
                "Kernel objective writes require a dedicated Kernel principal"
            )
        with self._actor_checkout(
            actor_role=actor_role, actor_worker_id=actor_worker_id,
        ) as conn, conn.transaction():
            self._bind_actor_transaction(
                conn, actor_role=actor_role, actor_worker_id=actor_worker_id,
            )
            before = conn.execute(
                "SELECT state,version,work_subject_sha256 FROM kernel_company_objectives "
                "WHERE company_id=%s AND id=%s FOR UPDATE",
                (COMPANY_TENANT_ID, objective_id),
            ).fetchone()
            if before is None:
                raise CompanyWorkspaceError("company objective not found")
            if before["version"] != expected_version:
                raise CompanyWorkspaceConflict("company objective version changed")
            row = conn.execute(
                "UPDATE kernel_company_objectives SET state=%s,"
                "work_subject_sha256=CASE WHEN %s='ready_for_review' THEN %s "
                "ELSE work_subject_sha256 END WHERE company_id=%s AND id=%s AND version=%s "
                "RETURNING *",
                (to_state, to_state, work_subject_sha256, COMPANY_TENANT_ID, objective_id, expected_version),
            ).fetchone()
            if row is None:
                raise CompanyWorkspaceConflict("company objective version changed")
        return _objective(row)

    def record_event(
        self, objective_id: uuid.UUID, *, event_kind: str, actor_role: str,
        payload: Mapping[str, Any], actor_worker_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        if event_kind not in CALLER_EVENT_KINDS:
            raise CompanyWorkspaceError("unsupported company event kind")
        with self._actor_checkout(
            actor_role=actor_role, actor_worker_id=actor_worker_id,
        ) as conn, conn.transaction():
            self._bind_actor_transaction(
                conn, actor_role=actor_role, actor_worker_id=actor_worker_id,
            )
            return self._append_event(
                conn, objective_id=objective_id, event_kind=event_kind,
                actor_role=actor_role, actor_worker_id=actor_worker_id, payload=payload,
            )

    def import_legacy_receipt(
        self, objective_id: uuid.UUID, *, actor_role: str, source_ref: str,
        source_sha256: str, payload: Mapping[str, Any],
        actor_worker_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise CompanyWorkspaceError("legacy import requires a SHA-256 source binding")
        if not source_ref.strip():
            raise CompanyWorkspaceError("legacy import requires a source reference")
        with self._actor_checkout(
            actor_role=actor_role, actor_worker_id=actor_worker_id,
        ) as conn, conn.transaction():
            self._bind_actor_transaction(
                conn, actor_role=actor_role, actor_worker_id=actor_worker_id,
            )
            return self._append_event(
                conn, objective_id=objective_id, event_kind="legacy_import",
                actor_role=actor_role, actor_worker_id=actor_worker_id,
                payload={
                    **dict(payload), "source_ref": source_ref,
                    "source_sha256": source_sha256,
                },
            )

    def record_review(
        self, objective_id: uuid.UUID, *, reviewer_worker_id: uuid.UUID,
        subject_sha256: str, verdict: str, effort: str, result_uuid: uuid.UUID,
        session_id: uuid.UUID, review_output_sha256: str, signature: bytes,
        model: str | None = None,
    ) -> CompanyReviewReceipt:
        model = model or company_role_spec("ccpo").default_model
        if model not in FABLE_REVIEW_MODELS:
            raise CompanyWorkspaceError("unsupported formal review model")
        if verdict not in {"CLEAN", "CHANGES_REQUIRED"}:
            raise CompanyWorkspaceError("unsupported formal review verdict")
        if effort not in {"medium", "high"}:
            raise CompanyWorkspaceError("unsupported Fable review effort")
        receipt_id = uuid.uuid4()
        with self._review_checkout() as conn, conn.transaction():
            key_row = conn.execute(
                "SELECT kernel_company_reviewer_public_key(%s) AS public_key",
                (reviewer_worker_id,),
            ).fetchone()
            public_key = None if key_row is None else key_row["public_key"]
            if public_key is None:
                raise CompanyWorkspaceError("reviewer is not the active registered CCPO Fable worker")
            try:
                Ed25519PublicKey.from_public_bytes(bytes(public_key)).verify(
                    signature,
                    review_signature_payload(
                        objective_id=objective_id, reviewer_worker_id=reviewer_worker_id,
                        subject_sha256=subject_sha256, verdict=verdict, effort=effort,
                        result_uuid=result_uuid, session_id=session_id,
                        review_output_sha256=review_output_sha256, model=model,
                    ),
                )
            except (InvalidSignature, ValueError) as exc:
                raise CompanyWorkspaceError(
                    "formal review signature does not match the registered CCPO identity"
                ) from exc
        # The isolated signer can bind an already verified signature to every
        # receipt field, but cannot read the HMAC key or persist a receipt.
        with self._mac_signer_checkout() as conn, conn.transaction():
            verification_mac = conn.execute(
                "SELECT kernel_company_review_mac("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS verification_mac",
                (
                    COMPANY_TENANT_ID, objective_id, reviewer_worker_id, effort,
                    subject_sha256, verdict, result_uuid, session_id,
                    review_output_sha256, signature,
                ),
            ).fetchone()["verification_mac"]
        # The attestor can persist only a signer-bound receipt.  It cannot call
        # the MAC function, and direct reviewer access still cannot insert.
        with self._attestor_checkout() as conn, conn.transaction():
            row = conn.execute(
                "INSERT INTO kernel_company_review_receipts "
                "(id,company_id,objective_id,reviewer_worker_id,reviewer_role,model,effort,"
                "subject_sha256,verdict,result_uuid,session_id,review_output_sha256,signature,"
                "verification_mac) "
                "VALUES (%s,%s,%s,%s,'ccpo',%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING id,objective_id,subject_sha256,verdict,result_uuid,session_id,issued_at",
                (
                    receipt_id, COMPANY_TENANT_ID, objective_id, reviewer_worker_id,
                    model, effort, subject_sha256, verdict, result_uuid, session_id,
                    review_output_sha256, signature, verification_mac,
                ),
            ).fetchone()
        return CompanyReviewReceipt(**row)

    def dashboard(self) -> dict[str, Any]:
        """Derive operating and role activity metrics from immutable Kernel events."""
        with self._checkout() as conn:
            states = conn.execute(
                "SELECT state,count(*)::BIGINT AS count FROM kernel_company_objectives "
                "WHERE company_id=%s GROUP BY state ORDER BY state",
                (COMPANY_TENANT_ID,),
            ).fetchall()
            roles = conn.execute(
                "SELECT actor_role,count(*)::BIGINT AS event_count,"
                "max(recorded_at) AS last_activity_at FROM kernel_company_objective_events "
                "WHERE company_id=%s GROUP BY actor_role ORDER BY actor_role",
                (COMPANY_TENANT_ID,),
            ).fetchall()
            reviews = conn.execute(
                "SELECT verdict,count(*)::BIGINT AS count FROM kernel_company_review_receipts "
                "WHERE company_id=%s GROUP BY verdict ORDER BY verdict",
                (COMPANY_TENANT_ID,),
            ).fetchall()
            recent = conn.execute(
                "SELECT id,objective_id,event_seq,event_kind,actor_role,actor_worker_id,"
                "payload,recorded_at "
                "FROM kernel_company_objective_events WHERE company_id=%s "
                "ORDER BY recorded_at DESC,event_seq DESC,id DESC LIMIT 50",
                (COMPANY_TENANT_ID,),
            ).fetchall()
        return {
            "company_id": COMPANY_TENANT_ID,
            "objective_states": {row["state"]: row["count"] for row in states},
            "role_activity": [dict(row) for row in roles],
            "review_verdicts": {row["verdict"]: row["count"] for row in reviews},
            "recent_events": [dict(row) for row in recent],
        }
