"""Sample: Connect to third-party MCP server over JSON-RPC 2.0, list tools, and execute calls."""
import json
import subprocess
from typing import Optional

PROTOCOL = '2024-11-05'


class McpClient:
    def __init__(self, transport=None):
        # Sample: Initialize client with custom transport object or mock pipe.
        self.transport = transport
        self._next_id = 1
        self._server_info = {}

    def _next_request_id(self) -> int:
        # Sample: Increment and return next monotonic request id.
        req_id = self._next_id
        self._next_id += 1
        return req_id

    def send(self, method: str, params: Optional[dict] = None) -> dict:
        # Sample: Send JSON-RPC request message with method and params. Return result payload or raise on error.
        req_id = self._next_request_id()
        msg = {
            'jsonrpc': '2.0',
            'id': req_id,
            'method': method,
            'params': params or {},
        }
        if self.transport is None:
            raise RuntimeError('Transport is not configured on McpClient')

        resp = self.transport.send_and_receive(msg)
        if not isinstance(resp, dict):
            raise RuntimeError('Invalid non-dict response from MCP server')
        if 'error' in resp:
            err = resp['error']
            raise RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}")
        return resp.get('result', {})

    def initialize(self, client_name: str = 'durai-consumer', client_version: str = '1') -> dict:
        # Sample: Initialize handshake with MCP server and verify protocol version.
        params = {
            'protocolVersion': PROTOCOL,
            'capabilities': {},
            'clientInfo': {'name': client_name, 'version': client_version},
        }
        result = self.send('initialize', params)
        self._server_info = result.get('serverInfo', {})
        return result

    def list_tools(self) -> list:
        # Sample: Query tools/list endpoint. Return list of tool definition dicts.
        result = self.send('tools/list')
        return result.get('tools', [])

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict:
        # Sample: Call tool named fetch_data with arg id 12. Return text and isError flag.
        params = {'name': name, 'arguments': arguments or {}}
        result = self.send('tools/call', params)
        content = result.get('content', [])
        text = ''
        if isinstance(content, list) and content:
            text = content[0].get('text', '')
        return {
            'content': content,
            'text': text,
            'isError': bool(result.get('isError', False)),
        }

    def adapt_as_driver_tools(self) -> dict:
        # Sample: Convert discovered MCP tools into callables usable by the build driver.
        tools_dict = {}
        for tool in self.list_tools():
            name = tool['name']

            def make_caller(tname):
                def _caller(**kwargs):
                    return self.call_tool(tname, kwargs)
                return _caller

            tools_dict[name] = make_caller(name)
        return tools_dict
