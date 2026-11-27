import argparse
import json
from pathlib import Path, PurePosixPath
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZipFile, ZipInfo

from durai.reporting.contract import MEASURES

SCOPES = ('oil', 'gas')
COLUMN_TYPES = {
    'business_date': 'Date32',
    'event_cutoff': 'Date32',
    'known_at': 'String',
    'revision': 'UInt32',
    **dict.fromkeys(MEASURES, 'Decimal(38, 12)'),
    'settlement_cash': 'Nullable(Decimal(38, 12))',
}


def remap(value, identifiers):
    if isinstance(value, str):
        return identifiers.get(value, value)
    if isinstance(value, dict):
        return {key: remap(item, identifiers) for key, item in value.items()}
    if isinstance(value, list):
        return [remap(item, identifiers) for item in value]
    return value


def rebind(source, output):
    # Sample: Read the oil and gas dashboard bundle. Check two ClickHouse connections and the same metrics.
    with ZipFile(source) as archive:
        assets = {
            name: json.loads(archive.read(name))
            for name in archive.namelist() if not name.endswith('/')
        }
    identifiers = {
        asset['uuid']: str(uuid5(NAMESPACE_URL, f'https://durai.local/clickhouse/{asset["uuid"]}'))
        for asset in assets.values() if 'uuid' in asset
    }
    seen = {'databases': set(), 'datasets': set()}
    rebound = {}
    for name, original in assets.items():
        asset = remap(original, identifiers)
        path = PurePosixPath(name)
        kind = path.parts[1]
        if kind == 'databases':
            scope = path.stem
            if scope not in SCOPES:
                raise ValueError('Use oil and gas database assets')
            seen[kind].add(scope)
            asset['database_name'] = f'Durai ClickHouse {scope}'
            asset['sqlalchemy_uri'] = f'clickhousedb://{scope}_conn@clickhouse:8123/{scope}'
        if kind == 'datasets':
            scope = asset['schema']
            if scope not in SCOPES or asset['table_name'] != 'energy_futures_close':
                raise ValueError('Use the scoped Energy Futures Close dataset')
            seen[kind].add(scope)
            for column in asset['columns']:
                column['type'] = COLUMN_TYPES.get(column['column_name'], 'String')
                if column['column_name'] == 'known_at':
                    column['is_dttm'] = False
        if kind == 'dashboards':
            asset['slug'] += '-clickhouse'
        rebound[name] = asset

    if any(scopes != set(SCOPES) for scopes in seen.values()):
        raise ValueError('Supply both desk databases and datasets')
    with ZipFile(output, mode='x') as archive:
        for name, asset in sorted(rebound.items()):
            archive.writestr(ZipInfo(name), json.dumps(asset, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    rebind(args.source, args.output)
