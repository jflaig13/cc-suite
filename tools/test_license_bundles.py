# SPDX-License-Identifier: MPL-2.0
# Copyright (c) 2026 Mise Inc.
"""Regression coverage for offline bundle notices and failure-before-write."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

import license_bundles as licenses


class BundleLicenseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source_text = b"Copyright Example Contributors\nPermission text. */ is harmless here.\n"
        self.license_path = "LICENSES/third-party/example-1.0.0-LICENSE.txt"
        self.write(self.license_path, self.source_text)
        self.agent = "channels/agent/channel.bundle.js"
        self.scribe = "channels/scribe/webhook.bundle.js"
        self.agent_body = b"#!/usr/bin/env bun\r\n// @bun\n// node_modules/example/index.js\nvar a = 1;\n"
        self.scribe_body = b"#!/usr/bin/env bun\n// @bun\n" + licenses.INTRINSIC + b"\nvar b = 2;\n"
        self.write(self.agent, self.agent_body)
        self.write(self.scribe, self.scribe_body)
        self.lock = {"lockfileVersion": 1, "packages": {
            "example": ["example@1.0.0", "", {}, "sha512-reviewed-archive"]}}
        self.write("channels/agent/bun.lock", json.dumps(self.lock))
        self.write("channels/scribe/bun.lock", '{"lockfileVersion":1,"packages":{}}')
        self.manifest = {
            "schema_version": 1,
            "source_repository": "https://github.com/jflaig13/cc-suite",
            "bundles": [
                {"path": self.agent, "lockfile": "channels/agent/bun.lock",
                 "notice_mode": "managed", "components": ["example"]},
                {"path": self.scribe, "lockfile": "channels/scribe/bun.lock",
                 "notice_mode": "intrinsic", "components": []}],
            "components": [{
                "name": "example", "version": "1.0.0", "license": "MIT",
                "metadata_url": "https://registry.npmjs.org/example/1.0.0",
                "tarball_url": "https://registry.npmjs.org/example/-/example-1.0.0.tgz",
                "integrity": "sha512-reviewed-archive",
                "license_files": [{"path": self.license_path,
                                   "sha256": hashlib.sha256(self.source_text).hexdigest(),
                                   "archive_path": "package/LICENSE"}]}],
        }
        self.save_manifest()

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return path

    def save_manifest(self):
        self.write(licenses.MANIFEST, json.dumps(self.manifest))

    def check(self, write=False, paths=None):
        return licenses.process(self.root, paths or [], write=write)

    def test_missing_notice_fails_without_mutating_bundle(self):
        with self.assertRaisesRegex(licenses.LicenseError, "missing or stale"):
            self.check()
        self.assertEqual((self.root / self.agent).read_bytes(), self.agent_body)

    def test_write_is_idempotent_and_preserves_shebang_and_code_exactly(self):
        self.assertEqual(self.check(write=True)["bundles_updated"], [self.agent])
        output = (self.root / self.agent).read_bytes()
        shebang, body, notice = licenses.split_notice(output)
        self.assertEqual(shebang + body, self.agent_body)
        self.assertTrue(output.startswith(b"#!/usr/bin/env bun\r\n"))
        self.assertIn(b"// Permission text. */ is harmless here.", notice)
        self.assertEqual(self.check(write=True)["bundles_updated"], [])
        self.assertEqual((self.root / self.agent).read_bytes(), output)
        self.assertEqual(self.check()["status"], "PASS")

    def test_scribe_intrinsic_notice_is_not_rewritten(self):
        self.check(write=True)
        self.assertEqual((self.root / self.scribe).read_bytes(), self.scribe_body)

    def test_missing_intrinsic_notice_fails_before_other_bundle_is_written(self):
        self.write(self.scribe, self.scribe_body.replace(licenses.INTRINSIC, b""))
        with self.assertRaisesRegex(licenses.LicenseError, "intrinsic license notice"):
            self.check(write=True)
        self.assertEqual((self.root / self.agent).read_bytes(), self.agent_body)

    def test_tampered_intrinsic_notice_fails(self):
        self.write(self.scribe, self.scribe_body.replace(b"MPL-2.0", b"MIT"))
        with self.assertRaisesRegex(licenses.LicenseError, "intrinsic license notice"):
            self.check(write=True)

    def test_unclassified_dependency_fails_and_does_not_write(self):
        changed = self.agent_body + b"// node_modules/unreviewed/index.js\nvar c = 3;\n"
        self.write(self.agent, changed)
        with self.assertRaisesRegex(licenses.LicenseError, "unclassified=.*unreviewed"):
            self.check(write=True)
        self.assertEqual((self.root / self.agent).read_bytes(), changed)

    def test_new_scribe_dependency_cannot_hide_behind_intrinsic_notice(self):
        self.write(self.scribe, self.scribe_body + b"// node_modules/example/index.js\n")
        with self.assertRaisesRegex(licenses.LicenseError, "unclassified=.*example"):
            self.check(write=True)

    def test_removed_source_markers_fail_closed(self):
        self.write(self.agent, b"var a=1;")
        with self.assertRaisesRegex(licenses.LicenseError, "absent=.*example"):
            self.check(write=True)

    def test_license_text_tampering_is_detected(self):
        self.write(self.license_path, self.source_text.replace(b"Permission", b"Changed"))
        with self.assertRaisesRegex(licenses.LicenseError, "license text hash mismatch"):
            self.check(write=True)

    def test_missing_license_text_is_detected(self):
        (self.root / self.license_path).unlink()
        with self.assertRaisesRegex(licenses.LicenseError, "missing or outside-repository"):
            self.check(write=True)

    def test_changed_locked_version_is_detected(self):
        self.lock["packages"]["example"][0] = "example@2.0.0"
        self.write("channels/agent/bun.lock", json.dumps(self.lock))
        with self.assertRaisesRegex(licenses.LicenseError, "locked version mismatch"):
            self.check(write=True)

    def test_changed_locked_integrity_is_detected(self):
        self.lock["packages"]["example"][3] = "sha512-other-archive"
        self.write("channels/agent/bun.lock", json.dumps(self.lock))
        with self.assertRaisesRegex(licenses.LicenseError, "locked archive integrity mismatch"):
            self.check(write=True)

    def test_multiple_locked_versions_are_not_silently_conflated(self):
        self.lock["packages"]["other/example"] = ["example@2.0.0", "", {}, "sha512-another"]
        self.write("channels/agent/bun.lock", json.dumps(self.lock))
        with self.assertRaisesRegex(licenses.LicenseError, "multiple locked versions"):
            self.check(write=True)

    def test_stale_managed_notice_is_replaced_without_changing_code(self):
        self.check(write=True)
        changed = (self.root / self.agent).read_bytes().replace(b"Mise-owned portions:", b"Stale wording:")
        self.write(self.agent, changed)
        with self.assertRaisesRegex(licenses.LicenseError, "missing or stale"):
            self.check()
        self.check(write=True)
        shebang, body, _ = licenses.split_notice((self.root / self.agent).read_bytes())
        self.assertEqual(shebang + body, self.agent_body)

    def test_malformed_or_duplicate_managed_blocks_are_not_deleted(self):
        for extra in (licenses.BEGIN, licenses.END, licenses.BEGIN + licenses.END + licenses.BEGIN):
            with self.subTest(extra=extra):
                original = self.agent_body + extra
                self.write(self.agent, original)
                with self.assertRaisesRegex(licenses.LicenseError, "managed license notice"):
                    self.check(write=True)
                self.assertEqual((self.root / self.agent).read_bytes(), original)

    def test_a_non_comment_line_inside_managed_notice_is_not_deleted(self):
        self.write(self.agent, licenses.BEGIN + b"var important = 1;\n" + licenses.END + self.agent_body)
        with self.assertRaisesRegex(licenses.LicenseError, "non-comment line"):
            self.check(write=True)

    def test_nested_dependency_layout_requires_review(self):
        self.write(self.agent, b"// node_modules/other/node_modules/example/index.js\n")
        with self.assertRaisesRegex(licenses.LicenseError, "unsupported nested"):
            self.check(write=True)

    def test_scoped_dependency_markers_are_identified(self):
        self.assertEqual(licenses.bundled_components(
            b"// ../node_modules/@scope/example/lib.js\n// node_modules/other/index.js\n"
        ), {"@scope/example", "other"})

    def test_jsonc_comments_and_trailing_commas_preserve_string_contents(self):
        sample = r'''{
          // A comment
          "url": "https://example.invalid/a,}",
          "quote": "\\\"/* not a comment */",
          "list": [1, /* another comment */ 2,],
        }'''
        data = licenses.jsonc(sample)
        self.assertEqual(data["url"], "https://example.invalid/a,}")
        self.assertEqual(data["list"], [1, 2])
        self.assertIn("/* not a comment */", data["quote"])

    def test_duplicate_lock_keys_are_not_silently_overwritten(self):
        with self.assertRaisesRegex(licenses.LicenseError, "duplicate JSON key"):
            licenses.jsonc('{"packages":{},"packages":{"example":[]}}')

    def test_unicode_line_separators_in_license_text_stay_commented(self):
        content = "Copyright Example\nPermission text.\u2028throw new Error('must stay a comment');\n".encode()
        self.write(self.license_path, content)
        self.manifest["components"][0]["license_files"][0]["sha256"] = hashlib.sha256(content).hexdigest()
        self.save_manifest()
        self.check(write=True)
        _, _, notice = licenses.split_notice((self.root / self.agent).read_bytes())
        self.assertIn(b"// throw new Error('must stay a comment');\n", notice)

    def test_manifest_path_cannot_escape_repository(self):
        self.manifest["components"][0]["license_files"][0]["path"] = "LICENSES/third-party/../../../outside"
        self.save_manifest()
        with self.assertRaisesRegex(licenses.LicenseError, "not a repository-relative"):
            self.check(write=True)

    def test_duplicate_manifest_component_is_rejected(self):
        self.manifest["components"].append(self.manifest["components"][0])
        self.save_manifest()
        with self.assertRaisesRegex(licenses.LicenseError, "duplicate component"):
            self.check(write=True)

    def test_cli_uses_channel_cwd_and_nonzero_check_failure(self):
        tool = self.root / "tools/license_bundles.py"
        tool.parent.mkdir()
        shutil.copyfile(Path(licenses.__file__), tool)
        command = [sys.executable, "-B", str(tool)]
        cwd = self.root / "channels/agent"
        before = subprocess.run(command + ["--check", "channel.bundle.js"], cwd=cwd, capture_output=True)
        self.assertEqual(before.returncode, 1)
        written = subprocess.run(command + ["--write", "channel.bundle.js"], cwd=cwd, capture_output=True)
        self.assertEqual(written.returncode, 0, written.stderr)
        after = subprocess.run(command + ["--check", "channel.bundle.js"], cwd=cwd, capture_output=True)
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertEqual(json.loads(after.stdout)["bundles_checked"], [self.agent])


if __name__ == "__main__":
    unittest.main()
