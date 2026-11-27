import argparse
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from databricks.factory import package as factory
from durai.reporting.contract import MEASURES, canonical, verify_envelope

FORMAT = 'durai-databricks-reporting/v1'
HERE = Path(__file__).resolve().parent
FIXTURE = ROOT / 'design/reporting/energy_futures_close.json'
COLUMNS = ('business_date', 'book', 'contract', 'currency', 'revision', *MEASURES)
KEY_COLUMNS = ('application', 'business_date', 'book', 'revision', 'status', 'content_sha256', 'payload')
IDENTITY = 'sql/fixture_identity.sql'
RULES = 'sql/fixture_business_rules.sql'
VISIBLE = 'sql/visible.sql'
PARITY = 'sql/fixture_parity.sql'
ARTIFACTS = ('databricks.yml', 'end_of_day.lvdash.json', IDENTITY, RULES, 'sql/publish.sql',
             'sql/reconcile.sql', 'sql/test_grants.sql', VISIBLE, PARITY)
RESOURCES = {'resources.jobs.report_acceptance', 'resources.dashboards.end_of_day',
             'resources.dashboards.end_of_day.permissions'}
DECIMAL = 'DECIMAL(38,12)'


def render(path, substitutions):
    template = (HERE / path).read_text(encoding='utf-8')
    return re.sub(r'__[A-Z_]+__', lambda token: substitutions[token[0]], template).rstrip()


def sql_text(value):
    return f"decode(unhex('{value.encode('utf-8').hex()}'), 'UTF-8')"


def sql_values(records, columns):
    return '\n  UNION ALL\n  '.join(
        'SELECT ' + ', '.join(f'{value} AS {column}' for value, column in zip(values, columns, strict=True))
        for values in records)


def report_sql(reports):
    return sql_values([
        (sql_text(key['application']), f"DATE '{key['business_date']}'", sql_text(key['book']),
         str(key['revision']), sql_text(report['status']), sql_text(report['content_sha256']),
         sql_text(canonical(report).decode('utf-8')))
        for report in reports for key in (report['key'],)
    ], KEY_COLUMNS)


def latest_rows(reports):
    # Sample: Add incomplete gas revision 2. Keep gas revision 1 in the result.
    complete = {}
    ordered = sorted(reports, key=lambda report: report['key']['revision'])
    for report in ordered:
        key = report['key']
        if report['status'] == 'COMPLETE':
            complete[key['business_date'], key['book']] = report
    return sorted([row['business_date'], row['book'], row['contract'], row['currency'],
                   str(report['key']['revision']), *(row[field] for field in MEASURES)]
                  for report in complete.values() for row in report['rows'])


def read_fixture(path):
    raw = path.read_bytes()
    fixture = json.loads(raw)
    reports = [verify_envelope(report) for report in fixture['reports']]
    keys = set()
    for report in reports:
        key = report['key']
        if key['application'] != 'energy-futures' or key['book'] not in {'SYN-OIL', 'SYN-GAS'}:
            raise ValueError('Use an energy futures report and a test desk')
        if type(key['revision']) is not int or not 0 < key['revision'] < 2**63:
            raise ValueError('Use a positive report revision')
        if date.fromisoformat(key['business_date']).isoformat() != key['business_date']:
            raise ValueError('Use an ISO report date')
        revision = (key['application'], key['business_date'], key['book'], key['revision'])
        if revision in keys:
            raise ValueError('The fixture repeats a report revision')
        keys.add(revision)
        rows = report['rows']
        if len({(row['contract'], row['currency']) for row in rows}) != len(rows):
            raise ValueError('The fixture repeats a report row')
        for row in rows:
            for field in MEASURES:
                if field == 'settlement_cash' and row[field] is None:
                    continue
                if not isinstance(row[field], str) or not re.fullmatch(r'-?[0-9]{1,26}(?:\.[0-9]{1,12})?', row[field]):
                    raise ValueError('Use a decimal string within the report precision')
    if not latest_rows(reports):
        raise ValueError('Supply an acceptance fixture with complete reports')
    return raw, fixture, reports


def dataset(table):
    column_list = ', '.join(COLUMNS)
    return f'SELECT {column_list}\nFROM {table}\nORDER BY business_date, book, contract, currency'


def dashboard(query):
    fields = [{'name': column, 'expression': f'`{column}`'} for column in COLUMNS]
    encodings = [{'fieldName': column, 'displayName': column.replace('_', ' ').capitalize()} for column in COLUMNS]
    widget = {'name': 'desk_close_rows',
              'queries': [{'name': 'main_query', 'query': {'datasetName': 'desk_close', 'fields': fields,
                                                          'disaggregated': True}}],
              'spec': {'version': 2, 'widgetType': 'table', 'encodings': {'columns': encodings}}}
    return {'datasets': [{'name': 'desk_close', 'displayName': 'Desk close', 'queryLines': [query]}],
            'pages': [{'name': 'end_of_day', 'displayName': 'End-of-day P&L', 'pageType': 'PAGE_TYPE_CANVAS',
                       'layout': [{'widget': widget, 'position': {'x': 0, 'y': 0, 'width': 12, 'height': 8}}]}]}


