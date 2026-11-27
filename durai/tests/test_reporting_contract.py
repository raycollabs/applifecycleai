import json
from decimal import Decimal
import unittest

from durai.reporting.contract import digest, select_profile, verify_envelope
from tools.verify_reporting_offerings import FIXTURE, expected_rows, verify_rows


class ProfileSelectionTests(unittest.TestCase):
    def test_explicit_profile(self):
        # Sample: Select ClickHouse for one PostgreSQL source. Check ClickHouse.
        self.assertEqual(select_profile('clickhouse')['profile'], 'clickhouse')

    def test_default_profile(self):
        # Sample: Read PostgreSQL and files with permission to transfer. Select Databricks.
        self.assertEqual(select_profile()['profile'], 'postgres')
        self.assertEqual(select_profile(sources=('postgres', 'files'), transfers_allowed=True)['profile'], 'databricks')
        self.assertIsNone(select_profile(sources=('postgres', 'files'))['profile'])


class ReportContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(FIXTURE.read_bytes())

    def test_missing_mark_blocks_publication(self):
        # Sample: Read the incomplete gas close. Check zero published rows for that revision.
        failed, = [report for report in self.fixture['reports'] if report['status'] == 'INCOMPLETE']
        self.assertEqual(verify_envelope(failed)['rows'], [])
        rows = expected_rows(self.fixture)
        self.assertEqual({row['book']: row['revision'] for row in rows}, {'SYN-OIL': 2, 'SYN-GAS': 1})

    def test_changed_revision_digest_conflicts(self):
        # Sample: Change revision 1 after it is hashed. Refuse the envelope.
        report = self.fixture['reports'][0]
        report['rows'][0]['net_pnl'] = '0'
        with self.assertRaisesRegex(ValueError, 'digest differs'):
            verify_envelope(report)
        original = json.loads(FIXTURE.read_bytes())['reports'][0]
        self.fixture['reports'][0] = original
        changed = {key: value for key, value in original.items() if key != 'content_sha256'}
        changed['known_at'] = '2026-11-03 23:01:00+00:00'
        self.fixture['reports'].append(changed | {'content_sha256': digest(changed)})
        with self.assertRaisesRegex(ValueError, 'Conflicting report revision'):
            expected_rows(self.fixture)

    def test_rehashed_pnl_must_reconcile(self):
        # Sample: Set net P&L to 319 USD. Make a new digest. Refuse the report.
        report = self.fixture['reports'][0]
        report['rows'][0]['net_pnl'] = '319'
        content = {key: value for key, value in report.items() if key != 'content_sha256'}
        with self.assertRaisesRegex(ValueError, 'does not reconcile'):
            verify_envelope(content | {'content_sha256': digest(content)})

    def test_reconcile_preserves_full_decimal_precision(self):
        # Sample: Add 0.000000000001 USD to 12345678901234567890123456 USD. Keep the fraction.
        report = self.fixture['reports'][0]
        report['rows'][0].update(opening_pnl='12345678901234567890123456', fill_pnl='0.000000000001',
                                 net_pnl='12345678901234567890123456.000000000001')
        content = {key: value for key, value in report.items() if key != 'content_sha256'}
        self.assertEqual(verify_envelope(content | {'content_sha256': digest(content)})['rows'], report['rows'])

    def test_same_fixture_totals(self):
        # Sample: Read oil 320 and gas 300. Check both rows. Refuse a duplicate.
        rows = expected_rows(self.fixture)
        self.assertEqual(verify_rows(self.fixture, rows)['visible_rows'], 2)
        self.assertEqual({row['book']: Decimal(row['net_pnl']) for row in rows},
                         {'SYN-OIL': 320, 'SYN-GAS': 300})
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            verify_rows(self.fixture, rows + rows[:1])

    def test_desk_export_scope(self):
        # Sample: Export as oil-reader. Refuse a row from SYN-GAS.
        rows = expected_rows(self.fixture)
        oil = [row for row in rows if row['book'] == 'SYN-OIL']
        self.assertEqual(verify_rows(self.fixture, oil, identity='oil-reader')['visible_rows'], 1)
        self.assertEqual(verify_rows(self.fixture, [], identity='unscoped')['visible_rows'], 0)
        with self.assertRaisesRegex(ValueError, 'SYN-GAS'):
            verify_rows(self.fixture, rows, identity='oil-reader')
