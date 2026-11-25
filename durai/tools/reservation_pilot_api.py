"""Sample: Hold seat A1 through HTTP. Confirm the order at revision 1."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from hmac import compare_digest
import json
from threading import Thread
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from durai.common import Conflict
from durai.reservations import change_hold, hold_seat, read_order


class ReservationHTTP(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Sample: Submit a request without exposing its authentication value in a log.
        return

    def respond(self, status, result):
        # Sample: Return status 409 and a conflict result.
        content = json.dumps(result,separators=(',',':')).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def authorized(self):
        # Sample: Refuse a request that does not have the pilot token.
        supplied = self.headers.get('Authorization','').encode()
        return compare_digest(supplied,b'Bearer '+self.server.token.encode())

    def do_GET(self):
        # Sample: Read GET /health without a token. Check status 200 and ok true.
        if self.path=='/health':
            return self.respond(200,{'ok':True})
        # Sample: Read a confirmed order under the pilot login.
        if not self.authorized():
            return self.respond(401,{'error':'UNAUTHORIZED'})
        prefix = '/orders/'
        if not self.path.startswith(prefix):
            return self.respond(404,{'error':'NOT_FOUND'})
        try:
            result = read_order(self.path.removeprefix(prefix))
        except (KeyError,ValueError):
            return self.respond(404,{'error':'NOT_FOUND'})
        self.respond(200,result)

    def do_POST(self):
        # Sample: Send a seat hold, then confirm it with the current revision.
        if not self.authorized():
            return self.respond(401,{'error':'UNAUTHORIZED'})
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 1 <= length <= 4096:
                raise ValueError('Invalid body size')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body,dict):
                raise ValueError('Use a JSON object')
            if self.path == '/holds' and set(body)=={'seatId','ttlSeconds','requestKey'}:
                result = hold_seat(body['seatId'],body['ttlSeconds'],body['requestKey'])
            elif self.path in ('/holds/confirm','/holds/release','/holds/expire') and set(body)=={'orderId','revision','requestKey'}:
                result = change_hold(self.path.rsplit('/',1)[-1],body['orderId'],body['revision'],body['requestKey'])
            else:
                return self.respond(400,{'error':'INVALID_COMMAND'})
        except Conflict:
            return self.respond(409,{'error':'CONFLICT'})
        except KeyError:
            return self.respond(404,{'error':'NOT_FOUND'})
        except (ValueError,UnicodeError):
            return self.respond(400,{'error':'INVALID_BODY'})
        self.respond(200,result)


class PilotHTTPServer(ThreadingHTTPServer):
    request_queue_size = 32


@contextmanager
def serve(token):
    # Sample: Start the pilot API on a free loopback port and close it after the check.
    with PilotHTTPServer(('127.0.0.1',0),ReservationHTTP) as server:
        server.token = token
        worker = Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            yield 'http://127.0.0.1:'+str(server.server_port)
        finally:
            server.shutdown()
            worker.join(5)


def serve_forever(token, host, port):
    # Sample: Listen on 0.0.0.0 port 8080 inside the staging network with token T-1.
    with PilotHTTPServer((host,port),ReservationHTTP) as server:
        server.token = token
        server.serve_forever()


def request(base, path, token, body=None):
    # Sample: Send the HTTP command and read its status and JSON result.
    command = Request(base+path,data=json.dumps(body).encode() if body is not None else None,
                      headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with urlopen(command,timeout=15) as result:
            return result.status,json.loads(result.read())
    except HTTPError as result:
        with result:
            return result.code,json.loads(result.read())
