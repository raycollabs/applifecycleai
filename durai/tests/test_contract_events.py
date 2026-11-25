"""Sample: Run EventContractTests on each adapter. Check publish order, redelivery without ack and independent groups."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pg_support  # noqa: E402
from durai.adapters.events.memory import MemoryEventBus  # noqa: E402
from durai.adapters.events.outbox_table import OutboxEventBus  # noqa: E402
from durai.ports.errors import NotFound  # noqa: E402


class EventContract:
    def make_bus(self):
        raise NotImplementedError

    def setUp(self):
        # Sample: Make an empty bus.
        self.bus = self.make_bus()

    def test_order_kept_per_key(self):
        # Sample: Publish n 1, n 2, n 3 on topic T with key P-1. Consume. Check n 1, 2, 3 in that order.
        for n in (1, 2, 3):
            self.bus.publish('T', 'P-1', {'n': n})
        self.assertEqual([1, 2, 3], [e['payload']['n'] for e in self.bus.consume('T', 'G')])

    def test_unacked_event_is_redelivered(self):
        # Sample: Publish 1 event. Consume twice with no ack. Check both consumes return it.
        self.bus.publish('T', 'P-1', {'n': 1})
        self.assertEqual(1, len(self.bus.consume('T', 'G')))
        self.assertEqual(1, len(self.bus.consume('T', 'G')))

    def test_ack_removes_the_event_for_that_group_only(self):
        # Sample: Publish 2 events. Ack the first for group G. Consume for G gives 1. Consume for H gives 2.
        first = self.bus.publish('T', 'P-1', {'n': 1})
        self.bus.publish('T', 'P-1', {'n': 2})
        self.bus.ack('G', first)
        self.assertEqual(1, len(self.bus.consume('T', 'G')))
        self.assertEqual(2, len(self.bus.consume('T', 'H')))

    def test_max_events_is_kept(self):
        # Sample: Publish 3 events. Consume with max 2. Check 2 events, oldest first.
        for n in (1, 2, 3):
            self.bus.publish('T', 'P-1', {'n': n})
        self.assertEqual([1, 2], [e['payload']['n'] for e in self.bus.consume('T', 'G', 2)])

    def test_topics_are_separate(self):
        # Sample: Publish one event on topic A and one on topic B. Consume topic A. Check 1 event.
        self.bus.publish('A', 'P-1', {'n': 1})
        self.bus.publish('B', 'P-1', {'n': 2})
        self.assertEqual([1], [e['payload']['n'] for e in self.bus.consume('A', 'G')])

    def test_ack_of_unknown_event_is_not_found(self):
        # Sample: Ack E-999. Check NotFound.
        with self.assertRaises(NotFound):
            self.bus.ack('G', 'E-999')


class MemoryEventBusTests(EventContract, unittest.TestCase):
    def make_bus(self):
        # Sample: Return a new MemoryEventBus.
        return MemoryEventBus()


@unittest.skipUnless(pg_support.docker_ready(), 'Docker is not available')
class OutboxEventBusTests(EventContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start a disposable PostgreSQL 17 container once for this class.
        cls.connect = pg_support.start_postgres()

    @classmethod
    def tearDownClass(cls):
        # Sample: Remove the container.
        pg_support.stop_postgres()

    def make_bus(self):
        # Sample: Create the tables. Empty them. Return a bus on the container.
        bus = OutboxEventBus(type(self).connect)
        bus.ensure_schema()
        with type(self).connect() as conn:
            conn.execute('TRUNCATE outbox_event, outbox_ack RESTART IDENTITY')
        return bus


if __name__ == '__main__':
    unittest.main()
