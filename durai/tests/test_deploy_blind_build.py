"""Sample: Call down with project trade-pilot. Check ValueError and no docker command."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import deploy_blind_build  # noqa: E402


class DeployTests(unittest.TestCase):
    def test_down_refuses_other_projects(self):
        # Sample: Call down with trade-pilot. Check ValueError and no docker command.
        with mock.patch.object(deploy_blind_build, 'docker') as docker:
            for project in ('trade-pilot', 'durai-local-db', 'durai-app-', 'durai-app-x_y', 'durai-app-ok;rm'):
                with self.assertRaises(ValueError):
                    deploy_blind_build.down(project)
            docker.assert_not_called()
        self.assertEqual('durai-app-deepseek-r2', deploy_blind_build.check_project('durai-app-deepseek-r2'))


if __name__ == '__main__':
    unittest.main()
