# SPDX-License-Identifier: MPL-2.0
"""Synthetic, disposable Company workflow using the real exported implementation.

This starts only a temporary Unix-socket PostgreSQL instance. It performs no
model calls, browser actions, channel deliveries, or persistent host installs.
Generated signing keys belong only to the synthetic registered reviewer.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import uuid

import psycopg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def postgres_bin() -> Path:
    configured = os.environ.get('CC_SUITE_TEST_PG_BIN')
    candidates = [Path(configured)] if configured else []
    initdb = shutil.which('initdb')
    if initdb:
        candidates.append(Path(initdb).parent)
    pg_config = shutil.which('pg_config')
    if pg_config:
        result = subprocess.run([pg_config, '--bindir'], capture_output=True, text=True, check=True)
        candidates.append(Path(result.stdout.strip()))
    for candidate in candidates:
        if all((candidate / name).is_file() and os.access(candidate / name, os.X_OK) for name in ('initdb', 'pg_ctl')):
            return candidate.resolve()
    raise RuntimeError('PostgreSQL is required: set CC_SUITE_TEST_PG_BIN to its bin directory or put initdb/pg_config on PATH')


@dataclass
class DisposableCompany:
    conninfo: dict
    workers: dict
    private_keys: dict
    workspace: object
    restart: object


@contextmanager
def disposable_company():
    """Use a new local database; deliberately ignore every database URL setting."""
    if os.environ.get('CC_SUITE_COMPANY_ID') != 'example-company':
        raise ValueError('The synthetic example requires CC_SUITE_COMPANY_ID=example-company')
    if os.environ.get('CC_SUITE_SCRIBE_DEPLOYMENT_ID') != 'Scribe-Example':
        raise ValueError('The synthetic example requires CC_SUITE_SCRIBE_DEPLOYMENT_ID=Scribe-Example')
    from fleet_kernel import identity
    from fleet_kernel.database import install_schema
    from fleet_kernel.company_workspace import CompanyWorkspace
    from fleet_kernel.m8.db_isolation import provision_company_workspace_isolation
    pg = postgres_bin()
    clean_env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C', 'LANG': 'C'}
    with tempfile.TemporaryDirectory(prefix='cc-suite-') as directory:
        root = Path(directory)
        data = root / 'postgres'
        socket_dir = root / 'socket'
        socket_dir.mkdir(mode=0o700)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        def command(*args):
            return subprocess.run([str(pg / args[0]), *args[1:]], env=clean_env, check=True, capture_output=True, text=True)
        command('initdb', '-D', str(data), '-A', 'trust', '--locale=C', '--encoding=UTF8')
        def start():
            command('pg_ctl', '-D', str(data), '-o', f"-p {port} -k {socket_dir} -h ''", '-l', str(root / 'postgres.log'), '-w', 'start')
        def stop():
            command('pg_ctl', '-D', str(data), '-m', 'fast', '-w', 'stop')
        def restart():
            stop()
            start()
        start()
        conninfo = {'dbname': 'postgres', 'host': str(socket_dir), 'port': port, 'connect_timeout': 10}
        try:
            with psycopg.connect(**conninfo, autocommit=True) as conn:
                conn.execute('CREATE ROLE fleet_kernel_provisioner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
                install_schema(conn)
                manifest = identity.provision_company_manifest(conn, manifest_version=1)
                workers, keys = {}, {}
                for role in sorted(identity.COMPANY_ROLE_TYPES):
                    worker_id = uuid.uuid4()
                    keys[role] = Ed25519PrivateKey.generate()
                    workers[role] = identity.register_runtime_worker(
                        conn, tenant_id=manifest.tenant_id, manifest_id=manifest.manifest_id,
                        role_type=role, os_principal=f'example-{role}',
                        filesystem_root=root / 'workers' / role,
                        database_principal=identity.company_worker_database_principal(worker_id, role),
                        signing_public_key=keys[role].public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw),
                        worker_id=worker_id,
                    ).worker_id
            provision_company_workspace_isolation(conninfo)
            # Authentication-shaped values satisfy the native explicit-credential
            # requirement. Only the temporary owner-only Unix socket uses trust.
            credentials = lambda: {'password': 'synthetic-only'}
            workspace = CompanyWorkspace(
                conninfo, credential_lookup=credentials, reviewer_credential_lookup=credentials,
                attestor_credential_lookup=credentials, mac_signer_credential_lookup=credentials,
                worker_credential_lookup=lambda _: credentials(),
            )
            yield DisposableCompany(conninfo, workers, keys, workspace, restart)
        finally:
            stop()


def run_example() -> dict:
    from fleet_kernel.company_workspace import review_signature_payload
    from fleet_kernel.identity import company_role_spec
    with disposable_company() as sandbox:
        workspace = sandbox.workspace
        objective = workspace.create_objective(title='Verify a synthetic operating note', owner_role='ccde')
        active = workspace.transition(objective.id, to_state='active', expected_version=objective.version, actor_role='founder')
        subject = hashlib.sha256(b'Synthetic note: no external action requested.').hexdigest()
        ready = workspace.transition(objective.id, to_state='ready_for_review', expected_version=active.version, actor_role='founder', work_subject_sha256=subject)
        result_id, session_id = uuid.uuid4(), uuid.uuid4()
        output = hashlib.sha256(b'Synthetic signed reviewer result.').hexdigest()
        model = company_role_spec('ccpo').default_model
        fields = dict(objective_id=objective.id, reviewer_worker_id=sandbox.workers['ccpo'], subject_sha256=subject, verdict='CLEAN', effort='high', result_uuid=result_id, session_id=session_id, review_output_sha256=output, model=model)
        signature = sandbox.private_keys['ccpo'].sign(review_signature_payload(**fields))
        receipt = workspace.record_review(objective.id, **{k:v for k,v in fields.items() if k != 'objective_id'}, signature=signature)
        completed = workspace.transition(objective.id, to_state='completed', expected_version=ready.version, actor_role='founder')
        sandbox.restart()
        recovered = workspace.get_objective(objective.id)
        if recovered != completed:
            raise AssertionError('Objective changed across database restart')
        detail = workspace.objective_detail(objective.id)
        return {'mode':'synthetic-local-only', 'model_calls':0, 'external_effects':0, 'state':recovered.state, 'review_model':model, 'subject_sha256':subject, 'review_receipt_id':str(receipt.id), 'event_count':len(detail['events']), 'restart_persistence':'verified'}


if __name__ == '__main__':
    print(json.dumps(run_example(), indent=2))
