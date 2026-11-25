"""Sample: Select patterns for the funds task, build a brief, review it with a scripted reviewer, and deliver the advice in the loop."""
import json
import time
import unittest
from durai.advisor_brief import build_brief
from durai.advisor_review import NOTICE, Advisor, review
from durai.advisor_select import CACHE, cached_patterns, select_patterns
from durai.architecture_defaults import catalog_patterns

PROFILE = {'id': 'deepseek-flash-think-low', 'model': 'deepseek-flash', 'thinking': True, 'reasoningEffort': 'low',
           'maxTokens': 4000, 'timeoutSeconds': 5}
FRAMEWORK = {'bindingId': 'foundation-test', 'bindingDigest': 'B-1', 'profile': 'transaction',
             'patterns': [item for item in catalog_patterns() if 'code-reviewer' in item['audience']]}
FUNDS = {'objective': 'Store each refund request once with its request key and outcome. Retry with the same key returns the stored outcome.',
         'failingAcceptanceGoal': 'test_capture_and_refund_limit', 'writePaths': ['/workspace/durai/payment_funds.py'],
         'editScope': {'/workspace/durai/payment_funds.py': ['create_operation']}}
FILES = {'/workspace/durai/payment_funds.py': b'from .idempotency import execute_request\n\n'
                                              b'def create_operation(kind, payment_id, amount_minor, request_key):\n'
                                              b"    raise NotImplementedError('Complete the public task')\n",
         '/workspace/durai/idempotency.py': b'def execute_request(operation, key, body, handler):\n    return {}\n'}
HISTORY = [{'attemptId': 'A-1', 'verdict': 'FAIL', 'feedback': 'A protected acceptance goal failed.',
            'goals': ['test_capture_and_refund_limit']}]


def reply(steps=None, **changes):
    # Sample: Return one valid advice object with step "Store the outcome" under idempotency correctness.
    advice = {'steps': steps or [{'action': 'Store the outcome with the request key.', 'rule': 'architecture.idempotency.correctness',
                                  'files': ['durai/payment_funds.py']}], 'departures': [], 'risks': [], 'confidence': 'medium'}
    advice.update(changes)
    return json.dumps(advice)


def scripted(text, delay=0.0):
    # Sample: Return a reviewer that sleeps 0.0 seconds and replies with text. Count each call.
    calls = []

    def call(system, brief, profile):
        calls.append(brief)
        time.sleep(delay)
        return text
    call.calls = calls
    return call


class SelectTests(unittest.TestCase):
    def test_funds_selects_idempotency(self):
        # Sample: Select patterns for the funds task. Check architecture.idempotency.default first.
        selection = select_patterns(FUNDS, FILES, FRAMEWORK)
        self.assertEqual(selection['patternIds'][0], 'architecture.idempotency.default')

    def test_pattern_pulls_required_companion(self):
        # Sample: Select idempotency.default. Check idempotency.correctness follows it.
        ids = select_patterns(FUNDS, FILES, FRAMEWORK)['patternIds']
        self.assertEqual(ids[ids.index('architecture.idempotency.default') + 1], 'architecture.idempotency.correctness')

    def test_selection_cached_by_binding_digest(self):
        # Sample: Load binding digest B-9 twice. Check the loader runs once. Load B-10. Check a second run.
        runs = []
        CACHE.pop(('B-9', 'transaction'), None)
        CACHE.pop(('B-10', 'transaction'), None)
        for key in ('B-9', 'B-9', 'B-10'):
            cached_patterns(key, 'transaction', lambda: runs.append(1) or {'patterns': []})
        self.assertEqual(len(runs), 2)

    def test_unrelated_task_selects_nothing(self):
        # Sample: Select patterns for a text-only task about colors. Check no pattern.
        task = {'objective': 'Paint the logo blue.', 'writePaths': ['/workspace/logo.txt']}
        self.assertEqual(select_patterns(task, {'/workspace/logo.txt': b'red'}, FRAMEWORK)['patternIds'], [])


