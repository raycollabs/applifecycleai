import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from durai.reporting.contract import MEASURES, verify_envelope


KEY = ('application', 'business_date', 'book', 'revision')
VISIBLE_COLUMNS = ('business_date', 'book', 'contract', 'currency', 'revision', *MEASURES)
RUN_FIELDS = ('content_sha256', 'input_sha256', 'status', 'event_cutoff', 'known_at', 'calculation_version')
RUN_INSERT = '''
    INSERT INTO reporting_private.runs
        (application, business_date, book, revision, content_sha256, input_sha256,
         status, event_cutoff, known_at, calculation_version, issues, mark_references, row_count)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (application, business_date, book, revision) DO NOTHING
    RETURNING content_sha256, status, row_count
'''
RUN_SELECT = '''
    SELECT content_sha256, status, row_count FROM reporting_private.runs
    WHERE application = %s AND business_date = %s AND book = %s AND revision = %s
'''
ROW_INSERT = '''
    INSERT INTO reporting_private.rows
        (application, business_date, book, revision, contract, currency,
         opening_units, closing_units, opening_pnl, fill_pnl, fees, net_pnl, settlement_cash)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
'''


class RevisionConflict(ValueError):
    pass


def _amount(value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError('Report amounts must be decimal strings')
    amount = Decimal(value)
    if not amount.is_finite():
        raise ValueError('Report amounts must be finite')
    return amount


def publish(connection, report):
    # Sample: Publish oil revision 2 twice. Check one run and one visible row.
    report = verify_envelope(report)
    key = tuple(report['key'][field] for field in KEY)
    rows = [(*key, row['contract'], row['currency'],
             *(_amount(row[field], nullable=field == 'settlement_cash') for field in MEASURES))
            for row in report['rows']]
    evidence = (*key, *(report[field] for field in RUN_FIELDS),
                Jsonb(report['issues']), Jsonb(report['mark_references']), len(rows))
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(RUN_INSERT, evidence)
        result = cursor.fetchone()
        if result is None:
            cursor.execute(RUN_SELECT, key)
            result = cursor.fetchone()
            if result['content_sha256'] != report['content_sha256']:
                raise RevisionConflict('Report revision already has a different digest')
        elif rows:
            cursor.executemany(ROW_INSERT, rows)
    return {'key': report['key'], **result}


def dump_visible(connection, scope):
    # Sample: Read the oil scope. Check book SYN-OIL and net P&L 320.00.
    if scope not in {'oil', 'gas'}:
        raise ValueError('Choose oil or gas')
    query = sql.SQL('SELECT {} FROM {} ORDER BY business_date, book, contract, currency').format(
        sql.SQL(', ').join(map(sql.Identifier, VISIBLE_COLUMNS)), sql.Identifier(scope, 'energy_futures_close'))
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(query).fetchall()


def main():
    parser = argparse.ArgumentParser(description='Publish or read PostgreSQL report revisions')
    commands = parser.add_subparsers(dest='command', required=True)
    load = commands.add_parser('publish')
    load.add_argument('fixture', type=Path)
    dump = commands.add_parser('dump')
    dump.add_argument('scope', choices=('oil', 'gas'))
    args = parser.parse_args()
    with psycopg.connect(os.environ['REPORTING_DSN']) as connection:
        if args.command == 'publish':
            data = json.loads(args.fixture.read_text(encoding='utf-8'))
            result = [publish(connection, report) for report in data['reports']]
        else:
            result = dump_visible(connection, args.scope)
    print(json.dumps(result, default=str, sort_keys=True))


if __name__ == '__main__':
    main()
