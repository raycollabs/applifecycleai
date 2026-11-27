from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
import unittest

from deploy.reporting.clickhouse.close import (
    ACCOUNTS, ClickHouse, JOURNAL, ClickHouseError, RevisionConflict, bootstrap, connect, dump,
    history, normalized_rows, publish,
)
from durai.reporting.contract import digest

FIXTURE = Path(__file__).resolve().parents[1] / 'design/reporting/energy_futures_close.json'
REPORTS = json.loads(FIXTURE.read_text(encoding='utf-8'))['reports']


def rehash(report):
    report['content_sha256'] = digest({
        key: value for key, value in report.items() if key != 'content_sha256'
    })
    return report


class ClickHouseInputTests(unittest.TestCase):
    def test_duplicate_report_grain_is_refused(self):
        # Sample: Add CLF27 twice for one oil close. Check the duplicate grain error.
        report = deepcopy(REPORTS[0])
        report['rows'].append(report['rows'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            normalized_rows(rehash(report))

    def test_decimal_never_rounds_on_publication(self):
        # Sample: Set fees to 0.0000000000001 USD. Check the exact decimal error.
        report = deepcopy(REPORTS[0])
        report['rows'][0]['fees'] = '0.0000000000001'
        with self.assertRaisesRegex(ValueError, 'Decimal'):
            normalized_rows(rehash(report))

    def test_content_digest_protects_input(self):
        # Sample: Change the oil fee to 12 USD. Keep the old digest. Check the digest error.
        report = deepcopy(REPORTS[0])
        report['rows'][0]['fees'] = '12.00'
        with self.assertRaisesRegex(ValueError, 'digest'):
            normalized_rows(report)


@unittest.skipUnless(os.environ.get('DURAI_CH_INTEGRATION') == '1', 'Requires disposable ClickHouse')
class ClickHouseReportingTests(unittest.TestCase):
    DATES = {
        'test_changed_bytes_conflict_after_stage': '2026-11-04',
        'test_concurrent_replay_one_terminal_record': '2026-11-05',
        'test_correction_failure_keeps_old_result_until_replay': '2026-11-06',
        'test_incomplete_close_is_evidence_only': '2026-11-07',
        'test_scope_and_grant_removal': '2026-11-08',
        'test_retry_after_lost_publication_ack': '2026-11-09',
    }

    @classmethod
    def setUpClass(cls):
        cls.admin = cls.enterClassContext(connect('reporting_admin', 'DURAI_CH_ADMIN_PASSWORD'))
        bootstrap(cls.admin)
        cls.oil = cls.enterClassContext(connect('oil_conn', 'DURAI_CH_OIL_PASSWORD'))
        cls.gas = cls.enterClassContext(connect('gas_conn', 'DURAI_CH_GAS_PASSWORD'))
        cls.work = TemporaryDirectory()
        cls.lock = Path(cls.work.name) / 'publish.lock'
        cls.addClassCleanup(cls.work.cleanup)

    def load(self, report, **options):
        with connect(
            'close_publisher', ACCOUNTS['close_publisher'][1], session='durai-close-publisher-v1'
        ) as publisher:
            return publish(publisher, report, self.lock, **options)

    def report(self, index):
        report = deepcopy(REPORTS[index])
        close_date = self.DATES[self._testMethodName]
        report['key']['business_date'] = close_date
        report['event_cutoff'] = close_date
        report['known_at'] = f'{close_date} 23:00:00+00:00'
        for row in report['rows']:
            row['business_date'] = close_date
        return rehash(report)

    def read_book(self, client, schema):
        return client.select(
            f'SELECT revision, contract, net_pnl FROM {schema}.energy_futures_close '
            'WHERE business_date = {business_date:Date32} ORDER BY contract',
            parameters={'business_date': self.DATES[self._testMethodName]},
        )

    def test_concurrent_replay_one_terminal_record(self):
        # Sample: Start two oil revision 1 loads. Check one publication receipt and one chart row.
        report = self.report(0)
        barrier = Barrier(2)

        def load_at_once():
            barrier.wait(timeout=10)
            return self.load(report)

        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda _: load_at_once(), range(2)))

        self.assertEqual({item['replay'] for item in results}, {False, True})
        self.assertEqual(results[0]['operation_id'], results[1]['operation_id'])
        self.assertCountEqual([r['phase'] for r in history(self.admin, report['key'])], ['staged', 'published'])
        rows = self.read_book(self.oil, 'oil')
        self.assertEqual([r['revision'] for r in rows], [1])
        self.assertEqual(Decimal(rows[0]['net_pnl']), Decimal('300'))

    def test_correction_failure_keeps_old_result_until_replay(self):
        # Sample: Stop after oil revision 2 staging. Check 300 USD. Repeat the load. Check 320 USD.
        first, second = self.report(0), self.report(2)
        self.load(first)

        def stop():
            raise RuntimeError('Stop after staging')

        with self.assertRaisesRegex(RuntimeError, 'Stop after staging'):
            self.load(second, after_stage=stop)
        self.assertEqual([r['phase'] for r in history(self.admin, second['key'])], ['staged'])
        self.assertEqual(Decimal(self.read_book(self.oil, 'oil')[0]['net_pnl']), Decimal('300'))
        self.load(second)
        rows = self.read_book(self.oil, 'oil')
        self.assertEqual([row['revision'] for row in rows], [2])
        self.assertEqual(Decimal(rows[0]['net_pnl']), Decimal('320'))
        self.assertTrue(self.load(first)['replay'])
        self.assertEqual(len(self.read_book(self.oil, 'oil')), 1)

    def test_changed_bytes_conflict_after_stage(self):
        # Sample: Stop an oil revision 1 load. Change the known-at time. Check the revision conflict.
        report = self.report(0)

        def stop():
            raise RuntimeError('Stop after staging')

        with self.assertRaisesRegex(RuntimeError, 'Stop after staging'):
            self.load(report, after_stage=stop)
        report['known_at'] = self.DATES[self._testMethodName] + ' 23:01:00+00:00'
        with self.assertRaises(RevisionConflict):
            self.load(rehash(report))
        self.assertEqual(self.read_book(self.oil, 'oil'), [])

    def test_retry_after_lost_publication_ack(self):
        # Sample: Publish oil revision 1. Lose the receipt. Repeat the load. Check the first publication ID.
        report = self.report(0)
        original_append = ClickHouse.append
        published_operations = []

        def lose_ack(client, record):
            original_append(client, record)
            if record['phase'] == 'published':
                published_operations.append(record['operation_id'])
                raise OSError('Lost publication response')

        with patch.object(ClickHouse, 'append', lose_ack):
            with self.assertRaisesRegex(OSError, 'Lost publication response'):
                self.load(report)
        result = self.load(report)
        self.assertTrue(result['replay'])
        self.assertEqual(published_operations, [result['operation_id']])
        self.assertEqual(len(self.read_book(self.oil, 'oil')), 1)

    def test_incomplete_close_is_evidence_only(self):
        # Sample: Load gas revision 1. Record the missing mark in revision 2. Check revision 1 and 300 USD.
        first, partial = self.report(1), self.report(3)
        self.load(first)
        self.assertEqual(self.load(partial)['phase'], 'recorded')
        self.assertTrue(self.load(partial)['replay'])
        entry, = history(self.admin, partial['key'])
        self.assertEqual(json.loads(entry['envelope'])['issues'][0]['code'], 'MISSING_CLOSE_MARK')
        rows = self.read_book(self.gas, 'gas')
        self.assertEqual([row['revision'] for row in rows], [1])
        self.assertEqual(Decimal(rows[0]['net_pnl']), Decimal('300'))

    def test_scope_and_grant_removal(self):
        # Sample: Read gas as the oil viewer. Check access denied. Remove the oil grant. Check access denied.
        self.load(self.report(0))
        self.load(self.report(1))
        other_app = self.report(0)
        other_app['key']['application'] = 'other-app'
        self.load(rehash(other_app))
        self.assertEqual([row['contract'] for row in self.read_book(self.oil, 'oil')], ['CLF27'])
        self.assertEqual([row['contract'] for row in self.read_book(self.gas, 'gas')], ['NGF27'])
        for query in ('SELECT * FROM gas.energy_futures_close', f'SELECT * FROM {JOURNAL}'):
            with self.assertRaises(ClickHouseError) as raised:
                self.oil.select(query)
            self.assertEqual(raised.exception.code, 497)
        self.admin.execute('REVOKE reporting_oil FROM oil_conn')
        try:
            with self.assertRaises(ClickHouseError) as raised:
                dump(self.oil, 'oil')
            self.assertEqual(raised.exception.code, 497)
        finally:
            self.admin.execute('GRANT reporting_oil TO oil_conn')

    def test_dashboard_query_uses_decimal_and_latest_revision(self):
        # Sample: Load the four fixture reports. Check gas 300 USD and oil 320 USD.
        for report in REPORTS:
            self.load(report)
        for client, schema, value in ((self.oil, 'oil', '320'), (self.gas, 'gas', '300')):
            (result,) = client.select(
                f'SELECT count() AS rows, sum(net_pnl) AS net_pnl FROM {schema}.energy_futures_close '
                "WHERE application = 'energy-futures' AND business_date = '2026-11-03'"
            )
            self.assertEqual(int(result['rows']), 1)
            self.assertEqual(Decimal(result['net_pnl']), Decimal(value))


if __name__ == '__main__':
    unittest.main()
