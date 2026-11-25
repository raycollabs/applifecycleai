"""Sample: Run PlansDirTests. Check the env override, the checkout default and the human reviewer list."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.plans_dir import ROOT, human_reviewers, plans_dir  # noqa: E402


class PlansDirTests(unittest.TestCase):
    def test_env_sets_the_folder(self):
        # Sample: Set DURAI_PLANS_DIR to /work/plans. Check plans_dir returns /work/plans.
        with mock.patch.dict(os.environ, {'DURAI_PLANS_DIR': '/work/plans'}):
            self.assertEqual(Path('/work/plans'), plans_dir())

    def test_default_is_above_the_checkout(self):
        # Sample: Remove DURAI_PLANS_DIR. Check plans_dir returns the folder two levels above the durai folder.
        with mock.patch.dict(os.environ):
            os.environ.pop('DURAI_PLANS_DIR', None)
            self.assertEqual(ROOT.parent.parent, plans_dir())

    def test_reviewers_without_env(self):
        # Sample: Remove DURAI_HUMAN_REVIEWERS. Check only durai-human-lead and operator.
        with mock.patch.dict(os.environ):
            os.environ.pop('DURAI_HUMAN_REVIEWERS', None)
            self.assertEqual(('durai-human-lead', 'operator'), human_reviewers())

    def test_reviewers_from_env_are_lower_cased(self):
        # Sample: Set DURAI_HUMAN_REVIEWERS to "Ana, Bo,". Check ana and bo are added and the empty item is dropped.
        with mock.patch.dict(os.environ, {'DURAI_HUMAN_REVIEWERS': 'Ana, Bo,'}):
            self.assertEqual(('durai-human-lead', 'operator', 'ana', 'bo'), human_reviewers())


if __name__ == '__main__':
    unittest.main()
