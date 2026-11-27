import csv
import io
import json
import os
from pathlib import Path
import subprocess
import unittest

import psycopg
import requests

from deploy.reporting.postgres.dashboard_bundle import MEASURES, REVISION, SUMMARY_COLUMNS
from deploy.reporting.postgres.publish import VISIBLE_COLUMNS, publish


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'design/reporting/energy_futures_close.json'
ENV = os.environ.get('DURAI_PG_PROOF')
RUN = os.environ.get('DURAI_SUPERSET_PROOF')
REVOKE = os.environ.get('DURAI_SUPERSET_REVOKE')


@unittest.skipUnless(ENV and RUN, 'Set DURAI_PG_PROOF and DURAI_SUPERSET_PROOF for the local stack')
class PostgresSupersetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.secrets = dict(line.split('=', 1) for line in Path(ENV).read_text().splitlines() if line)
        cls.url = f"http://127.0.0.1:{cls.secrets.get('SUPERSET_PORT', 55452)}"
        with psycopg.connect(host='127.0.0.1', port=cls.secrets.get('REPORTING_PORT', 55451),
                             dbname='reporting', user='report_publisher', autocommit=True,
                             password=cls.secrets['REPORTING_PUBLISHER_PASSWORD']) as connection:
            for report in json.loads(FIXTURE.read_text(encoding='utf-8'))['reports']:
                publish(connection, report)

    def session(self, username):
        browser = requests.Session()
        browser.trust_env = False
        password_name = 'SUPERSET_ADMIN_PASSWORD' if username == 'admin' else 'SUPERSET_TEST_PASSWORD'
        login = browser.post(f'{self.url}/api/v1/security/login', timeout=15, json={
            'username': username, 'password': self.secrets[password_name], 'provider': 'db', 'refresh': False,
        })
        login.raise_for_status()
        browser.headers['Authorization'] = f"Bearer {login.json()['access_token']}"
        token = browser.get(f'{self.url}/api/v1/security/csrf_token/', timeout=15)
        token.raise_for_status()
        browser.headers['X-CSRFToken'] = token.json()['result']
        self.addCleanup(browser.close)
        return browser

    def api(self, browser, method, path, **kwargs):
        return browser.request(method, f'{self.url}/api/v1/{path}', timeout=15, **kwargs)

    def result(self, response):
        self.assertTrue(response.ok, f'HTTP {response.status_code}: {response.text[:700]}')
        return response.json()['result']

    def scope(self, browser, scope, chart_name='Contract detail'):
        result = self.result(self.api(browser, 'GET', f'dashboard/{REVISION}-{scope}'))
        dashboard_id = result['id']
        charts = self.result(self.api(browser, 'GET', f'dashboard/{dashboard_id}/charts'))
        detail = next(chart for chart in charts if chart_name in chart['slice_name'])
        return dashboard_id, detail

    def chart_data(self, browser, dashboard_id, chart, result_format, **options):
        datasource_id = int(chart['form_data']['datasource'].split('__')[0])
        query = {'datasource': {'id': datasource_id, 'type': 'table'}, 'force': False,
                 'queries': [{'columns': list(VISIBLE_COLUMNS), 'row_limit': 20, **options}],
                 'form_data': {'slice_id': chart['id'], 'dashboardId': dashboard_id},
                 'result_format': result_format, 'result_type': 'full'}
        return self.api(browser, 'POST', 'chart/data', json=query)

    def test_summary_uses_date_and_book_filters(self):
        # Sample: Select SYN-OIL on 2026-11-03. Check USD 320.00. Select SYN-GAS. Check zero rows.
        reader = self.session('oil-reader')
        dashboard_id, chart = self.scope(reader, 'oil', 'Close summary')
        filters = [{'col': 'business_date', 'op': 'IN', 'val': ['2026-11-03']},
                   {'col': 'book', 'op': 'IN', 'val': ['SYN-OIL']}]
        for book, pnl in (('SYN-OIL', [320]), ('SYN-GAS', [])):
            filters[1]['val'] = [book]
            response = self.chart_data(reader, dashboard_id, chart, 'json', columns=list(SUMMARY_COLUMNS),
                                       metrics=list(MEASURES), filters=filters)
            rows = self.result(response)[0]['data']
            self.assertEqual([row['net_pnl'] for row in rows], pnl)

    def test_desk_export_and_cache(self):
        # Sample: Export oil twice. Check one oil row, and refuse it for gas or no desk.
        admin = self.session('admin')
        denied = self.session('unscoped')
        for scope, revision, pnl in (('oil', 2, 320), ('gas', 1, 300)):
            dashboard_id, chart = self.scope(admin, scope)
            other = self.session('gas-reader' if scope == 'oil' else 'oil-reader')
            for level in ('reader', 'author'):
                current = self.session(f'{scope}-{level}')
                self.assertEqual(self.scope(current, scope)[0], dashboard_id)
                for _ in range(2):
                    query = self.result(self.chart_data(current, dashboard_id, chart, 'json'))[0]
                    self.assertFalse(query['is_cached'])
                    self.assertEqual([(row['book'], row['revision'], row['net_pnl']) for row in query['data']],
                                     [(f'SYN-{scope.upper()}', revision, pnl)])
                export = self.chart_data(current, dashboard_id, chart, 'csv')
                self.assertTrue(export.ok, f'HTTP {export.status_code}: {export.text[:500]}')
                data = list(csv.DictReader(io.StringIO(export.text)))
                self.assertEqual([(row['book'], int(row['revision']), float(row['net_pnl'])) for row in data],
                                 [(f'SYN-{scope.upper()}', revision, pnl)])
            for browser in (other, denied):
                self.assertIn(self.api(browser, 'GET', f'dashboard/{dashboard_id}').status_code, (403, 404))
                for result_format in ('json', 'csv'):
                    self.assertIn(self.chart_data(browser, dashboard_id, chart, result_format).status_code, (403, 404))

    def test_desk_author_creates_scoped_chart(self):
        # Sample: Save an oil chart as oil-author. Refuse the gas source and reader changes.
        admin = self.session('admin')
        oil_author, oil_reader = self.session('oil-author'), self.session('oil-reader')
        _, oil = self.scope(admin, 'oil')
        _, gas = self.scope(admin, 'gas')
        payload = {'slice_name': 'Oil close proof chart', 'viz_type': 'table', 'datasource_type': 'table',
                   'datasource_id': int(oil['form_data']['datasource'].split('__')[0])}
        self.assertEqual(self.api(oil_reader, 'POST', 'chart/', json=payload).status_code, 403)
        response = self.api(oil_author, 'POST', 'chart/', json=payload)
        self.assertEqual(response.status_code, 201, response.text[:500])
        chart_id = response.json()['id']
        try:
            rename = self.api(oil_author, 'PUT', f'chart/{chart_id}', json={'slice_name': 'New oil proof chart'})
            self.assertEqual(rename.status_code, 200, rename.text[:500])
            payload['datasource_id'] = int(gas['form_data']['datasource'].split('__')[0])
            response = self.api(oil_author, 'POST', 'chart/', json=payload)
            if response.status_code == 201:
                self.api(admin, 'DELETE', f"chart/{response.json()['id']}")
            self.assertEqual(response.status_code, 403, response.text[:500])
            response = self.api(oil_author, 'PUT', f'chart/{chart_id}', json={'datasource_id': payload['datasource_id']})
            self.assertEqual(response.status_code, 403, response.text[:500])
        finally:
            self.api(admin, 'DELETE', f'chart/{chart_id}').raise_for_status()

    @unittest.skipUnless(REVOKE, 'Set DURAI_SUPERSET_REVOKE to run the local role-removal proof through Docker')
    def test_role_removal_denies_existing_session(self):
        # Sample: Export oil once. Remove the oil role. Refuse chart reads from the same session.
        def set_role(role):
            subprocess.run(['docker', 'compose', '-f', 'deploy/reporting/postgres/compose.yaml', 'exec', '-T',
                            'superset', 'python', '/app/durai-reporting/configure_superset.py',
                            '--oil-reader-role', role], cwd=ROOT, check=True, capture_output=True, timeout=60)

        reader = self.session('oil-reader')
        dashboard_id, chart = self.scope(reader, 'oil')
        self.assertTrue(self.chart_data(reader, dashboard_id, chart, 'csv').ok)
        self.addCleanup(set_role, 'oil-reader')
        set_role('unscoped')
        self.assertIn(self.api(reader, 'GET', f'dashboard/{dashboard_id}').status_code, (403, 404))
        for result_format in ('json', 'csv'):
            self.assertIn(self.chart_data(reader, dashboard_id, chart, result_format).status_code, (403, 404))


if __name__ == '__main__':
    unittest.main()
