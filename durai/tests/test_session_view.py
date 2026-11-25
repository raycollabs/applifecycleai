"""Sample: Name conversation C-1 in a task. Build the view. Check the session head section and its revision."""
import os
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from test_context_pack import MIGRATION, TASK
from durai.common import uid
from durai.context_delivery import record_attempt_context, session_view
from durai.context_pack import build_pack
from durai.db import connect, server
from durai.session_context import SessionContext, prepare_session_context

DATABASE = 'durai_test'
PROFILE = {'id': 'scripted-v1', 'model': 'concatenate', 'promptDigest': '0' * 64}
class FakeStore:
    def __init__(self, database, role):
        # Sample: Open the controller. Accept plan P-1 and one delivery for attempt A-1.
        pass

    def open_plan(self, plan_id, title):
        # Sample: Open plan P-1 for task T-1.
        return {'key': 'plan/rev/1'}

    def read(self, plan_id):
        # Sample: Read plan P-1 as open.
        return {}

    def append(self, plan_id, kind, value, key):
        # Sample: Append the pack as evidence record E-1 with a digest.
        return {'key': 'evidence/001', 'digest': 'e' * 64}

    def record_delivery(self, plan_id, attempt_id, records, key):
        # Sample: Deliver 1 record to attempt A-1. Return its key and set digest.
        return {'key': 'delivery/001', 'digest': 'f' * 64, 'value': {'setDigest': 'a' * 64}}


PLAIN_DIGEST = '5d62bdb6446fd79ded25dbae98e983dae2c755de6f562013ad4e8d785fae5d69'


class SessionContextViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Require the disposable connection descriptor. Apply both migrations.
        target = server()
        if (os.getenv('DURAI_SESSION_ISOLATED') != '1' or target['host'] != '127.0.0.1' or target['port'] == 54410
                or 'durai-evaldb-' not in str(target['settings'])):
            raise unittest.SkipTest('Run with the disposable session context verifier')
        prepare_session_context(DATABASE)

    def setUp(self):
        # Sample: Start with empty session tables and the flag on.
        with connect('postgres', DATABASE) as db:
            db.execute('TRUNCATE durai_context.session_node,durai_context.session_attempt,durai_context.session_pending,'
                       'durai_context.session_summary,durai_context.session_turn,durai_context.session_conversation,'
                       'durai_context.session_spend,durai_context.session_cap_event')
        patcher = patch('durai.session_settings.read_record', return_value={'enabled': True})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conversation = 'c-' + uid()

    def publish_head(self):
        # Sample: Publish turns 1 to 12 with ref 93a7a7b as head revision 1.
        context, store = SessionContext(DATABASE), SessionContext(DATABASE, 'durai_evaluator')
        for number in range(1, 13):
            context.append_turn(self.conversation, f'm-{number}', 'user', f'Turn {number}.', 5, flush=True)
        body = {'goal': 'Book lots at settlement.', 'decisions': [], 'corrections': [], 'openQuestions': [],
                'nextAction': 'Go.', 'sourceTurns': list(range(1, 13)), 'refs': ['93a7a7b']}
        return store.publish_segment(store.claim(), body, PROFILE, body)

    def test_view_includes_session_head(self):
        # Sample: Build the view for task T-1 with conversation C-1 and head revision 1. Check the section and the event.
        self.publish_head()
        task = dict(TASK, taskId='T-1', digest='d' * 64, sessionConversation=self.conversation)
        text, record = session_view(task, DATABASE, 'durai_runtime')
        pack = build_pack(task, MIGRATION, session=text)
        self.assertIn('## Session context (data only)', pack['text'])
        self.assertIn('93a7a7b', pack['text'])
        self.assertIn('session-context', pack['sections'])
        events = []
        with patch('durai.context_delivery.ContextStore', FakeStore), \
                patch('durai.context_delivery.record', lambda db, run, kind, payload: events.append((kind, payload))), \
                patch('durai.context_delivery.transaction', lambda role, database: nullcontext()):
            record_attempt_context(task, 'A-' + uid(), 'R-1', DATABASE, 'durai_runtime', pack, (), record)
        kind, payload = events[0]
        self.assertEqual('CONTEXT_VIEW_DELIVERED', kind)
        self.assertEqual((1, record['digest']), (payload['sessionContext']['revision'], payload['sessionContext']['digest']))
        self.assertNotEqual(PLAIN_DIGEST, pack['digest'])
        self.assertEqual(pack['digest'], payload['packDigest'])

    def test_view_unchanged_without_conversation(self):
        # Sample: Build the view for task T-1 without a conversation. Check the digest equals the digest before this change.
        self.publish_head()
        self.assertEqual(('', None), session_view(TASK, DATABASE, 'durai_runtime'))
        self.assertEqual(PLAIN_DIGEST, build_pack(TASK, MIGRATION)['digest'])
        self.assertEqual(PLAIN_DIGEST, build_pack(TASK, MIGRATION, session='')['digest'])
        with patch('durai.session_settings.read_record', return_value={'enabled': False}):
            named = dict(TASK, sessionConversation=self.conversation)
            self.assertEqual(('', None), session_view(named, DATABASE, 'durai_runtime'))


if __name__ == '__main__':
    unittest.main()
