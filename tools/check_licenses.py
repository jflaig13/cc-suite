#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Verify license coverage and legal files without importing application code."""
from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def classify(relative: str, rules: list[dict]) -> dict | None:
    return next((rule for rule in rules
                 if any(fnmatch.fnmatchcase(relative, pattern)
                        for pattern in rule['patterns'])), None)


def scan_licenses(root: Path, paths: list[Path]) -> dict:
    issues = []
    counts = {}
    try:
        policy = json.loads((root / 'provenance/license-map.json').read_text())
        if policy['schema_version'] != 1:
            raise ValueError('unsupported schema')
        for rule in policy['rules']:
            if not rule['patterns'] or not rule['kind'] or not rule['license']:
                raise ValueError('incomplete classification')
        for path in paths:
            relative = path.relative_to(root).as_posix()
            rule = classify(relative, policy['rules'])
            if rule is None:
                issues.append({'kind': 'unclassified-license', 'path': relative})
                continue
            counts[rule['kind']] = counts.get(rule['kind'], 0) + 1
            if rule['kind'] == 'software-with-notice':
                start = '\n'.join(path.read_text().splitlines()[:5])
                if 'SPDX-License-Identifier: MPL-2.0' not in start:
                    issues.append({'kind': 'missing-source-license', 'path': relative})
        for item in policy['canonical_license_texts']:
            path = root / item['path']
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                issues.append({'kind': 'changed-license-text', 'path': item['path']})
        required = set(policy['required_delivery_files'])
        required.update(path.relative_to(root).as_posix()
                        for path in (root / 'LICENSES/third-party').glob('*.txt'))
        for name in sorted(required):
            if not (root / name).is_file():
                issues.append({'kind': 'missing-license-delivery-file', 'path': name})
        payload = set(json.loads((root / 'fleet_kernel/release_files.json').read_text()))
        for name in sorted(required - payload):
            issues.append({'kind': 'payload-omits-license', 'path': name})
        project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
        if project.get('license') != 'MPL-2.0':
            issues.append({'kind': 'incorrect-python-license', 'path': 'pyproject.toml'})
        delivered = {path.relative_to(root).as_posix()
                     for pattern in project.get('license-files', [])
                     for path in root.glob(pattern) if path.is_file()}
        for name in sorted(required - delivered):
            issues.append({'kind': 'python-package-omits-license', 'path': name})
        for channel in ('agent', 'scribe'):
            name = f'channels/{channel}/package.json'
            if json.loads((root / name).read_text()).get('license') != 'MPL-2.0':
                issues.append({'kind': 'incorrect-channel-license', 'path': name})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        issues.append({'kind': 'invalid-license-policy', 'detail': str(exc)})
    return {'status': 'FAIL' if issues else 'PASS', 'classified_files': counts, 'issues': issues}


def main() -> int:
    from check_distribution import files
    result = scan_licenses(ROOT, files(ROOT))
    print(json.dumps(result, indent=2))
    return int(bool(result['issues']))


if __name__ == '__main__':
    sys.exit(main())