class BriefTests(unittest.TestCase):
    def test_brief_separates_rules_and_preferences(self):
        # Sample: Build the PLAN brief for funds. Check "Required rules" before "Preferences" and an alternative in a preference.
        brief = build_brief(FUNDS, FILES, select_patterns(FUNDS, FILES, FRAMEWORK), 'PLAN')
        text = brief['text']
        self.assertLess(text.index('## Required rules'), text.index('## Preferences'))
        self.assertIn('Alternative:', text[text.index('## Preferences'):])
        self.assertIn('def create_operation(kind, payment_id, amount_minor, request_key)', text)

    def test_brief_carries_failure_facts(self):
        # Sample: Build the RETRY brief after attempt 1 failed test_capture_and_refund_limit. Check the goal and feedback lines.
        brief = build_brief(FUNDS, FILES, select_patterns(FUNDS, FILES, FRAMEWORK), 'RETRY', HISTORY)
        self.assertIn('Failing goals: test_capture_and_refund_limit', brief['text'])
        self.assertIn('Feedback: A protected acceptance goal failed.', brief['text'])

    def test_brief_size_limit(self):
        # Sample: Give a 40000-character conversation head. Check a brief of 4000 characters or less and the Question kept.
        brief = build_brief(FUNDS, FILES, select_patterns(FUNDS, FILES, FRAMEWORK), 'PLAN', head='x' * 40000)
        self.assertLessEqual(brief['characters'], 4000)
        self.assertIn('## Question', brief['text'])
        self.assertIn('## Problem', brief['text'])


class ReviewTests(unittest.TestCase):
    def setUp(self):
        # Sample: Select patterns and build the PLAN brief for funds.
        self.selection = select_patterns(FUNDS, FILES, FRAMEWORK)
        self.brief = build_brief(FUNDS, FILES, self.selection, 'PLAN')

    def test_valid_advice_rendered_as_data(self):
        # Sample: Reply with 1 valid step. Check state ADVICE, the notice line, and the rule name in the text.
        result = review(self.brief, self.selection, FILES, PROFILE, scripted('```json\n' + reply() + '\n```'))
        self.assertEqual(result['state'], 'ADVICE', result.get('reason'))
        self.assertTrue(result['text'].startswith(NOTICE))
        self.assertIn('architecture.idempotency.correctness', result['text'])

    def test_invalid_advice_dropped(self):
        # Sample: Reply with a step that names durai/missing.py. Check state INVALID and the reason names missing.py.
        steps = [{'action': 'Edit it.', 'rule': '', 'files': ['durai/missing.py']}]
        result = review(self.brief, self.selection, FILES, PROFILE, scripted(reply(steps)))
        self.assertEqual(result['state'], 'INVALID')
        self.assertIn('missing.py', result['reason'])
        self.assertNotIn('text', result)

    def test_timeout_continues_turn(self):
        # Sample: The reviewer sleeps 1 second with limit 0.2. Check state TIMEOUT and no advice text.
        result = review(self.brief, self.selection, FILES, dict(PROFILE, timeoutSeconds=0.2), scripted(reply(), delay=1.0))
        self.assertEqual(result['state'], 'TIMEOUT')
        self.assertNotIn('text', result)

    def test_profile_recorded(self):
        # Sample: Review with profile deepseek-flash-think-low. Check thinking True, effort low, and a 64-character prompt digest.
        result = review(self.brief, self.selection, FILES, PROFILE, scripted(reply()))
        self.assertEqual((result['profile']['id'], result['profile']['thinking'], result['profile']['reasoningEffort']),
                         ('deepseek-flash-think-low', True, 'low'))
        self.assertEqual(len(result['profile']['promptDigest']), 64)

    def test_thinking_profile_refuses_temperature(self):
        # Sample: Add temperature 0.2 to a thinking profile. Check a ValueError.
        with self.assertRaises(ValueError):
            review(self.brief, self.selection, FILES, dict(PROFILE, temperature=0.2), scripted(reply()))


