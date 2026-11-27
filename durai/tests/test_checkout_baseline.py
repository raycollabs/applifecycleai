"""Sample: Test checkout baseline inventory holds, idempotent intents, partial refunds, balanced ledger, merchant isolation, and React UI rules."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'checkout-baseline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pg_support  # noqa: E402
from api.domain import (  # noqa: E402
    AccessDeniedError,
    CheckoutService,
    IdempotencyConflictError,
    InsufficientStockError,
    OverRefundError,
)
from api.postgres_store import PostgresCheckoutService  # noqa: E402
from durai.build_web_rules import check_web_rules  # noqa: E402


class CheckoutBaselineTests(unittest.TestCase):
    def setUp(self):
        # Sample: Initialize clock at 2026-11-25T12:00:00Z and seed 10 units of SKU-1 for merchant M-1.
        self.clock = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
        self.service = CheckoutService(now_fn=lambda: self.clock)
        self.service.seed_stock('M-1', 'SKU-1', 10)

    def test_inventory_reservation_and_no_overbook(self):
        # Sample: Reserve 6 units of SKU-1, check 4 available, refuse 5 units, advance clock past 600s TTL, and check 10 available.
        res = self.service.reserve_stock('M-1', 'SKU-1', 6, 'ORD-1', ttl_seconds=600)
        self.assertEqual(res['status'], 'HELD')
        self.assertEqual(self.service.get_sku('M-1', 'SKU-1')['available'], 4)
        with self.assertRaises(InsufficientStockError):
            self.service.reserve_stock('M-1', 'SKU-1', 5, 'ORD-2')
        self.clock += timedelta(seconds=601)
        self.assertEqual(self.service.get_sku('M-1', 'SKU-1')['available'], 10)

    def test_idempotent_intent_replay_and_conflict(self):
        # Sample: Create intent with key K-1 for 5000 cents, replay K-1 for same intent, and reuse K-1 with 6000 cents for IdempotencyConflictError.
        first, replayed1 = self.service.create_intent('M-1', 'K-1', 5000, 'USD', 'SKU-1', 2)
        second, replayed2 = self.service.create_intent('M-1', 'K-1', 5000, 'USD', 'SKU-1', 2)
        self.assertFalse(replayed1)
        self.assertTrue(replayed2)
        self.assertEqual(first['intent_id'], second['intent_id'])
        with self.assertRaises(IdempotencyConflictError):
            self.service.create_intent('M-1', 'K-1', 6000, 'USD', 'SKU-1', 2)

    def test_capture_partial_refund_and_over_refund_refused(self):
        # Sample: Capture 5000 cents, refund 1500 cents, refund 3500 cents, and refuse 1 more cent with OverRefundError.
        intent, _ = self.service.create_intent('M-1', 'K-2', 5000, 'USD', 'SKU-1', 2)
        captured = self.service.capture('M-1', intent['intent_id'], 5000, fee_cents=150)
        self.assertEqual(captured['status'], 'CAPTURED')
        self.assertEqual(self.service.get_sku('M-1', 'SKU-1')['total'], 8)
        r1 = self.service.refund('M-1', intent['intent_id'], 1500)
        self.assertEqual(r1['status'], 'PARTIALLY_REFUNDED')
        r2 = self.service.refund('M-1', intent['intent_id'], 3500)
        self.assertEqual(r2['status'], 'REFUNDED')
        with self.assertRaises(OverRefundError):
            self.service.refund('M-1', intent['intent_id'], 1)

    def test_double_entry_ledger_is_always_balanced(self):
        # Sample: Capture 4000 cents with 100 fee and refund 1000 cents. Check every journal entry has equal debits and credits.
        intent, _ = self.service.create_intent('M-1', 'K-3', 4000, 'USD')
        self.service.capture('M-1', intent['intent_id'], 4000, fee_cents=100)
        self.service.refund('M-1', intent['intent_id'], 1000)
        ledger = self.service.list_ledger('M-1')
        self.assertEqual(len(ledger), 2)
        for entry in ledger:
            self.assertEqual(entry['debit_total_cents'], entry['credit_total_cents'])

    def test_merchant_isolation_refuses_cross_merchant_access(self):
        # Sample: Create intent for M-1. Attempt capture and ledger read as M-2. Check AccessDeniedError and empty M-2 ledger.
        intent, _ = self.service.create_intent('M-1', 'K-4', 2500, 'USD')
        with self.assertRaises(AccessDeniedError):
            self.service.capture('M-2', intent['intent_id'], 2500)
        self.assertEqual(self.service.list_intents('M-2'), [])
        self.assertEqual(self.service.list_ledger('M-2'), [])

    def test_checkout_web_has_no_web_rule_violations_and_passes_ui_contract(self):
        # Sample: Run check_web_rules on apps/checkout-baseline/web and verify loading, error, empty, ok, and POST signIn/signOut states exist.
        web_dir = ROOT / 'apps' / 'checkout-baseline' / 'web'
        self.assertEqual(check_web_rules(web_dir), [])
        self.assertIn('data-shell-state="loading"', (web_dir / 'app' / 'loading.tsx').read_text(encoding='utf-8'))
        self.assertIn('data-shell-state="error"', (web_dir / 'app' / 'error.tsx').read_text(encoding='utf-8'))
        screen_tsx = (web_dir / 'app' / '[screen]' / 'page.tsx').read_text(encoding='utf-8')
        self.assertIn('data-shell-state="empty"', screen_tsx)
        self.assertIn('data-shell-state="ok"', screen_tsx)


class PostgresCheckoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start disposable PostgreSQL 17 container when Docker is ready.
        if not pg_support.docker_ready():
            raise unittest.SkipTest('Docker is not available')
        cls.connect = staticmethod(pg_support.start_postgres())
        with cls.connect() as conn:
            info = conn.info
            cls.dsn = f'postgresql://{info.user}:{info.password}@{info.host}:{info.port}/{info.dbname}'

    @classmethod
    def tearDownClass(cls):
        # Sample: Stop and remove disposable PostgreSQL container.
        pg_support.stop_postgres()

    def setUp(self):
        # Sample: Ensure checkout schema and truncate tables before each test.
        self.pg = PostgresCheckoutService(self.dsn)
        self.pg.ensure_schema()
        with self.pg._connect() as conn:
            conn.execute('TRUNCATE TABLE checkout.idempotency, checkout.payment_intent, checkout.reservation, checkout.inventory, checkout.journal_entry, checkout.session')

    def test_postgres_checkout_persists_across_restart(self):
        # Sample: Seed 10 units, create intent K-PG, capture 5000 cents, refund 1500 cents, and read back from a fresh PostgresCheckoutService.
        self.pg.seed_stock('M-1', 'SKU-1', 10)
        intent, replayed1 = self.pg.create_intent('M-1', 'K-PG', 5000, 'USD', 'SKU-1', 2)
        self.assertFalse(replayed1)
        _, replayed2 = self.pg.create_intent('M-1', 'K-PG', 5000, 'USD', 'SKU-1', 2)
        self.assertTrue(replayed2)
        with self.assertRaises(IdempotencyConflictError):
            self.pg.create_intent('M-1', 'K-PG', 6000, 'USD', 'SKU-1', 2)
        self.pg.capture('M-1', intent['intent_id'], 5000, fee_cents=100)
        self.pg.refund('M-1', intent['intent_id'], 1500)

        restarted = PostgresCheckoutService(self.dsn)
        self.assertEqual(restarted.get_sku('M-1', 'SKU-1')['total'], 8)
        self.assertEqual(len(restarted.list_intents('M-1')), 1)
        self.assertEqual(restarted.list_intents('M-1')[0]['refunded_cents'], 1500)
        ledger = restarted.list_ledger('M-1')
        self.assertEqual(len(ledger), 2)
        for row in ledger:
            self.assertEqual(row['debit_total_cents'], row['credit_total_cents'])


if __name__ == '__main__':
    unittest.main()
