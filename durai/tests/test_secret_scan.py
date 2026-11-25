"""Sample: Run SecretScanTests. Check sample keys are found, placeholders are not, and no value is printed."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import secret_scan  # noqa: E402

FAKE_GOOGLE = 'AIza' + 'A' * 35
FAKE_SK = 'sk-' + 'b' * 32


class SecretScanTests(unittest.TestCase):
    def test_google_key_is_found(self):
        # Sample: Scan the text apiKey = <39 character key starting AIza>. Check rule GOOGLE_API_KEY on line 1.
        found = secret_scan.scan_text('key = "' + FAKE_GOOGLE + '"')
        self.assertIn(('GOOGLE_API_KEY', 1), [(item['rule'], item['line']) for item in found])

    def test_sk_key_is_found_on_the_right_line(self):
        # Sample: Scan two lines. Put an sk- key on line 2. Check SK_KEY on line 2.
        found = secret_scan.scan_text('hello\ntoken ' + FAKE_SK)
        self.assertIn(('SK_KEY', 2), [(item['rule'], item['line']) for item in found])

    def test_placeholder_is_ignored(self):
        # Sample: Scan "apiKey": "your_key_goes_here_12345". Check no finding.
        self.assertEqual([], secret_scan.scan_text('"apiKey": "your_key_goes_here_12345"'))

    def test_findings_hold_no_secret_value(self):
        # Sample: Scan a file with a fake key. Check the JSON of the findings does not contain the key.
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(['git', 'init', '-q', folder], check=True)
            Path(folder, 'a.txt').write_text('x = "' + FAKE_GOOGLE + '"\n', encoding='utf-8')
            subprocess.run(['git', '-C', folder, 'add', 'a.txt'], check=True)
            found = secret_scan.scan_tree(folder)
            self.assertEqual(['a.txt'], [item['path'] for item in found])
            self.assertNotIn(FAKE_GOOGLE, json.dumps(found))

    def test_test_fixture_values_are_ignored(self):
        # Sample: Scan "apiKey": "sk-test-0000000000000000". Check no finding.
        self.assertEqual([], secret_scan.scan_text('"apiKey": "sk-test-0000000000000000"'))

    def test_history_finds_a_key_removed_later(self):
        # Sample: Commit a file with a fake key. Commit its removal. Scan the history. Check one finding for the first commit.
        with tempfile.TemporaryDirectory() as folder:
            run = lambda *args: subprocess.run(['git', '-C', folder, '-c', 'user.name=t', '-c', 'user.email=t@example.org', *args],
                                               check=True, capture_output=True)
            subprocess.run(['git', 'init', '-q', folder], check=True)
            Path(folder, 'a.txt').write_text('x = "' + FAKE_GOOGLE + '"\n', encoding='utf-8')
            run('add', 'a.txt')
            run('commit', '-q', '-m', 'add')
            Path(folder, 'a.txt').write_text('x = 1\n', encoding='utf-8')
            run('commit', '-q', '-am', 'remove')
            self.assertEqual([], secret_scan.scan_tree(folder))
            found = secret_scan.scan_history(folder)
            self.assertEqual(['GOOGLE_API_KEY'], [item['rule'] for item in found])
            self.assertNotIn(FAKE_GOOGLE, json.dumps(found))


if __name__ == '__main__':
    unittest.main()
