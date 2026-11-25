"""Sample: Start the MCP server on a temporary workspace. List tools and write docs/a.md."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class McpServerTests(unittest.TestCase):
    def test_tools_list_and_call(self):
        # Sample: Start the server. Send initialize, tools/list, tools/call write_file docs/a.md. Check 11 tools and the file. Start with --enhance on. Check 12 tools.
        with tempfile.TemporaryDirectory() as folder:
            lines = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'get_sample', 'arguments': {'sampleId': 'S-TRADE-BOOK'}}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'write_file', 'arguments': {'path': 'docs/a.md', 'content': 'hello\n'}}},
                {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'write_file', 'arguments': {'path': '../x.md', 'content': 'no\n'}}},
                {'jsonrpc': '2.0', 'id': 6, 'method': 'nothing/here'},
            ]
            done = subprocess.run([sys.executable, str(ROOT / 'tools/durai_build_mcp.py'), '--workspace', folder],
                                  input=''.join(json.dumps(line) + '\n' for line in lines), capture_output=True, text=True,
                                  timeout=60)
            self.assertEqual(0, done.returncode, done.stderr)
            replies = {item['id']: item for item in map(json.loads, done.stdout.splitlines())}
            self.assertEqual({1, 2, 3, 4, 5, 6}, set(replies))
            self.assertEqual('2.0', replies[1]['jsonrpc'])
            self.assertEqual(11, len(replies[2]['result']['tools']))
            on = subprocess.run([sys.executable, str(ROOT / 'tools/durai_build_mcp.py'), '--workspace', folder, '--enhance', 'on'],
                                input=json.dumps(lines[2]) + '\n', capture_output=True, text=True, timeout=60)
            names = [item['name'] for item in json.loads(on.stdout)['result']['tools']]
            self.assertEqual(12, len(names))
            self.assertIn('get_enhanced_request', names)
            self.assertFalse(replies[4]['result']['isError'])
            self.assertEqual('text', replies[4]['result']['content'][0]['type'])
            self.assertEqual('hello\n', (Path(folder) / 'docs/a.md').read_text(encoding='utf-8'))
            self.assertTrue(replies[5]['result']['isError'])
            self.assertEqual(-32601, replies[6]['error']['code'])
            self.assertFalse((Path(folder).parent / 'x.md').exists())


if __name__ == '__main__':
    unittest.main()
