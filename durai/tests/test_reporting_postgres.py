import concurrent.futures
import copy
import json
import os
from pathlib import Path
import unittest

import psycopg

from deploy.reporting.postgres.publish import RevisionConflict, dump_visible, publish
from durai.reporting.contract import digest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'design/reporting/energy_futures_close.json'
ENV = os.environ.get('DURAI_PG_PROOF')


@unittest.skipUnless(ENV, 'Set DURAI_PG_PROOF to the disposable PostgreSQL secrets file')
class PostgresReportingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.secrets = dict(line.split('=', 1) for line in Path(ENV).read_text().splitlines() if line)
        reports = json.loads(FIXTURE.read_text(encoding='utf-8'))['reports']
        cls.reports = {(report['key']['book'], report['key']['revision']): report for report in reports}

    def connect(self, role='postgres'):
        prefix = 'ADMIN' if role == 'postgres' else role.removeprefix('report_').upper()
        connection = psycopg.connect(host='127.0.0.1', port=self.secrets.get('REPORTING_PORT', 55451),
                                     dbname='reporting', user=role, password=self.secrets[f'REPORTING_{prefix}_PASSWORD'],
                                     autocommit=True)
        self.addCleanup(connection.close)
        return connection

    def setUp(self):
        self.admin = self.connect()
        self.admin.execute('TRUNCATE reporting_private.rows, reporting_private.runs')
        self.writer = self.connect('report_publisher')

    def counts(self):
        return self.admin.execute('''
            SELECT (SELECT count(*) FROM reporting_private.runs),
                   (SELECT count(*) FROM reporting_private.rows)
        ''').fetchone()

    def test_replay_creates_zero_rows(self):
        # Sample: Load SYN-OIL revision 1 twice. Check one run and one row.
        report = self.reports['SYN-OIL', 1]
        self.assertEqual(publish(self.writer, report), publish(self.writer, report))
        self.assertEqual(self.counts(), (1, 1))

    def test_concurrent_exact_replay(self):
        # Sample: Submit oil revision 1 on two connections. Check one run and one row.
        report = self.reports['SYN-OIL', 1]
        second = self.connect('report_publisher')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(publish, connection, report) for connection in (self.writer, second)]
            self.assertEqual(results[0].result(), results[1].result())
        self.assertEqual(self.counts(), (1, 1))

    def test_conflicting_digest_keeps_current_revision(self):
        # Sample: Load oil revision 2 with net P&L 320.00. Change the digest. Check refusal and 320.00.
        report = self.reports['SYN-OIL', 2]
        publish(self.writer, report)
        changed = copy.deepcopy(report)
        changed['known_at'] = '2026-11-04 00:00:00+00:00'
        changed['content_sha256'] = digest({k: v for k, v in changed.items() if k != 'content_sha256'})
        with self.assertRaises(RevisionConflict):
            publish(self.writer, changed)
        self.assertEqual(str(dump_visible(self.connect('report_oil'), 'oil')[0]['net_pnl']), '320.00')

    def test_incomplete_run_retains_issue_and_previous_complete(self):
        # Sample: Load gas revisions 1 and 2. Check the second run issue and visible revision 1.
        complete = self.reports['SYN-GAS', 1]
        incomplete = self.reports['SYN-GAS', 2]
        publish(self.writer, complete)
        result = publish(self.writer, incomplete)
        self.assertEqual(result['status'], 'INCOMPLETE')
        audit, = self.admin.execute('SELECT issues FROM reporting_private.runs WHERE revision = 2').fetchone()
        self.assertTrue(audit)
        self.assertEqual(audit, incomplete['issues'])
        self.assertEqual(dump_visible(self.connect('report_gas'), 'gas')[0]['revision'], 1)

    def test_failed_row_insert_rolls_back_run(self):
        # Sample: Load oil revision 1. Refuse the 320.00 row in revision 2. Check revision 1 stays visible.
        publish(self.writer, self.reports['SYN-OIL', 1])
        self.admin.execute('ALTER TABLE reporting_private.rows ADD CONSTRAINT proof_limit CHECK (net_pnl < 320)')
        try:
            with self.assertRaises(psycopg.errors.CheckViolation):
                publish(self.writer, self.reports['SYN-OIL', 2])
        finally:
            self.admin.execute('ALTER TABLE reporting_private.rows DROP CONSTRAINT proof_limit')
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(dump_visible(self.connect('report_oil'), 'oil')[0]['revision'], 1)

    def test_late_complete_data_keeps_highest_accepted_revision(self):
        # Sample: Load oil revision 2 before revision 1. Check net P&L 320.00 and revision 2.
        for revision in (2, 1):
            publish(self.writer, self.reports['SYN-OIL', revision])
        rows = dump_visible(self.connect('report_oil'), 'oil')
        self.assertEqual([(row['revision'], str(row['net_pnl'])) for row in rows], [(2, '320.00')])

    def test_desk_roles_restrict_exports_and_writes(self):
        # Sample: Read both books as oil. Check oil succeeds and gas, private rows, and writes fail.
        for report in self.reports.values():
            publish(self.writer, report)
        for scope, other in (('oil', 'gas'), ('gas', 'oil')):
            reader = self.connect(f'report_{scope}')
            self.assertEqual({row['book'] for row in dump_visible(reader, scope)}, {f'SYN-{scope.upper()}'})
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                dump_visible(reader, other)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                reader.execute('SELECT * FROM reporting_private.rows')
            allowed, = reader.execute('SELECT has_table_privilege(%s, %s)',
                                      (f'{scope}.energy_futures_close', 'DELETE')).fetchone()
            self.assertFalse(allowed)
        unassigned = self.connect('report_unassigned')
        for scope in ('oil', 'gas'):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                dump_visible(unassigned, scope)


if __name__ == '__main__':
    unittest.main()
