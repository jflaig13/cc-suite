"""Keep synthetic test state out of shared system temporary directories.

CC_SUITE_TEST_STATE_ROOT may select an existing secure parent; otherwise HOME
is used. Each session creates and cleans up its own private child directory.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile

import pytest


def _secure_test_parent(path: Path) -> Path:
    if not path.is_absolute() or '..' in path.parts:
        raise pytest.UsageError('Test state parent must be an absolute traversal-free path')
    for current in reversed((path, *path.parents)):
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise pytest.UsageError(f'Test state parent is unavailable: {current}') from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid not in {0, os.getuid()}
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise pytest.UsageError(
                f'Insecure test state ancestry: {current}; choose an owner-controlled '
                'HOME or CC_SUITE_TEST_STATE_ROOT without symlinks or group/world writes'
            )
    return path


def pytest_configure(config):
    # A user-specified basetemp is never erased by this hook. Its existing
    # secure parent hosts a fresh session instead, preserving pytest isolation.
    requested = config.option.basetemp
    if requested is not None:
        parent = Path(requested).absolute().parent
    else:
        configured = os.environ.get('CC_SUITE_TEST_STATE_ROOT') or os.environ.get('HOME')
        if not configured:
            raise pytest.UsageError('HOME or CC_SUITE_TEST_STATE_ROOT is required for private test storage')
        parent = Path(configured)
    _secure_test_parent(parent)
    session = tempfile.TemporaryDirectory(prefix='.cc-suite-tests-', dir=parent)
    root = Path(session.name)
    def cleanup():
        session.cleanup()

    config.add_cleanup(cleanup)
    # macOS can inherit a directory's group independently of the process egid.
    # Only this newly created synthetic directory is changed.
    os.chown(root, os.geteuid(), os.getegid())
    root.chmod(0o700)
    config.option.basetemp = str(root / 'pytest')
