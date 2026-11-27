"""Sample: Run one Next.js workspace with one passing and one failing vitest test and one page with status 200. Check the counts, the dispatch and the guidance topic app."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from durai.build_guidance import guidance_text
from durai.build_run_node import check_node_app, run_node_tests
from durai.build_run_tests import check_workspace_app, combine_counts, run_workspace_tests

PACKAGE = json.dumps({'name': 'smoke', 'private': True, 'version': '0.1.0'})
TEST_FILE = ("import { describe, expect, it } from 'vitest';\n"
             "describe('smoke', () => {\n"
             "  it('passes', () => { expect(2 + 2).toBe(4); });\n"
             "  it('fails', () => { expect(2 + 2).toBe(5); });\n"
             "});\n")
PAGE = ("export default function Page() {\n  return <main><h1>Smoke</h1></main>;\n}\n")
LAYOUT = ("export default function RootLayout({ children }: { children: React.ReactNode }) {\n"
          "  return <html lang=\"en\"><body>{children}</body></html>;\n}\n")


def write_workspace(root, with_fail=True):
    # Sample: Write package.json, app/layout.tsx, app/page.tsx and tests/smoke.test.ts under root. Return the root.
    files = {'package.json': PACKAGE, 'app/layout.tsx': LAYOUT, 'app/page.tsx': PAGE,
             'tests/smoke.test.ts': TEST_FILE if with_fail else TEST_FILE.replace('toBe(5)', 'toBe(4)')}
    for name, text in files.items():
        path = Path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
    return root


class NodeRunnerTests(unittest.TestCase):
    def setUp(self):
        # Sample: Open a temp folder for one Next.js workspace.
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def test_node_tests_count_pass_and_fail(self):
        # Sample: One passing and one failing vitest test. Return passed 1, failed 1, exitCode 1, and failure text.
        root = write_workspace(Path(self.folder.name, 'ws'), with_fail=True)
        result = run_node_tests(root)
        self.assertEqual((result['passed'], result['failed']), (1, 1))
        self.assertEqual(result['exitCode'], 1)
        self.assertIn('fails', result['output'])
        self.assertIn('5', result['output'])

    def test_node_app_serves_status_200(self):
        # Sample: Next.js app with app/page.tsx showing Smoke. Return status 200 and a body that includes Smoke.
        root = write_workspace(Path(self.folder.name, 'ws'), with_fail=False)
        result = check_node_app(root, seconds=240)
        self.assertEqual(result['status'], 200)
        self.assertIn('Smoke', result['body'])


class DispatchTests(unittest.TestCase):
    def test_python_only_workspace_uses_python_runner(self):
        # Sample: Workspace without package.json. Patch both runners. Read the Python result only.
        with mock.patch('durai.build_run_tests.run_python_tests', return_value={'passed': 1, 'failed': 1, 'exitCode': 1, 'timedOut': False, 'output': 'py'}), \
                mock.patch('durai.build_run_node.run_node_tests') as node:
            with tempfile.TemporaryDirectory() as folder:
                result = run_workspace_tests(folder)
        node.assert_not_called()
        self.assertEqual((result['passed'], result['failed']), (1, 1))

    def test_package_json_adds_node_counts(self):
        # Sample: Workspace with package.json. Python gives 2 passed 0 failed, Node gives 1 passed 1 failed. Return passed 3, failed 1, exitCode 1.
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'package.json').write_text(PACKAGE, encoding='utf-8')
            python = {'passed': 2, 'failed': 0, 'exitCode': 0, 'timedOut': False, 'output': 'py'}
            node = {'passed': 1, 'failed': 1, 'exitCode': 1, 'timedOut': False, 'output': 'js'}
            with mock.patch('durai.build_run_tests.run_python_tests', return_value=python), \
                    mock.patch('durai.build_run_node.run_node_tests', return_value=node):
                result = run_workspace_tests(folder)
        self.assertEqual((result['passed'], result['failed'], result['exitCode']), (3, 1, 1))

    def test_combine_keeps_first_nonzero_exit(self):
        # Sample: Python exit 0 and Node exit 2. Return exitCode 2. Python exit 3 and Node exit 0. Return exitCode 3.
        base = {'passed': 0, 'failed': 0, 'timedOut': False, 'output': ''}
        self.assertEqual(combine_counts({**base, 'exitCode': 0}, {**base, 'exitCode': 2})['exitCode'], 2)
        self.assertEqual(combine_counts({**base, 'exitCode': 3}, {**base, 'exitCode': 0})['exitCode'], 3)

    def test_check_app_dispatch_picks_node_for_package_json(self):
        # Sample: Workspace with package.json. Patch check_node_app. Call check_workspace_app. Read the patched answer.
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'package.json').write_text(PACKAGE, encoding='utf-8')
            with mock.patch('durai.build_run_node.check_node_app', return_value={'status': 200, 'body': 'Smoke', 'started': True, 'output': ''}) as node:
                result = check_workspace_app(folder)
        node.assert_called_once()
        self.assertEqual(result['status'], 200)


class GuidanceTests(unittest.TestCase):
    def test_app_topic_is_nextjs_only(self):
        # Sample: Read topic app. Check profile py-fastapi-next, the Next.js App Router for older workspaces, POST sign-in, the prefetched GET rule and the DEPLOYED gate. Check no standard library web server rule.
        text = guidance_text('app')
        self.assertIn('py-fastapi-next', text)
        self.assertIn('Next.js App Router', text)
        self.assertIn('Use a POST action for sign-in and sign-out. A prefetched GET must leave the cookie alone.', text)
        self.assertIn('Submit DEPLOYED only after check_app passes', text)
        self.assertNotIn('standard library web server', text)


if __name__ == '__main__':
    unittest.main()
