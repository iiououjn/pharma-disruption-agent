"""Prevent unintentional changes to source files from the user-supplied project."""
import hashlib
import json
import unittest
from pathlib import Path


class OriginalIntegrityTests(unittest.TestCase):
    def test_original_files_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        info = json.loads((root / 'docs/ORIGINAL_FILE_INTEGRITY.json').read_text())
        original = info['original_file_sha256']
        self.assertEqual(len(original), info['number_original_files'])
        for path, expected in original.items():
            with self.subTest(original_file=path):
                self.assertEqual(hashlib.sha256((root / path).read_bytes()).hexdigest(), expected)
