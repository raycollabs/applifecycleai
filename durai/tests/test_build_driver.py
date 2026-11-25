"""Sample: Run DriverTests with a scripted model. Check the call cap, the stage digests and the smoke CLI run."""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import ToolMessage

from durai.build_driver import BlindBuild, ScriptedModel, SYSTEM_PROMPT, component_prompt, shrink
from durai.build_run_tests import docker_available
from durai.build_tools import BuildTools
from test_build_tools import BASELINE, baseline_terms

ROOT = Path(__file__).resolve().parents[1]
NO_TESTS = lambda root: {'passed': 0, 'failed': 0, 'output': ''}


class DriverTests(unittest.TestCase):
    def setUp(self):
        # Sample: Make a workspace folder and a receipts folder.
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        # Sample: Remove the temporary folders.
        self.folder.cleanup()

    def build(self, script, per_component=80):
        # Sample: Make a build with the scripted model and caps per_component, 400, 10 USD.
        tools = BuildTools(self.root / 'ws', runner=NO_TESTS)
        return BlindBuild(tools, ScriptedModel(script), 'T-1', 'fake', 'fake-model', self.root / 'receipts',
                          calls_per_component=per_component, sleep=lambda seconds: None)

    def test_caps_stop_the_build(self):
        # Sample: Run a fake model that always lists files, cap 5. Check 5 calls and STOP_BUDGET.
        build = self.build([[('list_files', {})]] * 20, per_component=5)
        receipt = build.run_component('S-TRADE-BOOK')
        self.assertEqual(5, receipt['calls'])
        self.assertEqual('STOP_BUDGET', receipt['stopReason'])
        self.assertEqual(500, receipt['inputTokens'])
        self.assertAlmostEqual(0.0015, receipt['estimatedEnterpriseCost'], places=6)

    def test_receipt_records_stages(self):
        # Sample: Run a fake model that writes and submits DESIGN and PLAN, then finishes. Check the receipt has both stages with SHA-256.
        script = [[('write_file', {'path': 'docs/design.md', 'content': 'design\n'})],
                  [('submit_stage', {'stage': 'DESIGN', 'paths': ['docs/design.md']})],
                  [('write_file', {'path': 'docs/plan.md', 'content': 'plan\n'})],
                  [('submit_stage', {'stage': 'PLAN', 'paths': ['docs/plan.md']})],
                  [('write_file', {'path': '../bad.py', 'content': 'x\n'})],
                  [('finish', {})]]
        receipt = self.build(script).run_component('S-TRADE-BOOK')
        self.assertEqual('FINISHED', receipt['stopReason'])
        self.assertEqual(['DESIGN', 'PLAN'], [item['stage'] for item in receipt['stages']])
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{64}', item['sha256s'][0]) for item in receipt['stages']))
        self.assertEqual(1, receipt['toolErrors']['write_file'])
        self.assertEqual(1, receipt['revision'])
        stored = json.loads((self.root / 'receipts/blind-build-T-1-S-TRADE-BOOK.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['digest'], stored['digest'])

    def test_shrink_keeps_the_given_window(self):
        # Sample: Make 45 tool results of 3000 characters. Shrink with keep 40. Check the first 5 are cut to 1500 characters and the last 40 are whole.
        messages = [ToolMessage(content='x' * 3000, tool_call_id='c' + str(index)) for index in range(45)]
        kept = shrink(messages, 40)
        self.assertTrue(all(len(item.content) < 3000 for item in kept[:5]))
        self.assertTrue(all(len(item.content) == 3000 for item in kept[5:]))

    def test_receipt_records_keep_recent(self):
        # Sample: Run S-TRADE-BOOK with the scripted model and keep 40. Read the receipt. Check keepRecent is 40.
        tools = BuildTools(self.root / 'ws', runner=NO_TESTS)
        build = BlindBuild(tools, ScriptedModel([[('finish', {})]]), 'T-1', 'fake', 'fake-model', self.root / 'receipts',
                           sleep=lambda seconds: None, keep_recent=40)
        self.assertEqual(40, build.run_component('S-TRADE-BOOK')['keepRecent'])

    def test_trace_retention_and_streaming(self):
        # Sample: Run S-TRADE-BOOK with scripted model that submits DESIGN and finishes. Check receipt has traceDigest and trace file has durai-worker-trace/v1.
        script = [[('write_file', {'path': 'docs/design.md', 'content': 'design\n'})],
                  [('submit_stage', {'stage': 'DESIGN', 'paths': ['docs/design.md']})],
                  [('finish', {})]]
        receipt = self.build(script).run_component('S-TRADE-BOOK')
        self.assertIn('traceDigest', receipt)
        self.assertIn('tracePath', receipt)
        trace_file = Path(receipt['tracePath'])
        self.assertTrue(trace_file.is_file())
        trace_data = json.loads(trace_file.read_text(encoding='utf-8'))
        self.assertEqual('durai-worker-trace/v1', trace_data['format'])
        self.assertEqual(receipt['traceDigest'], trace_data['digest'])
        stream_file = self.root / 'traces/stream-T-1-S-TRADE-BOOK.jsonl'
        self.assertTrue(stream_file.is_file())
        self.assertGreater(len(stream_file.read_text(encoding='utf-8').splitlines()), 3)

    def test_stall_stop_rule_triggers_stop_stall(self):
        # Sample: Run a model that executes 5 tool calls without stage advance, nudge at 3, stop at 5. Check STOP_STALL and detail.
        tools = BuildTools(self.root / 'ws', runner=NO_TESTS)
        build = BlindBuild(tools, ScriptedModel([[('list_files', {})]] * 10), 'T-STALL', 'fake', 'fake-model',
                           self.root / 'receipts', sleep=lambda seconds: None,
                           stall_nudge_limit=3, stall_stop_limit=5)
        receipt = build.run_component('S-TRADE-BOOK')
        self.assertEqual('STOP_STALL', receipt['stopReason'])
        self.assertIsNotNone(receipt['stopDetail'])
        self.assertEqual(5, receipt['stopDetail']['callsSinceLastStage'])
        self.assertEqual(['list_files'] * 5, receipt['stopDetail']['recentTools'])
        self.assertEqual('NONE', receipt['stopDetail']['lastStage'])

    def test_prompt_has_no_baseline_terms(self):
        # Sample: Build the prompt for S-TRADE-CASH. Remove the user request text. Check no trade-pilot stem or table name.
        if not BASELINE.is_dir():
            self.skipTest('Baseline folder is not available')
        tools = BuildTools(self.root / 'ws')
        sample = tools.samples.get('S-TRADE-CASH')
        text = (SYSTEM_PROMPT + component_prompt(sample)).replace(sample['requestText'], '').lower()
        self.assertEqual(set(), baseline_terms() & set(re.findall(r'[a-z0-9_]+', text)))

    def test_bare_mode_allows_code_writing_without_stages(self):
        # Sample: In bare mode, verify write_file allows python files directly and tools exclude submit_stage.
        tools = BuildTools(self.root / 'ws', runner=NO_TESTS, bare=True)
        self.assertNotIn('submit_stage', tools.table)
        self.assertNotIn('get_guidance', tools.table)
        build = BlindBuild(tools, ScriptedModel([[('write_file', {'path': 'src/bare_code.py', 'content': '# Sample: bare\n'})],
                                                 [('finish', {})]]),
                           'T-BARE', 'fake', 'fake-model', self.root / 'receipts', bare=True)
        receipt = build.run_component('S-TRADE-BOOK')
        self.assertEqual('FINISHED', receipt['stopReason'])
        self.assertTrue(receipt['bare'])
        self.assertEqual([], receipt['stages'])
        self.assertTrue((self.root / 'ws/src/bare_code.py').is_file())


class SmokeTests(unittest.TestCase):
    @unittest.skipUnless(docker_available(), 'Docker is not available')
    def test_cli_fake_model_runs_one_component(self):
        # Sample: Run the CLI with the fake model for S-TRADE-BOOK. Check FINISHED, 1 test passed and four stages.
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([sys.executable, str(ROOT / 'tools/run_blind_build.py'), '--workspace', folder + '/ws',
                                   '--components', 'S-TRADE-BOOK', '--label', 'smoke', '--fake-model',
                                   '--receipts', folder + '/receipts'], capture_output=True, text=True, timeout=280)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            receipt = json.loads((Path(folder) / 'receipts/blind-build-smoke-S-TRADE-BOOK.json').read_text(encoding='utf-8'))
            self.assertEqual('FINISHED', receipt['stopReason'])
            self.assertEqual({'passed': 1, 'failed': 0}, receipt['lastRunTests'])
            self.assertEqual(['DESIGN', 'PLAN', 'IMPLEMENTATION', 'TESTS'], [item['stage'] for item in receipt['stages']])
            self.assertTrue((Path(folder) / 'receipts/blind-build-smoke-summary.json').is_file())


if __name__ == '__main__':
    unittest.main()
