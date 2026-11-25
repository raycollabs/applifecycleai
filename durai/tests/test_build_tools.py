"""Sample: Run BuildToolTests and RunTestsTests. Check path guard, stage gate, guidance and Docker test counts."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from durai.build_guidance import guidance_text, TOPICS
from durai.build_run_tests import check_workspace_app, docker_available, run_workspace_tests
from durai.build_samples import JsonSampleSource
from durai.build_tools import BuildTools

BASELINE = Path('D:/livebuild/q1-fintech-checkout-flow/apps/trade-pilot')
STOP_STEMS = {'main', 'test', 'readme', 'progress', 'go', 'index'}


def baseline_terms():
    # Sample: Read file stems and CREATE TABLE names of the baseline folder. Return lowercase terms such as booking_event.
    terms = set()
    paths = list(BASELINE.glob('*.py')) + [item for folder in ('cmd', 'internal') for item in (BASELINE / folder).rglob('*.go')]
    for path in paths:
        if path.is_file():
            stem = re.sub(r'_test$', '', path.stem.lower())
            if stem not in STOP_STEMS and len(stem) >= 4:
                terms.add(stem)
    for path in (BASELINE / 'migrations').glob('*.sql'):
        for name in re.findall(r'create table(?: if not exists)?\s+([a-z_\.]+)', path.read_text(encoding='utf-8').lower()):
            terms.add(name)
            terms.add(name.rsplit('.', 1)[-1])
    return terms


class BuildToolTests(unittest.TestCase):
    def setUp(self):
        # Sample: Make an empty workspace folder. Start tools on S-TRADE-BOOK.
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name) / 'ws'
        self.outside = Path(self.folder.name) / 'outside'
        self.outside.mkdir()
        self.tools = BuildTools(self.root, runner=lambda root: {'passed': 0, 'failed': 0, 'output': ''})
        self.tools.call('get_sample', {'sampleId': 'S-TRADE-BOOK'})

    def tearDown(self):
        # Sample: Remove the temporary folders.
        self.folder.cleanup()

    def test_path_outside_workspace_is_refused(self):
        # Sample: Call write_file with ../x.py, C:/x.py and a link to the parent. Check three refusals and no file outside.
        link = self.root / 'escape'
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.outside)], capture_output=True, check=True)
        else:
            os.symlink(self.outside, link)
        for path in ('../x.py', 'C:/x.py', 'escape/x.md'):
            result = self.tools.call('write_file', {'path': path, 'content': 'x = 1\n'})
            self.assertTrue(result['isError'], path)
        self.assertEqual([], list(self.outside.iterdir()))
        self.assertFalse((Path(self.folder.name) / 'x.py').exists())

    def test_code_before_plan_is_refused(self):
        # Sample: Submit DESIGN. Write booking.py. Check refusal "submit PLAN first". Write docs/design.md. Check accepted.
        self.tools.call('write_file', {'path': 'docs/design.md', 'content': 'design\n'})
        self.assertFalse(self.tools.call('submit_stage', {'stage': 'DESIGN', 'paths': ['docs/design.md']})['isError'])
        refused = self.tools.call('write_file', {'path': 'booking.py', 'content': 'x = 1\n'})
        self.assertTrue(refused['isError'])
        self.assertIn('Submit PLAN first', refused['text'])
        self.assertFalse((self.root / 'booking.py').exists())
        self.assertFalse(self.tools.call('write_file', {'path': 'docs/design.md', 'content': 'design 2\n'})['isError'])

    def test_stages_in_order(self):
        # Sample: Submit PLAN first. Check refusal. Submit DESIGN with docs/design.md, then PLAN with docs/plan.md. Check two digests.
        self.tools.call('write_file', {'path': 'docs/design.md', 'content': 'design\n'})
        self.tools.call('write_file', {'path': 'docs/plan.md', 'content': 'plan\n'})
        self.assertTrue(self.tools.call('submit_stage', {'stage': 'PLAN', 'paths': ['docs/plan.md']})['isError'])
        self.assertFalse(self.tools.call('submit_stage', {'stage': 'DESIGN', 'paths': ['docs/design.md']})['isError'])
        self.assertFalse(self.tools.call('submit_stage', {'stage': 'PLAN', 'paths': ['docs/plan.md']})['isError'])
        stages = self.tools.components['S-TRADE-BOOK']['stages']
        self.assertEqual(['DESIGN', 'PLAN'], [item['stage'] for item in stages])
        self.assertTrue(all(len(item['sha256s'][0]) == 64 for item in stages))

    def test_stage_artifacts_are_archived(self):
        # Sample: Write docs/design.md "Design A". Submit DESIGN. Overwrite with "Design B". Check the archive file named by the first digest holds "Design A", and read_file of the archive path is refused.
        self.tools.call('write_file', {'path': 'docs/design.md', 'content': 'Design A\n'})
        self.assertFalse(self.tools.call('submit_stage', {'stage': 'DESIGN', 'paths': ['docs/design.md']})['isError'])
        digest = self.tools.components['S-TRADE-BOOK']['stages'][0]['sha256s'][0]
        self.tools.call('write_file', {'path': 'docs/design.md', 'content': 'Design B\n'})
        kept = Path(self.folder.name) / 'ws-artifacts' / digest
        self.assertEqual(b'Design A\n', kept.read_bytes().replace(b'\r\n', b'\n'))
        self.assertTrue(self.tools.call('read_file', {'path': '../ws-artifacts/' + digest})['isError'])

    def test_guidance_has_no_baseline_terms(self):
        # Sample: Read all guidance topics. Check no term from the trade-pilot manifest stems or tables.
        if not BASELINE.is_dir():
            self.skipTest('Baseline folder is not available')
        terms = baseline_terms()
        self.assertGreater(len(terms), 20)
        words = set(re.findall(r'[a-z0-9_\.]+', guidance_text().lower())) | set(re.findall(r'[a-z0-9_]+', guidance_text().lower()))
        self.assertEqual(set(), terms & words)

    def test_sample_hides_goals(self):
        # Sample: Get sample S-TRADE-BOOK. Check the request text and no key named hidden_goals.
        sample = JsonSampleSource().get('S-TRADE-BOOK')
        self.assertIn('it must count once', sample['requestText'])
        self.assertEqual({'sampleId', 'revision', 'title', 'requestText', 'digest'}, set(sample))
        text = self.tools.call('get_sample', {'sampleId': 'S-TRADE-BOOK'})['text']
        self.assertNotIn('hidden', text.lower())
        self.assertNotIn('goal', text.lower())


class RunTestsTests(unittest.TestCase):
    @unittest.skipUnless(docker_available(), 'Docker is not available')
    def test_run_tests_reports_counts(self):
        # Sample: Write test_a.py with one pass and one fail. Call run_tests. Check passed 1, failed 1.
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'tests').mkdir()
            (Path(folder) / 'tests/test_a.py').write_text(
                'import os\nimport unittest\nimport psycopg\n\n\nclass T(unittest.TestCase):\n'
                '    def test_pass(self):\n'
                '        with psycopg.connect(os.environ["DURAI_BUILD_DATABASE_URL"]) as c:\n'
                '            self.assertEqual(1, c.execute("SELECT 1").fetchone()[0])\n\n'
                '    def test_fail(self):\n        self.assertEqual(1, 2)\n', encoding='utf-8')
            result = run_workspace_tests(folder)
        self.assertEqual((1, 1), (result['passed'], result['failed']))




APP_SOURCE = '''import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


HTTPServer(("0.0.0.0", int(os.environ["DURAI_APP_PORT"])), Handler).serve_forever()
'''


class CheckAppTests(unittest.TestCase):
    @unittest.skipUnless(docker_available(), 'Docker is not available')
    def test_check_app_reports_status(self):
        # Sample: Write src/app.py that serves ok. Call check_app. Check status 200 and ok.
        with tempfile.TemporaryDirectory() as folder:
            tools = BuildTools(Path(folder) / 'ws', app_runner=check_workspace_app)
            tools.call('get_sample', {'sampleId': 'S-TRADE-BOOK'})
            (tools.root / 'src').mkdir()
            (tools.root / 'src/__init__.py').write_text('', encoding='utf-8', newline='\n')
            (tools.root / 'src/app.py').write_text(APP_SOURCE, encoding='utf-8', newline='\n')
            result = tools.call('check_app', {})
        self.assertFalse(result['isError'])
        self.assertIn('status: 200', result['text'])
        self.assertTrue(result['text'].rstrip().endswith('ok'))


if __name__ == '__main__':
    unittest.main()
