"""Sample: Call the five governance tools through GovernanceTools with a LocalGovernance port. When the MCP SDK is installed, start the server as a child process and call a tool over stdio."""
import asyncio
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

from durai.adapters.governance.local import LocalGovernance
from durai.mcp_server import RESOURCE_URI, TOOLS, GovernanceTools, port_from_environment

ROOT = Path(__file__).resolve().parents[1]
HAS_SDK = importlib.util.find_spec('mcp') is not None
LISTED = 'https://api.deepinfra.com/v1/chat/completions'
UNLISTED = 'https://example.com/data'


class BrokenPort(LocalGovernance):
    def check_tool_call(self, *args, **kwargs):
        raise RuntimeError('connection string has sample-password')

    def gate_state(self, workflow_id, subject_digest):
        raise RuntimeError('connection string has sample-password')


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.tools = GovernanceTools(LocalGovernance())

    def test_tools_listed(self):
        # Sample: List the tools. Expect durai_check_call, durai_open_gate, durai_gate_state, durai_egress_check and durai_receipt.
        names = [tool['name'] for tool in self.tools.list_tools()]
        self.assertEqual(names, ['durai_check_call', 'durai_open_gate', 'durai_gate_state', 'durai_egress_check', 'durai_receipt'])

    def test_no_approve_tool(self):
        # Sample: No tool name or description offers to approve, grant or decide a gate.
        for tool in self.tools.list_tools():
            self.assertFalse(any(word in tool['name'] for word in ('approve', 'grant', 'decide')), tool['name'])
        self.assertEqual(self.tools.call('durai_approve', {})['error'], 'UnknownTool')

    def test_check_call_returns_port_answer(self):
        # Sample: Call durai_check_call for a listed host. Expect allow with reason egress-listed. For example.com expect deny with reason egress-not-listed.
        listed = self.tools.call('durai_check_call', {'tool_name': 'fetch', 'arguments': {'url': LISTED}})
        unlisted = self.tools.call('durai_check_call', {'tool_name': 'fetch', 'arguments': {'url': UNLISTED}})
        self.assertEqual((listed['isError'], listed['result']['decision'], listed['result']['reason']), (False, 'allow', 'egress-listed'))
        self.assertEqual((unlisted['result']['decision'], unlisted['result']['reason']), ('deny', 'egress-not-listed'))

    def test_gate_state_without_a_gate_store(self):
        # Sample: No gate store. Call durai_gate_state. Expect state NOT_OPEN. Call durai_open_gate. Expect isError true with error Unsupported.
        state = self.tools.call('durai_gate_state', {'workflow_id': 'W-1', 'subject_digest': 'D-1'})
        opened = self.tools.call('durai_open_gate', {'workflow_id': 'W-1', 'entry': 'example.com:443'})
        self.assertEqual(state['result'], {'state': 'NOT_OPEN'})
        self.assertEqual((opened['isError'], opened['error']), (True, 'Unsupported'))

    def test_egress_check_and_receipt(self):
        # Sample: Call durai_egress_check for api.deepinfra.com:443. Expect listed true. Call durai_receipt. Expect a digest of 64 characters and the model name.
        check = self.tools.call('durai_egress_check', {'entry': 'api.deepinfra.com:443'})
        receipt = self.tools.call('durai_receipt', {'tool_name': 'fetch', 'arguments': {'url': LISTED}, 'result': {'status': 200}, 'model': 'Beam-1.0-501B-A23B'})
        self.assertTrue(check['result']['listed'])
        self.assertEqual((len(receipt['result']['digest']), receipt['result']['model']), (64, 'Beam-1.0-501B-A23B'))

    def test_missing_field_is_an_error(self):
        # Sample: Call durai_check_call with no arguments field. Expect isError true.
        self.assertTrue(self.tools.call('durai_check_call', {'tool_name': 'fetch'})['isError'])

    def test_secrets_not_in_output(self):
        # Sample: Put a fake token in a url query, in the arguments and in the result. Check, record and break the port. None of the outputs holds the token or the password text.
        token = 'xo' + 'xb-' + 'sample-token-value'
        arguments = {'url': UNLISTED + '?key=' + token, 'authorization': 'Bearer ' + token}
        outputs = [self.tools.call('durai_check_call', {'tool_name': 'fetch', 'arguments': arguments}),
                   self.tools.call('durai_receipt', {'tool_name': 'fetch', 'arguments': arguments, 'result': 'got ' + token}),
                   GovernanceTools(BrokenPort()).call('durai_gate_state', {'workflow_id': 'W-1', 'subject_digest': 'D-1'}),
                   GovernanceTools(BrokenPort()).call('durai_check_call', {'tool_name': 'fetch', 'arguments': arguments})]
        text = json.dumps(outputs)
        self.assertNotIn(token, text)
        self.assertNotIn('sample-password', text)
        self.assertEqual(outputs[2]['error'], 'RuntimeError')

    def test_resource_is_read_only_and_holds_the_egress_record(self):
        # Sample: List resources. Expect one with uri durai://egress/starter. Read it. Expect record id sandbox.egress and a digest. Another uri raises KeyError.
        self.assertEqual([item['uri'] for item in self.tools.list_resources()], [RESOURCE_URI])
        record = json.loads(self.tools.read_resource(RESOURCE_URI))
        self.assertEqual((record['id'], len(record['digest'])), ('sandbox.egress', 64))
        with self.assertRaises(KeyError):
            self.tools.read_resource('durai://other')
        self.assertFalse(hasattr(self.tools, 'write_resource'))

    def test_port_from_environment_reads_boundary_and_database(self):
        # Sample: Set DURAI_EGRESS_BOUNDARY to api.deepinfra.com:443 and DURAI_GOVERNANCE_DB to durai_test. Expect a port with a ceiling and a gate. Set neither. Expect neither.
        plain = port_from_environment({})
        full = port_from_environment({'DURAI_EGRESS_BOUNDARY': 'api.deepinfra.com:443', 'DURAI_GOVERNANCE_DB': 'durai_test'})
        self.assertEqual((plain.gate, plain.ceiling), (None, None))
        self.assertEqual((full.gate.database, len(full.ceiling.boundary)), ('durai_test', 1))

    def test_every_tool_has_a_schema_with_required_fields(self):
        # Sample: Each tool has an object schema with a required list.
        for tool in TOOLS:
            self.assertEqual(tool['inputSchema']['type'], 'object')
            self.assertTrue(tool['inputSchema']['required'])


