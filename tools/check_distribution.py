#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Check a portable source checkout without loading or running its application code."""
from __future__ import annotations
import ast
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'build', 'dist', '.mypy_cache', 'node_modules'}
SECRET_PATTERNS = {
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'provider-key': re.compile(r'\bsk-(?:ant-api\d+-)?[A-Za-z0-9_-]{32,}\b'),
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    'aws-access-id': re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
    'slack-token': re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{20,}\b'),
    'private-home-path': re.compile('/' + r'Users/[A-Za-z0-9_.-]+(?:/|\b)'),
}

def files(root: Path):
    return sorted(p for p in root.rglob('*') if p.is_file()
                  and not any(part in IGNORED or part.endswith('.egg-info')
                              for part in p.relative_to(root).parts))

def scan(root: Path) -> dict:
    root = root.resolve()
    issues = []
    paths = files(root)
    for path in paths:
        rel = path.relative_to(root).as_posix()
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            issues.append({'path': rel, 'kind': 'outside-root-symlink'})
            continue
        if path.suffix in {'.pyc', '.pyo'}:
            issues.append({'path': rel, 'kind': 'generated-bytecode'})
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        for kind, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                issues.append({'path': rel, 'line': text.count('\n', 0, match.start()) + 1, 'kind': kind})
        if path.suffix == '.md':
            # Match ordinary inline Markdown links. Template prose and external URLs
            # are allowed; each relative repository target must resolve inside the tree.
            for target in re.findall(r'\[[^\]\n]*\]\(([^)\n]+)\)', text):
                target = target.strip().split(' "', 1)[0].strip('<>')
                parsed = urlsplit(target)
                if parsed.scheme or target.startswith(('//', '#')):
                    continue
                link_path = unquote(parsed.path)
                dest = (path.parent / link_path).resolve()
                if not dest.is_relative_to(root) or not dest.exists():
                    issues.append({'path': rel, 'kind': 'broken-local-link', 'target': target})
        if path.suffix == '.py':
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError as exc:
                issues.append({'path': rel, 'line': exc.lineno, 'kind': 'python-syntax'})
                continue
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules = [node.module]
                for module in modules:
                    if module == 'fleet_kernel' or module.startswith('fleet_kernel.'):
                        dest = root.joinpath(*module.split('.'))
                        if not dest.with_suffix('.py').is_file() and not (dest / '__init__.py').is_file():
                            issues.append({'path': rel, 'line': node.lineno,
                                           'kind': 'missing-internal-import', 'module': module})
    for manifest in sorted((root / 'provenance').glob('*inventory.json')):
        rel = manifest.relative_to(root).as_posix()
        try:
            data = json.loads(manifest.read_text())
            entries = data.get('files', data.get('inventory', []))
            for entry in entries:
                expected = entry.get('output_sha256')
                target = entry.get('output_path', entry.get('path'))
                if not expected:
                    continue  # Excluded reference files have no public output.
                dest = (root / target).resolve() if target else None
                if dest is None or not dest.is_relative_to(root) or not dest.is_file():
                    issues.append({'path': rel, 'kind': 'missing-inventoried-output',
                                   'target': target})
                elif hashlib.sha256(dest.read_bytes()).hexdigest() != expected:
                    issues.append({'path': rel, 'kind': 'inventory-hash-mismatch',
                                   'target': target})
        except (ValueError, TypeError, AttributeError):
            issues.append({'path': rel, 'kind': 'invalid-source-inventory'})
    return {'files_checked': len(paths), 'issues': issues,
            'status': 'PASS' if not issues else 'FAIL'}

def main() -> int:
    result = scan(ROOT)
    from check_licenses import scan_licenses
    licensing = scan_licenses(ROOT, files(ROOT))
    result['issues'].extend(licensing['issues'])
    result['status'] = 'FAIL' if result['issues'] else 'PASS'
    print(json.dumps(result, indent=2))
    return 1 if result['issues'] else 0

if __name__ == '__main__':
    raise SystemExit(main())
