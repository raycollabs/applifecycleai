"""Exercise the real loopback protocol without creating an approval."""
import json
import threading
import unittest
import urllib.request
import urllib.error
from durai.server import serve


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = 54429
        ready = threading.Event()
        def connected(server):
            cls.server = server
            ready.set()
        cls.thread = threading.Thread(target=serve, args=(cls.port,), kwargs={'ready': connected}, daemon=True)
        cls.thread.start()
        if not ready.wait(5):
            raise RuntimeError('Loopback test server did not start')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_status_and_event_replay_are_read_only(self):
        # Read the local synthetic run. Replay from sequence 0. Create no approval.
        base = 'http://127.0.0.1:' + str(self.port)
        with urllib.request.urlopen(base + '/api/status') as response:
            status = json.load(response)
        before = len(status['approvals'])
        self.assertEqual(status['database'], 'durai_local')
        self.assertTrue(status['versions'])
        if status['runs']:
            run_id = status['runs'][0]['id']
            with urllib.request.urlopen(base + '/api/events?run=' + run_id + '&after=0') as response:
                events = json.load(response)['events']
            self.assertEqual([row['sequence'] for row in events], sorted(row['sequence'] for row in events))
        with urllib.request.urlopen(base + '/api/status') as response:
            self.assertEqual(len(json.load(response)['approvals']), before)

    def test_cross_origin_release_request_is_denied(self):
        # A foreign origin cannot create a reviewer session or activate a release.
        request = urllib.request.Request('http://127.0.0.1:' + str(self.port) + '/api/approve',
            data=b'{}', headers={'Content-Type': 'application/json', 'Origin': 'https://foreign.example'})
        with self.assertRaises(urllib.error.HTTPError) as result:
            urllib.request.urlopen(request)
        self.assertEqual(result.exception.code, 403)
