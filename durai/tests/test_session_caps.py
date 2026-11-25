"""Sample: Turn the flag off. Send turns 1 to 5. Check no rows, no claims, and no provider calls."""
import json
import os
import unittest
from unittest.mock import patch

from durai.common import uid
from durai.db import connect, server
from durai.session_settings import load_session_settings
from durai.session_context import SessionContext, prepare_session_context
from durai.session_host import ConversationGrant, SessionHost, SessionIdentity
from durai.session_summarizer import DEEPSEEK_FLASH, SessionSummaryWorker, SummaryCompletion

DATABASE = 'durai_test'
RECORD = 'durai.session_settings.read_record'


def good(refs=(), turns=(1,)):
    # Sample: Build a valid summary for turn 1 with the given references.
    return json.dumps({'goal': 'Review the change.', 'decisions': [], 'corrections': [], 'openQuestions': [],
                       'nextAction': 'Run the proof.', 'sourceTurns': list(turns), 'refs': list(refs)})


class ScriptedProvider:
    def __init__(self, *results, on_call=None):
        # Sample: Script one failed call and one valid call. Count each call.
        self.results = list(results)
        self.contents = []
        self.on_call = on_call

    def complete(self, system, content, profile):
        # Sample: Return the next scripted completion. Count one call.
        self.contents.append(content)
        if self.on_call:
            self.on_call()
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return SummaryCompletion(result, 'stop')


class SessionCapBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Require the disposable connection descriptor. Apply the migration to the test database.
        target = server()
        if (os.getenv('DURAI_SESSION_ISOLATED') != '1' or target['host'] != '127.0.0.1' or target['port'] == 54410
                or 'durai-evaldb-' not in str(target['settings'])):
            raise unittest.SkipTest('Run with the disposable session context verifier')
        prepare_session_context(DATABASE)

    def setUp(self):
        # Sample: Start with an empty queue, an empty ledger, and the flag on with default caps.
        with connect('postgres', DATABASE) as db:
            db.execute('TRUNCATE durai_context.session_node,durai_context.session_attempt,durai_context.session_pending,'
                       'durai_context.session_summary,durai_context.session_turn,durai_context.session_conversation,'
                       'durai_context.session_spend,durai_context.session_cap_event')
        self.record = {'enabled': True}
        patcher = patch(RECORD, side_effect=lambda: self.record)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conversation = 'c-' + uid()
        self.context = SessionContext(DATABASE)
        self.store = SessionContext(DATABASE, 'durai_evaluator')

    def append(self, number, *, conversation=None):
        # Sample: Append flushed turn 3 to the current conversation.
        return self.context.append_turn(conversation or self.conversation, f'm-{number}', 'user',
                                        f'Turn {number}.', 5, flush=True)

    def worker(self, provider):
        # Sample: Build a worker with a scripted provider and the exact reference commit 5c841aa.
        return SessionSummaryWorker(self.store, provider, DEEPSEEK_FLASH, len, lambda claim: ['commit:5c841aa'])

    def make_due(self):
        # Sample: Move the next retry time of every pending range to now.
        with connect('postgres', DATABASE) as db:
            db.execute('UPDATE durai_context.session_pending SET due_at=clock_timestamp() WHERE due_at<>%s',
                       ('infinity',))


class SessionFlagTests(SessionCapBase):
    def test_disabled_flag_makes_no_writes_or_calls(self):
        # Sample: Set enabled false. Send turns 1 to 5 for C-1. Check 0 turn rows, 0 claims, 0 provider calls.
        self.record = {'enabled': False}
        identity = SessionIdentity('trading', 'alice')
        grant = ConversationGrant(identity, self.conversation, 1)
        host = SessionHost(self.context, {'s': identity}.get, lambda who, conversation: grant)
        for number in range(1, 6):
            self.assertIsNone(host.append_turn('s', self.conversation, f'm-{number}', 'user', f'Turn {number}.'))
        self.assertIsNone(host.get_context('s', self.conversation))
        provider = ScriptedProvider()
        worker = host.summary_worker(self.store, None, {grant.storage_id(): grant}.get, provider, DEEPSEEK_FLASH)
        self.assertIsNone(worker.run_once())
        self.assertEqual([], provider.contents)
        with connect('postgres', DATABASE) as db:
            self.assertEqual(0, db.execute('SELECT count(*) AS n FROM durai_context.session_turn').fetchone()['n'])
            self.assertEqual(0, db.execute('SELECT count(*) AS n FROM durai_context.session_attempt').fetchone()['n'])

    def test_flag_off_mid_run_blocks_publish(self):
        # Sample: Claim turns 1 to 12. Set enabled false while the scripted call runs. Check no summary and state failed.
        for number in range(1, 13):
            self.append(number)
        provider = ScriptedProvider(good(['commit:5c841aa'], range(1, 13)),
                                    on_call=lambda: self.record.update(enabled=False))
        self.assertEqual('failed', self.worker(provider).run_once()['state'])
        self.assertEqual(1, len(provider.contents))
        self.assertIsNone(self.store.claim())
        self.assertIsNone(self.context.get_context(self.conversation)['summary'])
        with connect('postgres', DATABASE) as db:
            self.assertEqual(['failed'], [row['state'] for row in db.execute(
                'SELECT state FROM durai_context.session_attempt').fetchall()])

    def test_missing_record_means_enabled(self):
        # Sample: Read a settings file with no sessionSummaries record. Check enabled true and 3 attempts.
        self.record = {}
        loaded = load_session_settings()
        self.assertEqual((True, 3), (loaded.enabled, loaded.maxAttemptsPerRange))


