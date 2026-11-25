"""Sample: Run AnalyticsSinkContractTests on each adapter. Check idempotent append, schema refusal and filter order."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pg_support  # noqa: E402
from durai.adapters.analytics_sink.memory import MemoryAnalyticsSink  # noqa: E402
from durai.adapters.analytics_sink.postgres_table import PostgresAnalyticsSink  # noqa: E402
from durai.ports.errors import NotFound, SchemaMismatch  # noqa: E402


def receipt(label, sample, calls):
    # Sample: Make a build receipt row for label L-1, sample S-TRADE-BOOK and 28 calls.
    return {'label': label, 'sample_id': sample, 'calls': calls, 'stop_reason': 'FINISHED', 'cost_enterprise': 3.02}


class AnalyticsSinkContract:
    def make_sink(self):
        raise NotImplementedError

    def setUp(self):
        # Sample: Make an empty sink.
        self.sink = self.make_sink()

    def test_append_is_idempotent_on_key(self):
        # Sample: Append one receipt with key R-1 twice. Check both calls return 1 and the table holds one row.
        rows = [receipt('L-1', 'S-TRADE-BOOK', 28)]
        self.assertEqual((1, 1), (self.sink.append('build_receipt', rows, 'R-1'), self.sink.append('build_receipt', rows, 'R-1')))
        self.assertEqual(1, len(self.sink.query('build_receipt')))

    def test_missing_column_is_refused_and_writes_nothing(self):
        # Sample: Append 2 rows where the second has no calls column. Check SchemaMismatch and an empty table.
        bad = receipt('L-1', 'S-TRADE-ACCESS', 67)
        del bad['calls']
        with self.assertRaises(SchemaMismatch):
            self.sink.append('build_receipt', [receipt('L-1', 'S-TRADE-BOOK', 28), bad], 'R-2')
        self.assertEqual([], self.sink.query('build_receipt'))

    def test_unknown_table_is_refused(self):
        # Sample: Append to table nothing. Check SchemaMismatch.
        with self.assertRaises(SchemaMismatch):
            self.sink.append('nothing', [{}], 'R-3')

    def test_query_filters_and_keeps_append_order(self):
        # Sample: Append rows for labels L-1, L-2, L-1. Query label L-1. Check 2 rows in append order.
        self.sink.append('build_receipt', [receipt('L-1', 'S-TRADE-BOOK', 28), receipt('L-2', 'S-TRADE-BOOK', 30), receipt('L-1', 'S-TRADE-ACCESS', 67)], 'R-4')
        found = self.sink.query('build_receipt', {'label': 'L-1'})
        self.assertEqual(['S-TRADE-BOOK', 'S-TRADE-ACCESS'], [row['sample_id'] for row in found])

    def test_query_limit_is_kept(self):
        # Sample: Append 3 rows. Query with limit 2. Check 2 rows.
        self.sink.append('build_receipt', [receipt('L-1', 'A', 1), receipt('L-1', 'B', 2), receipt('L-1', 'C', 3)], 'R-5')
        self.assertEqual(2, len(self.sink.query('build_receipt', limit=2)))

    def test_query_unknown_table_is_not_found(self):
        # Sample: Query table nothing. Check NotFound.
        with self.assertRaises(NotFound):
            self.sink.query('nothing')


class MemoryAnalyticsSinkTests(AnalyticsSinkContract, unittest.TestCase):
    def make_sink(self):
        # Sample: Return a new MemoryAnalyticsSink.
        return MemoryAnalyticsSink()


@unittest.skipUnless(pg_support.docker_ready(), 'Docker is not available')
class PostgresAnalyticsSinkTests(AnalyticsSinkContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start a disposable PostgreSQL 17 container once for this class.
        cls.connect = pg_support.start_postgres()

    @classmethod
    def tearDownClass(cls):
        # Sample: Remove the container.
        pg_support.stop_postgres()

    def make_sink(self):
        # Sample: Create the tables. Empty them. Return a sink on the container.
        sink = PostgresAnalyticsSink(type(self).connect)
        sink.ensure_schema()
        with type(self).connect() as conn:
            conn.execute('TRUNCATE analytics_row, analytics_key')
        return sink


if __name__ == '__main__':
    unittest.main()
