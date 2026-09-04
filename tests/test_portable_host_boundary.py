"""Hermetic checks for exported configuration/admission boundaries; no services start."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
BUN = shutil.which('bun')


def run(argv, env, cwd):
    return subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=10)


def environment(tmp_path):
    root = tmp_path.resolve()
    workspace = root / 'workspace'; workspace.mkdir()
    return {'PATH': os.defpath, 'HOME': str(root / 'empty-home'),
            'CC_SUITE_COMPANY_ID': 'example-company',
            'CC_SUITE_SCRIBE_DEPLOYMENT_ID': 'Scribe-Example',
            'CC_SUITE_WORKSPACE': str(workspace),
            'CC_SUITE_PACKAGE_ROOT': str(ROOT),
            'CC_SUITE_STATE_ROOT': str(root / 'state'),
            'CC_SUITE_CHANNEL_SHARED_DIR': str(root / 'bus'),
            'CC_SUITE_CHANNEL_STATE_DIR': str(root / 'state/company-ccto/channel'),
            'CC_SUITE_PYTHON': str(Path(sys.executable).absolute()),
            'AGENT_ROLE': 'ccto', 'AGENT_PORT': '8790'}


def authorizer(tmp_path, env, patch=None, malformed=False):
    receipt = {'authorized': True, 'role': 'ccto', 'operation': 'channel-push',
               'workspace': env['CC_SUITE_WORKSPACE'], 'state_root': env['CC_SUITE_STATE_ROOT'],
               'channel_shared_dir': env['CC_SUITE_CHANNEL_SHARED_DIR']}
    receipt.update(patch or {})
    script = tmp_path / 'synthetic_authorizer.py'
    output = 'invalid-json' if malformed else json.dumps(receipt)
    script.write_text('#!' + str(Path(sys.executable).resolve()) + '\nprint(' + repr(output) + ')\n')
    script.chmod(0o700)
    env['CC_SUITE_HOST_AUTHORIZER'] = str(script.resolve())


def assert_no_state(env):
    assert not Path(env['CC_SUITE_STATE_ROOT']).exists()
    assert not Path(env['CC_SUITE_CHANNEL_SHARED_DIR']).exists()


def test_missing_authority_refuses_without_side_effects(tmp_path):
    env = environment(tmp_path)
    result = run([sys.executable, str(ROOT/'scripts/portable_host.py'), 'ccto', 'channel-push'], env, tmp_path)
    assert result.returncode == 78
    assert_no_state(env)


@pytest.mark.parametrize('patch', [{'authorized': False}, {'authorized': 1}, {'role': 'ccpo'},
                                 {'operation': 'launch-claude'}, {'state_root': '/different'},
                                 {'workspace': '/different'}, {'channel_shared_dir': '/different'}])
def test_mismatched_receipt_refuses(tmp_path, patch):
    env = environment(tmp_path); authorizer(tmp_path, env, patch)
    result = run([sys.executable, str(ROOT/'scripts/portable_host.py'), 'ccto', 'channel-push'], env, tmp_path)
    assert result.returncode == 78
    assert_no_state(env)


def test_malformed_receipt_refuses(tmp_path):
    env = environment(tmp_path); authorizer(tmp_path, env, malformed=True)
    result = run([sys.executable, str(ROOT/'scripts/portable_host.py'), 'ccto', 'channel-push'], env, tmp_path)
    assert result.returncode == 78
    assert_no_state(env)


def test_exact_receipt_validates_without_launch_or_state_mutation(tmp_path):
    env = environment(tmp_path); authorizer(tmp_path, env)
    result = run([sys.executable, str(ROOT/'scripts/portable_host.py'), 'ccto', 'channel-push'], env, tmp_path)
    assert result.returncode == 0
    assert_no_state(env)


@pytest.mark.skipif(not BUN, reason='Bun 1.3.12 required')
@pytest.mark.parametrize('channel', ['scribe', 'agent'])
def test_built_channel_denies_unadmitted_start_before_effects(tmp_path, channel):
    env = environment(tmp_path)
    name = 'webhook.bundle.js' if channel == 'scribe' else 'channel.bundle.js'
    isolated_bundle = tmp_path / name
    shutil.copyfile(ROOT/'channels'/channel/name, isolated_bundle)
    result = run([BUN, '--no-install', str(isolated_bundle)], env, tmp_path)
    assert result.returncode == 78
    assert 'REFUSED' in result.stderr
    assert_no_state(env)


@pytest.mark.skipif(not BUN, reason='Bun 1.3.12 required')
def test_scribe_closed_graph_offline_probe_has_no_effects(tmp_path):
    env = environment(tmp_path); env['SCRIBE_OFFLINE_PROBE'] = '1'
    result = run([BUN, '--no-install', str(ROOT/'channels/scribe/webhook.bundle.js')], env, tmp_path)
    assert result.returncode == 0 and result.stdout.strip() == 'offline-bun-ok'
    assert_no_state(env)


@pytest.mark.skipif(not BUN, reason='Bun 1.3.12 required')
def test_scribe_rejects_wrong_admitted_parent(tmp_path):
    env = environment(tmp_path)
    env.update(MISE_SCRIBE_ADMITTED='1', SCRIBE_EXPECTED_PARENT_PID='999999999')
    result = run([BUN, '--no-install', str(ROOT/'channels/scribe/webhook.bundle.js')], env, tmp_path)
    assert result.returncode == 78
    assert_no_state(env)


@pytest.mark.skipif(not BUN, reason='Bun 1.3.12 required')
def test_generic_channel_rejects_other_roles_state(tmp_path):
    env = environment(tmp_path); authorizer(tmp_path, env)
    env['CC_SUITE_CHANNEL_STATE_DIR'] = str(tmp_path.resolve()/'state/company-ccpo/channel')
    result = run([BUN, '--no-install', str(ROOT/'channels/agent/channel.bundle.js')], env, tmp_path)
    assert result.returncode == 78
    assert_no_state(env)


@pytest.mark.parametrize('artifact', ['ACTIVE.json', 'restart-request.json', 'restart-intent-epoch-1.json'])
def test_native_legacy_fence_refuses_recoverable_lineage(tmp_path, artifact):
    state = tmp_path/'lineage'; state.mkdir()
    (state/artifact).write_text(json.dumps({'role_type': 'ccto', 'state': 'awaiting-external-launch'}))
    result = run([sys.executable, str(ROOT/'scripts/company_role_kernel_lineage_probe.py'), '--role', 'ccto', '--state-dir', str(state)], {'PATH':os.defpath}, tmp_path)
    assert result.returncode == 78
    assert 'fenced' in result.stderr


def test_kernel_bridge_opt_in_is_required_without_reading_mirror_or_database(monkeypatch):
    spec = importlib.util.spec_from_file_location('portable_kernel_bridge', ROOT/'mcp_servers/channel_relay/kernel_plane_bridge.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.delenv('CC_SUITE_ENABLE_KERNEL_BRIDGE', raising=False)
    monkeypatch.setattr(module, '_read_mirror', lambda: pytest.fail('read live state'))
    assert module.get_cutover_state('ccto') == 'file_bus_only'
    assert module.dual_write_send(sender_role='ccto', target_role='ccpo', event_type='agent_direct', content='synthetic', send_id='synthetic') == {'status':'skipped', 'reason':'cutover_inactive'}


def test_configs_preserve_native_expected_server_names():
    roles = 'scribe ccto ccpo utility ccro ccfo ccmo cclo ccgo ccco cos ccde'.split()
    for role in roles:
        cfg = json.loads((ROOT/f'channels/configs/role-{role}.mcp.json').read_text())
        assert f'{role}-channel-push' in cfg['mcpServers']
        assert cfg['mcpServers'][f'{role}-channel-push']['args'][-2:] == [role, 'push']


@pytest.mark.parametrize('flag', ['--help', '-h'])
def test_fleet_help_needs_no_configuration_or_state(tmp_path, flag):
    shell = shutil.which('zsh')
    assert shell, 'zsh is required for the exported host checks'
    before = set(tmp_path.iterdir())
    result = run([shell, str(ROOT/'scripts/fleet_spawn.sh'), flag],
                 {'PATH': os.defpath, 'HOME': str(tmp_path)}, tmp_path)
    assert result.returncode == 0 and not result.stderr
    assert 'Usage: fleet_spawn.sh' in result.stdout
    assert '--dry-run' in result.stdout and 'write launcher command files' in result.stdout
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize('script,args', [
    ('launch_claude.sh', ['ccto']),
    ('fleet_spawn.sh', ['--roles', 'ccto', '--dry-run', '--force']),
])
@pytest.mark.parametrize('identity', ['CC_SUITE_COMPANY_ID', 'CC_SUITE_SCRIBE_DEPLOYMENT_ID'])
def test_legacy_launchers_refuse_missing_identity_before_effects(tmp_path, script, args, identity):
    env = environment(tmp_path)
    authorizer(tmp_path, env)
    env.pop(identity)
    shell = shutil.which('zsh' if script == 'fleet_spawn.sh' else 'bash')
    assert shell, 'Bash and zsh are required for the exported host checks'
    result = run([shell, str(ROOT/'scripts'/script), *args], env, tmp_path)
    assert result.returncode == 78
    assert 'REFUSED' in result.stderr and identity in result.stderr
    assert 'Traceback' not in result.stderr
    assert_no_state(env)


@pytest.mark.parametrize('shell_name', ['bash', 'zsh'])
@pytest.mark.parametrize('value', ['', '_invalid', 'contains space', 'non-ascii-é', 'x' * 129])
def test_shared_host_guard_rejects_invalid_identity(tmp_path, shell_name, value):
    env = environment(tmp_path)
    authorizer(tmp_path, env)
    env['CC_SUITE_COMPANY_ID'] = value
    shell = shutil.which(shell_name)
    assert shell, 'Bash and zsh are required for the exported host checks'
    result = run([shell, '-c', '. "$1"', '--', str(ROOT/'scripts/portable_host_env.sh')], env, tmp_path)
    assert result.returncode == 78
    assert 'REFUSED' in result.stderr and 'CC_SUITE_COMPANY_ID' in result.stderr
    assert 'Traceback' not in result.stderr
    assert_no_state(env)


def test_generated_fleet_command_preserves_identity_through_real_host_gate(tmp_path):
    env = environment(tmp_path)
    old_workspace = Path(env['CC_SUITE_WORKSPACE'])
    workspace = old_workspace.with_name("workspace with 'quotes' and $dollar")
    old_workspace.rename(workspace)
    env['CC_SUITE_WORKSPACE'] = str(workspace)
    env['CC_SUITE_COMPANY_ID'] = 'Example.Company_7-test'
    env['CC_SUITE_SCRIBE_DEPLOYMENT_ID'] = 'Scribe.Example_9-test'
    env['CC_SUITE_PERMISSION_MODE'] = 'plan'
    sentinel = tmp_path / 'host-must-not-run.sh'
    sentinel.write_text('#!/bin/sh\necho HOST_WAS_RUN >&2\nexit 99\n')
    sentinel.chmod(0o700)
    env['CC_SUITE_CLAUDE'] = str(sentinel)
    # Test-only policy adapter accepts the exact synthetic subject and only the
    # two operations used here. No model executable or Terminal is invoked.
    policy = tmp_path / 'synthetic_launch_authorizer.py'
    policy.write_text('#!' + str(Path(sys.executable).resolve()) + '\n' +
        'import json,sys\n' +
        'args=dict(zip(sys.argv[1::2],sys.argv[2::2]))\n' +
        'print(json.dumps({"authorized":args["--role"]=="ccto" and args["--operation"] in {"fleet-spawn","launch-claude"},' +
        '"role":args["--role"],"operation":args["--operation"],"workspace":args["--workspace"],' +
        '"state_root":args["--state-root"],"channel_shared_dir":args["--channel-shared-dir"]}))\n')
    policy.chmod(0o700)
    env['CC_SUITE_HOST_AUTHORIZER'] = str(policy)
    shell = shutil.which('zsh')
    assert shell, 'zsh is required to verify generated fleet commands'
    result = run([shell, str(ROOT/'scripts/fleet_spawn.sh'), '--roles', 'ccto', '--dry-run', '--force'], env, tmp_path)
    assert result.returncode == 0, result.stderr
    command = Path(env['CC_SUITE_STATE_ROOT']) / 'launchers/launch_ccto.command'
    generated = command.read_text()
    for name in ('CC_SUITE_COMPANY_ID', 'CC_SUITE_SCRIBE_DEPLOYMENT_ID'):
        assert 'export ' + name + '=' + env[name] in generated
    # A fresh Terminal does not inherit these deployment settings. The actual
    # generated command must provide them, pass the actual Python host gate,
    # and stop on the missing adopter init file before touching the host/bus.
    fresh = {'PATH': os.defpath, 'HOME': env['HOME']}
    result = run([shell, str(command)], fresh, tmp_path)
    assert result.returncode == 1
    assert 'CLEAR: ccto is officially' in result.stdout
    assert 'Init command not found' in result.stderr
    assert 'Traceback' not in result.stderr and 'HOST_WAS_RUN' not in result.stderr
    assert not Path(env['CC_SUITE_CHANNEL_SHARED_DIR']).exists()
