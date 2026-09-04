"""Exact-byte release identity scoped to the public Company runtime."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]

def runtime_release_files() -> tuple[str, ...]:
    manifest = ROOT / 'fleet_kernel/release_files.json'
    paths = json.loads(manifest.read_text())
    if not isinstance(paths, list) or paths != sorted(set(paths)):
        raise ValueError('Public runtime inventory is invalid')
    for item in paths:
        if not isinstance(item, str) or Path(item).is_absolute() or '..' in Path(item).parts:
            raise ValueError('Public runtime inventory path is unsafe')
    return tuple(paths)

def runtime_release_manifest_bytes() -> bytes:
    records = []
    for relative in runtime_release_files():
        path = ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Public runtime inventory file missing or symlinked: {relative}')
        data = path.read_bytes()
        records.append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
    return json.dumps(records,sort_keys=True,separators=(',', ':')).encode()
