"""Loopback trading sample. Read endpoints expose synthetic domain records only."""
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlsplit
from .common import Conflict
from .db import settings, transaction
from .registry import Registry

ROOT = Path(__file__).resolve().parents[1]


def serve(port=54420, *, ready=None):
    origin = 'http://127.0.0.1:' + str(port)
    sessions = set()
    session_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'DuraiLocal/1'

        def log_message(self, format, *args):
            # Do not log request bodies, session cookies, or reviewer credentials.
            pass

        def send(self, code, value, content_type='application/json', cookie=None):
            body = value if isinstance(value, bytes) else json.dumps(value, default=str).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()
            self.wfile.write(body)

        def permitted(self):
            return self.headers.get('Host') == '127.0.0.1:' + str(port) and self.headers.get('Origin', origin) == origin

        def do_GET(self):
            if not self.permitted():
                return self.send(403, {'error': 'Origin or host is not allowed'})
            path = urlsplit(self.path)
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path.path in assets:
                name, content_type = assets[path.path]
                return self.send(200, (ROOT / 'apps/trading-demo/web' / name).read_bytes(), content_type)
            try:
                if path.path == '/health':
                    with transaction() as db:
                        db.execute('SELECT 1')
                    return self.send(200, {'state': 'READY', 'application': 'durai-trading-local'})
                if path.path == '/api/status':
                    from manage import status
                    return self.send(200, status())
                if path.path == '/api/source':
                    version = parse_qs(path.query).get('version', [''])[0]
                    row = Registry().get(version)
                    return self.send(200, {'versionId': version, 'digest': row['digest'],
                                           'source': row['source_bytes'].decode()})
                if path.path == '/api/events':
                    query = parse_qs(path.query)
                    run_id = query.get('run', [''])[0]
                    after = int(query.get('after', ['0'])[0])
                    with transaction() as db:
                        rows = db.execute('SELECT sequence,id,kind,payload FROM durai_coordination.event WHERE run_id=%s AND sequence>%s ORDER BY sequence LIMIT 100', (run_id, after)).fetchall()
                    return self.send(200, {'events': rows, 'delivery': 'at-least-once; deduplicate by event ID'})
            except (ValueError, KeyError):
                return self.send(400, {'error': 'Request is outside the domain contract'})
            except Exception:
                return self.send(503, {'error': 'Local service is unavailable'})
            self.send(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.permitted() or self.headers.get('Origin') != origin:
                return self.send(403, {'error': 'Same-origin operator request is required'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 8192:
                    return self.send(413, {'error': 'Request size is outside bounds'})
                value = json.loads(self.rfile.read(length))
                if self.path == '/api/session':
                    if not hmac.compare_digest(str(value.get('key', '')), settings()['reviewKey']):
                        return self.send(403, {'error': 'Reviewer key is invalid'})
                    token = secrets.token_urlsafe(32)
                    with session_lock:
                        if len(sessions) >= 16:
                            sessions.clear()
                        sessions.add(token)
                    return self.send(200, {'state': 'AUTHENTICATED'},
                                     cookie='durai_review=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600')
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                token = cookies.get('durai_review')
                with session_lock:
                    authenticated = token is not None and token.value in sessions
                if not authenticated:
                    return self.send(403, {'error': 'Reviewer sign-in is required'})
                from manage import components
                controller = components()[3]
                if self.path == '/api/approve':
                    approval = controller.approve(value['versionId'], value['digest'], 'trading-local', value['reviewer'])
                    with transaction() as db:
                        run = db.execute('SELECT checkpoint FROM durai_agents.run WHERE id=%s', (value['runId'],)).fetchone()
                    if run and run['checkpoint'].get('stage') == 'AWAITING_APPROVAL':
                        from manage import resume_factory
                        result = resume_factory(value['runId'], approval)
                    else:
                        result = controller.activate(approval, value['findingId'], value['runId'])
                    return self.send(200, result)
                if self.path == '/api/rollback':
                    return self.send(200, controller.rollback('trading-local'))
                return self.send(404, {'error': 'Unknown operator command'})
            except (ValueError, KeyError, Conflict) as error:
                return self.send(409, {'error': str(error)})
            except Exception:
                return self.send(503, {'error': 'Operator action failed; retained evidence is available for review'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    if ready:
        ready(server)
    print('Durai trading sample: ' + origin, flush=True)
    server.serve_forever()
