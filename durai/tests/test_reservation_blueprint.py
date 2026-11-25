"""Sample: Send two requests for the last seat. Check one hold and one unavailable result."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

from test_event_blueprint import install_events

from durai.common import Conflict, digest, uid
from durai.db import ROOT, connect, transaction
from durai.outbox import claim_batch, enqueue, settle
from durai.reservations import change_hold, due_holds, hold_seat, read_order

DATABASE = 'durai_test'


def install_reservations():
    # Sample: Install the event dependency. Apply reservation schema v1 only on the isolated server.
    install_events()
    source = (ROOT / 'blueprints/reservations/v1.sql').read_bytes()
    checksum = digest(source)
    with connect('postgres', DATABASE) as db:
        exists = db.execute("SELECT to_regnamespace('durai_reservations') AS id").fetchone()['id']
        if exists is None:
            db.execute(source.decode('utf-8'), prepare=False)
            db.execute('INSERT INTO durai_reservations.schema_version VALUES (1,%s)', (checksum,))
        else:
            row = db.execute('SELECT source_digest FROM durai_reservations.schema_version WHERE version=1').fetchone()
            if row is None or row['source_digest'] != checksum:
                raise ValueError('Applied reservation blueprint differs. Use a fresh isolated server.')


class ReservationBlueprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Install the schema once before the reservation checks.
        install_reservations()

    def setUp(self):
        # Sample: Register one seat for a new show.
        self.seat = self.add_seat()

    def add_seat(self, applications=('trading',)):
        # Sample: Register seat A-1 under each named test tenant.
        seat = 'show.' + uid() + ':A-1'
        with connect('postgres', DATABASE) as db:
            for application in applications:
                db.execute('INSERT INTO durai_reservations.seat VALUES (%s,%s)', (application, seat))
        return seat

    def hold(self, *, seat=None, ttl=30, key=None, role='durai_runtime'):
        # Sample: Hold the test seat with a new request key.
        return hold_seat(self.seat if seat is None else seat, ttl, uid() if key is None else key, DATABASE, role)

    def move(self, held, action, *, revision=1, key=None, role='durai_runtime'):
        # Sample: Confirm the held order at revision 1.
        return change_hold(action, held['outcome']['order']['orderId'], revision,
                           uid() if key is None else key, DATABASE, role)

    def events(self, held):
        # Sample: Read the retained events for O-1 in sequence order.
        stream = held['outcome']['event']['streamId']
        with transaction(database=DATABASE) as db:
            return db.execute('SELECT sequence, event_type, event_id FROM durai_events.outbox '
                              'WHERE stream_id=%s ORDER BY sequence', (stream,)).fetchall()

    def wait_for_deadline(self, held):
        # Sample: Wait for the database deadline of the one-second hold.
        deadline = held['outcome']['order']['expiresAt']
        with transaction(database=DATABASE) as db:
            db.execute("SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (%s::timestamptz - clock_timestamp()))))",
                       (deadline,))

    def test_two_requests_for_last_seat(self):
        # Sample: Start two requests for A-1 together. Check one stored order and one event.
        gate = threading.Barrier(2)

        def request(key):
            # Sample: Wait for both requests and hold A-1.
            gate.wait(timeout=10)
            return self.hold(key=key)

        keys = [uid(), uid()]
        with ThreadPoolExecutor(max_workers=2) as workers:
            first, second = workers.map(request, keys)
        self.assertCountEqual([first['outcome']['result'], second['outcome']['result']], ['HELD', 'SEAT_UNAVAILABLE'])
        winner = next(r for r in (first, second) if r['outcome']['result'] == 'HELD')
        with transaction(database=DATABASE) as db:
            count = db.execute('SELECT count(*) AS n FROM durai_reservations.seat_order WHERE seat_id=%s',
                               (self.seat,)).fetchone()['n']
        self.assertEqual(count, 1)
        self.assertEqual(len(self.events(winner)), 1)
        for key, result in zip(keys, (first, second)):
            retry = self.hold(key=key)
            self.assertTrue(retry['replayed'])
            self.assertEqual(retry['outcome'], result['outcome'])

    def test_hold_retry_keeps_order_and_event(self):
        # Sample: Hold A-1 with K-1. Repeat it. Change the duration and check refusal.
        key = uid()
        first = self.hold(key=key)
        replay = self.hold(key=key)
        self.assertEqual(replay['outcome'], first['outcome'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(first['outcome']['order']['orderStatus'], 'PENDING')
        self.assertEqual(first['outcome']['event']['sequence'], 1)
        with self.assertRaises(Conflict):
            self.hold(key=key, ttl=31)
        self.assertEqual(len(self.events(first)), 1)

    def test_confirm_keeps_seat_and_event_order(self):
        # Sample: Confirm O-1. Check A-1 stays occupied. Confirm delivery of event 1 before claiming event 2.
        held = self.hold()
        confirmed = self.move(held, 'confirm')['outcome']
        self.assertEqual(confirmed['order']['orderStatus'], 'CONFIRMED')
        self.assertEqual(confirmed['order']['revision'], 2)
        self.assertEqual(self.hold()['outcome']['result'], 'SEAT_UNAVAILABLE')
        stream = confirmed['event']['streamId']
        claims = claim_batch(DATABASE, stream_id=stream)
        self.assertEqual([row['event'] for row in claims], [held['outcome']['event']])
        settle(claims[0], True, DATABASE)
        self.assertEqual([row['event'] for row in claim_batch(DATABASE, stream_id=stream)], [confirmed['event']])

    def test_release_opens_seat(self):
        # Sample: Release O-1. Check CANCELLED and hold A-1 with a new order.
        held = self.hold()
        unavailable_key = uid()
        unavailable = self.hold(key=unavailable_key)
        released = self.move(held, 'release')['outcome']
        self.assertEqual(released['order']['orderStatus'], 'CANCELLED')
        self.assertEqual(released['event']['eventType'], 'order.cancelled')
        self.assertEqual(self.hold(key=unavailable_key)['outcome'], unavailable['outcome'])
        replacement = self.hold()['outcome']
        self.assertEqual(replacement['result'], 'HELD')
        self.assertNotEqual(replacement['order']['orderId'], released['order']['orderId'])

    def test_expiry_opens_seat(self):
        # Sample: Let O-1 become due. Expire it. Hold A-1 again and keep the expired order.
        held = self.hold(ttl=1)
        order = held['outcome']['order']
        self.wait_for_deadline(held)
        self.assertIn(order, due_holds(DATABASE, limit=100))
        self.assertEqual(self.hold()['outcome']['result'], 'SEAT_UNAVAILABLE')
        expired = self.move(held, 'expire')['outcome']
        self.assertEqual(expired['event']['eventType'], 'order.expired')
        self.assertEqual(read_order(order['orderId'], DATABASE)['orderStatus'], 'EXPIRED')
        self.assertEqual(self.hold()['outcome']['result'], 'HELD')

    def test_deadline_rejects_early_expiry_and_late_confirmation(self):
        # Sample: Try to expire a live hold. Try to confirm a due hold. Check both refusals.
        live = self.hold()
        with self.assertRaisesRegex(Conflict, 'deadline'):
            self.move(live, 'expire')
        due = self.hold(seat=self.add_seat(), ttl=1)
        self.wait_for_deadline(due)
        with self.assertRaisesRegex(Conflict, 'deadline'):
            self.move(due, 'confirm')
        self.assertEqual(len(self.events(due)), 1)
        self.assertEqual(self.move(due, 'expire')['outcome']['result'], 'EXPIRED')

    def test_confirm_and_release_race(self):
        # Sample: Send confirm and release at revision 1. Check one transition and one event 2.
        held = self.hold()
        gate = threading.Barrier(2)

        def change(action):
            # Sample: Start the two transitions together. Return CONFLICT for a refused write.
            gate.wait(timeout=10)
            try:
                return self.move(held, action)['outcome']['result']
            except Conflict:
                return 'CONFLICT'

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(change, ['confirm', 'release']))
        self.assertEqual(results.count('CONFLICT'), 1)
        winner = next(result for result in results if result != 'CONFLICT')
        self.assertEqual(read_order(held['outcome']['order']['orderId'], DATABASE)['reservationState'], winner)
        self.assertEqual([row['sequence'] for row in self.events(held)], [1, 2])

    def test_stale_revision_and_terminal_state_refuse(self):
        # Sample: Refuse revision 2 on a held order. Confirm revision 1. Refuse release after confirmation.
        held = self.hold()
        with self.assertRaises(Conflict):
            self.move(held, 'confirm', revision=2)
        self.move(held, 'confirm')
        with self.assertRaises(Conflict):
            self.move(held, 'release', revision=2)
        self.assertEqual(len(self.events(held)), 2)

    def test_transition_replay_returns_same_event(self):
        # Sample: Confirm O-1 with K-1 twice. Check the same order and event.
        held = self.hold()
        key = uid()
        first = self.move(held, 'confirm', key=key)
        retry = self.move(held, 'confirm', key=key)
        self.assertTrue(retry['replayed'])
        self.assertEqual(retry['outcome'], first['outcome'])
        self.assertEqual(len(self.events(held)), 2)

    def test_outbox_failure_rolls_back_transition(self):
        # Sample: Write event 2 and raise an error. Check that O-1 stays held and retry the same command.
        held = self.hold()

        def fail(db, stream, event_type, body):
            # Sample: Enqueue the confirmation and stop the transaction before commit.
            enqueue(db, stream, event_type, body)
            raise RuntimeError('Sample failure before commit')

        key = uid()
        with patch('durai.reservations.enqueue', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.move(held, 'confirm', key=key)
        self.assertEqual(read_order(held['outcome']['order']['orderId'], DATABASE), held['outcome']['order'])
        self.assertEqual(len(self.events(held)), 1)
        retry = self.move(held, 'confirm', key=key)
        self.assertFalse(retry['replayed'])
        self.assertEqual(retry['outcome']['event']['sequence'], 2)

    def test_tenant_scope(self):
        # Sample: Hold the same seat label and use K-1 in two tenants. Refuse access to the foreign order.
        seat = self.add_seat(('trading', 'foreign'))
        key = uid()
        trading = self.hold(seat=seat, key=key)
        foreign = self.hold(seat=seat, key=key, role='durai_foreign')
        self.assertEqual(foreign['outcome']['result'], 'HELD')
        order_id = trading['outcome']['order']['orderId']
        self.assertNotEqual(order_id, foreign['outcome']['order']['orderId'])
        with self.assertRaises(KeyError):
            read_order(order_id, DATABASE, 'durai_foreign')
        with self.assertRaises(Conflict):
            self.move(trading, 'release', role='durai_foreign')
        self.assertEqual(read_order(order_id, DATABASE)['reservationState'], 'HELD')

    def test_unknown_seat_and_invalid_inputs(self):
        # Sample: Request an unknown seat, a hold time of zero, and an invalid seat label.
        with self.assertRaises(KeyError):
            self.hold(seat='missing.' + uid())
        for options in ({'ttl': 0}, {'ttl': True}, {'ttl': 86401}, {'seat': 'invalid seat'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.hold(**options)

    def test_due_list_is_bounded(self):
        # Sample: Create three due holds. Read the first two and check deadline order.
        orders = [self.hold(seat=self.add_seat(), ttl=1) for _ in range(3)]
        self.wait_for_deadline(orders[-1])
        due = due_holds(DATABASE, limit=100)
        self.assertTrue(all(held['outcome']['order'] in due for held in orders))
        first_two = due_holds(DATABASE, limit=2)
        self.assertEqual(first_two, due[:2])
        self.assertEqual(len(first_two), 2)
        self.assertEqual(first_two, sorted(first_two, key=lambda row: (row['expiresAt'], row['orderId'])))
        with self.assertRaises(ValueError):
            due_holds(DATABASE, limit=101)
        for held in orders:
            self.move(held, 'expire')
