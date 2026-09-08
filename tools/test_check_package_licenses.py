# SPDX-License-Identifier: MPL-2.0
"""Prove archive inspection catches delivery failures, independent of setuptools."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from check_package_licenses import inspect_archive


class PackageLicenseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'pyproject.toml').write_text(
            '[project]\nlicense="MPL-2.0"\nlicense-files=["LICENSE"]\n')
        (self.root / 'LICENSE').write_text('expected license text\n')

    def wheel(self, *, license_text='expected license text\n', expression='MPL-2.0'):
        archive = self.root / 'example.whl'
        with zipfile.ZipFile(archive, 'w') as handle:
            handle.writestr('example/__init__.py', '# SPDX-License-Identifier: MPL-2.0\n')
            handle.writestr('example.dist-info/METADATA',
                            f'Metadata-Version: 2.4\nLicense-Expression: {expression}\nLicense-File: LICENSE\n')
            if license_text is not None:
                handle.writestr('example.dist-info/licenses/LICENSE', license_text)
        return archive

    def test_complete_archive_passes(self):
        self.assertEqual(inspect_archive(self.wheel(), self.root), [])

    def test_missing_or_changed_license_fails(self):
        for text in (None, 'truncated terms'):
            with self.subTest(text=text):
                self.assertIn('legal file absent or changed: LICENSE',
                              inspect_archive(self.wheel(license_text=text), self.root))

    def test_old_metadata_does_not_pass(self):
        self.assertIn('incorrect License-Expression',
                      inspect_archive(self.wheel(expression='CC-BY-SA-4.0'), self.root))


if __name__ == '__main__':
    unittest.main()
