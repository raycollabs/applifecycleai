"""Sample: Refund 600 of 1000 units. Keep the amount reserved while the result is uncertain."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from unittest.mock import patch

from psycopg.errors import CheckViolation

import test_payment_blueprint as payment_fixture
from durai.common import Conflict, digest, uid
from durai.db import ROOT, connect, transaction
from durai.outbox import enqueue
from durai.payment_funds import (balances, create_operation, dispatch, read_result, reconcile,
                                 record_result, register_authorization, register_direct_payment)
from durai.payment_intents import create_intent, dispatch_charge

DATABASE = 'durai_test'


class FundsProvider:
    def __init__(self, state='SUCCEEDED', lost=False):
        # Sample: Start with no submitted operations. Select a success or a decline.
        self.state = state
        self.lost = lost
        self.submissions = []
        self.lookups = []
        self.results = {}

    def submit(self, operation):
        # Sample: Store the provider result for R-1 and lose the response when selected.
        identity = operation['operationId']
        self.submissions.append(identity)
        self.results[identity] = dict(operation, state=self.state, providerReference='provider.' + identity)
        if self.lost:
            raise TimeoutError('Sample provider response was lost')
        return self.results[identity]

    def lookup(self, operation):
        # Sample: Use the original operation ID to retrieve the provider result.
        identity = operation['operationId']
        self.lookups.append(identity)
        return self.results.get(identity)


class CaptureRefundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Install the original schema and record the source digest for v2.
        payment_fixture.PaymentTests.setUpClass()
        source = (ROOT / 'blueprints/payments/v2.sql').read_bytes()
        checksum = digest(source)
        with connect('postgres', DATABASE) as db:
            row = db.execute('SELECT source_digest FROM durai_payments.schema_version WHERE version=2').fetchone()
            if row is None:
                db.execute(source.decode(), prepare=False)
                db.execute('INSERT INTO durai_payments.schema_version VALUES (2,%s)', (checksum,))
            elif row['source_digest'] != checksum:
                raise ValueError('Applied v2 differs. Use a fresh isolated server.')

    def manual(self, order=None):
        # Sample: Register an authorized payment for 1000 minor units.
        order = order or 'order.' + uid()
        proof = {'orderId': order, 'applicationId': 'trading', 'account': 'merchant-a',
                 'providerReference': 'auth.' + uid(), 'amountMinor': 1000, 'currency': 'USD', 'state': 'AUTHORIZED'}
        return register_authorization(order, 1000, 'USD', proof, uid())['outcome']['paymentId']

    def direct(self):
        # Sample: Charge 1000 units and register the confirmed payment for refunds.
        payment = create_intent('order.' + uid(), 1000, 'USD', uid())['outcome']['intent']['operationId']
        dispatch_charge(payment, payment_fixture.SampleProvider())
        return register_direct_payment(payment, 'merchant-a')['paymentId']

    def operation(self, payment, kind, amount, key=None, role='durai_runtime'):
        # Sample: Prepare R-1 with 600 units reserved.
        return create_operation(kind, payment, amount, key or uid(), role=role)['outcome']

    def events(self, identity):
        # Sample: Count the committed events for R-1.
        with transaction(database=DATABASE) as db:
            return db.execute('SELECT count(*) AS n FROM durai_events.outbox WHERE stream_id=%s',
                              ('payment.op.' + identity,)).fetchone()['n']

    def test_manual_capture_recovers_lost_response(self):
        # Sample: Capture 600 of 1000 units. Lose the response and recover it by lookup.
        payment = self.manual()
        op = self.operation(payment, 'CAPTURE', 600)['operation']['operationId']
        provider = FundsProvider(lost=True)
        self.assertEqual(dispatch(op, provider)['state'], 'UNCERTAIN')
        self.assertEqual(balances(payment)['captured'], 0)
        self.assertEqual(dispatch(op, provider)['state'], 'UNCERTAIN')
        self.assertEqual(reconcile(op, provider)['state'], 'SUCCEEDED')
        self.assertEqual(provider.submissions, [op])
        self.assertEqual(provider.lookups, [op])
        self.assertEqual(balances(payment)['captured'], 600)
        self.assertEqual(self.events(op), 3)
        refund = self.operation(payment, 'REFUND', 600)['operation']
        self.assertEqual(refund['targetReference'], provider.results[op]['providerReference'])

    def test_capture_validation_and_direct_charge_exclusion(self):
        # Sample: Refuse capture of 1100 units and a direct charge on the manual payment.
        order = 'order.' + uid()
        payment = self.manual(order)
        with self.assertRaises(Conflict):
            self.operation(payment, 'CAPTURE', 1100)
        provider = payment_fixture.SampleProvider()
        with self.assertRaises(CheckViolation):
            dispatch_charge(payment, provider)
        self.assertEqual(provider.charges, [])
        old = create_intent(order, 1000, 'USD', uid())['outcome']
        self.assertEqual(old['intent']['operationId'], payment)
        self.assertIsNone(old['event'])
        self.operation(payment, 'CAPTURE', 1000)
        with self.assertRaises(Conflict):
            self.operation(payment, 'CAPTURE', 1000)
        with self.assertRaises(Conflict):
            self.operation(self.direct(), 'CAPTURE', 600)

    def test_direct_charge_supports_partial_refunds(self):
        # Sample: Refund 300 and 700 units from a confirmed direct payment.
        payment = self.direct()
        provider = FundsProvider()
        for amount in (300, 700):
            op = self.operation(payment, 'REFUND', amount)['operation']['operationId']
            self.assertEqual(dispatch(op, provider)['state'], 'SUCCEEDED')
        self.assertEqual(balances(payment), {'captured':1000,'refund_reserved':0,'refunded':1000})
        self.assertEqual(self.operation(payment, 'REFUND', 1)['result'], 'INSUFFICIENT_REFUND_BALANCE')

    def test_concurrent_refunds_share_captured_limit(self):
        # Sample: Start two refunds of 600 units against 1000 captured. Check one accepted operation.
        payment = self.direct()
        gate = Barrier(2)

        def reserve(_):
            # Sample: Submit both refund requests after the gate opens.
            gate.wait(timeout=10)
            return self.operation(payment, 'REFUND', 600)['result']

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, range(2)))
        self.assertCountEqual(results, ['READY','INSUFFICIENT_REFUND_BALANCE'])
        self.assertEqual(balances(payment)['refund_reserved'], 600)

    def test_uncertain_refund_reserves_balance(self):
        # Sample: Lose the response to R-1. Keep 600 units reserved until lookup confirms R-1.
        payment = self.direct()
        op = self.operation(payment, 'REFUND', 600)['operation']['operationId']
        provider = FundsProvider(lost=True)
        dispatch(op, provider)
        self.assertEqual(reconcile(op, FundsProvider())['state'], 'UNCERTAIN')
        self.assertEqual(self.operation(payment, 'REFUND', 600)['result'], 'INSUFFICIENT_REFUND_BALANCE')
        self.assertEqual(dispatch(op, provider)['state'], 'UNCERTAIN')
        self.assertEqual(reconcile(op, provider)['state'], 'SUCCEEDED')
        self.assertEqual(provider.submissions, [op])
        self.assertEqual(provider.lookups, [op])
        self.assertEqual(balances(payment), {'captured':1000,'refund_reserved':0,'refunded':600})

    def test_declined_refund_releases_balance(self):
        # Sample: Decline a refund and release its 600-unit reservation.
        payment = self.direct()
        op = self.operation(payment, 'REFUND', 600)['operation']['operationId']
        provider = FundsProvider(state='DECLINED')
        self.assertEqual(dispatch(op, provider)['state'], 'DECLINED')
        self.assertEqual(balances(payment)['refund_reserved'], 0)
        self.assertEqual(self.operation(payment, 'REFUND', 1000)['result'], 'READY')

    def test_replay_and_proof_match(self):
        # Sample: Replay K-1. Refuse a changed amount and a changed provider target.
        payment = self.direct()
        key = uid()
        first = self.operation(payment, 'REFUND', 300, key)
        self.assertEqual(self.operation(payment, 'REFUND', 300, key), first)
        with self.assertRaises(Conflict):
            self.operation(payment, 'REFUND', 400, key)
        op = first['operation']['operationId']
        provider = FundsProvider(lost=True)
        dispatch(op, provider)
        proof = provider.results[op]
        with self.assertRaises(Conflict):
            record_result(op, dict(proof, account='merchant-b'))
        with self.assertRaises(Conflict):
            record_result(op, dict(proof, targetReference='wrong-target'))
        self.assertEqual(read_result(op)['state'], 'UNCERTAIN')
        confirmed = record_result(op, proof)
        self.assertEqual(record_result(op, proof), confirmed)
        with self.assertRaises(Conflict):
            record_result(op, dict(proof, state='DECLINED'))
        self.assertEqual(self.events(op), 3)

    def test_foreign_scope_and_unpaid_intent(self):
        # Sample: Refuse a foreign refund and a refund of a ready payment.
        ready = create_intent('order.' + uid(), 1000, 'USD', uid())['outcome']['intent']['operationId']
        with self.assertRaises(Conflict):
            register_direct_payment(ready, 'merchant-a')
        payment = self.direct()
        with self.assertRaises(KeyError):
            self.operation(payment, 'REFUND', 100, role='durai_foreign')
        op = self.operation(payment, 'REFUND', 100)['operation']['operationId']
        provider = FundsProvider()
        with self.assertRaises(KeyError):
            dispatch(op, provider, role='durai_foreign')
        self.assertEqual(provider.submissions, [])

    def test_terminal_write_failure_rolls_back(self):
        # Sample: Save the refund result and stop before commit. Recover and move the reserved units once.
        payment = self.direct()
        op = self.operation(payment, 'REFUND', 600)['operation']['operationId']
        provider = FundsProvider()

        def fail(db, stream, event_type, doc):
            # Sample: Write the final event and raise an error before commit.
            event = enqueue(db, stream, event_type, doc)
            if event_type == 'payment.refund.succeeded':
                raise RuntimeError('Sample outbox failure')
            return event

        with patch('durai.payment_funds.enqueue', side_effect=fail), self.assertRaises(RuntimeError):
            dispatch(op, provider)
        self.assertEqual(balances(payment), {'captured':1000,'refund_reserved':600,'refunded':0})
        self.assertEqual(self.events(op), 2)
        self.assertEqual(reconcile(op, provider)['state'], 'SUCCEEDED')
        self.assertEqual(balances(payment)['refunded'], 600)
        self.assertEqual(provider.submissions, [op])
