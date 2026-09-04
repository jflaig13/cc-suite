"""Public portability boundaries with no actual host install or authority."""
import importlib
import json
from pathlib import Path
import pytest
from fleet_kernel import configuration
from fleet_kernel.company_scribe_admission import CompanyScribeAdmissionError
from scripts import company_scribe_supervisor as supervisor


def test_no_implicit_company_or_database_configuration(monkeypatch):
    monkeypatch.delenv('CC_SUITE_COMPANY_ID')
    with pytest.raises(configuration.ConfigurationError):
        configuration.company_id()
    from fleet_kernel.company_message_plane.client import CompanyMessagePlaneClient, MessagePlaneError
    with pytest.raises(MessagePlaneError, match='explicit database'):
        CompanyMessagePlaneClient()


def test_no_private_authority_is_bundled():
    from fleet_kernel import founder_authority
    assert founder_authority.PHASE4_MANDATE_ID is None
    assert founder_authority.PHASE4_MANDATE_ARTIFACT_SHA256 is None
    assert founder_authority.MANDATE_ARTIFACT_SHA256 is None
    assert founder_authority.UTILITY_WORKER_ID is None


def test_install_supervisor_requires_explicit_destination():
    with pytest.raises(SystemExit) as exc:
        supervisor.parser().parse_args(['install-supervisor', '--package', '/example/package', '--binding', '/example/binding', '--state-dir', '/example/state'])
    assert exc.value.code == 2


def test_atomic_plist_never_clobbers_without_explicit_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    target = tmp_path / 'Library/LaunchAgents/example.ccsuite.plist'
    supervisor._atomic_plist(target, {'Label':'example.original'})
    original = target.read_bytes()
    with pytest.raises(CompanyScribeAdmissionError, match='replace-existing'):
        supervisor._atomic_plist(target, {'Label':'example.updated'})
    assert target.read_bytes() == original
    supervisor._atomic_plist(target, {'Label':'example.updated'}, replace_existing=True)
    assert target.read_bytes() != original


def test_isolated_broker_identity_does_not_forward_authority(monkeypatch, tmp_path):
    from fleet_kernel.company_scribe_runtime import _runtime_child_environment
    from scripts.company_scribe_effect_broker import _command_environment
    source = {'CC_SUITE_COMPANY_ID':'example-company','CC_SUITE_SCRIBE_DEPLOYMENT_ID':'Scribe-Example','CC_SUITE_AUTHORITY_BINDINGS_FILE':'/not-forwarded/authority.json','PATH':'/usr/bin:/bin'}
    child = _runtime_child_environment(source)
    assert child['CC_SUITE_COMPANY_ID'] == 'example-company'
    assert 'CC_SUITE_AUTHORITY_BINDINGS_FILE' not in child
    env = _command_environment(tmp_path, tmp_path / 'workspace')
    assert env['CC_SUITE_SCRIBE_DEPLOYMENT_ID'] == 'Scribe-Example'
    assert all('DATABASE' not in key and 'AUTHORITY' not in key for key in env)


def test_shared_dispatch_state_root_is_explicit(monkeypatch, tmp_path):
    from fleet_kernel.m5.read_backend import dispatch_gap_marker_path
    from fleet_kernel.m5.dispatch_approval_migration import DispatchApprovalMigrationError
    monkeypatch.delenv('FLEET_KERNEL_M5_GAP_ROOT', raising=False)
    with pytest.raises(DispatchApprovalMigrationError, match='explicit absolute'):
        dispatch_gap_marker_path(tmp_path / 'dispatch.json', 'example-company')
    monkeypatch.setenv('FLEET_KERNEL_M5_GAP_ROOT', str(tmp_path / 'gaps'))
    assert dispatch_gap_marker_path(tmp_path / 'dispatch.json', 'example-company').parent == tmp_path / 'gaps'
    assert not (tmp_path / 'gaps').exists()


def test_role_channel_code_uses_verified_payload_not_ambient_workspace(tmp_path):
    from types import SimpleNamespace
    from fleet_kernel.company_role_runtime import runtime_child_environment
    profile = SimpleNamespace(role_type='ccpo', deployment_id='CCPO-Example', sha256='a' * 64)
    result = runtime_child_environment(
        {'CC_SUITE_PACKAGE_ROOT':'/unreviewed/code', 'CC_SUITE_WORKSPACE':'/wrong/workspace'},
        profile=profile, repo=tmp_path / 'workspace', output_root=tmp_path / 'outbox',
        package_payload=tmp_path / 'reviewed-package/payload',
    )
    assert result['CC_SUITE_PACKAGE_ROOT'] == str(tmp_path / 'reviewed-package/payload')
    assert result['CC_SUITE_WORKSPACE'] == str(tmp_path / 'workspace')
