# SPDX-License-Identifier: MPL-2.0
"""Current Company workflow, identity, signed review, and durable-state proofs."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import uuid
import psycopg
import pytest
from fleet_kernel.company_workspace import CompanyWorkspaceConflict, CompanyWorkspaceError, review_signature_payload
from fleet_kernel.example import disposable_company
from fleet_kernel.identity import company_role_spec
from fleet_kernel.m8 import db_isolation

@pytest.fixture(scope='module')
def company():
    with disposable_company() as sandbox:
        yield sandbox

def ready_objective(company):
    workspace = company.workspace
    objective = workspace.create_objective(title='Synthetic reviewed work', owner_role='ccde')
    active = workspace.transition(objective.id, to_state='active', expected_version=1, actor_role='founder')
    subject = hashlib.sha256(str(objective.id).encode()).hexdigest()
    return workspace.transition(objective.id, to_state='ready_for_review', expected_version=active.version, actor_role='founder', work_subject_sha256=subject)

def signed_fields(company, objective, **updates):
    fields = dict(objective_id=objective.id, reviewer_worker_id=company.workers['ccpo'], subject_sha256=objective.work_subject_sha256, verdict='CLEAN', effort='high', result_uuid=uuid.uuid4(), session_id=uuid.uuid4(), review_output_sha256='b'*64, model=company_role_spec('ccpo').default_model)
    fields.update(updates)
    signature = company.private_keys['ccpo'].sign(review_signature_payload(**fields))
    return fields, signature

def record(company, fields, signature):
    return company.workspace.record_review(**fields, signature=signature)

def test_current_default_review_and_restart_persistence(company):
    objective = ready_objective(company)
    with pytest.raises(psycopg.errors.RaiseException, match='exact-subject CLEAN'):
        company.workspace.transition(objective.id, to_state='completed', expected_version=objective.version, actor_role='founder')
    fields, signature = signed_fields(company, objective)
    receipt = record(company, fields, signature)
    with psycopg.connect(**company.conninfo) as conn:
        model, mac_length = conn.execute('SELECT model, octet_length(verification_mac) FROM kernel_company_review_receipts WHERE id=%s', (receipt.id,)).fetchone()
    assert model == company_role_spec('ccpo').default_model == 'claude-fable-5-1'
    assert mac_length == 32
    completed = company.workspace.transition(objective.id, to_state='completed', expected_version=objective.version, actor_role='founder')
    before = company.workspace.objective_detail(objective.id)
    company.restart()
    assert company.workspace.get_objective(objective.id) == completed
    assert company.workspace.objective_detail(objective.id) == before
    assert len(before['events']) == 5

def test_stale_versions_and_unadmitted_worker_effects_are_rejected(company):
    workspace = company.workspace
    objective = workspace.create_objective(title='Identity boundaries', owner_role='ccde')
    with pytest.raises(CompanyWorkspaceConflict):
        workspace.transition(objective.id, to_state='active', expected_version=99, actor_role='founder')
    with pytest.raises(psycopg.errors.RaiseException, match='admission'):
        workspace.transition(objective.id, to_state='active', expected_version=1, actor_role='ccde', actor_worker_id=company.workers['ccde'])
    with pytest.raises(CompanyWorkspaceError, match='exact live admission'):
        workspace.transition(objective.id, to_state='active', expected_version=1, actor_role='scribe', actor_worker_id=company.workers['scribe'])
    assert workspace.get_objective(objective.id) == objective
    assert len(workspace.objective_detail(objective.id)['events']) == 1

def test_signature_subject_model_and_worker_identity_are_all_bound(company):
    objective = ready_objective(company)
    fields, signature = signed_fields(company, objective)
    with pytest.raises(CompanyWorkspaceError, match='signature'):
        record(company, fields, b'forged')
    with pytest.raises(CompanyWorkspaceError, match='signature'):
        record(company, {**fields, 'review_output_sha256': 'c'*64}, signature)
    wrong_subject, wrong_signature = signed_fields(company, objective, subject_sha256='d'*64)
    with pytest.raises(psycopg.errors.RaiseException, match='current ready-for-review subject'):
        record(company, wrong_subject, wrong_signature)
    # An authentic signature claiming the other admitted model still cannot
    # impersonate the immutable model route of the registered worker.
    wrong_model, model_signature = signed_fields(company, objective, model='claude-fable-5')
    with pytest.raises(psycopg.errors.RaiseException, match='active CCPO Fable worker'):
        record(company, wrong_model, model_signature)
    unknown_worker, unknown_signature = signed_fields(company, objective, reviewer_worker_id=uuid.uuid4())
    with pytest.raises(CompanyWorkspaceError, match='active registered CCPO'):
        record(company, unknown_worker, unknown_signature)
    with pytest.raises(CompanyWorkspaceError, match='unsupported formal review model'):
        signed_fields(company, objective, model='unadmitted-reviewer')
    assert company.workspace.objective_detail(objective.id)['reviews'] == []

def test_principals_cannot_mint_reviews_or_rewrite_history(company):
    objective = ready_objective(company)
    role = db_isolation.company_principal_for().db_role
    with psycopg.connect(**company.conninfo, user=role, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('DELETE FROM kernel_company_objective_events WHERE objective_id=%s', (objective.id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('SELECT secret_key FROM kernel_company_review_hmac_keys')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('UPDATE kernel_company_review_receipts SET verdict=\'CLEAN\'')
    with psycopg.connect(**company.conninfo, user=db_isolation.company_review_attestor_principal_for().db_role, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match='exact verification MAC'):
            conn.execute("INSERT INTO kernel_company_review_receipts (id,company_id,objective_id,reviewer_worker_id,reviewer_role,model,effort,subject_sha256,verdict,result_uuid,session_id,review_output_sha256,signature,verification_mac) VALUES (%s,'example-company',%s,%s,'ccpo','claude-fable-5-1','high',%s,'CLEAN',%s,%s,%s,%s,%s)", (uuid.uuid4(),objective.id,company.workers['ccpo'],objective.work_subject_sha256,uuid.uuid4(),uuid.uuid4(),'a'*64,b'forged',b'\0'*32))

def test_concurrent_current_reviews_serialize_without_deadlock(company):
    objective = ready_objective(company)
    def review(_):
        fields, signature = signed_fields(company, objective)
        return record(company, fields, signature)
    with ThreadPoolExecutor(max_workers=2) as workers:
        receipts = list(workers.map(review, range(2)))
    assert len({receipt.id for receipt in receipts}) == 2
    detail = company.workspace.objective_detail(objective.id)
    assert len(detail['reviews']) == 2
    assert [event['event_seq'] for event in detail['events']] == list(range(1, len(detail['events'])+1))

def test_message_plane_refuses_missing_admission_history(company):
    from fleet_kernel.company_message_plane.client import CompanyMessagePlaneClient
    from psycopg.conninfo import make_conninfo
    client = CompanyMessagePlaneClient(make_conninfo(**company.conninfo, user='fleet_kernel_provisioner'))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        client.send(sender_role='ccde', recipient_role='ccpo', message_kind='audit_request', payload='Synthetic review request', idempotency_key='no-live-admission', require_live_admission=True)
    with psycopg.connect(**company.conninfo) as conn:
        assert conn.execute('SELECT count(*) FROM kernel_company_messages').fetchone()[0] == 0
