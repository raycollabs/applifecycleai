import argparse
import base64
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic, sleep
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from durai.reporting.contract import MEASURES, canonical, verify_envelope

HERE = Path(__file__).resolve().parent
JOURNAL = 'reporting_internal.close_revisions'
KEY = (
    'application = {application:String} AND business_date = {business_date:Date32} '
    'AND book = {book:String} AND revision = {revision:UInt32}'
)
BOOKS = {'oil': 'SYN-OIL', 'gas': 'SYN-GAS'}
READER = {'oil': 'oil_conn', 'gas': 'gas_conn'}
ACCOUNTS = {
    'close_publisher': ('reporting_publisher', 'DURAI_CH_PUBLISHER_PASSWORD'),
    'oil_conn': ('reporting_oil', 'DURAI_CH_OIL_PASSWORD'),
    'gas_conn': ('reporting_gas', 'DURAI_CH_GAS_PASSWORD'),
}


class ClickHouseError(RuntimeError):
    def __init__(self, status, code):
        self.status = status
        self.code = code
        super().__init__(f'ClickHouse request failed with HTTP {status} and database code {code}')


class RevisionConflict(ValueError):
    pass


class ClickHouse:
    def __init__(self, endpoint, username, password, *, session=None):
        target = urlsplit(endpoint)
        if target.scheme not in {'http', 'https'} or not target.hostname or target.username:
            raise ValueError('Supply a ClickHouse HTTP or HTTPS endpoint')
        if target.query or target.fragment:
            raise ValueError('The endpoint cannot contain a query or fragment')
        connector = HTTPSConnection if target.scheme == 'https' else HTTPConnection
        self.connection = connector(target.hostname, target.port, timeout=45)
        self.path = target.path.rstrip('/') + '/'
        credentials = base64.b64encode(f'{username}:{password}'.encode()).decode()
        self.headers = {'Authorization': f'Basic {credentials}', 'Content-Type': 'text/plain'}
        self.session = session
        self.last_query_id = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()

    def execute(self, sql, *, data=None, parameters=None, query_id=None):
        options = {'wait_end_of_query': '1'}
        if self.session:
            options['session_id'] = self.session
        if query_id:
            options['query_id'] = query_id
        options.update({f'param_{key}': value for key, value in (parameters or {}).items()})
        body = sql.encode()
        if data is not None:
            body += b'\n' + data + b'\n'
        try:
            self.connection.request('POST', f'{self.path}?{urlencode(options)}', body=body, headers=self.headers)
            response = self.connection.getresponse()
            self.last_query_id = response.getheader('X-ClickHouse-Query-Id')
            result = response.read()
        except (HTTPException, OSError):
            self.connection.close()
            raise
        if response.status != 200:
            match = re.search(rb'Code: (\d+)\b', result[:192])
            raise ClickHouseError(response.status, int(match[1]) if match else None)
        return result

    def select(self, sql, *, parameters=None):
        response = self.execute(
            sql + ' FORMAT JSONEachRow',
            parameters=parameters,
        )
        return [json.loads(line, parse_float=str) for line in response.splitlines()]

    def append(self, record):
        self.execute(
            f'INSERT INTO {JOURNAL} SETTINGS async_insert=0 FORMAT JSONEachRow',
            data=canonical(record),
            query_id=record['operation_id'],
        )


@contextmanager
def publisher_lock(path, *, timeout=45):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        deadline = monotonic() + timeout
        while True:
            try:
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt

                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, PermissionError, OSError) as error:
                if monotonic() >= deadline:
                    raise TimeoutError('Wait for the active close publisher') from error
                sleep(0.05)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def normalized_rows(report):
    verify_envelope(report)
    key = report['key']
    for field in ('application', 'book'):
        if not isinstance(key[field], str) or not key[field] or len(key[field]) > 256:
            raise ValueError(f'Supply a valid {field}')
    if type(key['revision']) is not int or not 1 <= key['revision'] <= 2**32 - 1:
        raise ValueError('Supply a positive UInt32 report revision')
    for value in (key['business_date'], report['event_cutoff']):
        parsed_date = date.fromisoformat(value)
        if parsed_date.isoformat() != value or not 1900 <= parsed_date.year <= 2299:
            raise ValueError('Supply a Date32 date in YYYY-MM-DD form')
    if datetime.fromisoformat(report['known_at']).utcoffset() is None:
        raise ValueError('Supply a known-at time with a timezone')

    seen = set()
    values = []
    for row in report['rows']:
        grain = (row['contract'], row['currency'])
        if any(not isinstance(part, str) or not part or len(part) > 256 for part in grain):
            raise ValueError('Supply a contract and currency')
        if grain in seen:
            raise ValueError('Report contains a duplicate contract and currency')
        seen.add(grain)
        measures = {}
        for field in MEASURES:
            value = row[field]
            if value is None and field == 'settlement_cash':
                measures[field] = None
                continue
            if not isinstance(value, str):
                raise ValueError('Supply each measure as a decimal string')
            try:
                number = Decimal(value)
            except InvalidOperation:
                raise ValueError('Supply each measure as a decimal string') from None
            if not number.is_finite() or number.adjusted() >= 26 or number.as_tuple().exponent < -12:
                raise ValueError('Measure exceeds exact Decimal(38, 12) range')
            measures[field] = format(number, 'f')
        values.append({'contract': grain[0], 'currency': grain[1], **measures})
    if len(values) > 10000 or len(canonical(report)) > 8 * 1024 * 1024:
        raise ValueError('Report exceeds the batch limit')
    return values


