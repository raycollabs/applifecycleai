"""Sample: Test MCP client protocol handshake, tool discovery, and execution against an MCP server."""
import pytest
from durai.mcp_client import McpClient
from durai.build_mcp import handle


class FakeTools:
    def tool_list(self):
        # Sample: Return mock tool definitions for testing.
        return [
            {
                'name': 'echo_tool',
                'description': 'Echo input text',
                'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}},
            }
        ]

    def call(self, name, arguments):
        # Sample: Mock tool handler returning echo output.
        if name == 'echo_tool':
            return {'text': f"echo: {arguments.get('text', '')}", 'isError': False}
        return {'text': f"Unknown tool: {name}", 'isError': True}


class MemoryTransport:
    def __init__(self, tools):
        # Sample: In-memory transport wiring requests directly to build_mcp.handle.
        self.tools = tools

    def send_and_receive(self, message):
        # Sample: Dispatch message directly to handle and return response.
        return handle(self.tools, message)


class TestMcpClient:
    def test_initialize_handshake(self):
        # Sample: Perform initialization handshake and check server info.
        transport = MemoryTransport(FakeTools())
        client = McpClient(transport)
        init_res = client.initialize()
        assert init_res['protocolVersion'] == '2024-11-05'
        assert init_res['serverInfo']['name'] == 'durai-build'

    def test_list_tools(self):
        # Sample: List tools and verify tool properties.
        transport = MemoryTransport(FakeTools())
        client = McpClient(transport)
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0]['name'] == 'echo_tool'

    def test_call_tool(self):
        # Sample: Execute tool call and verify returned text and error status.
        transport = MemoryTransport(FakeTools())
        client = McpClient(transport)
        result = client.call_tool('echo_tool', {'text': 'hello MCP'})
        assert not result['isError']
        assert result['text'] == 'echo: hello MCP'

    def test_adapt_as_driver_tools(self):
        # Sample: Adapt MCP tools to callable functions.
        transport = MemoryTransport(FakeTools())
        client = McpClient(transport)
        callables = client.adapt_as_driver_tools()
        assert 'echo_tool' in callables
        res = callables['echo_tool'](text='adapted test')
        assert res['text'] == 'echo: adapted test'
