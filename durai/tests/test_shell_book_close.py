from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from databricks.datalab.book_close import calculate


class BookCloseTests(unittest.TestCase):
    def setUp(self):
        self.prior = date(2026, 11, 2)
        self.day = date(2026, 11, 3)
        self.cutoff = datetime(2026, 11, 3, 23, tzinfo=timezone.utc)
        base = {'instrument_code': 'CLF27', 'contract_size': Decimal(1000), 'currency': 'USD'}
        self.fills = [
            base | {'side': 'BUY', 'quantity': Decimal(2), 'price': Decimal('74.32'), 'trade_date': self.prior},
            base | {'side': 'SELL', 'quantity': Decimal(1), 'price': Decimal('74.57'), 'trade_date': self.day},
        ]
        self.fees = [base | {'amount': Decimal('3')}]
        self.marks = [
            base | {'settlement_date': self.prior, 'settlement_price': Decimal('74.32'), 'cash_amount': Decimal(0)},
            base | {'settlement_date': self.day, 'settlement_price': Decimal('74.47'), 'cash_amount': Decimal(300)},
        ]

    def test_book_close_uses_opening_and_fill_marks(self):
        # Sample: Close one short fill and two opening long lots. Check net P&L of 397 USD.
        report = calculate('SYN-OIL', self.day, self.prior, self.cutoff, self.fills, self.fees, self.marks)
        row, = report['instruments']
        self.assertEqual(report['status'], 'COMPLETE')
        self.assertEqual(report['net_pnl'], Decimal(397))
        self.assertEqual((row['opening_pnl'], row['fill_pnl'], row['fees']), (300, 100, 3))
        self.assertEqual((row['closing_units'], row['settlement_cash']), (1000, 300))

    def test_book_close_with_missing_marks(self):
        # Sample: Omit the close mark. Check that the report has no final total.
        report = calculate('SYN-OIL', self.day, self.prior, self.cutoff, self.fills, self.fees, self.marks[:1])
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertIsNone(report['net_pnl'])
        self.assertEqual(report['issues'], [{'instrument': 'CLF27', 'code': 'MISSING_CLOSE_MARK'}])

    def test_book_close_preserves_input_identity(self):
        # Sample: Read the same close twice. Change one mark. Check a new digest.
        args = ('SYN-OIL', self.day, self.prior, self.cutoff, self.fills, self.fees)
        before = calculate(*args, self.marks)['input_sha256']
        self.assertEqual(before, calculate(*args, self.marks)['input_sha256'])
        changed = self.marks[:1] + [self.marks[1] | {'settlement_price': Decimal('74.48')}]
        self.assertNotEqual(before, calculate(*args, changed)['input_sha256'])
