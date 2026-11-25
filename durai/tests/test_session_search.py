"""Sample: Publish a node with commit 93a7a7b. Search the commit. Check the node first."""
import os
import unittest
from unittest.mock import patch

from durai.common import uid
from durai.db import connect, server
from durai.session_context import SessionContext, prepare_session_context
from durai.session_host import ConversationGrant, SessionHost, SessionIdentity

DATABASE = 'durai_test'
PROFILE = {'id': 'scripted-v1', 'model': 'concatenate', 'promptDigest': '0' * 64}


class SessionSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Require the disposable connection descriptor. Apply both migrations.
        target = server()
        if (os.getenv('DURAI_SESSION_ISOLATED') != '1' or target['host'] != '127.0.0.1' or target['port'] == 54410
                or 'durai-evaldb-' not in str(target['settings'])):
            raise unittest.SkipTest('Run with the disposable session context verifier')
        prepare_session_context(DATABASE)

    def setUp(self):
        # Sample: Start with empty session tables, flag on, and 12 turns published as one node with ref 93a7a7b.
        with connect('postgres', DATABASE) as db:
            db.execute('TRUNCATE durai_context.session_node,durai_context.session_attempt,durai_context.session_pending,'
                       'durai_context.session_summary,durai_context.session_turn,durai_context.session_conversation,'
                       'durai_context.session_spend,durai_context.session_cap_event')
        patcher = patch('durai.session_settings.read_record', return_value={'enabled': True})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conversation = 'c-' + uid()
        self.context = SessionContext(DATABASE)
        self.store = SessionContext(DATABASE, 'durai_evaluator')
        for number in range(1, 13):
            self.context.append_turn(self.conversation, f'm-{number}', 'user', f'Turn {number} about lots.', 5, flush=True)
        claim = self.store.claim()
        body = {'goal': 'Book lots at settlement.', 'decisions': ['Use commit 93a7a7b'], 'corrections': [],
                'openQuestions': [], 'nextAction': 'Go.', 'sourceTurns': list(range(1, 13)), 'refs': ['93a7a7b']}
        self.store.publish_segment(claim, body, PROFILE, body)

    def test_search_finds_reference_in_node(self):
        # Sample: Publish a node for turns 1 to 12 with ref 93a7a7b. Search "93a7a7b". Check the first row is that node.
        rows = self.store.search(self.conversation, '93a7a7b')
        self.assertEqual(('node', [1, 12], ['93a7a7b']), (rows[0]['kind'], rows[0]['turns'], rows[0]['refs']))
        self.assertEqual('node:0:1-12', rows[0]['id'])
        self.assertLessEqual(len(rows[0]['excerpt']), 200)
        self.assertEqual(12, len(self.store.search(self.conversation, 'lots', 20)) - 1)

    def test_search_respects_owner(self):
        # Sample: Search the same conversation as owner B. Check refusal and a foreign-owner handle with 0 rows.
        identities = {'alice': SessionIdentity('trading', 'alice'), 'bob': SessionIdentity('trading', 'bob')}
        grant = ConversationGrant(identities['alice'], self.conversation, 1)
        host = SessionHost(self.context, identities.get, lambda who, conversation: grant)
        with self.assertRaises(PermissionError):
            host.search('bob', self.conversation, '93a7a7b')
        self.assertEqual([], self.store.search('c-other-' + uid(), '93a7a7b'))


if __name__ == '__main__':
    unittest.main()
