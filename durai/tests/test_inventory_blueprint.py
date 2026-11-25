"""Sample: Send two requests for the last 3 units. Check one 2-unit hold."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from unittest.mock import patch

from test_reservation_blueprint import install_reservations

from durai.common import Conflict, digest, uid
from durai.db import ROOT, connect, transaction
from durai.inventory import change_hold, due_holds, hold_units, read_stock
from durai.outbox import enqueue

DATABASE = 'durai_test'


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Install v1 and check the same source digest on a second run.
        install_reservations()
        source = (ROOT / 'blueprints/inventory/v1.sql').read_bytes()
        checksum = digest(source)
        with connect('postgres', DATABASE) as db:
            if db.execute("SELECT to_regnamespace('durai_inventory') AS id").fetchone()['id'] is None:
                db.execute(source.decode(), prepare=False)
                db.execute('INSERT INTO durai_inventory.schema_version VALUES (1,%s)', (checksum,))
            else:
                row = db.execute('SELECT source_digest FROM durai_inventory.schema_version WHERE version=1').fetchone()
                if row is None or row['source_digest'] != checksum:
                    raise ValueError('Applied inventory blueprint differs. Use a fresh isolated server.')

    def setUp(self):
        # Sample: Create item I-1 with 3 available units per test tenant.
        self.item = 'item.' + uid()
        with connect('postgres', DATABASE) as db:
            for tenant in ('trading', 'foreign'):
                db.execute('INSERT INTO durai_inventory.stock(application_id,item_id,capacity,available) '
                           'VALUES (%s,%s,3,3)', (tenant, self.item))

    def reserve(self, quantity=2, ttl=30, key=None, role='durai_runtime'):
        # Sample: Request 2 units of I-1 for 30 seconds.
        return hold_units(self.item, quantity, ttl, key or uid(), DATABASE, role)

    def move(self, held, action, revision=1, key=None, role='durai_runtime'):
        # Sample: Confirm the held units at revision 1.
        return change_hold(action, held['outcome']['hold']['holdId'], revision, key or uid(), DATABASE, role)

    def balances(self, role='durai_runtime'):
        # Sample: Read the three balances and check their sum is 3.
        row = read_stock(self.item, DATABASE, role)
        result = (row['available'], row['held'], row['allocated'])
        self.assertEqual(sum(result), row['capacity'])
        return result

    def event_count(self, held):
        # Sample: Count committed events for the hold stream.
        with transaction(database=DATABASE) as db:
            return db.execute('SELECT count(*) AS n FROM durai_events.outbox WHERE stream_id=%s',
                              (held['outcome']['event']['streamId'],)).fetchone()['n']

    def test_last_units_race(self):
        # Sample: Request 2 units twice when 3 units are available. Check one hold and one refusal.
        gate = Barrier(2)

        def request(key):
            # Sample: Start both requests together.
            gate.wait(timeout=10)
            return self.reserve(key=key)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request, [uid(), uid()]))
        self.assertCountEqual([r['outcome']['result'] for r in results], ['HELD', 'INSUFFICIENT_STOCK'])
        winner = next(r for r in results if r['outcome']['result'] == 'HELD')
        self.assertEqual(self.balances(), (1, 2, 0))
        self.assertEqual(self.event_count(winner), 1)
        with transaction(database=DATABASE) as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM durai_inventory.item_hold WHERE item_id=%s',
                                        (self.item,)).fetchone()['n'], 1)

    def test_repeat_and_changed_request(self):
        # Sample: Repeat K-1, then change its quantity. Keep one hold and one event.
        key = uid()
        first = self.reserve(key=key)
        replay = self.reserve(key=key)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['outcome'], first['outcome'])
        with self.assertRaises(Conflict):
            self.reserve(3, key=key)
        self.assertEqual(self.balances(), (1, 2, 0))
        self.assertEqual(self.event_count(first), 1)

    def test_confirm_moves_units_to_allocated(self):
        # Sample: Confirm 2 held units. Check 2 allocated units and event 2.
        held = self.reserve()
        result = self.move(held, 'confirm')['outcome']
        self.assertEqual(result['result'], 'CONFIRMED')
        self.assertEqual(result['event']['sequence'], 2)
        self.assertEqual(result['event']['eventType'], 'inventory.confirmed')
        self.assertEqual(self.balances(), (1, 0, 2))

    def test_release_returns_units(self):
        # Sample: Release 2 units twice with the same key. Check 3 available units both times.
        held = self.reserve()
        declined_key = uid()
        declined = self.reserve(key=declined_key)
        self.assertEqual(declined['outcome']['result'], 'INSUFFICIENT_STOCK')
        key = uid()
        released = self.move(held, 'release', key=key)
        self.assertEqual(released['outcome']['result'], 'RELEASED')
        self.assertEqual(self.move(held, 'release', key=key)['outcome'], released['outcome'])
        self.assertEqual(self.balances(), (3, 0, 0))
        self.assertEqual(self.reserve(key=declined_key)['outcome'], declined['outcome'])
        self.assertEqual(self.reserve(3)['outcome']['result'], 'HELD')

    def test_expiry_returns_units(self):
        # Sample: Refuse early expiry. Wait for another hold to be due. Expire it and return 2 units.
        live = self.reserve(1)
        with self.assertRaisesRegex(Conflict, 'deadline'):
            self.move(live, 'expire')
        soon = self.reserve(ttl=1)
        deadline = soon['outcome']['hold']['expiresAt']
        with transaction(database=DATABASE) as db:
            db.execute('SELECT pg_sleep(GREATEST(0,extract(epoch FROM (%s::timestamptz-clock_timestamp()))))',
                       (deadline,))
        self.assertIn(soon['outcome']['hold'], due_holds(DATABASE, limit=100))
        with self.assertRaisesRegex(Conflict, 'deadline'):
            self.move(soon, 'confirm')
        self.assertEqual(self.balances(), (0, 3, 0))
        self.assertEqual(self.move(soon, 'expire')['outcome']['result'], 'EXPIRED')
        self.assertEqual(self.balances(), (2, 1, 0))

    def test_competing_transitions(self):
        # Sample: Release and confirm the same revision. Check that only one transaction moves units.
        held = self.reserve()
        gate = Barrier(2)

        def request(action):
            # Sample: Start the two terminal changes together.
            gate.wait(timeout=10)
            try:
                return self.move(held, action)['outcome']['result']
            except Conflict:
                return 'CONFLICT'

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request, ['confirm', 'release']))
        self.assertEqual(results.count('CONFLICT'), 1)
        self.assertEqual(self.event_count(held), 2)
        self.assertEqual(self.balances(), (1, 0, 2) if 'CONFIRMED' in results else (3, 0, 0))

    def test_event_failure_rolls_back(self):
        # Sample: Write an event and stop before commit. Retry the request after checking rollback.
        key = uid()
        ids = []

        def stop(db, stream, event_type, body):
            # Sample: Queue the event and raise an error before commit.
            ids.append(enqueue(db, stream, event_type, body)['eventId'])
            raise RuntimeError('Sample failure before commit')

        with patch('durai.inventory.enqueue', side_effect=stop), self.assertRaises(RuntimeError):
            self.reserve(key=key)
        self.assertEqual(self.balances(), (3, 0, 0))
        with transaction(database=DATABASE) as db:
            self.assertIsNone(db.execute('SELECT hold_id FROM durai_inventory.item_hold WHERE item_id=%s',
                                         (self.item,)).fetchone())
            self.assertIsNone(db.execute('SELECT event_id FROM durai_events.outbox WHERE event_id=%s',
                                         (ids[0],)).fetchone())
        retried = self.reserve(key=key)
        self.assertFalse(retried['replayed'])
        with patch('durai.inventory.enqueue', side_effect=stop), self.assertRaises(RuntimeError):
            self.move(retried, 'confirm')
        self.assertEqual(self.balances(), (1, 2, 0))
        self.assertEqual(self.event_count(retried), 1)
        self.assertEqual(self.move(retried, 'confirm')['outcome']['event']['sequence'], 2)

    def test_tenant_scope(self):
        # Sample: Hold the same item in both tenants. Refuse a change to the foreign hold.
        key = uid()
        trading = self.reserve(key=key)
        foreign = self.reserve(key=key, role='durai_foreign')
        self.assertNotEqual(trading['outcome']['hold']['holdId'], foreign['outcome']['hold']['holdId'])
        with self.assertRaises(Conflict):
            self.move(trading, 'release', role='durai_foreign')
        self.move(foreign, 'release', role='durai_foreign')
        self.assertEqual(self.balances('durai_foreign'), (3, 0, 0))
        self.assertEqual(self.balances(), (1, 2, 0))

    def test_inputs_and_stale_revision(self):
        # Sample: Refuse 0 units and a stale revision. Keep the valid hold and its balances.
        for quantity in (0, -1, True, 2147483648):
            with self.assertRaises(ValueError):
                self.reserve(quantity)
        held = self.reserve()
        with self.assertRaises(Conflict):
            self.move(held, 'confirm', revision=2)
        self.assertEqual(self.balances(), (1, 2, 0))
        self.assertEqual(self.event_count(held), 1)
