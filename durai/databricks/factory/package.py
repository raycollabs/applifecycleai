import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from databricks.datalab.chat_mcp import Warehouse, run_command
from databricks.datalab.prepare_energy_futures import json_bytes, prepare, warehouse_rows


FACTORY = Path(__file__).resolve().parent
FORMAT = 'durai-databricks-acceptance/v1'
IDENTITY = 'sql/fixture_identity.sql'
RULES = 'sql/fixture_business_rules.sql'
ARTIFACTS = ('databricks.yml', IDENTITY, RULES)


def digest(raw):
    return sha256(raw).hexdigest()


def stage(destination, workspace, warehouse, catalog, fixture_version=0):
    # Sample: Stage one package twice for warehouse a1 and fixture version 0. Check equal IDs.
    if not re.fullmatch(r'[0-9]+', workspace) or not re.fullmatch(r'[a-f0-9]{16}', warehouse):
        raise ValueError('Supply a workspace ID and a SQL warehouse ID')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', catalog):
        raise ValueError('Supply a catalog identifier')
    if type(fixture_version) is not int or fixture_version < 0:
        raise ValueError('Use a nonnegative Delta table version')
    with TemporaryDirectory(prefix='durai-factory-fixture-') as folder:
        source = Path(folder) / 'fixture'
        seed = prepare(source)
        rows = warehouse_rows(source)
    batch = digest(json_bytes(rows))
    snapshot = digest('\n'.join(digest(f'{ordinal}:{kind}:{payload}'.encode('utf-8'))
                                for ordinal, (kind, payload) in enumerate(rows)).encode('utf-8'))
    table = f'{catalog}.durai_synthetic.energy_futures_{batch[:16]}'
    bookings = seed['files']['bookings.jsonl']['rows']
    params = {'__FIXTURE_TABLE__': table, '__FIXTURE_VERSION__': str(fixture_version),
              '__EXPECTED_ROWS__': str(len(rows)), '__EXPECTED_DIGEST__': snapshot,
              '__EXPECTED_BOOKINGS__': str(bookings)}
    prepared = {'databricks.yml': (FACTORY / 'bundle.yml').read_text(encoding='utf-8').replace(
        '__WAREHOUSE_ID__', warehouse).encode('utf-8')}
    for name in (IDENTITY, RULES):
        statement = (FACTORY / name).read_text(encoding='utf-8')
        for key, value in params.items():
            statement = statement.replace(key, value)
        prepared[name] = statement.encode('utf-8')

    manifest = {'format': FORMAT, 'application': 'energy-futures', 'workspace_id': workspace,
                'warehouse_id': warehouse, 'catalog': catalog,
                'fixture': {'table': table, 'version': fixture_version, 'batch_sha256': batch,
                            'snapshot_sha256': snapshot, 'source_sha256': seed['source_sha256'],
                            'rows': len(rows), 'bookings': bookings},
                'artifacts': {name: digest(prepared[name]) for name in ARTIFACTS}}
    manifest['package_id'] = digest(json_bytes(manifest))
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'sql').mkdir()
    for name, contents in prepared.items():
        (destination / name).write_bytes(contents)
    (destination / 'manifest.json').write_bytes(json_bytes(manifest))
    return manifest


def verify_artifacts(directory, expected_id):
    # Sample: Change the staged fixture SQL. Refuse the package.
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    package_id = manifest.pop('package_id')
    if package_id != expected_id or package_id != digest(json_bytes(manifest)) or manifest['format'] != FORMAT:
        raise ValueError('Package ID differs from the expected release')
    if set(manifest['artifacts']) != set(ARTIFACTS):
        raise ValueError('Package artifact list differs')
    statements = {}
    for name in ARTIFACTS:
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Package artifact is missing: {name}')
        body = path.read_bytes()
        if digest(body) != manifest['artifacts'][name]:
            raise ValueError(f'Package artifact differs: {name}')
        if name != 'databricks.yml':
            statements[name] = body.decode('utf-8')
    return manifest | {'package_id': package_id}, statements


def verify_fixture(directory, expected_id, open_warehouse):
    # Sample: Check 16 fixture rows and four bookings. Return two statement IDs.
    manifest, statements = verify_artifacts(directory, expected_id)
    warehouse = open_warehouse(manifest)
    fixture = manifest['fixture']
    rows, identity_stmt = warehouse.sql(statements[IDENTITY])
    if rows != [[str(fixture['rows']), fixture['snapshot_sha256'], None]]:
        raise ValueError('Fixture identity check differs from the package')
    rows, business_stmt = warehouse.sql(statements[RULES])
    if rows != [[str(fixture['bookings']), '0', None]]:
        raise ValueError('Fixture business check differs from the package')
    return {'status': 'FIXTURE_VERIFIED', 'package_id': manifest['package_id'],
            'workspace_id': manifest['workspace_id'], 'warehouse_id': manifest['warehouse_id'],
            'fixture': fixture, 'statement_ids': {'identity': identity_stmt, 'business_rules': business_stmt}}


def open_profile(profile, manifest):
    # Sample: Open a profile for a different workspace. Refuse before SQL runs.
    details = json.loads(run_command(['databricks', 'auth', 'describe', '--profile', profile, '--output', 'json']))
    current_id = details.get('details', {}).get('configuration', {}).get('workspace_id', {}).get('value')
    if str(current_id) != manifest['workspace_id']:
        raise ValueError('Databricks profile points to a different workspace or has no workspace ID')
    return Warehouse(profile, manifest['warehouse_id'], manifest['catalog'])


def preview(directory, expected_id, profile):
    # Sample: Preview a new package. Record one action to create the acceptance job.
    manifest, _ = verify_artifacts(directory, expected_id)
    open_profile(profile, manifest)
    cli = ['databricks', 'bundle']
    flags = ['--profile', profile, '--output', 'json']
    run_command([*cli, 'validate', '--strict', *flags], cwd=directory)
    result = json.loads(run_command([*cli, 'plan', *flags], cwd=directory))
    actions = []
    for resource, entry in sorted(result['plan'].items()):
        action = entry['action']
        if resource != 'resources.jobs.acceptance' or action not in {'create', 'update', 'no-op'}:
            raise ValueError(f'Unexpected bundle action: {action} {resource}')
        actions.append({'resource': resource, 'action': action})
    return {'status': 'BUNDLE_PREVIEWED', 'package_id': expected_id, 'workspace_id': manifest['workspace_id'],
            'cli_version': result['cli_version'], 'actions': actions}


def main():
    # Sample: Stage a package. Verify its ID and save a fixture receipt.
    parser = argparse.ArgumentParser(description='Stage or verify a Databricks acceptance package')
    commands = parser.add_subparsers(dest='action', required=True)
    staging = commands.add_parser('stage')
    staging.add_argument('directory', type=Path)
    staging.add_argument('--workspace', required=True)
    staging.add_argument('--warehouse', required=True)
    staging.add_argument('--catalog', required=True)
    staging.add_argument('--fixture-version', type=int, default=0)
    for action in ('verify', 'preview'):
        command = commands.add_parser(action)
        command.add_argument('directory', type=Path)
        command.add_argument('--expected-package-id', required=True)
        command.add_argument('--profile', required=True)
        command.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'stage':
        value = stage(args.directory, args.workspace, args.warehouse, args.catalog, args.fixture_version)
    else:
        if args.action == 'verify':
            value = verify_fixture(args.directory, args.expected_package_id, lambda manifest: open_profile(args.profile, manifest))
        else:
            value = preview(args.directory, args.expected_package_id, args.profile)
        with args.receipt.open('xb') as output:
            output.write(json_bytes(value))
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
