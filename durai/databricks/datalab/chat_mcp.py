import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from databricks.datalab.prepare_energy_futures import TABLES, json_bytes, prepare, warehouse_rows
from durai.build_mcp import serve
from durai.fixture_batch import FixtureBatch

DATASET = 'energy-futures-v1'
TOOL = 'prepare_trading_data'
WTI_TOOL = 'get_wti_spot_reference'
WTI_TABLE = 'rearc_crude_oil_prices_west_texas_intermediate_cushing_oklahoma_fred.fs_fred.fred_dcoilwtico'
WTI_SOURCE = 'https://fred.stlouisfed.org/series/DCOILWTICO'
WTI_FRESHNESS_DAYS = 7
SQL_API = '/api/2.0/sql/statements'


def run_command(args, *, payload=None, cwd=None):
    # Sample: Run a Databricks command. Read the JSON result on stdout.
    result = subprocess.run(args, input=payload, cwd=cwd, capture_output=True, encoding='utf-8', timeout=60)
    if result.returncode:
        raise RuntimeError(f'{args[0]} command failed with exit {result.returncode}')
    return result.stdout


class Warehouse:
    def __init__(self, profile, warehouse_id, catalog):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', catalog):
            raise ValueError('Set a valid destination catalog')
        self.cli = ['databricks', '--profile', profile, '--output', 'json', 'api']
        self.warehouse_id = warehouse_id
        self.schema = f'`{catalog}`.`durai_synthetic`'

    def sql(self, statement, parameters=()):
        # Sample: Execute SELECT :value with value 7. Read 7 from the SQL result.
        request = {'warehouse_id': self.warehouse_id, 'statement': statement, 'wait_timeout': '20s',
                   'on_wait_timeout': 'CONTINUE',
                   'parameters': [{'name': name, 'value': value} for name, value in parameters]}
        with TemporaryDirectory(prefix='durai-synthetic-sql-') as folder:
            path = Path(folder) / 'statement.json'
            path.write_bytes(json_bytes(request))
            value = json.loads(run_command([*self.cli, 'post', SQL_API, '--json', '@' + str(path)]))
        deadline = time.monotonic() + 180
        while value['status']['state'] in {'PENDING', 'RUNNING'}:
            if time.monotonic() >= deadline:
                raise RuntimeError('Databricks SQL is still running. Retry the same dataset.')
            time.sleep(3)
            value = json.loads(run_command([*self.cli, 'get', SQL_API + '/' + value['statement_id']]))
        if value['status']['state'] != 'SUCCEEDED':
            error = value['status'].get('error', {}).get('error_code', value['status']['state'])
            raise RuntimeError(f"Databricks statement {value['statement_id']} failed: {error}")
        if value.get('manifest', {}).get('truncated') or value.get('result', {}).get('next_chunk_index') is not None:
            raise RuntimeError('Databricks returned an incomplete result')
        return value.get('result', {}).get('data_array', []), value['statement_id']

    def publish(self, digest, rows):
        # Sample: Publish three rows. Read the same three rows in order.
        table = self.schema + '.`energy_futures_' + digest[:16] + '`'
        _, create_schema = self.sql('CREATE SCHEMA IF NOT EXISTS ' + self.schema)
        values = ', '.join(f"({index}, '{kind}', :row{index})" for index, (kind, _) in enumerate(rows))
        _, create_table = self.sql(
            f'CREATE TABLE IF NOT EXISTS {table} USING DELTA AS '
            f'SELECT ordinal, entity, payload FROM VALUES {values} AS seed(ordinal, entity, payload)',
            [(f'row{index}', payload) for index, (_, payload) in enumerate(rows)],
        )
        records, select = self.sql(f'SELECT entity, payload FROM {table} ORDER BY ordinal')
        return table, records, [create_schema, create_table, select]


class TradeDemo:
    def __init__(self, project):
        if not re.fullmatch(r'durai-app-[a-z0-9-]+', project):
            raise ValueError('Set a Docker trading demo project')
        self.project = project

    def load(self, data):
        # Sample: Pass a verified batch to the trade demo. Read the created row counts.
        container = self.project + '-app'
        owner = run_command(['docker', 'inspect', '--format',
                             '{{index .Config.Labels "com.durai.owner"}}', container]).strip()
        if owner != self.project:
            raise ValueError('Trade demo container owner differs from the configured project')
        loader = Path(__file__).with_name('load_trade_demo.py').read_text(encoding='utf-8')
        response = run_command(['docker', 'exec', '-i', container, 'python', '-c', loader],
                               payload=json.dumps(data))
        return json.loads(response)


