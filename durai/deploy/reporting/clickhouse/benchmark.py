import argparse
from decimal import Decimal
import json
from math import ceil
from pathlib import Path
from time import perf_counter_ns

from deploy.reporting.clickhouse.close import ACCOUNTS, connect, dump
from durai.reporting.contract import digest


def benchmark(workload, fixture):
    spec = json.loads(workload.read_text(encoding='utf-8'))
    fixture_digest = digest(json.loads(fixture.read_text(encoding='utf-8')))
    if fixture_digest != spec['fixture_sha256']:
        raise ValueError('Workload fixture digest differs')
    if spec['format'] != 'durai-reporting-workload/v1' or spec['query']['book'] != 'SYN-OIL':
        raise ValueError('Use the oil reference workload')
    measure = spec['measurement']
    if measure['concurrency'] != 1:
        raise ValueError('Use one client for the reference workload')

    started = perf_counter_ns()
    client = connect('oil_conn', ACCOUNTS['oil_conn'][1])
    client.connection.connect()
    connection_ms = (perf_counter_ns() - started) / 1_000_000
    try:
        business_date = spec['query']['business_date']
        for _ in range(measure['warmup_queries']):
            dump(client, 'oil', business_date=business_date)
        active_socket = client.connection.sock
        if active_socket is None:
            raise RuntimeError('ClickHouse closed the warm connection')
        observations = []
        query_ids = []
        for _ in range(measure['measurements']):
            start = perf_counter_ns()
            rows = dump(client, 'oil', business_date=business_date)
            observations.append((perf_counter_ns() - start) / 1_000_000)
            if client.connection.sock is not active_socket:
                raise RuntimeError('ClickHouse reopened the warm connection')
            query_ids.append(client.last_query_id)
            expected = spec['query']
            if (len(rows) != expected['expected_rows']
                    or rows[0]['revision'] != expected['expected_revision']
                    or Decimal(rows[0]['net_pnl']) != Decimal(expected['expected_net_pnl'])):
                raise ValueError('The query result differs from the reference')
    finally:
        client.connection.close()

    observations.sort()
    return {
        'fixture_sha256': fixture_digest,
        'version': 'ClickHouse 25.8.25.37',
        'client': 'Python http.client and JSONEachRow',
        'connection_reused': True,
        'boundary': measure['boundary'],
        'concurrency': measure['concurrency'],
        'cache_state': measure['cache_state'],
        'warmup_queries': measure['warmup_queries'],
        'measurements': len(observations),
        'query_ids': query_ids,
        'p50_ms': observations[ceil(len(observations) * 0.5) - 1],
        'p95_ms': observations[ceil(len(observations) * 0.95) - 1],
        'connection_ms': connection_ms,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('workload', type=Path)
    parser.add_argument('fixture', type=Path)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.workload, args.fixture), sort_keys=True))
