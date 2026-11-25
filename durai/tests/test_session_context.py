"""Sample: Claim an isolated conversation. Publish a summary and read its raw tail."""
import os
from concurrent.futures import ThreadPoolExecutor
import json
import time
import unittest
from unittest.mock import patch

import psycopg

from durai.common import Conflict, digest, uid
from durai.context_store import ContextStore
from durai.db import connect, server, use_server
from durai.session_context import SessionContext, prepare_session_context
from durai.session_host import ConversationGrant, PlanPin, SessionHost, SessionIdentity
from durai.session_summarizer import DEEPSEEK_FLASH, SessionSummaryWorker, SummaryCompletion

DATABASE = 'durai_test'
PROFILE = {'id': 'scripted-v1', 'model': 'concatenate', 'promptDigest': '0' * 64}


class SessionContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Require the disposable connection descriptor. Check that the migration applies once.
        target = server()
        if (os.getenv('DURAI_SESSION_ISOLATED') != '1' or target['host'] != '127.0.0.1' or target['port'] == 54410
                or 'durai-evaldb-' not in str(target['settings'])):
            raise unittest.SkipTest('Run with the disposable session context verifier')
        first = prepare_session_context(DATABASE)
        if first != prepare_session_context(DATABASE):
            raise AssertionError('Session migration receipt changed')

    def setUp(self):
        # Sample: Start each case with an empty queue in the disposable database.
        with connect('postgres', DATABASE) as db:
            db.execute('TRUNCATE durai_context.session_node,durai_context.session_attempt,durai_context.session_pending,'
                       'durai_context.session_summary,durai_context.session_turn,durai_context.session_conversation,'
                       'durai_context.session_spend,durai_context.session_cap_event')
        patcher = patch('durai.session_settings.read_record', return_value={'enabled': True})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conversation = 'c-' + uid()
        self.context = SessionContext(DATABASE)
        self.worker = SessionContext(DATABASE, 'durai_evaluator')

    def append(self, number, *, text=None, flush=False, tokens=5):
        # Sample: Append M-3 to the current conversation. Return turn 3.
        return self.context.append_turn(self.conversation, f'm-{number}', 'user', text or f'Turn {number}.',
                                        tokens, flush=flush)

    def test_append_is_idempotent(self):
        # Sample: Save M-1 twice. Refuse different text under M-1. Check that M-2 receives turn 2.
        original = self.append(1)
        self.assertEqual(original, self.append(1))
        with self.assertRaises(Conflict):
            self.append(1, text='Changed text.')
        self.assertEqual(2, self.append(2)['turn_number'])

    def test_concurrent_appends_assign_gap_free_turns(self):
        # Sample: Append eight messages on eight connections. Check turn numbers 1 to 8.
        target = server()

        def append(number):
            # Sample: Send message M-3 to the disposable server. Return its assigned turn.
            with use_server(target['host'], target['port'], target['settings']):
                return self.append(number)['turn_number']

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(range(1, 9)), sorted(pool.map(append, range(1, 9))))

    def test_tenant_scope(self):
        # Sample: Store C-1 as trading. Read C-1 as foreign. Store the same ID as foreign. Check each text.
        self.append(1)
        foreign = SessionContext(DATABASE, 'durai_foreign')
        self.assertIsNone(foreign.get_context(self.conversation))
        self.assertEqual([], foreign.read_turns(self.conversation, 1, 1))
        foreign.append_turn(self.conversation, 'm-1', 'user', 'Other tenant.', 5)
        self.assertEqual('Other tenant.', foreign.get_context(self.conversation)['turns'][0]['eligible_text'])
        self.assertEqual('Turn 1.', self.context.get_context(self.conversation)['turns'][0]['eligible_text'])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.context.claim()

    def test_claim_and_publish(self):
        # Sample: Claim turns 1 and 2. Add turn 3. Publish through turn 2 and check that turn 3 is pending.
        self.append(1)
        self.append(2, flush=True)
        claim = self.worker.claim()
        self.assertIsNone(self.worker.claim())
        self.assertEqual((1, 2, 0), (claim['firstTurn'], claim['lastTurn'], claim['revision']))
        self.append(3, flush=True)
        text = ' '.join(item['text'] for item in claim['turns'])
        summary = {'narrative': text, 'sourceTurns': [1, 2]}
        self.assertEqual({'state': 'succeeded', 'revision': 1, 'coveredTurn': 2},
                         self.worker.finish(claim, 'succeeded', summary, PROFILE))
        head = self.context.get_context(self.conversation)
        self.assertEqual(summary, head['summary'])
        self.assertEqual([3], [item['turn_number'] for item in head['turns']])
        next_claim = self.worker.claim()
        self.assertEqual((3, 3, summary), (next_claim['firstTurn'], next_claim['lastTurn'], next_claim['summary']))

    def test_expired_lease_cannot_publish(self):
        # Sample: Wait for generation 1 to expire. Claim generation 2. Refuse generation 1.
        self.append(1, flush=True)
        old = self.worker.claim(lease_seconds=1)
        time.sleep(1.1)
        current = self.worker.claim()
        self.assertEqual(old['generation'] + 1, current['generation'])
        with self.assertRaises(Conflict):
            self.worker.finish(old, 'succeeded', {'narrative': 'Stale text.'}, PROFILE)
        with connect('postgres', DATABASE) as db:
            self.assertEqual(['lost', 'running'], [row['state'] for row in db.execute(
                'SELECT state FROM durai_context.session_attempt ORDER BY generation').fetchall()])

    def test_read_reports_omitted_range(self):
        # Sample: Append five turns. Limit the read to ten tokens. Check turns 4 and 5 and omission 1 to 3.
        for number in range(1, 6):
            self.append(number)
        head = self.context.get_context(self.conversation, tail_tokens=10)
        self.assertEqual((0, 5, [1, 3]), (head['coveredTurn'], head['latestTurn'], head['omittedRange']))
        self.assertEqual([4, 5], [turn['turn_number'] for turn in head['turns']])
        self.assertEqual([1, 2, 3], [turn['turn_number'] for turn in self.context.read_turns(self.conversation, 1, 3)])

    def test_claim_respects_the_token_limit(self):
        # Sample: Append two turns of 2,000 tokens. Claim the first turn. Publish it. Check that the next claim starts at turn 2.
        self.append(1, tokens=2000)
        self.append(2, tokens=2000)
        first = self.worker.claim()
        self.assertEqual((1, 1), (first['firstTurn'], first['lastTurn']))
        self.worker.finish(first, 'succeeded', {'narrative': 'Turn 1.'}, PROFILE)
        second = self.worker.claim()
        self.assertEqual((2, 2), (second['firstTurn'], second['lastTurn']))

    def test_dead_work_keeps_the_head_and_tail(self):
        # Sample: Publish turn 1. End the job for turn 2 as dead. Check turn 1 and the raw second turn.
        self.append(1, flush=True)
        summary = {'narrative': 'Turn 1.'}
        self.worker.finish(self.worker.claim(), 'succeeded', summary, PROFILE)
        self.append(2, flush=True)
        self.worker.finish(self.worker.claim(), 'dead')
        head = self.context.get_context(self.conversation)
        self.assertEqual(summary, head['summary'])
        self.assertEqual([2], [turn['turn_number'] for turn in head['turns']])
        self.assertIsNone(self.worker.claim())

    def test_withdrawn_source_invalidates_head_and_lease(self):
        # Sample: Publish a summary with turn 1. Claim turn 2. Withdraw turn 1. Refuse the old publication.
        self.append(1, flush=True)
        first = self.worker.claim()
        self.worker.finish(first, 'succeeded', {'narrative': 'Turn 1.'}, PROFILE)
        self.append(2, flush=True)
        stale = self.worker.claim()
        self.assertTrue(self.worker.withdraw_turn(self.conversation, 1))
        head = self.context.get_context(self.conversation)
        self.assertIsNone(head['summary'])
        self.assertIsNone(head['turns'][0]['eligible_text'])
        self.assertIsNone(self.append(1)['eligible_text'])
        with self.assertRaises(Conflict):
            self.worker.finish(stale, 'succeeded', {'narrative': 'Includes withdrawn text.'}, PROFILE)

    def test_scripted_worker_preserves_head_and_references(self):
        # Sample: Publish turn 1 and its commit. Return a summary that cites turn 9 for turn 2. Keep the first head.
        inputs = []
        body = {'goal': 'Review the change.', 'decisions': [], 'corrections': [], 'openQuestions': [],
                'nextAction': 'Check the receipt.', 'sourceTurns': [1], 'refs': ['commit:abc123']}
        case = self

        class ScriptedProvider:
            def complete(self, system, content, profile):
                # Sample: Check the lease is committed before the provider enters. Return two scripted bodies.
                inputs.append(json.loads(content))
                case.assertIsNone(case.worker.claim())
                result = body if len(inputs) == 1 else dict(body, sourceTurns=[9], refs=[])
                return SummaryCompletion(json.dumps(result), 'stop')

        self.append(1, flush=True)
        worker = SessionSummaryWorker(self.worker, ScriptedProvider(), DEEPSEEK_FLASH, len,
                                      lambda claim: ['commit:abc123'] if claim['firstTurn'] == 1 else [])
        retries = patch('durai.session_settings.read_record', return_value={
            'enabled': True, 'invalidOutputRetries': 0, 'maxNodeVisibleTokens': 800})
        retries.start()
        self.addCleanup(retries.stop)
        self.assertEqual('succeeded', worker.run_once()['state'])
        self.append(2, flush=True)
        self.assertEqual('failed', worker.run_once()['state'])
        self.assertEqual([['commit:abc123'], []], [entry['requiredRefs'] for entry in inputs])
        head = self.context.get_context(self.conversation)
        self.assertEqual((1, body, [2]), (head['coveredTurn'], head['summary'], [row['turn_number'] for row in head['turns']]))
        with connect('postgres', DATABASE) as db:
            profile = db.execute('SELECT profile FROM durai_context.session_summary').fetchone()['profile']
        self.assertEqual(dict(DEEPSEEK_FLASH.record(), maxVisibleTokens=800), profile)

    def host(self, *, identity=None, plan_ids=frozenset(), pins=frozenset()):
        # Sample: Register C-1 for Alice. Return a session host with three synthetic sign-ins.
        identities = {'alice-session': SessionIdentity('trading', 'alice'),
                      'bob-session': SessionIdentity('trading', 'bob'),
                      'foreign-session': SessionIdentity('foreign', 'alice')}
        registry = {self.conversation: ConversationGrant(identity or identities['alice-session'],
                                                         self.conversation, 1, plan_ids, pins)}
        host = SessionHost(self.context, identities.get, lambda subject, conversation: registry.get(conversation))
        return host, registry, identities

    def test_host_checks_owner_and_tenant(self):
        # Sample: Save C-1 as Alice. Refuse Bob and the foreign login. Reassign C-1 to Bob and check his empty context.
        host, registry, identities = self.host()
        row = host.append_turn('alice-session', self.conversation, 'm-1', 'user', 'Alice turn.')
        self.assertEqual(self.conversation, row['conversation_id'])
        first = host.get_context('alice-session', self.conversation)
        self.assertEqual(first, host.get_context('alice-session', self.conversation))
        self.assertEqual(self.conversation, first['conversationId'])
        for token in ('unknown-session', 'bob-session', 'foreign-session'):
            with self.assertRaises(PermissionError):
                host.get_context(token, self.conversation)
            with self.assertRaises(PermissionError):
                host.read_turns(token, self.conversation, 1, 1)
        registry[self.conversation] = ConversationGrant(identities['foreign-session'], self.conversation, 2)
        with self.assertRaises(PermissionError):
            host.get_context('foreign-session', self.conversation)
        registry[self.conversation] = ConversationGrant(identities['bob-session'], self.conversation, 3)
        self.assertIsNone(host.get_context('bob-session', self.conversation))
        with self.assertRaises(PermissionError):
            host.get_context('alice-session', self.conversation)
        later = host.append_turn('bob-session', self.conversation, 'm-1', 'user', 'Bob turn.')
        self.assertEqual(1, later['turn_number'])
        self.assertEqual('Bob turn.', host.get_context('bob-session', self.conversation)['turns'][0]['eligible_text'])
        registry[self.conversation] = ConversationGrant(identities['bob-session'], self.conversation, 4)
        self.assertIsNone(host.get_context('bob-session', self.conversation))

    def test_host_stores_visible_text_and_counts_tokens(self):
        # Sample: Save é as visible assistant text. Count two bytes. Refuse a tool turn, a credential, and an unassigned plan.
        host, _, _ = self.host()
        row = host.append_turn('alice-session', self.conversation, 'm-1', 'assistant', 'é')
        self.assertEqual((2, 'é'), (row['token_count'], row['eligible_text']))
        with self.assertRaises(ValueError):
            host.append_turn('alice-session', self.conversation, 'tool-1', 'tool', 'Protected tool result.')
        with self.assertRaises(Conflict):
            host.append_turn('alice-session', self.conversation, 'secret-1', 'user', 'Bearer private-test-credential')
        with self.assertRaises(PermissionError):
            host.append_turn('alice-session', self.conversation, 'plan-1', 'user', 'Turn 2.', plan_id='not-assigned')
        context = host.get_context('alice-session', self.conversation)
        self.assertEqual(1, context['latestTurn'])

    def test_host_worker_pins_exact_plan_records(self):
        # Sample: Pin status and approval/001. Check that the provider receives their keys and digests only.
        plan = 'session-plan-' + uid()
        plans = ContextStore(DATABASE, 'durai_evaluator')
        reviewer = ContextStore(DATABASE, 'durai_reviewer')
        plans.open_plan(plan, 'Summary pilot.')
        revision = plans.append(plan, 'plan', {'text': 'Plan text.', 'sourcePath': 'synthetic.md',
                                 'textDigest': digest(b'Plan text.'), 'previousRevisionDigest': None}, 'plan')
        status = plans.set_status(plan, {'phase': 'B', 'state': 'RUNNING', 'nextAction': 'Private status body.',
                                  'planRevisionDigest': revision['digest']}, 0, 'status')
        approval = reviewer.append(plan, 'approval', {'subjectDigest': revision['digest'], 'decision': 'APPROVE',
                                   'scope': 'Private approval body.'}, 'approve')
        pins = frozenset({PlanPin(plan, 'status'), PlanPin(plan, approval['key'])})
        host, registry, _ = self.host(plan_ids=frozenset({plan}), pins=pins)
        grant = registry[self.conversation]
        host.append_turn('alice-session', self.conversation, 'm-1', 'user', 'Review the staged plan.', plan, flush=True)
        inputs = []

        class ScriptedProvider:
            def complete(self, system, content, profile):
                # Sample: Echo the exact pins for the staged plan. Return a summary for turn 1.
                inputs.append(content)
                refs = json.loads(content)['requiredRefs']
                body = {'goal': 'Review the plan.', 'decisions': [], 'corrections': [], 'openQuestions': [],
                        'nextAction': '', 'sourceTurns': [1], 'refs': refs}
                return SummaryCompletion(json.dumps(body), 'stop')

        worker = host.summary_worker(self.worker, plans, {grant.storage_id(): grant}.get,
                                     ScriptedProvider(), DEEPSEEK_FLASH)
        wide = patch('durai.session_settings.read_record', return_value={'enabled': True, 'maxNodeVisibleTokens': 800})
        wide.start()
        self.addCleanup(wide.stop)
        self.assertEqual('succeeded', worker.run_once()['state'])
        expected = [f'plan:{plan}:{approval["key"]}@{approval["digest"]}', f'plan:{plan}:status@{status["digest"]}']
        head = host.get_context('alice-session', self.conversation)
        self.assertEqual(expected, head['summary']['refs'])
        self.assertNotIn('Private status body.', inputs[0])
        self.assertNotIn('Private approval body.', inputs[0])
        self.assertEqual('APPROVE', plans.read(plan, approval['key'])['value']['decision'])

    def test_host_worker_checks_revocation_before_publication(self):
        # Sample: Remove Alice's grant during the scripted call. Check that the old claim cannot publish.
        host, registry, identities = self.host()
        grant = registry[self.conversation]
        host.append_turn('alice-session', self.conversation, 'm-1', 'user', 'Old turn.', flush=True)
        case = self

        class ScriptedProvider:
            def complete(self, system, content, profile):
                # Sample: Reassign the conversation to Bob. Return a summary of Alice's old turn.
                registry[case.conversation] = ConversationGrant(identities['bob-session'], case.conversation, 2)
                body = {'goal': 'Summarize the old turn.', 'decisions': [], 'corrections': [], 'openQuestions': [],
                        'nextAction': '', 'sourceTurns': [1], 'refs': []}
                return SummaryCompletion(json.dumps(body), 'stop')

        worker = host.summary_worker(self.worker, ContextStore(DATABASE, 'durai_evaluator'),
                                     {grant.storage_id(): grant}.get, ScriptedProvider(), DEEPSEEK_FLASH)
        self.assertEqual('dead', worker.run_once()['state'])
        self.assertIsNone(host.get_context('bob-session', self.conversation))
        with self.assertRaises(PermissionError):
            host.get_context('alice-session', self.conversation)
        self.assertIsNone(self.context.get_context(grant.storage_id())['summary'])