class SessionCapTests(SessionCapBase):
    def fail_calls(self, count):
        # Sample: Script 3 failed calls for turns 1 to 12. Run the worker 3 times. Return the 3 results.
        self.append(1)
        provider = ScriptedProvider(*[TimeoutError('Scripted failure.')] * count)
        worker = self.worker(provider)
        results = []
        for _ in range(count):
            self.make_due()
            results.append(worker.run_once()['state'])
        return results, provider

    def test_range_goes_dead_after_max_attempts(self):
        # Sample: Script 3 failed calls for turns 1 to 12. Check the range state dead and the head unchanged.
        results, provider = self.fail_calls(3)
        self.assertEqual(['failed', 'failed', 'dead'], results)
        self.make_due()
        self.assertIsNone(self.worker(provider).run_once())
        head = self.context.get_context(self.conversation)
        self.assertEqual((None, 0), (head['summary'], head['coveredTurn']))

    def test_invalid_output_retried_once(self):
        # Sample: Script output that omits commit 5c841aa, then valid output. Check 2 calls and a summary with 5c841aa.
        provider = ScriptedProvider(good(), good(['commit:5c841aa']))
        self.append(1)
        self.assertEqual('succeeded', self.worker(provider).run_once()['state'])
        self.assertEqual(2, len(provider.contents))
        self.assertEqual(['commit:5c841aa'], json.loads(provider.contents[1])['retry']['missingRefs'])
        self.assertEqual(['commit:5c841aa'], self.context.get_context(self.conversation)['summary']['refs'])
        self.append(2)
        provider = ScriptedProvider(good(turns=(2,)), good(turns=(2,)))
        self.assertEqual('failed', self.worker(provider).run_once()['state'])
        self.assertEqual(2, len(provider.contents))

    def test_caps_read_from_settings(self):
        # Sample: Set maxAttemptsPerRange 1. Script 1 failed call. Check the range state dead.
        self.record = {'enabled': True, 'maxAttemptsPerRange': 1}
        results, _ = self.fail_calls(1)
        self.assertEqual(['dead'], results)

    def test_summary_output_limit_default(self):
        # Sample: Read a sessionSummaries record with no maxOutputTokens. Check 8000 output tokens and 800 visible tokens.
        with patch('durai.session_settings.read_record', return_value={'enabled': True}):
            loaded = load_session_settings()
        self.assertEqual((8000, 800), (loaded.maxOutputTokens, loaded.maxVisibleTokens))

    def record_today(self, conversation, calls, cost):
        # Sample: Record 50 calls and 5.00 USD for C-1 on the current UTC day.
        with connect('postgres', DATABASE) as db:
            db.execute("INSERT INTO durai_context.session_spend(application_id,conversation_id,day,calls,cost_usd) "
                       "VALUES(%s,%s,(clock_timestamp() AT TIME ZONE 'UTC')::date,%s,%s)",
                       (self.context.application_id(), conversation, calls, cost))

    def cap_events(self):
        # Sample: Read the CAP_REACHED events as cap and conversation pairs.
        with connect('postgres', DATABASE) as db:
            return [(row['cap'], row['conversation_id']) for row in db.execute(
                'SELECT cap,conversation_id FROM durai_context.session_cap_event ORDER BY cap,conversation_id').fetchall()]

    def test_conversation_daily_cap_blocks_claim(self):
        # Sample: Record 50 calls for C-1 today. Check call 51 for C-1 is not claimed and C-2 is claimed.
        other = 'c-' + uid()
        self.append(1)
        self.append(1, conversation=other)
        self.record_today(self.conversation, 50, 0)
        self.make_due()
        claim = self.store.claim()
        self.assertEqual(other, claim['conversationId'])
        self.assertIsNone(self.store.claim())
        self.assertEqual([('maxCallsPerConversationPerDay', self.conversation)], self.cap_events())

    def test_daily_cost_cap_blocks_claim(self):
        # Sample: Record calls with cost 5.00 USD today. Check no claim and one CAP_REACHED event for maxCostUsdPerDay.
        self.append(1)
        self.record_today(self.conversation, 1, 5.00)
        self.make_due()
        self.assertIsNone(self.store.claim())
        self.assertIsNone(self.store.claim())
        self.assertEqual([('maxCostUsdPerDay', '')], self.cap_events())
