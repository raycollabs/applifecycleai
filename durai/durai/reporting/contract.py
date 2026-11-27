import hashlib
import json
from decimal import Decimal, InvalidOperation, localcontext

FORMAT = 'durai-report/v1'
PROFILES = ('postgres', 'databricks', 'clickhouse')
MEASURES = ('opening_units', 'closing_units', 'opening_pnl', 'fill_pnl', 'fees', 'net_pnl', 'settlement_cash')
DECIMAL_PRECISION = 38
DECIMAL_SCALE = 12


def canonical(value):
    return json.dumps(value, default=str, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def select_profile(requested=None, *, sources=('postgres',), transfers_allowed=False):
    # Sample: Select Databricks for one PostgreSQL source. Check the selected profile.
    if requested is not None:
        if requested not in PROFILES:
            raise ValueError('Choose postgres, databricks, or clickhouse')
        return {'profile': requested, 'reason': 'explicit'}
    if tuple(sources) == ('postgres',):
        return {'profile': 'postgres', 'reason': 'single_postgres'}
    if len(sources) > 1 and transfers_allowed:
        return {'profile': 'databricks', 'reason': 'permitted_multi_source'}
    return {'profile': None, 'reason': 'choice_required'}


def close_envelope(close, *, application, revision, mark_references):
    # Sample: Open with two lots. Close one lot. Check the report row and its digest.
    if not application or type(revision) is not int or revision < 1:
        raise ValueError('Supply an application and positive report revision')
    if close['status'] not in {'COMPLETE', 'INCOMPLETE', 'EMPTY'}:
        raise ValueError('Use a valid report status')
    key = {'application': application, 'business_date': str(close['business_date']),
           'book': close['book'], 'revision': revision}
    columns = {'business_date': key['business_date'], 'book': key['book'], 'currency': close['currency']}
    rows = [columns | {'contract': line['instrument']} |
            {field: str(line[field]) if line[field] is not None else None for field in MEASURES}
            for line in close['instruments']] if close['status'] == 'COMPLETE' else []
    if close['status'] == 'COMPLETE' and (not rows or close['issues'] or
            len({row['contract'] for row in rows}) != len(rows) or
            sum(Decimal(row['net_pnl']) for row in rows) != Decimal(close['net_pnl'])):
        raise ValueError('Complete report rows differ from the close')
    report = {'format': FORMAT, 'key': key, 'status': close['status'],
              'event_cutoff': key['business_date'], 'known_at': str(close['known_at']),
              'calculation_version': close['version'], 'input_sha256': close['input_sha256'],
              'mark_references': mark_references, 'issues': close['issues'], 'rows': rows}
    normalized = json.loads(canonical(report))
    return verify_envelope(normalized | {'content_sha256': digest(normalized)})


def verify_envelope(report):
    # Sample: Change a report row. Check the digest refusal.
    content = {key: value for key, value in report.items() if key != 'content_sha256'}
    if content.get('format') != FORMAT or report.get('content_sha256') != digest(content):
        raise ValueError('Report contract or digest differs')
    if content['status'] not in {'COMPLETE', 'INCOMPLETE', 'EMPTY'}:
        raise ValueError('Use a valid report status')
    rows = content['rows']
    if content['status'] != 'COMPLETE' and rows:
        raise ValueError('Only complete reports may contain publishable rows')
    if content['status'] == 'COMPLETE' and not rows:
        raise ValueError('Complete reports require rows')
    key = content['key']
    if (set(key) != {'application', 'business_date', 'book', 'revision'} or
            type(key['revision']) is not int or key['revision'] < 1 or
            any(not isinstance(key[field], str) or not key[field]
                for field in ('application', 'business_date', 'book'))):
        raise ValueError('Use a valid report key')
    expected = (key['business_date'], key['book'])
    if any((row['business_date'], row['book']) != expected for row in rows):
        raise ValueError('Report rows differ from the report key')
    row_keys = {(row['contract'], row['currency']) for row in rows}
    if len(row_keys) != len(rows):
        raise ValueError('Report contains a duplicate contract and currency')
    if any(not contract or not currency for contract, currency in row_keys):
        raise ValueError('Supply a contract and currency')
    if content['status'] == 'COMPLETE' and content['issues']:
        raise ValueError('Complete reports cannot contain issues')
    for row in rows:
        verify_measures(row)
    return report


def verify_measures(row):
    # Sample: Start with 300 USD in opening P&L. Add 30 USD and remove 10 USD in fees. Check 320 USD.
    values = {}
    for field in MEASURES:
        value = row[field]
        if field == 'settlement_cash' and value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f'Use a decimal string for {field}')
        try:
            number = Decimal(value)
        except InvalidOperation as error:
            raise ValueError(f'Use a decimal string for {field}') from error
        if (not number.is_finite() or number.adjusted() >= DECIMAL_PRECISION - DECIMAL_SCALE or
                number.as_tuple().exponent < -DECIMAL_SCALE):
            raise ValueError(f'Use an exact Decimal(38, 12) value for {field}')
        values[field] = number
    with localcontext(prec=DECIMAL_PRECISION + 2):
        net = values['opening_pnl'] + values['fill_pnl'] - values['fees']
    if net != values['net_pnl']:
        raise ValueError('Report P&L does not reconcile')
