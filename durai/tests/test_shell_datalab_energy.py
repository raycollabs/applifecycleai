from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from databricks.datalab import prepare_energy_futures as energy


class EnergyFuturesSeedTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(energy.SEED.read_bytes())
        self.contracts = energy.unique(self.data['contracts'], 'instrument_code')
        self.traders = energy.unique(self.data['traders'], 'trader_code')
        self.bookings = energy.unique(self.data['bookings'], 'idempotency_key')

    def test_booking_refuses_a_price_outside_the_tick(self):
        # Sample: Set CLF27 to 74.321. Check that the price is refused.
        order = self.data['bookings'][0] | {'price': '74.321'}
        with self.assertRaisesRegex(ValueError, 'does not match the tick'):
            energy.validate_booking(order, self.contracts, self.traders)

    def test_replay_keeps_the_key_and_changes_the_side(self):
        # Sample: Replay SYN-B-001. Change the side. Check one key and two expected results.
        replay, conflict = energy.validate_replay(self.data['replay'], self.bookings,
                                                  self.contracts, self.traders)
        self.assertEqual(replay['request'], self.bookings['SYN-B-001'])
        self.assertEqual(conflict['request']['idempotency_key'], 'SYN-B-001')
        self.assertEqual(conflict['request']['side'], 'SELL')
        self.assertEqual((replay['expected'], conflict['expected']), ('replay', 'conflict'))

    def test_price_correction_requires_an_earlier_event(self):
        # Sample: Put the correction before the first price. Refuse the link.
        with self.assertRaisesRegex(ValueError, 'needs one earlier price'):
            energy.validate_price_correction(reversed(self.data['price_events']), self.contracts)

    def test_settlement_uses_the_signed_lot_count(self):
        # Sample: Settle three short NGF27 contracts from 3.250 to 3.235. Refuse a loss of 450 USD.
        settlement = self.data['settlements'][1] | {'expected_cash_usd': '-450.00'}
        with self.assertRaisesRegex(ValueError, 'cash does not match'):
            energy.validate_settlement(settlement, self.contracts, self.bookings.values(), self.traders)

    def test_prepare_records_the_export_counts_and_digests(self):
        # Sample: Export four contracts and two settlements. Check the receipt against the files.
        with TemporaryDirectory() as folder:
            root = Path(folder) / 'energy'
            result = energy.prepare(root)
            self.assertEqual(result, json.loads((root / 'receipt.json').read_bytes()))
            self.assertEqual(result['files']['contracts.jsonl']['rows'], 4)
            self.assertEqual(result['files']['settlements.jsonl']['rows'], 2)
            for name, evidence in result['files'].items():
                body = (root / name).read_bytes()
                self.assertEqual(len(body.splitlines()), evidence['rows'])
                self.assertEqual(sha256(body).hexdigest(), evidence['sha256'])
