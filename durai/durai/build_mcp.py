"""Sample: Send initialize, tools/list and tools/call write_file docs/a.md as JSON lines. Read three replies. Check the file."""
import json
import sys

PROTOCOL = '2024-11-05'
SERVER = {'name': 'durai-build', 'version': '1'}


def handle(tools, message):
    # Sample: Message tools/list with id 2. Return a result with 10 tools. Message notifications/initialized. Return None.
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
        return {'jsonrpc': '2.0', 'id': (message or {}).get('id') if isinstance(message, dict) else None,
                'error': {'code': -32600, 'message': 'Invalid request'}}
    method, ident, params = message['method'], message.get('id'), message.get('params') or {}
    if 'id' not in message:
        return None

    def reply(result):
        return {'jsonrpc': '2.0', 'id': ident, 'result': result}

    if method == 'initialize':
        return reply({'protocolVersion': PROTOCOL, 'capabilities': {'tools': {}}, 'serverInfo': SERVER})
    if method == 'ping':
        return reply({})
    if method == 'tools/list':
        return reply({'tools': tools.tool_list()})
    if method == 'tools/call':
        if not isinstance(params.get('name'), str):
            return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': 'Tool name is required'}}
        outcome = tools.call(params['name'], params.get('arguments'))
        return reply({'content': [{'type': 'text', 'text': outcome['text']}], 'isError': outcome['isError']})
    return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': 'Method not found: ' + method}}


def serve(tools, stdin=None, stdout=None):
    # Sample: Read one JSON line per message until end of input. Write one JSON line per reply. Answer bad JSON with error -32700.
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        else:
            response = handle(tools, message)
        if response is not None:
            stdout.write(json.dumps(response) + '\n')
            stdout.flush()
