import argparse
import json
import os
from decimal import Decimal
from math import ceil
from pathlib import Path
from statistics import median
from time import perf_counter_ns

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from durai.reporting.contract import digest

from .publish import VISIBLE_COLUMNS


def measure(dsn, workload, fixture_hash):
    # Sample: Read SYN-OIL on 2026-11-03 thirty times. Check one row and revision 2.
    if fixture_hash != workload['fixture_sha256']:
        raise ValueError('Fixture digest differs from the workload')
    measure_config = workload['measurement']
    selected = workload['query']
    if selected['identity'] != 'oil-reader' or measure_config['concurrency'] != 1:
        raise ValueError('Use the single-client oil-reader workload')
    query = sql.SQL('SELECT {} FROM oil.energy_futures_close '
                    'WHERE business_date = %s AND book = %s ORDER BY {}').format(
        sql.SQL(', ').join(map(sql.Identifier, VISIBLE_COLUMNS)),
        sql.SQL(', ').join(map(sql.Identifier, selected['order'])),
    )
    args = (selected['business_date'], selected['book'])
    start = perf_counter_ns()
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
        connect_ms = (perf_counter_ns() - start) / 1_000_000
        database = connection.execute(
            'SELECT version() AS version, current_user AS role, pg_backend_pid() AS pid').fetchone()
        if database['role'] != 'report_oil':
            raise ValueError('Connect as report_oil for the workload')
        explain = connection.execute(sql.SQL('EXPLAIN (VERBOSE, FORMAT JSON) ') + query, args).fetchone()
        query_id = explain['QUERY PLAN'][0].get('Query Identifier')
        for _ in range(measure_config['warmup_queries']):
            connection.execute(query, args).fetchall()
        durations = []
        rows = []
        for _ in range(measure_config['measurements']):
            start = perf_counter_ns()
            rows = connection.execute(query, args).fetchall()
            durations.append((perf_counter_ns() - start) / 1_000_000)
            if len(rows) != selected['expected_rows'] or any(
                    row['revision'] != selected['expected_revision'] or
                    row['net_pnl'] != Decimal(selected['expected_net_pnl']) for row in rows):
                raise ValueError('Scoped workload result differs')
    return {'profile': 'postgres', 'fixture_sha256': fixture_hash, 'database': database,
            'driver_version': psycopg.__version__, 'query_id': query_id, 'connection_ms': connect_ms,
            'boundary': measure_config['boundary'], 'measurements': len(durations),
            'warmup_queries': measure_config['warmup_queries'], 'concurrency': 1,
            'p50_ms': median(durations), 'p95_ms': sorted(durations)[ceil(.95 * len(durations)) - 1],
            'rows': rows}


def main():
    parser = argparse.ArgumentParser(description='Measure a scoped PostgreSQL reporting query')
    parser.add_argument('workload', type=Path)
    args = parser.parse_args()
    workload = json.loads(args.workload.read_text(encoding='utf-8'))
    fixture = args.workload.with_name(workload['fixture'])
    fixture_hash = digest(json.loads(fixture.read_text(encoding='utf-8')))
    result = measure(os.environ['REPORTING_DSN'], workload, fixture_hash)
    print(json.dumps(result, default=str, sort_keys=True))


if __name__ == '__main__':
    main()