def history(client, key):
    return client.select(
        f'SELECT phase, status, content_sha256, operation_id, envelope FROM {JOURNAL} WHERE {KEY}',
        parameters=key,
    )


def publish(client, report, lock_path, *, after_stage=None):
    # Sample: Stage oil revision 2. Stop the load. Check oil revision 1. Repeat the load. Check oil revision 2.
    normalized_rows(report)
    key = report['key']
    content_digest = report['content_sha256']
    envelope = canonical(report).decode()
    fields = dict(
        key, content_sha256=content_digest, status=report['status'], envelope=envelope,
        event_cutoff=report['event_cutoff'], known_at=report['known_at'],
    )
    with publisher_lock(lock_path):
        records = history(client, key)
        if any(row['content_sha256'] != content_digest for row in records):
            raise RevisionConflict('This report revision has a different content digest')
        terminal = [row for row in records if row['phase'] in {'published', 'recorded'}]
        if len(terminal) > 1:
            raise RuntimeError('Report revision has multiple terminal records')
        if terminal:
            return receipt(key, terminal[0], replay=True)
        if report['status'] != 'COMPLETE':
            return store(client, fields, 'recorded', [])

        staged = [row for row in records if row['phase'] == 'staged']
        if not staged:
            store(client, fields, 'staged', [])
            staged = [row for row in history(client, key) if row['phase'] == 'staged']
        if len(staged) != 1 or staged[0]['envelope'] != envelope:
            raise RuntimeError('Stored stage does not match the incoming envelope')
        rows = normalized_rows(json.loads(staged[0]['envelope']))
        if after_stage:
            after_stage()
        return store(client, fields, 'published', rows)


def store(client, fields, phase, rows):
    record = dict(fields, phase=phase, operation_id=str(uuid4()), report_rows=rows)
    client.append(record)
    key = {field: fields[field] for field in ('application', 'business_date', 'book', 'revision')}
    return receipt(key, record, replay=False)


def receipt(key, record, *, replay):
    return {
        'key': key, 'phase': record['phase'], 'status': record['status'],
        'content_sha256': record['content_sha256'], 'operation_id': record['operation_id'],
        'replay': replay,
    }


def connect(account, secret_var, *, session=None):
    return ClickHouse(
        os.environ.get('DURAI_CH_HTTP', 'http://127.0.0.1:58123'),
        account,
        os.environ[secret_var],
        session=session,
    )


def bootstrap(client):
    credentials = {account: os.environ[variable] for account, (_, variable) in ACCOUNTS.items()}
    if any(not password for password in credentials.values()):
        raise ValueError('Set nonempty credentials for each account')
    for sql in (HERE / 'schema.sql').read_text().split(';'):
        if sql.strip():
            client.execute(sql)
    for account, (role, _) in ACCOUNTS.items():
        password_digest = hashlib.sha256(credentials[account].encode()).hexdigest()
        settings = ' SETTINGS readonly = 1, output_format_json_quote_decimals = 1' if account in READER.values() else ''
        client.execute(
            f"CREATE USER IF NOT EXISTS {account} IDENTIFIED WITH sha256_hash BY '{password_digest}'"
            + settings
        )
        client.execute(f'GRANT {role} TO {account}')


def dump(client, scope, *, business_date=None):
    if scope not in BOOKS:
        raise ValueError('Choose oil or gas')
    columns = ', '.join(('business_date', 'book', 'contract', 'currency', 'revision', *MEASURES))
    if business_date is not None:
        return client.select(
            f'SELECT {columns} FROM {scope}.energy_futures_close '
            'WHERE business_date = {business_date:Date32} AND book = {book:String} '
            'ORDER BY contract, currency',
            parameters={'business_date': business_date, 'book': BOOKS[scope]},
        )
    return client.select(
        f'SELECT {columns} FROM {scope}.energy_futures_close ORDER BY business_date, book, contract, currency'
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('bootstrap')
    load = commands.add_parser('load')
    load.add_argument('fixture', type=Path)
    load.add_argument('--lock', required=True, type=Path)
    read = commands.add_parser('dump')
    read.add_argument('scope', choices=BOOKS)
    read.add_argument('--business-date')
    args = parser.parse_args(argv)
    if args.command == 'bootstrap':
        with connect('reporting_admin', 'DURAI_CH_ADMIN_PASSWORD') as client:
            bootstrap(client)
        return
    if args.command == 'load':
        fixture = json.loads(args.fixture.read_text(encoding='utf-8'))
        reports = fixture['reports']
        for report in reports:
            normalized_rows(report)
        with connect(
            'close_publisher', 'DURAI_CH_PUBLISHER_PASSWORD', session='durai-close-publisher-v1'
        ) as client:
            result = [publish(client, report, args.lock) for report in reports]
    else:
        account = READER[args.scope]
        with connect(account, ACCOUNTS[account][1]) as client:
            result = dump(client, args.scope, business_date=args.business_date)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ClickHouseError, KeyError, OSError, ValueError) as error:
        sys.exit(str(error))
