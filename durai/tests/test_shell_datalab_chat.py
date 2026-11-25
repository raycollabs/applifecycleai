from io import StringIO
import json
import unittest

from databricks.datalab.chat_mcp import DATASET, TOOL, TradingDataTools
from durai.build_mcp import serve


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

    def test_changed_cloud_rows(self):
        # Sample: Change the last Databricks row. Check that no data goes to the trade demo.
        app = TestTradeDemo()
        outcome = TradingDataTools(TestWarehouse(change=True), app).call(TOOL, {'dataset': DATASET})
        self.assertTrue(outcome['isError'])
        self.assertIsNone(app.loaded)
