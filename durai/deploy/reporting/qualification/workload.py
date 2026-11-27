import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta
import json
import math
from pathlib import Path
from time import perf_counter

import psycopg

from deploy.reporting.clickhouse.close import ClickHouse, publish as publish_ch
from deploy.reporting.postgres.publish import publish as publish_pg
from durai.reporting.contract import digest
from tools.verify_reporting_offerings import FIXTURE


DAYS = 128
CONTRACTS = 8
START = date(2025, 1, 1)
QUERY = ('SELECT business_date, book, contract, currency, revision, net_pnl '
         'FROM oil.energy_futures_close WHERE business_date >= {} AND business_date < {} '
         'ORDER BY business_date, contract, currency')


def read_env(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines() if line and '=' in line)


def reports():
    # Sample: Build eight oil rows for 2025-01-01. Check eight contract keys and one report digest.
    fixture = json.loads(FIXTURE.read_bytes())
    for offset in range(DAYS):
        business_date = str(START + timedelta(days=offset))
        for original in fixture['reports'][:2]:
            report = deepcopy(original)
            report.pop('content_sha256')
            report['key']['business_date'] = report['event_cutoff'] = business_date
            report['known_at'] = f'{business_date} 23:00:00+00:00'
            template = report['rows'][0]
            report['rows'] = [template | {'business_date': business_date,
                                         'contract': f"{template['contract']}-{index:02d}"}
                              for index in range(CONTRACTS)]
            report['input_sha256'] = digest([business_date, report['rows']])
            yield report | {'content_sha256': digest(report)}


def pg(settings, role):
    return psycopg.connect(host='127.0.0.1', port=int(settings['REPORTING_PORT']), dbname='reporting',
                           user=f'report_{role}', password=settings[f'REPORTING_{role.upper()}_PASSWORD'],
                           autocommit=True)


def clickhouse(settings, account, role):
    return ClickHouse('http://127.0.0.1:58123', account, settings[f'DURAI_CH_{role.upper()}_PASSWORD'])


def stats(values):
    ordered = sorted(values)
    return {f'p{percent}_ms': round(ordered[math.ceil(len(ordered) * percent / 100) - 1] * 1000, 3)
            for percent in (50, 95, 99)}


def benchmark(connect, select, *, concurrency, observations):
    # Sample: Run four clients. Fetch eight days per query. Check 64 rows per query.
    def request(client, offset):
        start = START + timedelta(days=offset)
        finish = start + timedelta(days=8)
        began = perf_counter()
        result = select(client, start, finish)
        elapsed = perf_counter() - began
        assert len(result) == 8 * CONTRACTS
        return elapsed

    def reader(worker):
        with connect() as client:
            request(client, worker)
            return [request(client, (worker * 17 + index * 7) % (DAYS - 8)) for index in range(observations)]

    began = perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        durations = [sample for client in pool.map(reader, range(concurrency)) for sample in client]
    wall = perf_counter() - began
    return {**stats(durations), 'queries_per_second': round(len(durations) / wall, 2),
            'concurrency': concurrency, 'queries': len(durations), 'rows_per_query': 8 * CONTRACTS}


def main():
    parser = argparse.ArgumentParser(description='Measure matching futures reporting workloads')
    parser.add_argument('pg_env', type=Path)
    parser.add_argument('ch_env', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    pg_env, ch_env = read_env(args.pg_env), read_env(args.ch_env)
    lock = args.output.parent / 'ch-publish.lock'
    seed = list(reports())
    result = {'reports': len(seed), 'generated_rows': len(seed) * CONTRACTS, 'fixture_sha256': digest(seed)}

    def run(name, writer, publish, reader, select):
        latencies = []
        with writer() as client:
            for report in seed:
                start = perf_counter()
                publish(client, report)
                latencies.append(perf_counter() - start)
        result[name] = {'publication': stats(latencies), 'publication_seconds': round(sum(latencies), 3),
                        'queries': [benchmark(reader, select, concurrency=count, observations=40)
                                    for count in (1, 4)]}

    run('postgres', lambda: pg(pg_env, 'publisher'), publish_pg, lambda: pg(pg_env, 'oil'),
        lambda client, since, until: client.execute(QUERY.format('%s', '%s'), (since, until)).fetchall())
    run('clickhouse', lambda: clickhouse(ch_env, 'close_publisher', 'publisher'),
        lambda client, report: publish_ch(client, report, lock), lambda: clickhouse(ch_env, 'oil_conn', 'oil'),
        lambda client, since, until: client.select(QUERY.format('{since:Date32}', '{until:Date32}'),
                                                   parameters={'since': str(since), 'until': str(until)}))
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
