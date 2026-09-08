#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2026 Mise Inc.
"""Check or insert offline license notices in the declared, unminified Bun bundles.

The manifest is a reviewed snapshot, not a dependency resolver. A changed package
set, lock version, archive integrity, or license text fails closed until that
snapshot is reviewed. This tool never installs, imports, or executes packages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "provenance/third-party-licenses.json"
BEGIN = b"// BEGIN CC-SUITE GENERATED LICENSE NOTICES\n"
END = b"// END CC-SUITE GENERATED LICENSE NOTICES\n"
INTRINSIC = (b"/*! SPDX-License-Identifier: MPL-2.0\n"
             b" * Source: https://github.com/jflaig13/cc-suite\n"
             b" * License: https://mozilla.org/MPL/2.0/\n */")
PACKAGE_NAME = re.compile(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+\Z")


class LicenseError(ValueError):
    """The reviewed license snapshot does not describe the supplied bundle."""


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise LicenseError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def inside(root: Path, relative: str) -> Path:
    """Only use existing regular files inside this source checkout."""
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise LicenseError(f"not a repository-relative file: {relative}")
    path = (root / raw).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise LicenseError(f"missing or outside-repository file: {relative}")
    return path


def jsonc(text: str) -> dict:
    """Parse Bun's JSON-with-comments/trailing-commas without altering strings."""
    cleaned = list(text)
    index = 0
    while index < len(text):
        if text[index] == '"':
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                elif text[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            cleaned[index:end] = " " * (end - index)
            index = end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise LicenseError("unterminated lockfile comment")
            end += 2
            cleaned[index:end] = ["\n" if char == "\n" else " " for char in text[index:end]]
            index = end
        else:
            index += 1
    text = "".join(cleaned)
    index = 0
    while index < len(text):
        if text[index] == '"':
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                elif text[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
        else:
            if text[index] == ",":
                next_index = index + 1
                while next_index < len(text) and text[next_index].isspace():
                    next_index += 1
                if next_index < len(text) and text[next_index] in "]}":
                    cleaned[index] = " "
            index += 1
    result = json.loads("".join(cleaned), object_pairs_hook=unique_object)
    if not isinstance(result, dict):
        raise LicenseError("expected a lockfile object")
    return result


def load_manifest(root: Path) -> dict:
    manifest = json.loads(inside(root, MANIFEST).read_text(encoding="utf-8"),
                          object_pairs_hook=unique_object)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise LicenseError("unsupported license manifest schema")
    if manifest.get("source_repository") != "https://github.com/jflaig13/cc-suite":
        raise LicenseError("unexpected source repository in license manifest")
    if not isinstance(manifest.get("bundles"), list) or not manifest["bundles"]:
        raise LicenseError("license manifest must declare at least one bundle")
    names = set()
    for component in manifest["components"]:
        name = component["name"]
        if not PACKAGE_NAME.fullmatch(name) or name in names:
            raise LicenseError(f"invalid or duplicate component: {name}")
        names.add(name)
        if not component["license_files"]:
            raise LicenseError(f"missing license text for {name}")
        paths = set()
        for item in component["license_files"]:
            path = item["path"]
            if path in paths or not path.startswith("LICENSES/third-party/"):
                raise LicenseError(f"invalid or duplicate license text path: {path}")
            paths.add(path)
            data = inside(root, path).read_bytes()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise LicenseError(f"license text hash mismatch: {path}")
            if not data.decode("utf-8").strip():
                raise LicenseError(f"empty license text: {path}")
    bundle_paths = set()
    used = set()
    for bundle in manifest["bundles"]:
        path = bundle["path"]
        if path in bundle_paths or not path.endswith(".bundle.js"):
            raise LicenseError(f"invalid or duplicate bundle: {path}")
        bundle_paths.add(path)
        if bundle["notice_mode"] not in {"managed", "intrinsic"}:
            raise LicenseError(f"unsupported bundle notice mode: {path}")
        required = bundle["components"]
        if len(required) != len(set(required)) or not set(required) <= names:
            raise LicenseError(f"unclassified or duplicate components in {path}")
        if bundle["notice_mode"] == "intrinsic" and required:
            raise LicenseError(f"intrinsic notice cannot cover third-party components: {path}")
        used.update(required)
        inside(root, path)
        inside(root, bundle["lockfile"])
    if used != names:
        raise LicenseError("license manifest contains components absent from all declared bundles")
    return manifest


def split_notice(data: bytes) -> tuple[bytes, bytes, bytes]:
    """Return shebang, unmanaged body, old notice; never rewrite body bytes."""
    shebang = b""
    if data.startswith(b"#!"):
        newline = data.find(b"\n")
        if newline < 0:
            raise LicenseError("bundle shebang has no terminating newline")
        shebang, data = data[:newline + 1], data[newline + 1:]
    if BEGIN not in data and END not in data:
        return shebang, data, b""
    if not data.startswith(BEGIN) or data.count(BEGIN) != 1 or data.count(END) != 1:
        raise LicenseError("malformed, duplicate, or misplaced managed license notice")
    end = data.index(END) + len(END)
    notice = data[:end]
    if any(not line.startswith(b"//") for line in notice.splitlines()):
        raise LicenseError("managed license notice contains a non-comment line")
    return shebang, data[end:], notice


def bundled_components(body: bytes) -> set[str]:
    """Read Bun source-path markers; fail on unsupported or ambiguous layouts.

    This deliberately requires unminified source markers. It cannot identify
    third-party code whose provenance has been removed from the generated file.
    """
    found = set()
    for line in body.decode("utf-8").splitlines():
        if not line.startswith("// ") or "node_modules/" not in line:
            continue
        path = line[3:].strip()
        if path.count("node_modules/") != 1:
            raise LicenseError(f"unsupported nested dependency source marker: {path}")
        suffix = path.split("node_modules/", 1)[1]
        parts = suffix.split("/")
        length = 2 if suffix.startswith("@") else 1
        name = "/".join(parts[:length])
        if len(parts) <= length or not PACKAGE_NAME.fullmatch(name) or name.startswith("."):
            raise LicenseError(f"unsupported dependency source marker: {path}")
        found.add(name)
    return found


def verify_lock(root: Path, bundle: dict, components: dict) -> None:
    lock = jsonc(inside(root, bundle["lockfile"]).read_text(encoding="utf-8"))
    if lock.get("lockfileVersion") != 1 or not isinstance(lock.get("packages"), dict):
        raise LicenseError(f"unsupported Bun lockfile: {bundle['lockfile']}")
    packages = lock["packages"]
    for name in bundle["components"]:
        component = components[name]
        entry = packages.get(name)
        if not isinstance(entry, list) or len(entry) != 4:
            raise LicenseError(f"missing or unsupported lock entry: {name}")
        if entry[0] != f"{name}@{component['version']}":
            raise LicenseError(f"locked version mismatch: {name}")
        if entry[3] != component["integrity"]:
            raise LicenseError(f"locked archive integrity mismatch: {name}")
        versions = {
            value[0] for value in packages.values()
            if isinstance(value, list) and value and isinstance(value[0], str)
            and value[0].startswith(name + "@")
        }
        if versions != {entry[0]}:
            raise LicenseError(f"multiple locked versions need explicit source mapping: {name}")


def render_notice(root: Path, manifest: dict, bundle: dict) -> bytes:
    components = {item["name"]: item for item in manifest["components"]}
    lines = [
        "Generated by tools/license_bundles.py; edit the reviewed license sources, not this notice.",
        "Mise-owned portions: Copyright (c) 2026 Mise Inc.",
        "This Source Code Form is subject to the terms of the Mozilla Public",
        "License, v. 2.0. If a copy of the MPL was not distributed with this",
        "file, You can obtain one at https://mozilla.org/MPL/2.0/.",
        "Source Code Form: " + manifest["source_repository"],
        "Third-party portions retain the following upstream licenses and notices.",
    ]
    for name in sorted(bundle["components"]):
        component = components[name]
        lines.extend(["", f"{name}@{component['version']} ({component['license']})",
                      "Source archive: " + component["tarball_url"]])
        for item in component["license_files"]:
            lines.append("")
            # Each physical line is a line comment, including any */ sequences
            # and Unicode line separators from upstream. No text can close it.
            lines.extend(inside(root, item["path"]).read_text(encoding="utf-8").splitlines())
    physical_lines = [physical for line in lines for physical in (line.splitlines() or [""])]
    comments = "".join("//" + (" " + line if line else "") + "\n" for line in physical_lines)
    return BEGIN + comments.encode("utf-8") + END


def process(root: Path, paths: list[str], write: bool = False) -> dict:
    root = root.resolve()
    manifest = load_manifest(root)
    declared = {item["path"]: item for item in manifest["bundles"]}
    selected = []
    for value in paths:
        path = Path(value).resolve()
        if not path.is_relative_to(root):
            raise LicenseError(f"bundle is outside the repository: {value}")
        relative = path.relative_to(root).as_posix()
        if relative not in declared:
            raise LicenseError(f"undeclared bundle: {relative}")
        if relative not in selected:
            selected.append(relative)
    selected = sorted(selected or declared)
    components = {item["name"]: item for item in manifest["components"]}
    prepared = []
    for relative in selected:
        bundle = declared[relative]
        path = inside(root, relative)
        original = path.read_bytes()
        shebang, body, old_notice = split_notice(original)
        actual = bundled_components(body)
        expected = set(bundle["components"])
        if actual != expected:
            raise LicenseError(f"bundle component mismatch for {relative}: "
                               f"unclassified={sorted(actual - expected)}, absent={sorted(expected - actual)}")
        verify_lock(root, bundle, components)
        if bundle["notice_mode"] == "intrinsic":
            # Scribe's frozen validator rebuilds the entrypoint directly with
            # Bun. Its /*! source notice survives that build without a separate
            # postprocessor, keeping the admitted payload exactly reproducible.
            if old_notice or body.count(INTRINSIC) != 1:
                raise LicenseError(f"missing or altered intrinsic license notice: {relative}; "
                                   "rebuild from the licensed source entrypoint")
            desired = original
        else:
            desired = shebang + render_notice(root, manifest, bundle) + body
        prepared.append((path, relative, original, desired))
    # Validate every selected input before writing any bundle.
    stale = [relative for _, relative, original, desired in prepared if original != desired]
    if stale and not write:
        raise LicenseError("missing or stale bundle license notice: " + ", ".join(stale))
    if write:
        for path, _, original, desired in prepared:
            if original != desired:
                path.write_bytes(desired)
    return {"status": "PASS", "bundles_checked": selected, "bundles_updated": stale if write else []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="insert or replace managed notices")
    mode.add_argument("--check", action="store_true", help="fail if notices are missing or stale")
    parser.add_argument("paths", nargs="*", help="bundle paths relative to CWD; default: all declared bundles")
    args = parser.parse_args()
    try:
        result = process(ROOT, args.paths, write=args.write)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
