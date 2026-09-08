#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Compare legal files in built wheels/sdists with the source release."""
from __future__ import annotations

from email.parser import Parser
import json
from pathlib import Path
import sys
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def inspect_archive(archive: Path, root: Path = ROOT) -> list[str]:
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    required = {path.relative_to(root).as_posix(): path.read_bytes()
                for pattern in project['license-files']
                for path in root.glob(pattern) if path.is_file()}
    if archive.suffix == '.whl':
        with zipfile.ZipFile(archive) as handle:
            contents = {name: handle.read(name) for name in handle.namelist()
                        if not name.endswith('/')}
        metadata_names = [name for name in contents if name.endswith('.dist-info/METADATA')]
        if len(metadata_names) != 1:
            return ['wheel metadata missing or ambiguous']
        metadata_name = metadata_names[0]
        prefix = metadata_name.rsplit('/', 1)[0] + '/licenses/'
    else:
        with tarfile.open(archive, 'r:*') as handle:
            contents = {member.name: handle.extractfile(member).read()
                        for member in handle.getmembers() if member.isfile()}
        metadata_names = [name for name in contents
                          if name.endswith('/PKG-INFO') and name.count('/') == 1]
        if len(metadata_names) != 1:
            return ['sdist metadata missing or ambiguous']
        metadata_name = metadata_names[0]
        prefix = metadata_name.rsplit('/', 1)[0] + '/'
    issues = []
    metadata = Parser().parsestr(contents[metadata_name].decode())
    if metadata.get('License-Expression') != 'MPL-2.0':
        issues.append('incorrect License-Expression')
    if set(metadata.get_all('License-File', [])) != set(required):
        issues.append('License-File metadata does not match the release')
    for relative, expected in required.items():
        if contents.get(prefix + relative) != expected:
            issues.append(f'legal file absent or changed: {relative}')
    sources = [name for name in contents if name.endswith('.py') and '/licenses/' not in name]
    if not sources:
        issues.append('archive contains no Python source')
    for name in sources:
        if name.endswith('/setup.py'):
            continue  # Generated setuptools support file, not project source.
        if 'SPDX-License-Identifier: MPL-2.0' not in '\n'.join(contents[name].decode().splitlines()[:5]):
            issues.append(f'Python source notice missing: {name}')
    return issues


def main() -> int:
    archives = [Path(name) for name in sys.argv[1:]]
    if not archives:
        raise SystemExit('Pass built .whl and .tar.gz paths')
    results = [{'archive': path.name, 'issues': inspect_archive(path)} for path in archives]
    print(json.dumps(results, indent=2))
    return int(any(result['issues'] for result in results))


if __name__ == '__main__':
    sys.exit(main())
