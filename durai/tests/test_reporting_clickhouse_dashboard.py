import csv
from decimal import Decimal
from io import StringIO
import os
import unittest

import requests

from durai.reporting.contract import MEASURES

URL = 'http://127.0.0.1:58061/api/v1'
COLUMNS = ('business_date', 'book', 'contract', 'currency', 'revision', *MEASURES)
DATE = '2026-11-03'


@unittest.skipUnless(os.getenv('DURAI_CH_DASHBOARD') == '1', 'Requires disposable Superset')
class ClickHouseDashboardTests(unittest.TestCase):
    def session(self, username):
        client = requests.Session()
        self.addCleanup(client.close)
        client.trust_env = False
        password_key = 'SUPERSET_ADMIN_PASSWORD' if username == 'admin' else 'SUPERSET_TEST_PASSWORD'
        login = self.api(client, 'POST', 'security/login', json={
            'username': username, 'password': os.environ[password_key], 'provider': 'db', 'refresh': False,
        })
        self.assertTrue(login.ok, f'Login returned HTTP {login.status_code}')
        client.headers['Authorization'] = f"Bearer {login.json()['access_token']}"
        client.headers['X-CSRFToken'] = self.result(self.api(client, 'GET', 'security/csrf_token/'))
        return client

    def api(self, client, method, path, **kwargs):
        return client.request(method, f'{URL}/{path}', timeout=15, **kwargs)

    def result(self, response):
        self.assertTrue(response.ok, f'HTTP {response.status_code}: {response.text[:600]}')
        return response.json()['result']

    def scope(self, client, desk, chart_name='Contract detail'):
        dashboard = self.result(self.api(client, 'GET', f'dashboard/energy-futures-close-v1-{desk}-clickhouse'))
        charts = self.result(self.api(client, 'GET', f"dashboard/{dashboard['id']}/charts"))
        chart = next(chart for chart in charts if chart_name in chart['slice_name'])
        return dashboard['id'], chart

    def chart_data(self, client, dashboard, chart, book, result_format, *, aggregate=False):
        datasource = int(chart['form_data']['datasource'].split('__')[0])
        return self.api(client, 'POST', 'chart/data', json={
            'datasource': {'id': datasource, 'type': 'table'},
            'force': False,
            'queries': [{
                'columns': ['book'] if aggregate else list(COLUMNS),
                'metrics': ['net_pnl'] if aggregate else [],
                'filters': [
                    {'col': 'business_date', 'op': '==', 'val': DATE},
                    {'col': 'book', 'op': '==', 'val': book},
                ],
                'row_limit': 20,
            }],
            'form_data': {'slice_id': chart['id'], 'dashboardId': dashboard},
            'result_format': result_format,
            'result_type': 'full',
        })

    def test_desk_filters_export_and_repeat(self):
        # Sample: Open and export oil twice. Check CLF27. Open gas. Check NGF27. Refuse cross-desk access.
        admin = self.session('admin')
        denied = self.session('unscoped')
        for desk, revision, pnl in (('oil', 2, 320), ('gas', 1, 300)):
            dashboard, chart = self.scope(admin, desk)
            book = f'SYN-{desk.upper()}'
            other_desk = 'gas' if desk == 'oil' else 'oil'
            other_book = f'SYN-{other_desk.upper()}'
            other = self.session(f'{other_desk}-reader')
            for level in ('reader', 'author'):
                client = self.session(f'{desk}-{level}')
                self.assertEqual(self.scope(client, desk)[0], dashboard)
                for _ in range(2):
                    result = self.result(self.chart_data(client, dashboard, chart, book, 'json'))[0]
                    self.assertFalse(result['is_cached'])
                    self.assertEqual(
                        [(row['book'], row['revision'], Decimal(str(row['net_pnl']))) for row in result['data']],
                        [(book, revision, Decimal(pnl))],
                    )
                _, summary = self.scope(client, desk, 'Close summary')
                total = self.result(self.chart_data(client, dashboard, summary, book, 'json', aggregate=True))[0]
                self.assertEqual(
                    [(row['book'], Decimal(str(row['net_pnl']))) for row in total['data']],
                    [(book, Decimal(pnl))],
                )
                cross_filter = self.result(self.chart_data(client, dashboard, chart, other_book, 'json'))[0]
                self.assertEqual(cross_filter['data'], [])
                csv_result = self.chart_data(client, dashboard, chart, book, 'csv')
                self.assertTrue(csv_result.ok, f'Export returned HTTP {csv_result.status_code}')
                self.assertEqual(
                    [(row['book'], int(row['revision']), Decimal(row['net_pnl']))
                     for row in csv.DictReader(StringIO(csv_result.text))],
                    [(book, revision, Decimal(pnl))],
                )
            for client in (other, denied):
                self.assertIn(self.api(client, 'GET', f'dashboard/{dashboard}').status_code, (403, 404))
                for form in ('json', 'csv'):
                    self.assertIn(self.chart_data(client, dashboard, chart, book, form).status_code, (403, 404))

    def test_author_creates_scoped_chart_reader_is_denied(self):
        # Sample: Save an oil chart as an oil author. Check that an oil reader cannot save it.
        admin = self.session('admin')
        _, oil = self.scope(admin, 'oil')
        _, gas = self.scope(admin, 'gas')
        author = self.session('oil-author')
        reader = self.session('oil-reader')
        chart = {
            'slice_name': 'Oil report author probe',
            'viz_type': 'table',
            'datasource_type': 'table',
            'datasource_id': int(oil['form_data']['datasource'].split('__')[0]),
        }
        self.assertIn(self.api(reader, 'POST', 'chart/', json=chart).status_code, (403, 404))
        response = self.api(author, 'POST', 'chart/', json=chart)
        self.assertEqual(response.status_code, 201, f'HTTP {response.status_code}: {response.text[:600]}')
        chart_id = response.json()['id']
        chart['datasource_id'] = int(gas['form_data']['datasource'].split('__')[0])
        self.assertIn(self.api(author, 'POST', 'chart/', json=chart).status_code, (403, 404))
        self.assertIn(self.api(author, 'PUT', f'chart/{chart_id}', json=chart).status_code, (403, 404))

    @unittest.skipUnless(os.getenv('DURAI_CH_ROLE_REMOVED') == '1', 'Requires a removed Superset role')
    def test_removed_desk_role_refuses_prior_chart_and_export(self):
        # Sample: Remove the oil desk role. Sign in as oil-reader. Check that chart data and CSV are refused.
        dashboard, chart = self.scope(self.session('admin'), 'oil')
        reader = self.session('oil-reader')
        self.assertIn(self.api(reader, 'GET', f'dashboard/{dashboard}').status_code, (403, 404))
        for form in ('json', 'csv'):
            self.assertIn(self.chart_data(reader, dashboard, chart, 'SYN-OIL', form).status_code, (403, 404))


if __name__ == '__main__':
    unittest.main()
