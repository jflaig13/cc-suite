# SPDX-License-Identifier: MPL-2.0
"""Postgres-principal tenant isolation and no-reuse serving connections."""
from __future__ import annotations
from fleet_kernel.configuration import company_id, scribe_deployment_id, authority_issuer, deployment_binding

import hashlib
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from fleet_kernel.identity import COMPANY_ROLE_TYPES


PRIVILEGED_PRINCIPALS = frozenset({
    "fleet_kernel_maintenance", "fleet_kernel_telemetry",
    "fleet_kernel_company_maintenance",
})


COMPANY_ONLY_KERNEL_TABLES = frozenset({
    "kernel_company_objectives", "kernel_company_objective_events",
    "kernel_company_review_receipts", "kernel_company_principals",
    "kernel_company_reviewer_principals", "kernel_company_review_attestor_principals",
    "kernel_company_review_mac_signer_principals", "kernel_company_review_hmac_keys",
    "kernel_company_worker_principals", "kernel_company_scribe_admission_keys",
    "kernel_company_scribe_packages", "kernel_company_scribe_admissions",
    "kernel_company_scribe_recovery_intents",
})


# Admitted memory is superseded by delete + re-admit, never edited in place.




class TenantDatabaseIsolationError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class TenantPrincipal:
    tenant_id: str
    db_role: str
    tenant_schema: str


@dataclass(frozen=True, slots=True)
class CompanyPrincipal:
    company_id: str
    db_role: str


@dataclass(frozen=True, slots=True)
class CompanyReviewerPrincipal:
    company_id: str
    db_role: str


@dataclass(frozen=True, slots=True)
class CompanyReviewAttestorPrincipal:
    company_id: str
    db_role: str


@dataclass(frozen=True, slots=True)
class CompanyReviewMacSignerPrincipal:
    company_id: str
    db_role: str


@dataclass(frozen=True, slots=True)
class ConnectionCheckout:
    connection_id: uuid.UUID
    tenant_id: str
    db_role: str


@dataclass(frozen=True, slots=True)
class TelemetryAccess:
    audit_id: uuid.UUID
    summary: tuple[dict, ...]


def _validate_tenant(tenant_id: str) -> str:
    value = tenant_id.strip()
    if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
        raise ValueError("tenant_id is invalid")
    return value


def principal_for(tenant_id: str) -> TenantPrincipal:
    value = _validate_tenant(tenant_id)
    if value == company_id():
        raise ValueError("the reserved Company Fleet identity cannot become a customer tenant")
    digest = hashlib.sha256(value.encode()).hexdigest()[:20]
    return TenantPrincipal(value, f"mise_t_{digest}", f"mise_tenant_{digest}")


def company_principal_for() -> CompanyPrincipal:
    return CompanyPrincipal(company_id(), "mise_company_workspace")


def company_reviewer_principal_for() -> CompanyReviewerPrincipal:
    return CompanyReviewerPrincipal(company_id(), "mise_company_reviewer")


def company_review_attestor_principal_for() -> CompanyReviewAttestorPrincipal:
    return CompanyReviewAttestorPrincipal(
        company_id(), "mise_company_review_attestor",
    )


def company_review_mac_signer_principal_for() -> CompanyReviewMacSignerPrincipal:
    return CompanyReviewMacSignerPrincipal(
        company_id(), "mise_company_review_mac_signer",
    )


def company_worker_principal_for(
    worker_id: uuid.UUID, role_type: str,
) -> CompanyPrincipal:
    from fleet_kernel.identity import company_worker_database_principal

    return CompanyPrincipal(
        company_id(), company_worker_database_principal(worker_id, role_type),
    )


