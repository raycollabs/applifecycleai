"""Sample: Accept P-1 and lose the response. Look up P-1 and check one charge."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import unittest
from unittest.mock import patch

from test_event_blueprint import install_events

from durai.common import Conflict, digest, uid
from durai.db import ROOT, connect, transaction
from durai.outbox import enqueue
from durai.payment_intents import create_intent, dispatch_charge, read_intent, reconcile, record_result

DATABASE = 'durai_test'


class SampleProvider:
    def __init__(self, mode='SUCCEEDED'):
        # Sample: Start with no charges. Select the outcome for the test.
        self.mode = mode
        self.charges = []
        self.lookups = []
        self.results = {}

    def charge(self, intent):
        # Sample: Accept P-1. Keep its result and lose the response when selected.
        identity = (intent['applicationId'], intent['operationId'])
        self.charges.append(identity)
        if self.mode == 'BEFORE_SEND':
            raise ConnectionError('Sample connection loss before send')
        proof = {key: intent[key] for key in ('applicationId', 'operationId', 'amountMinor', 'currency')}
        proof.update(state='DECLINED' if self.mode == 'DECLINED' else 'SUCCEEDED',
                     providerReference='payment.' + intent['operationId'])
        self.results[identity] = proof
        if self.mode == 'AFTER_ACCEPT':
            raise TimeoutError('Sample response was lost')
        return dict(proof)

    def lookup(self, intent):
        # Sample: Read P-1 under its original tenant. Return its retained proof.
        identity = (intent['applicationId'], intent['operationId'])
        self.lookups.append(identity)
        return self.results.get(identity)


class PaymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply v1 to the disposable server and keep its source digest.
        install_events()
        source = (ROOT / 'blueprints/payments/v1.sql').read_bytes()
        checksum = digest(source)
        with connect('postgres', DATABASE) as db:
            if db.execute("SELECT to_regnamespace('durai_payments') AS id").fetchone()['id'] is None:
                db.execute(source.decode(), prepare=False)
                db.execute('INSERT INTO durai_payments.schema_version VALUES (1,%s)', (checksum,))
            else:
                row = db.execute('SELECT source_digest FROM durai_payments.schema_version WHERE version=1').fetchone()
                if row is None or row['source_digest'] != checksum:
                    raise ValueError('Applied payment blueprint differs. Use a fresh isolated server.')

    def setUp(self):
        # Sample: Use a new order identity for each test.
        self.order = 'order.' + uid()

    def create(self, amount=2500, key=None, role='durai_runtime'):
        # Sample: Create a USD payment intent for the test order.
        return create_intent(self.order, amount, 'USD', key or uid(), DATABASE, role)

    def operation(self):
        # Sample: Create P-1 and return its operation ID.
        return self.create()['outcome']['intent']['operationId']

    def events(self, identity):
        # Sample: Read the local events for P-1 in sequence order.
        with transaction(database=DATABASE) as db:
            return db.execute('SELECT sequence,event_type FROM durai_events.outbox WHERE stream_id=%s ORDER BY sequence',
                              ('payment.' + identity,)).fetchall()

    def test_unknown_result_does_not_charge_again(self):
        # Sample: Accept and lose the response for P-1. Recover by lookup and check one charge.
        identity = self.operation()
        provider = SampleProvider('AFTER_ACCEPT')
        self.assertEqual(dispatch_charge(identity, provider)['state'], 'UNCERTAIN')
        self.assertEqual(dispatch_charge(identity, provider)['state'], 'UNCERTAIN')
        self.assertEqual(reconcile(identity, provider)['state'], 'SUCCEEDED')
        self.assertEqual(provider.charges, [('trading', identity)])
        self.assertEqual(provider.lookups, provider.charges)
        self.assertEqual([r['event_type'] for r in self.events(identity)],
                         ['payment.ready', 'payment.uncertain', 'payment.succeeded'])

    def test_missing_lookup_keeps_intent_uncertain(self):
        # Sample: Lose the connection before send. Look up P-1 and keep UNCERTAIN.
        identity = self.operation()
        provider = SampleProvider('BEFORE_SEND')
        self.assertEqual(dispatch_charge(identity, provider)['state'], 'UNCERTAIN')
        self.assertEqual(reconcile(identity, provider)['state'], 'UNCERTAIN')
        self.assertEqual(dispatch_charge(identity, provider)['state'], 'UNCERTAIN')
        self.assertEqual(len(provider.charges), 1)
        self.assertEqual(len(self.events(identity)), 2)

    def test_concurrent_dispatch_calls_provider_once(self):
        # Sample: Start two dispatches for P-1. Check one call and one paid result.
        identity = self.operation()
        provider = SampleProvider()
        original = provider.charge
        entered, finish = Event(), Event()

        def hold_response(intent):
            # Sample: Read the committed claim before the provider returns its response.
            self.assertEqual(read_intent(identity)['state'], 'UNCERTAIN')
            entered.set()
            self.assertTrue(finish.wait(timeout=10))
            return original(intent)

        with patch.object(provider, 'charge', side_effect=hold_response):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(dispatch_charge, identity, provider)
                try:
                    self.assertTrue(entered.wait(timeout=10))
                    second = pool.submit(dispatch_charge, identity, provider)
                    self.assertEqual(second.result(timeout=8)['state'], 'UNCERTAIN')
                finally:
                    finish.set()
                self.assertEqual(first.result(timeout=8)['state'], 'SUCCEEDED')
        self.assertEqual(provider.charges, [('trading', identity)])
        self.assertEqual(len(self.events(identity)), 3)

    def test_order_and_request_replay_keep_one_intent(self):
        # Sample: Race two creation requests for O-1. Keep one operation and one event.
        gate = Barrier(2)

        def create(key):
            # Sample: Start two creation requests together with the same terms.
            gate.wait(timeout=10)
            return self.create(key=key)

        keys = [uid(), uid()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, keys))
        identity = results[0]['outcome']['intent']['operationId']
        self.assertEqual(identity, results[1]['outcome']['intent']['operationId'])
        self.assertEqual(sum(r['outcome']['event'] is not None for r in results), 1)
        self.assertEqual(self.create(key=keys[0])['outcome'], results[0]['outcome'])
        with self.assertRaises(Conflict):
            self.create(amount=3000)
        with self.assertRaises(Conflict):
            self.create(amount=3000, key=keys[0])
        for amount in (0, True, 1000000000):
            with self.assertRaises(ValueError):
                self.create(amount=amount)
        self.assertEqual(len(self.events(identity)), 1)

    def test_declined_result_is_terminal(self):
        # Sample: Decline P-1. Retry dispatch and reconcile. Keep the decline and one terminal event.
        identity = self.operation()
        provider = SampleProvider('DECLINED')
        declined = dispatch_charge(identity, provider)
        self.assertEqual(declined['state'], 'DECLINED')
        self.assertEqual(dispatch_charge(identity, provider), declined)
        self.assertEqual(reconcile(identity, provider), declined)
        self.assertEqual(len(provider.charges), 1)
        self.assertEqual(self.events(identity)[-1]['event_type'], 'payment.declined')

    def test_lookup_rejects_wrong_identity_or_amount(self):
        # Sample: Look up P-1 and return a different operation, amount, currency, or tenant.
        identity = self.operation()
        provider = SampleProvider('AFTER_ACCEPT')
        dispatch_charge(identity, provider)
        proof = provider.results[('trading', identity)]
        for change in ({'operationId': uid()}, {'amountMinor': 3000}, {'currency': 'EUR'},
                       {'applicationId': 'foreign'}):
            with patch.object(provider, 'lookup', return_value=dict(proof, **change)), self.assertRaises(Conflict):
                reconcile(identity, provider)
        self.assertEqual(read_intent(identity)['state'], 'UNCERTAIN')
        self.assertEqual(len(self.events(identity)), 2)

    def test_late_conflicting_result_is_refused(self):
        # Sample: Commit success for P-1. Replay the proof, then return a late decline.
        identity = self.operation()
        provider = SampleProvider('AFTER_ACCEPT')
        dispatch_charge(identity, provider)
        proof = provider.results[('trading', identity)]
        gate = Barrier(2)

        def record(_):
            # Sample: Record the same final proof from two workers.
            gate.wait(timeout=10)
            return record_result(identity, proof)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(record, range(2))
        self.assertEqual(first, second)
        with self.assertRaises(Conflict):
            record_result(identity, dict(proof, state='DECLINED'))
        self.assertEqual(read_intent(identity)['state'], 'SUCCEEDED')
        self.assertEqual(len(self.events(identity)), 3)

    def test_outbox_failure_allows_lookup_recovery(self):
        # Sample: Accept P-1. Stop the terminal event transaction. Look up P-1 and finish locally.
        identity = self.operation()
        provider = SampleProvider()

        def fail(db, stream, event_type, body):
            # Sample: Save the terminal event and stop before its transaction commits.
            event = enqueue(db, stream, event_type, body)
            if event_type == 'payment.succeeded':
                raise RuntimeError('Sample outbox failure')
            return event

        with patch('durai.payment_intents.enqueue', side_effect=fail), self.assertRaises(RuntimeError):
            dispatch_charge(identity, provider)
        self.assertEqual(read_intent(identity)['state'], 'UNCERTAIN')
        self.assertEqual(len(self.events(identity)), 2)
        self.assertEqual(reconcile(identity, provider)['state'], 'SUCCEEDED')
        self.assertEqual(len(provider.charges), 1)
        self.assertEqual(len(self.events(identity)), 3)

    def test_tenant_scope(self):
        # Sample: Create O-1 in both tenants. Refuse access to the first operation with the second login.
        key = uid()
        first = self.create(key=key)['outcome']['intent']['operationId']
        second = self.create(key=key, role='durai_foreign')['outcome']['intent']['operationId']
        self.assertNotEqual(first, second)
        provider = SampleProvider('AFTER_ACCEPT')
        dispatch_charge(first, provider)
        with self.assertRaises(KeyError):
            reconcile(first, provider, role='durai_foreign')
        with self.assertRaises(KeyError):
            dispatch_charge(first, provider, role='durai_foreign')
        self.assertEqual(provider.lookups, [])
        self.assertEqual(provider.charges, [('trading', first)])
