"""Sample: Run python -m durai.mcp_server. An MCP client lists five tools, calls durai_check_call for a call to example.com:443, and gets deny with reason egress-not-listed. The server has no approve tool."""
import json
import os
import sys

from .egress_ceiling import CeilingExceeded
from .egress_policy import EgressPolicyError
from .egress_presets import make_record, starter_body
from .ports.errors import Unsupported

SERVER_NAME = 'durai-governance'
RESOURCE_URI = 'durai://egress/starter'
SAFE_ERRORS = (EgressPolicyError, CeilingExceeded, Unsupported)
MESSAGE_LIMIT = 300

TOOLS = (
    {'name': 'durai_check_call',
     'description': 'Check one planned tool call against the Durai egress list and operator boundary. Returns allow, deny or gate with a reason.',
     'inputSchema': {'type': 'object', 'properties': {'tool_name': {'type': 'string'}, 'arguments': {'type': 'object'},
                                                       'workflow_id': {'type': 'string'}, 'session_id': {'type': 'string'}, 'tool_call_id': {'type': 'string'}},
                     'required': ['tool_name', 'arguments']}},
    {'name': 'durai_open_gate',
     'description': 'Open a reviewer gate for one egress entry such as example.com:443. Only a human reviewer can approve it, outside this server.',
     'inputSchema': {'type': 'object', 'properties': {'workflow_id': {'type': 'string'}, 'entry': {'type': 'string'}, 'deadline': {'type': 'number'}},
                     'required': ['workflow_id', 'entry']}},
    {'name': 'durai_gate_state',
     'description': 'Read the state of a gate: WAITING, APPROVED, CANCELLED or NOT_OPEN. Read only.',
     'inputSchema': {'type': 'object', 'properties': {'workflow_id': {'type': 'string'}, 'subject_digest': {'type': 'string'}},
                     'required': ['workflow_id', 'subject_digest']}},
    {'name': 'durai_egress_check',
     'description': 'Say whether an egress entry is on the list and inside the operator boundary. Read only.',
     'inputSchema': {'type': 'object', 'properties': {'entry': {'type': 'string'}}, 'required': ['entry']}},
    {'name': 'durai_receipt',
     'description': 'Record the result of a tool call as an evidence receipt. The receipt keeps digests only, never the raw arguments or result.',
     'inputSchema': {'type': 'object', 'properties': {'tool_name': {'type': 'string'}, 'arguments': {'type': 'object'}, 'result': {},
                                                       'decision': {'type': 'string'}, 'model': {'type': 'string'}, 'session_id': {'type': 'string'},
                                                       'tool_call_id': {'type': 'string'}},
                     'required': ['tool_name', 'arguments', 'result']}},
)


class GovernanceTools:
    def __init__(self, port):
        # Sample: Wrap a GovernancePort. list_tools returns five tools. call runs one of them and returns a JSON safe dict.
        self.port = port

    def list_tools(self):
        # Sample: Return the five tool definitions as plain dicts.
        return [dict(tool) for tool in TOOLS]

    def list_resources(self):
        # Sample: Return one read only resource with uri durai://egress/starter.
        return [{'uri': RESOURCE_URI, 'name': 'Egress record', 'description': 'The sandbox.egress record in force, with its digest.', 'mimeType': 'application/json'}]

    def read_resource(self, uri):
        # Sample: Read durai://egress/starter. Return JSON text of the record with id sandbox.egress. Another uri raises KeyError.
        if uri != RESOURCE_URI:
            raise KeyError('Unknown resource')
        return json.dumps(make_record(getattr(self.port, 'body', None) or starter_body()), sort_keys=True)

    def call(self, name, arguments):
        # Sample: Call durai_check_call with a listed host. Return {"isError": false, "result": {"decision": "allow", ...}}. A port error returns isError true with the error type and, for known safe errors only, a short message.
        arguments = arguments or {}
        try:
            return {'isError': False, 'result': self.run(name, arguments)}
        except KeyError:
            return {'isError': True, 'error': 'UnknownTool', 'message': 'Unknown tool: ' + str(name)[:60]}
        except Exception as error:
            message = str(error)[:MESSAGE_LIMIT] if isinstance(error, SAFE_ERRORS) else ''
            return {'isError': True, 'error': type(error).__name__, 'message': message}

    def run(self, name, arguments):
        # Sample: Map durai_gate_state to port.gate_state. An unknown name raises KeyError. A missing required field raises ValueError.
        names = [tool['name'] for tool in TOOLS]
        if name not in names:
            raise KeyError(name)
        required = TOOLS[names.index(name)]['inputSchema']['required']
        if any(field not in arguments for field in required):
            raise ValueError('Missing field')
        port = self.port
        if name == 'durai_check_call':
            return port.check_tool_call(arguments['tool_name'], arguments['arguments'], arguments.get('workflow_id'), arguments.get('session_id'),
                                        arguments.get('tool_call_id'))
        if name == 'durai_open_gate':
            return port.open_gate(arguments['workflow_id'], arguments['entry'], arguments.get('deadline'))
        if name == 'durai_gate_state':
            return {'state': port.gate_state(arguments['workflow_id'], arguments['subject_digest'])}
        if name == 'durai_egress_check':
            return port.egress_check(arguments['entry'])
        return port.record_result(arguments['tool_name'], arguments['arguments'], arguments['result'], arguments.get('decision'),
                                  arguments.get('model'), arguments.get('session_id'), arguments.get('tool_call_id'))