def provision_company_workspace_isolation(conninfo: dict) -> CompanyPrincipal:
    """Create the single Company Workspace principal with company-only grants."""
    principal = company_principal_for()
    reviewer = company_reviewer_principal_for()
    attestor = company_review_attestor_principal_for()
    mac_signer = company_review_mac_signer_principal_for()
    allowed_tables = tuple(sorted(COMPANY_ONLY_KERNEL_TABLES))
    with psycopg.connect(**conninfo, row_factory=dict_row) as conn:
        with conn.transaction():
            if not conn.execute(
                "SELECT to_regclass('public.kernel_company_principals') IS NOT NULL AS present"
            ).fetchone()["present"]:
                raise TenantDatabaseIsolationError("Company Workspace DDL is not installed")
            workers = conn.execute(
                "SELECT worker.id,worker.role_type,worker.database_principal::TEXT AS db_role "
                "FROM kernel_runtime_workers worker "
                "JOIN kernel_tenant_fleet_manifests manifest "
                "ON manifest.id=worker.manifest_id AND manifest.tenant_id=worker.tenant_id "
                "WHERE worker.tenant_id=%s AND worker.fleet_type='company' "
                "AND worker.status='active' AND manifest.status='active' "
                "ORDER BY worker.role_type",
                (principal.company_id,),
            ).fetchall()
            if (
                {row["role_type"] for row in workers} != COMPANY_ROLE_TYPES
                or len(workers) != len(COMPANY_ROLE_TYPES)
            ):
                raise TenantDatabaseIsolationError(
                    "Company Workspace requires exactly one active worker for each Company role"
                )
            from fleet_kernel.identity import company_worker_database_principal

            for worker in workers:
                expected_principal = company_worker_database_principal(
                    worker["id"], worker["role_type"],
                )
                if worker["db_role"] != expected_principal:
                    raise TenantDatabaseIsolationError(
                        "Company worker database principal does not match its immutable identity"
                    )
            reviewer_worker = next(row for row in workers if row["role_type"] == "ccpo")
            all_runtime_roles = {
                principal.db_role, reviewer.db_role, attestor.db_role, mac_signer.db_role,
                *(row["db_role"] for row in workers),
            }
            for db_role in sorted(all_runtime_roles):
                exists = conn.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=%s) AS present",
                    (db_role,),
                ).fetchone()["present"]
                if not exists:
                    conn.execute(sql.SQL(
                        "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOBYPASSRLS"
                    ).format(sql.Identifier(db_role)))
                else:
                    conn.execute(sql.SQL(
                        "ALTER ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOBYPASSRLS"
                    ).format(sql.Identifier(db_role)))
            conn.execute(
                "INSERT INTO kernel_company_principals (company_id,db_role) "
                "VALUES (%s,%s) ON CONFLICT (company_id) DO UPDATE SET db_role=EXCLUDED.db_role",
                (principal.company_id, principal.db_role),
            )
            conn.execute(
                "INSERT INTO kernel_company_reviewer_principals "
                "(company_id,reviewer_worker_id,db_role) VALUES (%s,%s,%s) "
                "ON CONFLICT (company_id) DO UPDATE SET "
                "reviewer_worker_id=EXCLUDED.reviewer_worker_id,db_role=EXCLUDED.db_role",
                (reviewer.company_id, reviewer_worker["id"], reviewer.db_role),
            )
            conn.execute(
                "INSERT INTO kernel_company_review_attestor_principals "
                "(company_id,db_role) VALUES (%s,%s) "
                "ON CONFLICT (company_id) DO UPDATE SET db_role=EXCLUDED.db_role",
                (attestor.company_id, attestor.db_role),
            )
            conn.execute(
                "INSERT INTO kernel_company_review_mac_signer_principals "
                "(company_id,db_role) VALUES (%s,%s) "
                "ON CONFLICT (company_id) DO UPDATE SET db_role=EXCLUDED.db_role",
                (mac_signer.company_id, mac_signer.db_role),
            )
            previous_workers = conn.execute(
                "SELECT worker_id,db_role::TEXT AS db_role FROM kernel_company_worker_principals"
            ).fetchall()
            active_worker_ids = {row["id"] for row in workers}
            for old in previous_workers:
                if old["worker_id"] not in active_worker_ids:
                    if old["db_role"] not in all_runtime_roles:
                        conn.execute(sql.SQL("ALTER ROLE {} NOLOGIN")
                                     .format(sql.Identifier(old["db_role"])))
                        conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}")
                                     .format(sql.Identifier(old["db_role"])))
                    conn.execute(
                        "DELETE FROM kernel_company_worker_principals WHERE worker_id=%s",
                        (old["worker_id"],),
                    )
            for worker in workers:
                conn.execute(
                    "INSERT INTO kernel_company_worker_principals "
                    "(worker_id,company_id,role_type,db_role) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (worker_id) DO UPDATE SET role_type=EXCLUDED.role_type,"
                    "db_role=EXCLUDED.db_role",
                    (worker["id"], principal.company_id, worker["role_type"], worker["db_role"]),
                )
            # Deny every public table first, then add only the internal daily
            # driver's narrow capabilities.  Customer tables remain invisible
            # even if a future migration adds a permissive table grant.
            tables = conn.execute(
                "SELECT pc.relname AS table_name FROM pg_catalog.pg_class pc "
                "JOIN pg_catalog.pg_namespace pn ON pn.oid=pc.relnamespace "
                "WHERE pn.nspname='public' AND pc.relkind IN ('r','p','v','m','f')"
            ).fetchall()
            for row in tables:
                for db_role in sorted(all_runtime_roles):
                    conn.execute(sql.SQL("REVOKE ALL ON public.{} FROM {}")
                                 .format(sql.Identifier(row["table_name"]),
                                         sql.Identifier(db_role)))
            for db_role in sorted(all_runtime_roles):
                conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}")
                             .format(sql.Identifier(db_role)))
            conn.execute(sql.SQL(
                "GRANT SELECT,INSERT,UPDATE ON public.kernel_company_objectives TO {}"
            ).format(sql.Identifier(principal.db_role)))
            conn.execute(sql.SQL(
                "GRANT SELECT,INSERT ON public.kernel_company_objective_events TO {}"
            ).format(sql.Identifier(principal.db_role)))
            conn.execute(sql.SQL(
                "GRANT SELECT ON public.kernel_company_review_receipts TO {}"
            ).format(sql.Identifier(principal.db_role)))
            conn.execute(sql.SQL(
                "GRANT SELECT,INSERT ON public.kernel_company_review_receipts TO {}"
            ).format(sql.Identifier(attestor.db_role)))
            for worker in workers:
                if worker["role_type"] == "ccpo":
                    continue
                worker_role = sql.Identifier(worker["db_role"])
                conn.execute(sql.SQL(
                    "GRANT SELECT,INSERT,UPDATE ON public.kernel_company_objectives TO {}"
                ).format(worker_role))
                conn.execute(sql.SQL(
                    "GRANT SELECT,INSERT ON public.kernel_company_objective_events TO {}"
                ).format(worker_role))
                conn.execute(sql.SQL(
                    "GRANT SELECT ON public.kernel_company_review_receipts TO {}"
                ).format(worker_role))
                if worker["role_type"] == "scribe":
                    conn.execute(sql.SQL(
                        "GRANT EXECUTE ON FUNCTION "
                        "kernel_bind_company_scribe_admission("
                        "UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,"
                        "BIGINT,TEXT,TEXT,TEXT,BIGINT,TEXT,TEXT,UUID,"
                        "TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,"
                        "UUID,UUID,UUID,TEXT,TEXT) TO {}"
                    ).format(worker_role))
                    conn.execute(sql.SQL(
                        "GRANT EXECUTE ON FUNCTION "
                        "kernel_heartbeat_company_scribe_admission("
                        "UUID,TEXT,TIMESTAMPTZ) TO {}"
                    ).format(worker_role))
            for db_role in sorted(all_runtime_roles):
                conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION kernel_current_company() TO {}")
                             .format(sql.Identifier(db_role)))
            # This returns NULL for the company principal and is used as the
            # checkout-time proof that it is not also mapped as a restaurant.
            for db_role in sorted(all_runtime_roles):
                conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION kernel_current_tenant() TO {}")
                             .format(sql.Identifier(db_role)))
            conn.execute(sql.SQL(
                "GRANT EXECUTE ON FUNCTION kernel_company_reviewer_public_key(UUID) TO {}"
            ).format(sql.Identifier(reviewer.db_role)))
            conn.execute(sql.SQL(
                "GRANT EXECUTE ON FUNCTION kernel_company_review_mac("
                "TEXT,UUID,UUID,TEXT,TEXT,TEXT,UUID,UUID,TEXT,BYTEA) TO {}"
            ).format(sql.Identifier(mac_signer.db_role)))
            for table_name in allowed_tables:
                if not conn.execute(
                    "SELECT to_regclass(%s) IS NOT NULL AS present",
                    (f"public.{table_name}",),
                ).fetchone()["present"]:
                    raise TenantDatabaseIsolationError(
                        f"Company Workspace table is missing: {table_name}"
                    )
            allowed = {
                principal.db_role: {
                    "kernel_company_objectives", "kernel_company_objective_events",
                    "kernel_company_review_receipts",
                },
                reviewer.db_role: set(),
                attestor.db_role: {"kernel_company_review_receipts"},
                mac_signer.db_role: set(),
            }
            for worker in workers:
                if worker["role_type"] == "ccpo":
                    continue
                allowed[worker["db_role"]] = {
                    "kernel_company_objectives", "kernel_company_objective_events",
                    "kernel_company_review_receipts",
                }
            sequences = conn.execute(
                "SELECT pc.relname AS sequence_name FROM pg_catalog.pg_class pc "
                "JOIN pg_catalog.pg_namespace pn ON pn.oid=pc.relnamespace "
                "WHERE pn.nspname='public' AND pc.relkind='S'"
            ).fetchall()
            functions = conn.execute(
                "SELECT proc.oid,proc.proname,proc.prorettype='trigger'::regtype AS is_trigger "
                "FROM pg_catalog.pg_proc proc "
                "JOIN pg_catalog.pg_namespace pn ON pn.oid=proc.pronamespace "
                "WHERE pn.nspname='public' AND proc.prosecdef"
            ).fetchall()
            common_functions = {"kernel_current_company", "kernel_current_tenant"}
            function_allowlist = {
                db_role: set(common_functions) for db_role in all_runtime_roles
            }
            function_allowlist[reviewer.db_role].add("kernel_company_reviewer_public_key")
            function_allowlist[mac_signer.db_role].add("kernel_company_review_mac")
            scribe_worker = next(
                row for row in workers if row["role_type"] == "scribe"
            )
            function_allowlist[scribe_worker["db_role"]].update({
                "kernel_bind_company_scribe_admission",
                "kernel_heartbeat_company_scribe_admission",
            })
            for db_role, allowed_names in allowed.items():
                for row in tables:
                    if row["table_name"] in allowed_names:
                        continue
                    privileges = conn.execute(
                        "SELECT has_table_privilege(%s,%s,'SELECT') "
                        "OR has_table_privilege(%s,%s,'INSERT') "
                        "OR has_table_privilege(%s,%s,'UPDATE') "
                        "OR has_table_privilege(%s,%s,'DELETE') AS allowed",
                        (
                            db_role, f"public.{row['table_name']}",
                            db_role, f"public.{row['table_name']}",
                            db_role, f"public.{row['table_name']}",
                            db_role, f"public.{row['table_name']}",
                        ),
                    ).fetchone()["allowed"]
                    if privileges:
                        raise TenantDatabaseIsolationError(
                            f"Company principal inherited non-allowlisted table access: "
                            f"{db_role} -> {row['table_name']}"
                        )
                for sequence in sequences:
                    conn.execute(sql.SQL("REVOKE ALL ON SEQUENCE public.{} FROM {}")
                                 .format(sql.Identifier(sequence["sequence_name"]),
                                         sql.Identifier(db_role)))
                    if conn.execute(
                        "SELECT has_sequence_privilege(%s,%s,'USAGE') "
                        "OR has_sequence_privilege(%s,%s,'SELECT') "
                        "OR has_sequence_privilege(%s,%s,'UPDATE') AS allowed",
                        (
                            db_role, f"public.{sequence['sequence_name']}",
                            db_role, f"public.{sequence['sequence_name']}",
                            db_role, f"public.{sequence['sequence_name']}",
                        ),
                    ).fetchone()["allowed"]:
                        raise TenantDatabaseIsolationError(
                            f"Company principal inherited sequence access: "
                            f"{db_role} -> {sequence['sequence_name']}"
                        )
                for function in functions:
                    if function["is_trigger"]:
                        continue
                    has_execute = conn.execute(
                        "SELECT has_function_privilege(%s,%s,'EXECUTE') AS allowed",
                        (db_role, function["oid"]),
                    ).fetchone()["allowed"]
                    should_execute = (
                        function["proname"] in function_allowlist[db_role]
                    )
                    if has_execute != should_execute:
                        raise TenantDatabaseIsolationError(
                            "Company principal has an invalid security-definer grant: "
                            f"{db_role} -> {function['proname']} "
                            f"(expected execute={should_execute})"
                        )
    return principal


