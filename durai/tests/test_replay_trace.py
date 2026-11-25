"""Sample: Test trace replay tool in dry-run and simulated execution modes. Check step extraction, unique tools, and match verification."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.replay_trace import extract_tool_steps, replay_trace


class ReplayTraceTests(unittest.TestCase):
    def setUp(self):
        # Sample: Create temporary trace file for testing replay.
        self.folder = tempfile.TemporaryDirectory()
        self.trace_file = Path(self.folder.name) / 'sample_trace.json'
        trace_data = {
            'format': 'durai-worker-trace/v1',
            'attemptId': 'blind-test-replay-1',
            'messages': [
                {'role': 'human', 'content': 'start'},
                {'role': 'ai', 'content': '', 'tool_calls': [{'name': 'read_file', 'args': {'path': 'docs/design.md'}, 'id': 'c1'}]},
                {'role': 'tool', 'name': 'read_file', 'content': 'design content', 'tool_call_id': 'c1'},
                {'role': 'ai', 'content': '', 'tool_calls': [{'name': 'submit_stage', 'args': {'stage': 'DESIGN'}, 'id': 'c2'}]},
                {'role': 'tool', 'name': 'submit_stage', 'content': 'accepted', 'tool_call_id': 'c2'},
            ]
        }
        self.trace_file.write_text(json.dumps(trace_data), encoding='utf-8')

    def tearDown(self):
        # Sample: Clean up temporary directory.
        self.folder.cleanup()

    def test_extract_tool_steps(self):
        # Sample: Extract tool calls from trace. Check read_file and submit_stage steps.
        trace = json.loads(self.trace_file.read_text(encoding='utf-8'))
        steps = extract_tool_steps(trace)
        self.assertEqual(2, len(steps))
        self.assertEqual('read_file', steps[0]['name'])
        self.assertEqual('submit_stage', steps[1]['name'])

    def test_dry_run_replay(self):
        # Sample: Replay trace in dry-run mode. Check matched True and 2 steps executed.
        result = replay_trace(str(self.trace_file), dry_run=True)
        self.assertEqual(2, result['totalSteps'])
        self.assertTrue(result['matched'])
        self.assertEqual(['read_file', 'submit_stage'], result['uniqueTools'])
        self.assertEqual('blind-test-replay-1', result['attemptId'])


if __name__ == '__main__':
    unittest.main()