def port_from_environment(environ=None):
    # Sample: DURAI_EGRESS_TIER=balanced and no other variable return a LocalGovernance with the starter list, no gate and no boundary. DURAI_EGRESS_BOUNDARY=api.deepinfra.com:443 adds a boundary. DURAI_GOVERNANCE_DB=durai_test adds the gate store.
    from .adapters.governance.local import LocalGovernance
    from .egress_ceiling import EgressCeiling
    from .egress_gate import EgressGate
    environ = os.environ if environ is None else environ
    boundary = [item.strip() for item in environ.get('DURAI_EGRESS_BOUNDARY', '').split(',') if item.strip()]
    ceiling = EgressCeiling(boundary, environ=environ) if 'DURAI_EGRESS_BOUNDARY' in environ else None
    database = environ.get('DURAI_GOVERNANCE_DB')
    gate = EgressGate(database=database, ceiling=ceiling) if database else None
    return LocalGovernance(tier=environ.get('DURAI_EGRESS_TIER', 'balanced'), gate=gate, ceiling=ceiling)


def build_server(tools):
    # Sample: Build an MCP lowlevel server with the five tools and one resource. Needs the optional package mcp. A missing package raises ImportError that names pip install.
    try:
        import mcp.types as types
        from mcp.server.lowlevel import Server
        from mcp.server.lowlevel.helper_types import ReadResourceContents
    except ImportError:
        raise ImportError('Install the MCP SDK first: pip install "mcp==1.30.0"') from None
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=tool['name'], description=tool['description'], inputSchema=tool['inputSchema']) for tool in tools.list_tools()]

    @server.call_tool()
    async def call_tool(name, arguments):
        outcome = tools.call(name, arguments)
        if outcome['isError']:
            raise RuntimeError(outcome['error'] + (': ' + outcome['message'] if outcome['message'] else ''))
        return [types.TextContent(type='text', text=json.dumps(outcome['result'], sort_keys=True))]

    @server.list_resources()
    async def list_resources():
        return [types.Resource(uri=item['uri'], name=item['name'], description=item['description'], mimeType=item['mimeType'])
                for item in tools.list_resources()]

    @server.read_resource()
    async def read_resource(uri):
        return [ReadResourceContents(content=tools.read_resource(str(uri)), mime_type='application/json')]

    return server


async def serve(tools):
    # Sample: Serve the tools on standard input and output until the client closes the pipe. Nothing else writes to standard output.
    from mcp.server.stdio import stdio_server
    server = build_server(tools)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main():
    # Sample: Read the environment, build the port and serve on stdio. Return 0 when the client closes the pipe.
    import asyncio
    asyncio.run(serve(GovernanceTools(port_from_environment())))
    return 0


if __name__ == '__main__':
    sys.exit(main())
