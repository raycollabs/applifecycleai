from io import StringIO
from datetime import date
import json
import unittest
from unittest.mock import Mock

from databricks.datalab.chat_mcp import DATASET, TOOL, WTI_TOOL, WTI_TABLE, TradingDataTools
from durai.build_mcp import serve
from durai.fixture_batch import FixtureBatch


class TestWarehouse:
    def __init__(self, change=False):
        self.change = change

    def publish(self, digest, rows):
        result = [list(row) for row in rows]
        if self.change:
            result[-1][1] = '{}'
        return 'workspace.durai_synthetic.energy_futures_' + digest[:16], result, ['statement']


class TestTradeDemo:
    project = 'durai-app-test'

    def __init__(self):
        self.loaded = None

    def load(self, data):
        self.loaded = data
        return {'bookings': len(data['bookings'])}


class ChatWorkflowTests(unittest.TestCase):
    def test_mcp_request(self):
        # Sample: Send one MCP request for energy-futures-v1. Check four bookings.
        app = TestTradeDemo()
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': TOOL, 'arguments': {'dataset': DATASET}}}
        output = StringIO()
        serve(TradingDataTools(TestWarehouse(), app), StringIO(json.dumps(request) + '\n'), output)
        reply = json.loads(output.getvalue())['result']
        self.assertFalse(reply['isError'])
        self.assertEqual(json.loads(reply['content'][0]['text'])['created'], {'bookings': 4})
        self.assertEqual(len(app.loaded['settlements']), 2)

    def test_fixture_refuses_the_wrong_target(self):
        # Sample: Send a seat fixture to the trade app. Refuse the load.
        loader = Mock()
        with self.assertRaisesRegex(ValueError, 'test target'):
            FixtureBatch('reservation-pilot', 'seat-reservation/v1', {}).load('trade-demo', 'energy-futures-v1', loader)
        loader.assert_not_called()

    def test_changed_cloud_rows(self):
        # Sample: Change the last Databricks row. Check that no data goes to the trade demo.
        app = TestTradeDemo()
        outcome = TradingDataTools(TestWarehouse(change=True), app).call(TOOL, {'dataset': DATASET})
        self.assertTrue(outcome['isError'])
        self.assertIsNone(app.loaded)

    def test_wti_reference_reports_stale_data(self):
        # Sample: On June 10, check the latest price from June 2. Report stale and eight days.
        warehouse = Mock()
        warehouse.sql.return_value = ([['4', '3', '2024-05-30', '2024-06-02', '73.2500', '1']], 'statement')
        app = TestTradeDemo()
        result = TradingDataTools(warehouse, app).wti_reference(date(2024, 6, 10))
        self.assertEqual((result['freshness'], result['age_days']), ('stale', 8))
        self.assertEqual(result['latest_price'], '73.2500')
        self.assertEqual(result['invalid_or_missing_observations'], 1)
        self.assertEqual(result['negative_observations'], 1)
        self.assertIn(WTI_TABLE, warehouse.sql.call_args.args[0])
        self.assertIsNone(app.loaded)

    def test_wti_reference_reports_missing_data(self):
        # Sample: Read three rows with no valid price. Report no data and no age.
        warehouse = Mock()
        warehouse.sql.return_value = ([['3', '0', None, None, None, '0']], 'statement')
        result = TradingDataTools(warehouse, TestTradeDemo()).call(WTI_TOOL, {})
        self.assertFalse(result['isError'])
        summary = json.loads(result['text'])
        self.assertEqual(summary['freshness'], 'no_data')
        self.assertIsNone(summary['age_days'])

    def test_wti_reference_rejects_arguments(self):
        # Sample: Pass a table name to the WTI tool. Refuse the request before SQL runs.
        warehouse = Mock()
        result = TradingDataTools(warehouse, TestTradeDemo()).call(WTI_TOOL, {'table': 'other'})
        self.assertTrue(result['isError'])
        warehouse.sql.assert_not_called()
