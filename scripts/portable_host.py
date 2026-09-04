"""Explicit adopter configuration for reusable native host adapters.

The authorizer is supplied by the deploying organization. Its decision does
not replace the package supervisor's native admission/lineage checks.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROLES = frozenset('scribe cos ccto ccpo ccde ccro ccfo ccmo cclo ccgo ccco utility'.split())

def configured_path(name: str, *, executable: bool = False) -> Path:
    value = os.environ.get(name, '')
    path = Path(value)
    if not value or not path.is_absolute() or str(path.resolve()) != value:
        raise ValueError(f'{name} must name an explicit canonical absolute path')
    if executable and (not path.is_file() or not os.access(path, os.X_OK)):
        raise ValueError(f'{name} must name an executable file')
    return path

def authorize(role: str, operation: str) -> None:
    if role not in ROLES or not re.fullmatch(r'[a-z][a-z-]*', operation):
        raise ValueError('invalid Company role or host operation')
    workspace = configured_path('CC_SUITE_WORKSPACE')
    state = configured_path('CC_SUITE_STATE_ROOT')
    bus = configured_path('CC_SUITE_CHANNEL_SHARED_DIR')
    authorizer = configured_path('CC_SUITE_HOST_AUTHORIZER', executable=True)
    result = subprocess.run([str(authorizer), '--role', role, '--operation', operation,
                             '--workspace', str(workspace), '--state-root', str(state),
                             '--channel-shared-dir', str(bus)],
                            capture_output=True, text=True, check=False, timeout=10)
    if result.returncode != 0:
        raise ValueError('deployment authorizer refused the requested host operation')
    try:
        receipt = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError('deployment authorizer returned no structured receipt') from exc
    expected = {'authorized': True, 'role': role, 'operation': operation,
                'workspace': str(workspace), 'state_root': str(state),
                'channel_shared_dir': str(bus)}
    if not isinstance(receipt, dict) or receipt.get('authorized') is not True or any(receipt.get(k) != v for k,v in expected.items()):
        raise ValueError('deployment authorization receipt does not bind the requested subject')

def main() -> int:
    try:
        if len(sys.argv) != 3:
            raise ValueError('usage: portable_host.py ROLE OPERATION')
        authorize(sys.argv[1], sys.argv[2])
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'REFUSED: {exc}', file=sys.stderr)
        return 78

if __name__ == '__main__':
    raise SystemExit(main())