def assert_company_serving_principal(conn: psycopg.Connection) -> CompanyPrincipal:
    expected = company_principal_for()
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT session_user AS db_role,kernel_current_company() AS company_id,"
            "kernel_current_tenant() AS tenant_id"
        ).fetchone()
    if (row["db_role"], row["company_id"], row["tenant_id"]) != (
        expected.db_role, expected.company_id, None,
    ):
        raise TenantDatabaseIsolationError(
            "Company Workspace connection crossed a company/customer identity boundary"
        )
    return expected


def assert_company_reviewer_principal(
    conn: psycopg.Connection,
) -> CompanyReviewerPrincipal:
    expected = company_reviewer_principal_for()
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT session_user AS db_role,kernel_current_company() AS company_id,"
            "kernel_current_tenant() AS tenant_id"
        ).fetchone()
    if (row["db_role"], row["company_id"], row["tenant_id"]) != (
        expected.db_role, expected.company_id, None,
    ):
        raise TenantDatabaseIsolationError(
            "Company reviewer connection crossed a reviewer/company/customer boundary"
        )
    return expected


def assert_company_review_attestor_principal(
    conn: psycopg.Connection,
) -> CompanyReviewAttestorPrincipal:
    expected = company_review_attestor_principal_for()
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT session_user AS db_role,kernel_current_company() AS company_id,"
            "kernel_current_tenant() AS tenant_id"
        ).fetchone()
    if (row["db_role"], row["company_id"], row["tenant_id"]) != (
        expected.db_role, expected.company_id, None,
    ):
        raise TenantDatabaseIsolationError(
            "Company review attestor crossed an attestor/company/customer boundary"
        )
    return expected


def assert_company_review_mac_signer_principal(
    conn: psycopg.Connection,
) -> CompanyReviewMacSignerPrincipal:
    expected = company_review_mac_signer_principal_for()
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT session_user AS db_role,kernel_current_company() AS company_id,"
            "kernel_current_tenant() AS tenant_id"
        ).fetchone()
    if (row["db_role"], row["company_id"], row["tenant_id"]) != (
        expected.db_role, expected.company_id, None,
    ):
        raise TenantDatabaseIsolationError(
            "Company review MAC signer crossed a signer/company/customer boundary"
        )
    return expected


def assert_company_worker_principal(
    conn: psycopg.Connection, *, worker_id: uuid.UUID, role_type: str,
) -> CompanyPrincipal:
    expected = company_worker_principal_for(worker_id, role_type)
    with conn.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT session_user AS db_role,kernel_current_company() AS company_id,"
            "kernel_current_tenant() AS tenant_id"
        ).fetchone()
    if (row["db_role"], row["company_id"], row["tenant_id"]) != (
        expected.db_role, expected.company_id, None,
    ):
        raise TenantDatabaseIsolationError(
            "Company worker connection crossed its registered worker identity boundary"
        )
    return expected



