@unittest.skipUnless(HAS_SDK, 'The MCP SDK is not installed. Install the optional extra: pip install "mcp==1.30.0".')
class McpStdioTests(unittest.TestCase):
    def test_check_call_over_stdio(self):
        # Sample: Start python -m durai.mcp_server. Initialize. List five tools. Call durai_check_call for api.deepinfra.com. Expect allow. Read the resource. Expect sandbox.egress.
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        async def session():
            params = StdioServerParameters(command=sys.executable, args=['-m', 'durai.mcp_server'], cwd=str(ROOT),
                                           env={**os.environ, 'PYTHONPATH': str(ROOT)})
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    call = await client.call_tool('durai_check_call', {'tool_name': 'fetch', 'arguments': {'url': LISTED}})
                    denied = await client.call_tool('durai_check_call', {'tool_name': 'fetch', 'arguments': {'url': UNLISTED}})
                    resources = await client.list_resources()
                    record = await client.read_resource(RESOURCE_URI)
                    return tools, call, denied, resources, record
        tools, call, denied, resources, record = asyncio.run(session())
        self.assertEqual(len(tools.tools), 5)
        self.assertFalse(any('approve' in tool.name for tool in tools.tools))
        self.assertEqual(json.loads(call.content[0].text)['decision'], 'allow')
        self.assertEqual(json.loads(denied.content[0].text)['reason'], 'egress-not-listed')
        self.assertEqual([str(item.uri) for item in resources.resources], [RESOURCE_URI])
        self.assertEqual(json.loads(record.contents[0].text)['id'], 'sandbox.egress')


if __name__ == '__main__':
    unittest.main()
