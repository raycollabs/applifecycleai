"""Sample: Build 48 public turns. Run a scripted trial and stop at 10 calls."""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from durai.db import connect, server
from durai.session_context import prepare_session_context
from durai.session_summarizer import SummaryCompletion
import run_session_summary_trial as trial

DATABASE = 'durai_test'


class ScriptedSummaries:
    def __init__(self):
        # Sample: Count each call. Reply with a valid summary for the supplied turns.
        self.calls = 0

    def complete(self, system, content, profile):
        # Sample: Read turns 1 and 2 from the request. Return a summary with both turns and the required refs.
        self.calls += 1
        request = json.loads(content)
        turns = sorted({row['turn'] for row in request.get('turns', [])}
                       | {turn for child in request.get('children', []) for turn in child['sourceTurns']})
        return SummaryCompletion(json.dumps({'goal': 'Trial.', 'decisions': [], 'corrections': [], 'openQuestions': [],
                                             'nextAction': 'Continue.', 'sourceTurns': turns,
                                             'refs': request['requiredRefs']}), 'stop', 100, 50)


class SessionTrialTests(unittest.TestCase):
    def test_recorded_conversation_has_no_private_text(self):
        # Sample: Build the 48 turns. Check no "sk-", no "apiKey" and no "private\\" text.
        turns = trial.build_turns()
        self.assertEqual(48, len(turns))
        self.assertEqual('user', turns[0][0])
        joined = '\n'.join(text for _, text in turns)
        self.assertIsNone(re.search(r'sk-|apiKey|private\\|private/', joined, re.IGNORECASE))
        self.assertTrue(all(ref in joined for ref in trial.REFERENCES))
        self.assertTrue(all(0 < len(text.encode()) <= 1000 for _, text in turns))

    def test_trial_stops_at_call_limit(self):
        # Sample: Script a provider that always returns a valid summary. Queue 20 ranges. Check 10 calls and call-limit.
        target = server()
        if (os.getenv('DURAI_SESSION_ISOLATED') != '1' or target['host'] != '127.0.0.1' or target['port'] == 54410
                or 'durai-evaldb-' not in str(target['settings'])):
            raise unittest.SkipTest('Run with the disposable session context verifier')
        prepare_session_context(DATABASE)
        with connect('postgres', DATABASE) as db:
            db.execute('TRUNCATE durai_context.session_node,durai_context.session_attempt,durai_context.session_pending,'
                       'durai_context.session_summary,durai_context.session_turn,durai_context.session_conversation,'
                       'durai_context.session_spend,durai_context.session_cap_event')
        with patch('durai.session_settings.read_record', return_value={'enabled': True}), \
                patch.object(trial, 'build_turns', return_value=trial.build_turns()[:20]), \
                patch.object(trial, 'price_row', return_value=None), patch.object(trial, 'DATABASE', DATABASE):
            receipt = trial.run(DATABASE, 10, 1, ScriptedSummaries())
        self.assertEqual(10, receipt['providerCalls'])
        self.assertEqual('call-limit', receipt['stopReason'])
        self.assertEqual('FAIL', receipt['state'])
        self.assertGreaterEqual(10, receipt['resumeRead']['coveredTurn'])


if __name__ == '__main__':
    unittest.main()
