import argparse
import json
from pathlib import Path
from time import perf_counter

import psycopg

from deploy.reporting.clickhouse.close import dump as dump_ch, publish as publish_ch
from deploy.reporting.postgres.publish import publish as publish_pg
from deploy.reporting.qualification.workload import START, clickhouse, pg, read_env, reports
from durai.reporting.contract import digest


def revision():
    # Sample: Make oil revision 2. Add one dollar to opening P&L and net P&L.
    original = next(reports())
    report = {key: value for key, value in original.items() if key != 'content_sha256'}
    report['key']['revision'] = 2
    report['rows'] = [row | {'opening_pnl': '301', 'net_pnl': '301'} for row in report['rows']]
    report['input_sha256'] = digest(report['rows'])
    return report | {'content_sha256': digest(report)}


def exercise(publish, visible):
    # Sample: Fail revision 2. Check revision 1. Repeat the load. Check revision 2.
    assert set(visible()) == {1}
    started = perf_counter()
    publish(fail=True)
    assert set(visible()) == {1}
    retry = perf_counter()
    publish(fail=False)
    assert set(visible()) == {2}
    return {'failure_to_visible_ms': round((perf_counter() - started) * 1000, 3),
            'retry_to_visible_ms': round((perf_counter() - retry) * 1000, 3), 'accepted_revision': 2}


def prove_pg(settings, report):
    with psycopg.connect(host='127.0.0.1', port=int(settings['REPORTING_PORT']), dbname='reporting',
                          user='postgres', password=settings['REPORTING_ADMIN_PASSWORD'], autocommit=True) as admin, \
            pg(settings, 'publisher') as writer, pg(settings, 'oil') as reader:
        def publish(fail):
            if not fail:
                return publish_pg(writer, report)
            admin.execute("ALTER TABLE reporting_private.rows ADD CONSTRAINT qualification_fault "
                          "CHECK (business_date <> DATE '2025-01-01' OR revision <> 2)")
            try:
                try:
                    publish_pg(writer, report)
                except psycopg.errors.CheckViolation:
                    return
                raise AssertionError('Publication did not hit the test constraint')
            finally:
                admin.execute('ALTER TABLE reporting_private.rows DROP CONSTRAINT qualification_fault')

        def visible():
            return [row[0] for row in reader.execute('SELECT revision FROM oil.energy_futures_close '
                                                     'WHERE business_date = %s', (START,))]

        return exercise(publish, visible)


def prove_ch(settings, report, lock):
    def stop():
        raise InterruptedError('Test interruption after staging')

    with clickhouse(settings, 'close_publisher', 'publisher') as writer, clickhouse(settings, 'oil_conn', 'oil') as reader:
        def publish(fail):
            try:
                publish_ch(writer, report, lock, after_stage=stop if fail else None)
            except InterruptedError:
                if fail:
                    return
                raise
            if fail:
                raise AssertionError('Publication did not hit the test interruption')

        return exercise(publish, lambda: [row['revision'] for row in dump_ch(reader, 'oil', business_date=str(START))])


def main():
    parser = argparse.ArgumentParser(description='Measure the local recovery of a report revision')
    parser.add_argument('pg_env', type=Path)
    parser.add_argument('ch_env', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = revision()
    result = {'postgres': prove_pg(read_env(args.pg_env), report),
              'clickhouse': prove_ch(read_env(args.ch_env), report, args.output.parent / 'ch-publish.lock')}
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
