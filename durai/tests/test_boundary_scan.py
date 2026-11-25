"""Sample: Run BoundaryTests. Check the glob rules, the classes of sample paths and that every tracked file has a class."""
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import boundary_scan  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        # Sample: Load the rules file. Hold the rules and the hidden goal name patterns.
        self.rules, self.hidden = boundary_scan.load_rules()

    def test_key_setter_is_never(self):
        # Sample: Classify durai/set-gemini-key.cmd. Check NEVER.
        self.assertEqual('NEVER', boundary_scan.classify('durai/set-gemini-key.cmd', self.rules))

    def test_shell_module_is_open(self):
        # Sample: Classify durai/durai/db.py. Check OPEN.
        self.assertEqual('OPEN', boundary_scan.classify('durai/durai/db.py', self.rules))

    def test_blueprint_is_paid_pack(self):
        # Sample: Classify durai/blueprints/idempotency/v1.sql. Check PAID_PACK.
        self.assertEqual('PAID_PACK', boundary_scan.classify('durai/blueprints/idempotency/v1.sql', self.rules))

    def test_latest_philosophy_is_free_and_old_is_paid(self):
        # Sample: Classify engineering-philosophy-v15.json as FREE_PACK and engineering-philosophy-v3.json as PAID_PACK.
        self.assertEqual('FREE_PACK', boundary_scan.classify('durai/design/engineering-philosophy-v15.json', self.rules))
        self.assertEqual('PAID_PACK', boundary_scan.classify('durai/design/engineering-philosophy-v3.json', self.rules))

    def test_unknown_path_has_no_class(self):
        # Sample: Classify new/file.txt. Check None.
        self.assertIsNone(boundary_scan.classify('new/file.txt', self.rules))

    def test_star_does_not_cross_folders(self):
        # Sample: Glob durai/*.md matches durai/a.md and does not match durai/x/a.md.
        pattern = boundary_scan.glob_to_regex('durai/*.md')
        self.assertTrue(pattern.match('durai/a.md'))
        self.assertFalse(pattern.match('durai/x/a.md'))

    def test_hidden_goal_file_in_public_tree_is_reported(self):
        # Sample: Scan a.py (OPEN) and durai/durai/goals-hidden.json (OPEN). Check the hidden goal file is reported.
        report = boundary_scan.scan(['durai/durai/a.py', 'durai/durai/goals-hidden.json'], self.rules, self.hidden)
        self.assertEqual(['durai/durai/goals-hidden.json'], report['hiddenGoalInPublic'])

    @unittest.skipUnless(shutil.which('git') and (ROOT / '.git').exists(), 'Needs a git checkout')
    def test_every_tracked_file_has_a_class(self):
        # Sample: Scan every tracked file. Check no file is unclassified and no hidden goal file is public.
        report = boundary_scan.scan(boundary_scan.tracked_files(ROOT), self.rules, self.hidden)
        self.assertEqual([], report['unclassified'][:20])
        self.assertEqual([], report['hiddenGoalInPublic'])


if __name__ == '__main__':
    unittest.main()
