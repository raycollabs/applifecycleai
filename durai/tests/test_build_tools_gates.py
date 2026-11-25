"""Sample: Test DEPLOYED check_app gate, digest invalidation, and read-only shell manifest enforcement."""
import json
from pathlib import Path
import tempfile
import unittest
from durai.build_tools import BuildTools, ToolError
from tools import deploy_next_build


class BuildToolsGateTests(unittest.TestCase):
    def test_deployed_requires_passing_check_app_and_invalidates_on_edit(self):
        # Sample: Submit DESIGN, PLAN, IMPLEMENTATION, TESTS. Submit DEPLOYED before check_app. Refuse. Run passing check_app, edit file, submit DEPLOYED. Refuse. Re-run check_app, submit DEPLOYED. Accept with digest.
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            tools = BuildTools(ws)
            tools.get_sample('S-TRADE-BOOK')
            tools.write_file('docs/design.md', 'design')
            tools.submit_stage('DESIGN', ['docs/design.md'])
            tools.write_file('docs/plan.md', 'plan')
            tools.submit_stage('PLAN', ['docs/plan.md'])
            tools.write_file('src/app.py', 'print(1)')
            tools.submit_stage('IMPLEMENTATION', ['src/app.py'])
            tools.write_file('tests/test_app.py', 'assert True')
            tools.submit_stage('TESTS', ['tests/test_app.py'])

            with self.assertRaises(ToolError):
                tools.submit_stage('DEPLOYED', ['src/app.py'])

            tools.app_runner = lambda root: {'status': 200, 'body': 'ok', 'signIn': {'ok': True}}
            tools.check_app()
            tools.write_file('src/app.py', 'print(2)')
            with self.assertRaises(ToolError):
                tools.submit_stage('DEPLOYED', ['src/app.py'])

            tools.check_app()
            accepted = tools.submit_stage('DEPLOYED', ['src/app.py'])
            self.assertIn('Accepted DEPLOYED', accepted)
            stage_rec = tools.state()['stages'][-1]
            self.assertEqual(stage_rec['checkApp']['status'], 200)
            self.assertTrue(stage_rec['checkApp']['signIn']['ok'])

    def test_shell_manifest_paths_are_read_only(self):
        # Sample: Create shell-manifest.json listing app/Menu.tsx. Call write_file on app/Menu.tsx. Refuse with ToolError.
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / 'app').mkdir()
            (ws / 'app/Menu.tsx').write_text('export default function Menu(){}', encoding='utf-8')
            (ws / 'shell-manifest.json').write_text(json.dumps({'files': {'app/Menu.tsx': 'abc'}}), encoding='utf-8')
            tools = BuildTools(ws)
            tools.get_sample('S-TRADE-BOOK')
            tools.write_file('docs/design.md', 'design')
            tools.submit_stage('DESIGN', ['docs/design.md'])
            tools.write_file('docs/plan.md', 'plan')
            tools.submit_stage('PLAN', ['docs/plan.md'])
            with self.assertRaises(ToolError):
                tools.write_file('app/Menu.tsx', 'mutated')

    def test_deploy_next_build_handles_missing_prepare(self):
        # Sample: Check API_START checks importlib.util.find_spec before calling prepare.
        self.assertIn('find_spec', deploy_next_build.API_START)
        self.assertIn('hasattr', deploy_next_build.API_START)


if __name__ == '__main__':
    unittest.main()
