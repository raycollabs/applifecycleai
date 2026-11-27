import argparse
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZipFile, ZipInfo


VERSION = '1.0.0'
REVISION = 'energy-futures-close-v1'
SCOPES = ('oil', 'gas')
DATASET = 'energy_futures_close'
MEASURES = ('opening_units', 'closing_units', 'opening_pnl', 'fill_pnl', 'fees', 'net_pnl', 'settlement_cash')
SUMMARY_COLUMNS = ('business_date', 'book', 'currency', 'status', 'event_cutoff', 'known_at')
DETAIL_COLUMNS = (*SUMMARY_COLUMNS, 'contract', 'revision', *MEASURES)
COLUMN_TYPES = {'business_date': 'DATE', 'event_cutoff': 'DATE', 'known_at': 'TIMESTAMP WITH TIME ZONE',
                'revision': 'INTEGER', **dict.fromkeys(MEASURES, 'NUMERIC')}


def identifier(scope, asset):
    return str(uuid5(NAMESPACE_URL, f'https://durai.local/{REVISION}/{scope}/{asset}'))


def native_filter(scope, column, label):
    return {'id': f'NATIVE_FILTER-{column}', 'name': label, 'filterType': 'filter_select',
            'type': 'NATIVE_FILTER', 'controlValues': {'multiSelect': True, 'enableEmptyFilter': False},
            'targets': [{'datasetUuid': identifier(scope, 'dataset'), 'column': {'name': column}}],
            'defaultDataMask': {'extraFormData': {}, 'filterState': {}, 'ownState': {}},
            'cascadeParentIds': [], 'scope': {'rootPath': ['ROOT_ID'], 'excluded': []}}


def chart(scope, kind, index):
    name = 'Close summary' if kind == 'summary' else 'Contract detail'
    group = kind == 'summary'
    params = {'viz_type': 'table', 'query_mode': 'aggregate' if group else 'raw',
              'row_limit': 1000, 'time_range': 'No filter', 'granularity_sqla': 'business_date',
              'groupby': list(SUMMARY_COLUMNS) if group else [],
              'all_columns': [] if group else list(DETAIL_COLUMNS), 'metrics': list(MEASURES) if group else [],
              'adhoc_filters': [], 'server_pagination': False}
    config = {'version': VERSION, 'uuid': identifier(scope, kind), 'slice_name': f'{scope.upper()} {name}',
              'dataset_uuid': identifier(scope, 'dataset'), 'viz_type': 'table', 'cache_timeout': -1,
              'params': params}
    node = {'id': f'CHART-{kind}', 'type': 'CHART', 'children': [],
            'parents': ['ROOT_ID', 'GRID_ID', 'ROW-close'],
            'meta': {'chartId': index, 'uuid': config['uuid'], 'width': 6, 'height': 50,
                     'sliceName': config['slice_name']}}
    return config, node


def assets(passwords=None):
    # Sample: Build the export twice. Check the same four chart IDs and two dashboards.
    files = {'metadata.yaml': {'version': VERSION, 'type': 'Dashboard'}}
    for scope in SCOPES:
        database = {'version': VERSION, 'uuid': identifier(scope, 'database'),
                    'database_name': f'Durai PostgreSQL {scope}',
                    'sqlalchemy_uri': f'postgresql+psycopg2://report_{scope}@reporting:5432/reporting',
                    'expose_in_sqllab': False, 'allow_run_async': False, 'allow_dml': False,
                    'allow_ctas': False, 'allow_cvas': False, 'allow_csv_upload': False, 'cache_timeout': -1,
                    'extra': {}}
        if passwords is not None:
            database['password'] = passwords[scope]
            database['sqlalchemy_uri'] = database['sqlalchemy_uri'].replace('@', ':XXXXXXXXXX@', 1)
        files[f'databases/{scope}.yaml'] = database
        files[f'datasets/{scope}/{DATASET}.yaml'] = {
            'version': VERSION, 'uuid': identifier(scope, 'dataset'), 'table_name': DATASET, 'schema': scope,
            'database_uuid': database['uuid'], 'main_dttm_col': 'business_date', 'cache_timeout': -1,
            'columns': [{'column_name': column, 'type': COLUMN_TYPES.get(column, 'TEXT'),
                         'groupby': True, 'filterable': True,
                         'is_dttm': column in {'business_date', 'event_cutoff', 'known_at'}}
                        for column in DETAIL_COLUMNS],
            'metrics': [{'metric_name': measure, 'expression': f'SUM({measure})'} for measure in MEASURES],
        }
        position = {'DASHBOARD_VERSION_KEY': 'v2',
                    'HEADER_ID': {'id': 'HEADER_ID', 'type': 'HEADER', 'meta': {'text': f'{scope.upper()} Energy futures close'}},
                    'ROOT_ID': {'id': 'ROOT_ID', 'type': 'ROOT', 'children': ['GRID_ID']},
                    'GRID_ID': {'id': 'GRID_ID', 'type': 'GRID', 'children': ['ROW-close'], 'parents': ['ROOT_ID']},
                    'ROW-close': {'id': 'ROW-close', 'type': 'ROW', 'children': ['CHART-summary', 'CHART-detail'],
                                  'parents': ['ROOT_ID', 'GRID_ID'], 'meta': {'background': 'BACKGROUND_TRANSPARENT'}}}
        for index, kind in enumerate(('summary', 'detail'), start=1):
            config, node = chart(scope, kind, index)
            files[f'charts/{scope}_{kind}.yaml'] = config
            position[node['id']] = node
        files[f'dashboards/{scope}.yaml'] = {'version': VERSION, 'uuid': identifier(scope, 'dashboard'),
                                              'dashboard_title': f'{scope.upper()} Energy futures close',
                                              'slug': f'{REVISION}-{scope}', 'published': True, 'position': position,
                                              'metadata': {'native_filter_configuration': [
                                                  native_filter(scope, 'business_date', 'Business date'),
                                                  native_filter(scope, 'book', 'Book')]}}
    return {name: json.dumps(data, sort_keys=True) for name, data in files.items()}


def main():
    parser = argparse.ArgumentParser(description='Export the scoped Energy Futures Close Superset dashboards')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with ZipFile(args.output, mode='x') as bundle:
        for name, content in assets().items():
            bundle.writestr(ZipInfo(f'{REVISION}/{name}'), content)


if __name__ == '__main__':
    main()
