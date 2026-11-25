"""Sample: Commit O-1, lose an acknowledgment for E-1, and check one inventory change."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest

from test_idempotency_blueprint import install_fixture, order_handler

from durai.common import Conflict, digest, uid
from durai.db import ROOT, connect, transaction
from durai.idempotency import execute_request
from durai.inbox import consume
from durai.outbox import claim_batch, enqueue, relay_once, settle

DATABASE = 'durai_test'
SCHEMA = ('blueprints/events/v1.sql', 'blueprints/events/reference.sql')


def install_events():
    # Sample: Apply the event schema only to the isolated server. Check its source digest on replay.
    install_fixture()
    sources = [(ROOT / name).read_bytes() for name in SCHEMA]
    checksum = digest([digest(source) for source in sources])
    with connect('postgres', DATABASE) as db:
        exists = db.execute("SELECT to_regnamespace('durai_events') AS id").fetchone()['id']
        if exists is None:
            for source in sources:
                db.execute(source.decode('utf-8'), prepare=False)
            db.execute('INSERT INTO durai_events.schema_version VALUES (1, %s)', (checksum,))
        else:
            row = db.execute('SELECT source_digest FROM durai_events.schema_version WHERE version = 1').fetchone()
            if row is None or row['source_digest'] != checksum:
                raise ValueError('Applied event blueprint differs. Use a fresh isolated server.')


def record_inventory(db, event):
    # Sample: Add 2 items to the inventory change ledger for E-1. Return the entry ID.
    entry = uid()
    db.execute('INSERT INTO durai_events.example_inventory_change (id, event_id, units) VALUES (%s,%s,%s)',
               (entry, event['eventId'], event['payload']['units']))
    return {'entryId': entry, 'units': event['payload']['units']}


class EventBlueprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Install both event and idempotency schema versions on the disposable database.
        install_events()

    def setUp(self):
        # Sample: Give each test a separate order stream.
        self.stream = 'order.' + uid()

    def save(self, stream=None, max_attempts=5):
        # Sample: Commit E-1 with 2 items in stream O-1.
        with transaction(database=DATABASE) as db:
            return enqueue(db, stream or self.stream, 'order.created', {'units': 2}, max_attempts)

    def order(self, handler, key):
        # Sample: Use K-1 to create O-1 through the idempotency API.
        return execute_request('order.create.events', key, {'units': 2}, handler, DATABASE)

    def create_order(self, db, request):
        # Sample: Save order O-1 and enqueue its event in the same transaction.
        result = order_handler(db, request)
        event = enqueue(db, self.stream, 'order.created', {'units': request['units'], 'orderId': result['orderId']})
        return dict(result, event=event)

    def claims(self, **options):
        # Sample: Claim one due event from this test stream.
        return claim_batch(DATABASE, stream_id=self.stream, **options)

    def receive(self, event, handler=record_inventory, consumer='inventory', role='durai_runtime'):
        # Sample: Deliver E-1 to the inventory consumer under the trading login.
        return consume(consumer, event, handler, DATABASE, role)

    def count_effects(self, event, role='durai_runtime'):
        # Sample: Count the local inventory effects of E-1 in the current tenant.
        with transaction(role, DATABASE) as db:
            return db.execute('SELECT count(*) AS n FROM durai_events.example_inventory_change WHERE event_id=%s',
                              (event['eventId'],)).fetchone()['n']

    def event_state(self, event):
        # Sample: Read E-1 and check its retained publication state.
        with transaction(database=DATABASE) as db:
            return db.execute('SELECT state, attempts FROM durai_events.outbox WHERE event_id=%s',
                              (event['eventId'],)).fetchone()

    def test_rollback_removes_order_and_event(self):
        # Sample: Save an order and event and raise an error. Check that neither can be read.
        created = {}

        def stop(db, request):
            # Sample: Save O-1 and E-1. Stop before the commit.
            created.update(self.create_order(db, request))
            raise RuntimeError('Sample stop before commit')

        key = uid()
        with self.assertRaises(RuntimeError):
            self.order(stop, key)
        with transaction(database=DATABASE) as db:
            self.assertIsNone(db.execute('SELECT id FROM durai_idempotency.example_order WHERE id=%s',
                                         (created['orderId'],)).fetchone())
            self.assertIsNone(db.execute('SELECT stream_id FROM durai_events.stream WHERE stream_id=%s',
                                         (self.stream,)).fetchone())
        self.assertIsNone(self.event_state(created['event']))
        self.assertEqual(self.claims(), [])
        self.assertEqual(self.order(self.create_order, key)['outcome']['event']['sequence'], 1)

    def test_committed_order_survives_relay_restart(self):
        # Sample: Commit O-1 and lose its response. Retry K-1, then publish the retained E-1.
        key = uid()
        original = self.order(self.create_order, key)
        retry = self.order(lambda db, request: self.fail('Repeated producer action'), key)
        event = original['outcome']['event']
        self.assertEqual(retry['outcome']['event'], event)
        self.assertEqual(self.event_state(event)['state'], 'PENDING')
        seen = []

        def publish(delivered):
            # Sample: Receive E-1 from the relay and return its acknowledgment.
            seen.append(delivered)
            return True

        result = relay_once(publish, DATABASE, stream_id=self.stream)
        self.assertEqual(seen, [event])
        self.assertEqual(result, {'eventId': event['eventId'], 'state': 'PUBLISHED'})
        self.assertEqual(self.claims(), [])

    def test_lost_ack_reuses_identity_and_applies_once(self):
        # Sample: Apply E-1 and lose its acknowledgment. Retry the relay and apply E-1 once in total.
        event = self.save()
        deliveries = []

        def publish(delivered):
            # Sample: Commit the inventory change. Lose the first acknowledgment for E-1.
            deliveries.append(self.receive(delivered))
            if len(deliveries) == 1:
                raise TimeoutError('Sample acknowledgment loss')
            return True

        with self.assertRaises(TimeoutError):
            relay_once(publish, DATABASE, stream_id=self.stream, lease_seconds=1)
        self.assertEqual(self.event_state(event), {'state': 'CLAIMED', 'attempts': 1})
        with transaction(database=DATABASE) as db:
            db.execute('SELECT pg_sleep(1.05)')
        self.assertEqual(relay_once(publish, DATABASE, stream_id=self.stream)['eventId'], event['eventId'])
        self.assertTrue(deliveries[1]['replayed'])
        self.assertEqual(deliveries[0]['outcome'], deliveries[1]['outcome'])
        self.assertEqual(self.count_effects(event), 1)

    def test_consumer_failure_rolls_back_receipt_and_effect(self):
        # Sample: Write inventory for E-1 and fail. Check zero effects until the next delivery.
        event = self.save()

        def stop(db, body):
            # Sample: Write a change for E-1. Stop before its receipt commits.
            record_inventory(db, body)
            raise RuntimeError('Sample consumer stop')

        with self.assertRaises(RuntimeError):
            self.receive(event, stop)
        self.assertEqual(self.count_effects(event), 0)
        self.assertFalse(self.receive(event)['replayed'])
        self.assertEqual(self.count_effects(event), 1)

    def test_concurrent_delivery_applies_once(self):
        # Sample: Deliver E-1 to eight workers together. Check one new receipt and one effect.
        event = self.save()
        gate = threading.Barrier(8)

        def worker(_):
            # Sample: Wait for eight workers. Deliver E-1 after the gate opens.
            gate.wait(timeout=10)
            return self.receive(event)

        with ThreadPoolExecutor(max_workers=8) as pool:
            received = list(pool.map(worker, range(8)))
        self.assertEqual(sum(not result['replayed'] for result in received), 1)
        self.assertEqual(len({result['outcome']['entryId'] for result in received}), 1)
        self.assertEqual(self.count_effects(event), 1)

    def test_changed_envelope_is_refused(self):
        # Sample: Deliver E-1 for 2 items. Change it to 3 items. Check refusal and one effect.
        event = self.save()
        self.receive(event)
        with self.assertRaises(Conflict):
            self.receive(dict(event, payload={'units': 3}))
        self.assertEqual(self.count_effects(event), 1)

    def test_stream_order_allows_independent_streams(self):
        # Sample: Hold A-2 behind A-1 while stream B can be published.
        first = self.save()
        second = self.save()
        third = self.save('order.other.' + uid())
        self.assertEqual(second['sequence'], 2)
        claim = self.claims()[0]
        self.assertEqual(claim['event'], first)
        self.assertEqual(self.claims(), [])
        independent = claim_batch(DATABASE, stream_id=third['streamId'])[0]
        self.assertEqual(independent['event'], third)
        settle(independent, True, DATABASE)
        settle(claim, True, DATABASE)
        self.assertEqual(self.claims()[0]['event'], second)

    def test_competing_claims_and_stale_lease(self):
        # Sample: Race two relays for E-1. Recover after expiry. Refuse the old token.
        event = self.save()
        gate = threading.Barrier(2)

        def worker(_):
            # Sample: Start two claim requests together. Keep one claim.
            gate.wait(timeout=10)
            return self.claims(lease_seconds=1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(worker, range(2)))
        self.assertEqual(sorted(map(len, rows)), [0, 1])
        old = (rows[0] or rows[1])[0]
        with transaction(database=DATABASE) as db:
            db.execute('SELECT pg_sleep(1.05)')
        with self.assertRaises(Conflict):
            settle(old, True, DATABASE)
        current = self.claims()[0]
        self.assertEqual(current['event'], event)
        self.assertEqual(current['attempt'], 2)
        self.assertNotEqual(current['leaseToken'], old['leaseToken'])
        with self.assertRaises(Conflict):
            settle(old, True, DATABASE)
        self.assertEqual(settle(current, True, DATABASE), 'PUBLISHED')

    def test_exhausted_event_blocks_stream(self):
        # Sample: Expire the final attempt for E-1. Keep E-1 failed and E-2 pending.
        first = self.save(max_attempts=1)
        second = self.save()
        self.assertEqual(self.claims(lease_seconds=1)[0]['event'], first)
        with transaction(database=DATABASE) as db:
            db.execute('SELECT pg_sleep(1.05)')
        self.assertEqual(self.claims(), [])
        self.assertEqual(self.event_state(first), {'state': 'FAILED', 'attempts': 1})
        self.assertEqual(self.event_state(second), {'state': 'PENDING', 'attempts': 0})

    def test_tenant_scope_and_consumer_identity(self):
        # Sample: Deliver E-1 to inventory and ledger. Keep the trading event invisible to foreign.
        event = self.save()
        self.assertFalse(self.receive(event)['replayed'])
        self.assertFalse(self.receive(event, consumer='ledger')['replayed'])
        self.assertTrue(self.receive(event, consumer='ledger')['replayed'])
        self.assertEqual(self.count_effects(event), 2)
        self.assertEqual(self.count_effects(event, 'durai_foreign'), 0)
        self.assertEqual(claim_batch(DATABASE, 'durai_foreign', stream_id=self.stream), [])
        with self.assertRaises(Conflict):
            self.receive(event, role='durai_foreign')

    def test_rejection_delays_eligible_retry(self):
        # Sample: Reject E-1. Wait for its retry time. Reuse the same event ID.
        event = self.save()
        result = relay_once(lambda delivered: False, DATABASE, retry_seconds=1, stream_id=self.stream)
        self.assertEqual(result['state'], 'PENDING')
        self.assertEqual(self.claims(), [])
        with transaction(database=DATABASE) as db:
            db.execute('SELECT pg_sleep(1.05)')
        self.assertEqual(self.claims()[0]['event'], event)

    def test_publisher_cannot_use_uncommitted_event(self):
        # Sample: Refuse an idle producer. Check that an active producer stays invisible until commit.
        with connect('durai_runtime', DATABASE) as db:
            with self.assertRaises(ValueError):
                enqueue(db, self.stream, 'order.created', {'units': 2})
            db.execute('SELECT 1')
            event = enqueue(db, self.stream, 'order.created', {'units': 2})
            self.assertEqual(self.claims(), [])
        self.assertEqual(self.claims()[0]['event'], event)