class TriggerTests(unittest.TestCase):
    def test_retry_trigger_fires_once(self):
        # Sample: Advise run R-1 at PLAN, then after a FAIL. Check turns PLAN and RETRY and 2 reviewer calls. A PASS entry gives no call.
        call = scripted(reply())
        advisor = Advisor(FRAMEWORK, PROFILE, call)
        turns = [advisor.advise('R-1', FUNDS, FILES, history)['turn'] for history in ([], HISTORY)]
        self.assertEqual(turns, ['PLAN', 'RETRY'])
        self.assertIsNone(advisor.advise('R-1', FUNDS, FILES, [{'verdict': 'PASS'}]))
        self.assertEqual(len(call.calls), 2)

    def test_advisor_cap_per_run(self):
        # Sample: Cap 3. Give 4 triggers to run R-2. Check 3 calls and a CAPPED fourth result. Run R-3 still gets a call.
        call = scripted(reply())
        advisor = Advisor(FRAMEWORK, PROFILE, call, cap=3)
        states = [advisor.advise('R-2', FUNDS, FILES, HISTORY)['state'] for _ in range(4)]
        self.assertEqual(states, ['ADVICE', 'ADVICE', 'ADVICE', 'CAPPED'])
        self.assertEqual(advisor.advise('R-3', FUNDS, FILES, [])['state'], 'ADVICE')
        self.assertEqual(len(call.calls), 4)


class AuthorityTests(unittest.TestCase):
    def test_advice_grants_no_authority(self):
        # Sample: Reply with an extra key "approve": true. Check INVALID. Reply validly. Check dataOnly True and no authority keys.
        selection = select_patterns(FUNDS, FILES, FRAMEWORK)
        brief = build_brief(FUNDS, FILES, selection, 'PLAN')
        extra = json.loads(reply())
        extra['approve'] = True
        self.assertEqual(review(brief, selection, FILES, PROFILE, scripted(json.dumps(extra)))['state'], 'INVALID')
        result = review(brief, selection, FILES, PROFILE, scripted(reply()))
        self.assertTrue(result['dataOnly'])
        self.assertFalse({'approve', 'grants', 'releaseAuthority', 'allowedTools'} & set(result))


class LoopAdvisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply migrations and checkpoint tables to durai_test.
        from durai.db import prepare
        from durai.harness import prepare_checkpoints
        prepare('durai_test')
        prepare_checkpoints('durai_test')

    def test_loop_delivers_advice_at_plan_and_retry(self):
        # Sample: Run WRONG then FIX with a scripted reviewer. Check ADVICE_DELIVERED for PLAN and RETRY and the notice in both worker prompts.
        from durai.development_loop import build_loop, development_graph
        from durai.execution_events import replay
        from durai.common import uid
        from test_dev_loop import FIX, WRONG, LoopHarness, Sources, loop_spec, pins_for
        from test_workspace_worker import SequenceModel
        
        prompts = []

        class RecordingModel(SequenceModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                # Sample: Record the first human message. Then send the scripted step.
                prompts.append(next((str(m.content) for m in messages if m.type == 'human'), ''))
                return super()._generate(messages, stop, run_manager, **kwargs)

        harness = LoopHarness([[WRONG], [FIX]])

        def model(attempt):
            # Sample: Return the recording model for attempt 1 or 2.
            return RecordingModel(steps=harness.scripts[attempt - 1], replies=[])

        advisor = Advisor(dict(FRAMEWORK), PROFILE, scripted(json.dumps(
            {'steps': [{'action': 'Return lots times lot size.', 'rule': '', 'files': []}], 'departures': [], 'risks': [],
             'confidence': 'high'})))
        builder = build_loop(harness.ledger, harness.providers, harness.evaluator, harness.store, Sources(), model,
                             harness.scratch / 'work', advisor)
        run_id = 'run-' + uid()
        spec = loop_spec()
        with development_graph(builder, 'durai_test') as graph:
            state = graph.invoke({'run_id': run_id, 'spec': spec, 'current': pins_for(spec)}, {'configurable': {'thread_id': run_id}})
        self.assertEqual(state['outcome'], 'PASSED', state.get('verdict'))
        delivered = [row['payload'] for row in replay(run_id, database='durai_test') if row['kind'] == 'ADVICE_DELIVERED']
        self.assertEqual([row['turn'] for row in delivered], ['PLAN', 'RETRY'])
        self.assertTrue(all(row['dataOnly'] and len(row['digest']) == 64 for row in delivered))
        self.assertGreaterEqual(len({text for text in prompts if NOTICE in text}), 2)


if __name__ == '__main__':
    unittest.main()
