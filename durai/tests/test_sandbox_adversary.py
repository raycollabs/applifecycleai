"""Sample: Run the adversary with a fake model on the local process adapter. Check code extraction, one call per goal and the receipt state."""
import unittest

from durai.adapters.sandbox.local_process import LocalProcessSandbox
from durai.sandbox_adversary import GOALS, extract_code, run_adversary


class AdversaryTests(unittest.TestCase):
    def test_extract_code_reads_fenced_block(self):
        # Sample: Text has a python block with print(1). Return print(1).
        self.assertEqual(extract_code('Here:\n```python\nprint(1)\n```\n'), 'print(1)')

    def test_extract_code_returns_none_without_block(self):
        # Sample: Text says no code. Return None.
        self.assertIsNone(extract_code('I cannot help.'))

    def test_one_call_per_goal_and_refusal_passes(self):
        # Sample: Fake model refuses every goal. Six calls. No attempt ran. State PASS.
        calls = []
        receipt = run_adversary(LocalProcessSandbox(), 'local', lambda s, u: calls.append(u) or 'no', goals=GOALS)
        self.assertEqual((len(calls), receipt['modelCalls'], receipt['state']), (len(GOALS), len(GOALS), 'PASS'))
        self.assertFalse(any(item['ran'] for item in receipt['attempts']))

    def test_program_that_runs_is_checked(self):
        # Sample: Fake model returns print(1). The attempt runs and has an invariants dict with alive key.
        receipt = run_adversary(LocalProcessSandbox(), 'local', lambda s, u: '```python\nprint(1)\n```', goals=GOALS[:1])
        item = receipt['attempts'][0]
        self.assertTrue(item['ran'])
        self.assertIn('alive', item['invariants'])


if __name__ == '__main__':
    unittest.main()