def stage(directory, workspace, warehouse, catalog, schema, publisher, oil_group, gas_group, fixture_path=FIXTURE):
    # Sample: Stage the same reports twice. Check equal package IDs.
    if not re.fullmatch(r'durai_reporting_[a-z0-9_]+', schema):
        raise ValueError('Use a durai_reporting_ test schema')
    if not re.fullmatch(r'[A-Za-z0-9@._+ -]+', publisher) or not publisher.strip():
        raise ValueError('Supply a workspace user or a service principal application ID')
    if not all(re.fullmatch(r'[a-z0-9][a-z0-9_-]+', group) for group in (oil_group, gas_group)) or oil_group == gas_group:
        raise ValueError('Supply two distinct account group names')
    publisher_key = ('service_principal_name' if re.fullmatch(
        r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', publisher, re.IGNORECASE) else 'user_name')
    if publisher_key == 'user_name' and '@' not in publisher:
        raise ValueError('Supply a workspace email for a user publisher')
    raw_fixture, fixture, reports = read_fixture(fixture_path)
    with TemporaryDirectory(prefix='durai-reporting-source-') as temp:
        source = Path(temp) / 'factory'
        prior = factory.stage(source, workspace, warehouse, catalog, fixture_version=0)
        _, factory_sql = factory.verify_artifacts(source, prior['package_id'])
    qualified = f'`{catalog}`.`{schema}`'
    ledger = qualified + '.report_revisions'
    accepted = qualified + '.accepted_report'
    desk_view = qualified + '.desk_report'
    accepted_args = {'__DECIMAL_COLUMNS__': ',\n       '.join(
        f'CAST(item.{field} AS {DECIMAL}) AS {field}' for field in MEASURES),
        '__ROW_TYPE__': 'ARRAY<STRUCT<' + ','.join(f'{field}:STRING' for field in ('contract', 'currency', *MEASURES)) + '>>'}
    incoming = report_sql(reports)
    query = dataset(desk_view)
    expected_rows = latest_rows(reports)
    expected = sql_values([(f"DATE '{day}'", sql_text(book), sql_text(contract), sql_text(currency),
                            revision, *(f'CAST({value or "NULL"} AS {DECIMAL})' for value in measures))
                           for day, book, contract, currency, revision, *measures in expected_rows], COLUMNS)
    common = {'__INCOMING__': incoming, '__SCHEMA__': qualified, '__LEDGER__': ledger, '__ACCEPTED__': accepted,
              '__DESK_VIEW__': desk_view, '__COLUMNS__': ', '.join(COLUMNS)}
    bundle = render('bundle.yml', {'__WAREHOUSE__': warehouse, '__PUBLISHER_KEY__': publisher_key,
                                  '__PUBLISHER_VALUE__': json.dumps(publisher),
                                  '__OIL_GROUP__': json.dumps(oil_group), '__GAS_GROUP__': json.dumps(gas_group)})
    publish = render('sql/publish.sql', common | {
        '__ACCEPTED_SELECT__': render('sql/accepted.sql', accepted_args | {'__SOURCE__': ledger}),
        '__OIL_GROUP_SQL__': sql_text(oil_group), '__GAS_GROUP_SQL__': sql_text(gas_group)})
    parity = ('WITH fixture AS (\n  ' + incoming + '\n),\n'
              + render('sql/accepted.sql', accepted_args | {'__SOURCE__': 'fixture'}).removeprefix('WITH ')
              + '\nORDER BY business_date, book, contract, currency')
    grants = '\n'.join(f'GRANT USE CATALOG ON CATALOG `{catalog}` TO `{group}`;\n'
                       f'GRANT USE SCHEMA ON SCHEMA {qualified} TO `{group}`;\n'
                       f'GRANT SELECT ON VIEW {desk_view} TO `{group}`;' for group in (oil_group, gas_group))
    artifacts = factory_sql | {'databricks.yml': bundle, 'end_of_day.lvdash.json': canonical(dashboard(query)).decode(),
                               'sql/publish.sql': publish, 'sql/reconcile.sql': render('sql/reconcile.sql', common | {
                                   '__EXPECTED__': expected}),
                               'sql/test_grants.sql': grants, VISIBLE: query, PARITY: parity}
    files = {name: (body.rstrip() + '\n').encode('utf-8') for name, body in artifacts.items()}
    manifest = {'format': FORMAT, 'workspace_id': workspace, 'warehouse_id': warehouse, 'catalog': catalog,
                'schema': schema, 'publisher': {publisher_key: publisher}, 'desk_groups': {'SYN-OIL': oil_group, 'SYN-GAS': gas_group},
                'fixture': prior['fixture'], 'factory_package_id': prior['package_id'],
                'report_fixture_sha256': factory.digest(raw_fixture),
                'report_semantic_sha256': factory.digest(canonical(fixture)), 'expected_rows': expected_rows,
                'report_revisions': [report['key'] | {'status': report['status'], 'content_sha256': report['content_sha256'],
                                                    'issues': report['issues']} for report in reports],
                'artifacts': {name: factory.digest(body) for name, body in files.items()}}
    manifest['package_id'] = factory.digest(canonical(manifest))
    directory.mkdir(parents=True)
    (directory / 'sql').mkdir()
    for name, contents in files.items():
        (directory / name).write_bytes(contents)
    (directory / 'manifest.json').write_bytes(canonical(manifest) + b'\n')
    return manifest


def verified(directory, expected_id):
    # Sample: Change the staged dashboard query. Check that verification refuses the package.
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    package_id = manifest.pop('package_id')
    if package_id != expected_id or package_id != factory.digest(canonical(manifest)) or manifest['format'] != FORMAT:
        raise ValueError('Package ID differs from the expected release')
    if set(manifest['artifacts']) != set(ARTIFACTS):
        raise ValueError('Package artifact list differs')
    for name, digest in manifest['artifacts'].items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or factory.digest(path.read_bytes()) != digest:
            raise ValueError(f'Package artifact differs: {name}')
    return manifest | {'package_id': package_id}


def plan_scope(result):
    plan = result['plan']
    if set(plan) != RESOURCES:
        raise ValueError('Expected one report job and one dashboard with its permissions in the plan')
    if any(entry['action'] not in {'create', 'update', 'no-op'} for entry in plan.values()):
        raise ValueError('The bundle plan contains an unexpected action')
    return [{'resource': resource, 'action': entry['action']} for resource, entry in sorted(plan.items())]


def preview(directory, expected_id, profile):
    manifest = verified(directory, expected_id)
    factory.open_profile(profile, manifest)
    cli = ['databricks', 'bundle']
    flags = ['--profile', profile, '--output', 'json']
    factory.run_command([*cli, 'validate', '--strict', *flags], cwd=directory)
    result = json.loads(factory.run_command([*cli, 'plan', *flags], cwd=directory))
    return {'status': 'BUNDLE_PREVIEWED', 'package_id': expected_id, 'workspace_id': manifest['workspace_id'],
            'cli_version': result['cli_version'], 'actions': plan_scope(result)}


def same_rows(actual, expected):
    def normalized(rows):
        return sorted(tuple(row[:5]) + tuple(None if value is None else Decimal(value) for value in row[5:])
                      for row in rows)
    return normalized(actual) == normalized(expected)


def verify_fixture(directory, expected_id, profile, open_warehouse=factory.open_profile):
    manifest = verified(directory, expected_id)
    fixture = manifest['fixture']
    warehouse = open_warehouse(profile, manifest)
    expected = {IDENTITY: [[str(fixture['rows']), fixture['snapshot_sha256'], None]],
                RULES: [[str(fixture['bookings']), '0', None]], PARITY: manifest['expected_rows']}
    statement_ids = {}
    observed = {}
    for name, expected_rows in expected.items():
        rows, statement_ids[name] = warehouse.sql((directory / name).read_text(encoding='utf-8'))
        if not (same_rows(rows, expected_rows) if name == PARITY else rows == expected_rows):
            raise ValueError(f'Databricks SQL results differ for {name}')
        observed[name] = rows
    return {'status': 'SOURCE_AND_PARITY_VERIFIED', 'package_id': expected_id, 'workspace_id': manifest['workspace_id'],
            'warehouse_id': manifest['warehouse_id'], 'statement_ids': statement_ids,
            'columns': list(COLUMNS), 'observed_rows': observed[PARITY], 'expected_rows': manifest['expected_rows']}


def main():
    parser = argparse.ArgumentParser(description='Stage or check Databricks reporting acceptance')
    commands = parser.add_subparsers(dest='action', required=True)
    staging = commands.add_parser('stage')
    staging.add_argument('directory', type=Path)
    for option in ('workspace', 'warehouse', 'catalog', 'schema', 'publisher', 'oil-group', 'gas-group'):
        staging.add_argument('--' + option, required=True)
    for action in ('verify', 'preview'):
        command = commands.add_parser(action)
        command.add_argument('directory', type=Path)
        command.add_argument('--expected-package-id', required=True)
        command.add_argument('--profile', required=True)
        command.add_argument('--receipt', required=True, type=Path)
        if action == 'verify':
            command.add_argument('--rows', type=Path)
    args = parser.parse_args()
    if args.action == 'stage':
        value = stage(args.directory, args.workspace, args.warehouse, args.catalog, args.schema, args.publisher,
                      args.oil_group, args.gas_group)
    else:
        check = verify_fixture if args.action == 'verify' else preview
        value = check(args.directory, args.expected_package_id, args.profile)
        with args.receipt.open('xb') as output:
            output.write(canonical(value) + b'\n')
        if args.action == 'verify' and args.rows:
            with args.rows.open('xb') as output:
                output.write(canonical(value['observed_rows']) + b'\n')
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
