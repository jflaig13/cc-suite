# SPDX-License-Identifier: MPL-2.0
"""Negative controls for the source-only public distribution check."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from check_distribution import scan


class DistributionCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_valid_package_and_encoded_link(self):
        self.write('fleet_kernel/__init__.py', '')
        self.write('fleet_kernel/state.py', 'VALUE = 1\n')
        self.write('sample.py', 'from fleet_kernel.state import VALUE\n')
        self.write('docs/Quick start.md', '# Start\n')
        self.write('README.md', '[Start](docs/Quick%20start.md)\n')
        self.assertEqual(scan(self.root)['status'], 'PASS')

    def test_missing_module_and_link_fail(self):
        self.write('sample.py', 'import fleet_kernel.missing\n')
        self.write('README.md', '[Missing](docs/absent.md)\n')
        kinds = {issue['kind'] for issue in scan(self.root)['issues']}
        self.assertEqual(kinds, {'missing-internal-import', 'broken-local-link'})

    def test_secret_detection_reports_location_without_value(self):
        synthetic = 'gh' + 'p_' + 'a' * 36
        self.write('example.txt', 'first line\n' + synthetic + '\n')
        result = scan(self.root)
        self.assertEqual(result['issues'], [
            {'path': 'example.txt', 'line': 2, 'kind': 'github-token'}])
        self.assertNotIn(synthetic, str(result))

    def test_outside_symlink_is_rejected_without_reading(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'private.txt'
            target.write_text('not repository content')
            (self.root / 'outside.txt').symlink_to(target)
            self.assertEqual(scan(self.root)['issues'], [
                {'path': 'outside.txt', 'kind': 'outside-root-symlink'}])

    def test_inventory_detects_drift_after_review(self):
        content = '# Reviewed\n'
        path = self.write('README.md', content)
        self.write('provenance/example-source-inventory.json', json.dumps({
            'files': [{'path': 'README.md',
                       'output_sha256': hashlib.sha256(content.encode()).hexdigest()}]}))
        self.assertEqual(scan(self.root)['status'], 'PASS')
        path.write_text('# Changed after review\n')
        self.assertEqual(scan(self.root)['issues'], [{
            'path': 'provenance/example-source-inventory.json',
            'kind': 'inventory-hash-mismatch', 'target': 'README.md'}])


if __name__ == '__main__':
    unittest.main()
