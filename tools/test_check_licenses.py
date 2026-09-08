# SPDX-License-Identifier: MPL-2.0
"""Negative controls for source, payload and package licensing boundaries."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from check_licenses import ROOT, scan_licenses
from check_distribution import files


class LicenseCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # A real release fixture catches disagreements across independent surfaces.
        for source in files(ROOT):
            destination = self.root / source.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    def issues(self):
        return scan_licenses(self.root, files(self.root))['issues']

    def test_release_has_complete_license_coverage(self):
        self.assertEqual(self.issues(), [])

    def test_new_file_type_requires_classification(self):
        (self.root / 'unreviewed.wasm').write_bytes(b'example')
        self.assertIn({'kind': 'unclassified-license', 'path': 'unreviewed.wasm'}, self.issues())

    def test_source_notice_is_required(self):
        (self.root / 'new.py').write_text('VALUE = 1\n')
        self.assertIn({'kind': 'missing-source-license', 'path': 'new.py'}, self.issues())

    def test_legal_text_tamper_is_detected(self):
        (self.root / 'LICENSE').write_text('altered terms\n')
        self.assertIn({'kind': 'changed-license-text', 'path': 'LICENSE'}, self.issues())

    def test_payload_cannot_drop_notices(self):
        path = self.root / 'fleet_kernel/release_files.json'
        names = json.loads(path.read_text())
        names.remove('COPYING.md')
        path.write_text(json.dumps(names))
        self.assertIn({'kind': 'payload-omits-license', 'path': 'COPYING.md'}, self.issues())

    def test_python_packaging_cannot_drop_notices(self):
        path = self.root / 'pyproject.toml'
        lines = path.read_text().splitlines()
        path.write_text('\n'.join(line for line in lines if not line.startswith('license-files =')))
        self.assertIn({'kind': 'python-package-omits-license', 'path': 'LICENSE'}, self.issues())


if __name__ == '__main__':
    unittest.main()