class TradingDataTools:
    def __init__(self, warehouse, trade_demo):
        self.warehouse = warehouse
        self.trade_demo = trade_demo

    def tool_list(self):
        # Sample: List tools. Read prepare_trading_data and get_wti_spot_reference.
        return [{'name': TOOL, 'description': 'Prepare synthetic energy futures in Databricks and load the trading demo.',
                 'inputSchema': {'type': 'object', 'properties': {'dataset': {'type': 'string', 'enum': [DATASET]}},
                                 'required': ['dataset'], 'additionalProperties': False}},
                {'name': WTI_TOOL, 'description': 'Check the date range and freshness of the installed FRED WTI spot benchmark.',
                 'annotations': {'readOnlyHint': True, 'openWorldHint': True},
                 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]

    def call(self, name, arguments):
        # Sample: Prepare energy-futures-v1. Read the table name and created row counts.
        if name == WTI_TOOL and arguments not in (None, {}):
            return {'isError': True, 'text': 'The WTI spot reference takes no arguments.'}
        if name != WTI_TOOL and (name != TOOL or arguments != {'dataset': DATASET}):
            return {'isError': True, 'text': 'Choose the energy-futures-v1 dataset.'}
        try:
            result = self.wti_reference() if name == WTI_TOOL else self.prepare_batch()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return {'isError': True, 'text': str(error)}
        return {'isError': False, 'text': json.dumps(result)}

    def wti_reference(self, as_of=None):
        # Sample: Check the series on June 10. Its last price is June 2. Report stale.
        checked_on = as_of if as_of is not None else datetime.now(timezone.utc).date()
        rows, statement_id = self.warehouse.sql(f"""
            WITH typed AS (
                SELECT try_cast(DATE AS DATE) AS observed_on,
                       try_cast(DCOILWTICO AS DECIMAL(18, 4)) AS price
                FROM {WTI_TABLE}
            )
            SELECT COUNT(*), COUNT_IF(observed_on IS NOT NULL AND price IS NOT NULL),
                   MIN(CASE WHEN price IS NOT NULL THEN observed_on END),
                   MAX(CASE WHEN price IS NOT NULL THEN observed_on END),
                   MAX_BY(price, CASE WHEN price IS NOT NULL THEN observed_on END),
                   COUNT_IF(observed_on IS NOT NULL AND price < 0)
            FROM typed
        """)
        total, valid, earliest, latest, latest_price, negative = rows[0]
        age = (checked_on - date.fromisoformat(latest)).days if latest is not None else None
        if age is None:
            freshness = 'no_data'
        elif age < 0:
            freshness = 'future_date'
        else:
            freshness = 'stale' if age > WTI_FRESHNESS_DAYS else 'recent'
        return {'series': 'DCOILWTICO', 'market': 'WTI Cushing spot', 'unit': 'USD per barrel',
                'source': WTI_SOURCE, 'table': WTI_TABLE, 'statement_id': statement_id,
                'row_count': int(total), 'valid_observations': int(valid),
                'invalid_or_missing_observations': int(total) - int(valid), 'negative_observations': int(negative),
                'earliest_observation': earliest, 'latest_observation': latest, 'latest_price': latest_price,
                'checked_on_utc': checked_on.isoformat(), 'age_days': age, 'freshness_limit_days': WTI_FRESHNESS_DAYS,
                'freshness': freshness}

    def prepare_batch(self):
        # Sample: Read back one changed price from Databricks. Refuse the trade demo load.
        with TemporaryDirectory(prefix='durai-synthetic-seed-') as folder:
            source = Path(folder) / 'input'
            receipt = prepare(source)
            rows = warehouse_rows(source)
        digest = sha256(json_bytes(rows)).hexdigest()
        table, records, statements = self.warehouse.publish(digest, rows)
        if records != [list(row) for row in rows]:
            raise ValueError('Databricks data differs from the synthetic seed')
        data = {name: [] for name in TABLES}
        for name, payload in records:
            if name in data:
                data[name].append(json.loads(payload))
        batch = FixtureBatch(self.trade_demo.project, DATASET, data)
        loaded = batch.load(self.trade_demo.project, DATASET, lambda payload, _: self.trade_demo.load(payload))
        return {'dataset': DATASET, 'table': table, 'batch_sha256': digest,
                'source_sha256': receipt['source_sha256'], 'databricks_statement_ids': statements,
                'row_counts': {key: value['rows'] for key, value in receipt['files'].items()},
                'trade_demo_project': self.trade_demo.project, 'fixture_sha256': loaded['fixture_sha256'],
                'created': loaded['load']}


def main():
    # Sample: Start the stdio server and send a prepare_trading_data request.
    parser = argparse.ArgumentParser(description='Durai trading data MCP server')
    parser.add_argument('--profile', required=True)
    parser.add_argument('--warehouse', required=True)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--project', required=True)
    args = parser.parse_args()
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    serve(TradingDataTools(Warehouse(args.profile, args.warehouse, args.catalog), TradeDemo(args.project)))


if __name__ == '__main__':
    main()
