"""Sample: Send hook input with a real compare request and with notification text. Check the real request adds context and the notification adds none."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import enhance_hook  # noqa: E402


def hook(prompt):
    return enhance_hook.run(json.dumps({'prompt': prompt}))


class HookTriggerTests(unittest.TestCase):
    def test_compare_request_adds_context(self):
        # Sample: Send "Compare Durai with the LangGraph framework". The output holds additionalContext.
        self.assertIn('additionalContext', hook('Compare Durai with the LangGraph framework'))

    def test_build_request_adds_themes(self):
        # Sample: Send "Build a trade blotter screen". The output names a UI theme.
        self.assertIn('transaction-heavy', hook('Build a trade blotter screen'))

    def test_notification_text_adds_nothing(self):
        # Sample: Send a system notification that mentions a pipeline and Slack. The output is empty.
        self.assertEqual(hook('[SYSTEM NOTIFICATION - NOT USER INPUT]\nThe Slack agent finished the pipeline dashboard.'), '')

    def test_task_notification_block_adds_nothing(self):
        # Sample: Send text with a task-notification tag. The output is empty.
        self.assertEqual(hook('<system-reminder>\n<task-notification>Compare the dashboard build</task-notification></system-reminder>'), '')

    def test_long_text_adds_nothing(self):
        # Sample: Send a build request padded to 2500 characters. The output is empty.
        self.assertEqual(hook('Build a trade blotter screen. ' + 'x' * 2500), '')

    def test_broken_json_adds_nothing(self):
        # Sample: Send text that is not JSON. The output is empty.
        self.assertEqual(enhance_hook.run('not json'), '')


if __name__ == '__main__':
    unittest.main()
