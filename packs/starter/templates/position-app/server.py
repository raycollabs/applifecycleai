import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from program import execute

ROOT = Path(__file__).resolve().parent


class Handler(BaseHTTPRequestHandler):
    commands = []

    def reply(self, code, data, content_type='application/json'):
        # Sample: Return positions with status 200. Send a bounded explicit content length.
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Sample: Open /. Show the trade UI. Open /positions. Show the current snapshot.
        if self.headers.get('Host') not in ('127.0.0.1:54430', 'localhost:54430'):
            self.reply(403, {'error': 'Loopback host required'})
        elif self.path == '/':
            self.reply(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif self.path == '/positions':
            self.reply(200, execute({'commands': self.commands}))
        else:
            self.reply(404, {'error': 'Unknown resource'})

    def do_POST(self):
        # Sample: Submit a T-101 booking from the same origin. Retain only valid commands.
        allowed = ('http://127.0.0.1:54430', 'http://localhost:54430')
        if self.path != '/commands' or self.headers.get('Origin') not in allowed or self.headers.get('Host') not in ('127.0.0.1:54430', 'localhost:54430'):
            self.reply(403, {'error': 'Same-origin local command required'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError('Command size differs')
            command = json.loads(self.rfile.read(length))
            candidate = self.commands + [command]
            result = execute({'commands': candidate})
            if 'error' not in result:
                type(self).commands = candidate
            self.reply(400 if 'error' in result else 200, result)
        except (ValueError, TypeError):
            self.reply(400, {'error': 'Invalid command'})

    def log_message(self, format, *args):
        # Sample: Handle a local request. Keep the demonstration console quiet.
        pass


def main():
    # Sample: Start on 127.0.0.1:54430. Close the process to discard synthetic state.
    print('Synthetic positions: http://127.0.0.1:54430', flush=True)
    HTTPServer(('127.0.0.1', 54430), Handler).serve_forever()


if __name__ == '__main__':
    main()
