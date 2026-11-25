import os
import unittest
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from apps.shell.api.database import migrate, publish_outbox


@unittest.skipUnless(os.environ.get('DURAI_BUILD_DATABASE_URL'), 'Use the disposable build database')
class AppShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start a disposable test database. Install the shell schema once.
        with cls.connection() as connection:
            migrate(connection)

    @staticmethod
    def connection():
        # Sample: Connect to the disposable database. Check the database name before each write.
        url = os.environ['DURAI_BUILD_DATABASE_URL']
        params = psycopg.conninfo.conninfo_to_dict(url)
        if params.get('host') != 'db' or params.get('dbname') != 'postgres':
            raise ValueError('Shell tests need the disposable build database')
        return psycopg.connect(url, row_factory=dict_row)

    def setUp(self):
        # Sample: Add trader A. Sign in and keep the cookie in the test client.
        from fastapi.testclient import TestClient
        from apps.shell.api.app import Settings, create_app

        self.actor = 'TR-' + uuid4().hex
        self.client = self.enterContext(TestClient(create_app(Settings(self.actor, 'fixture-only', False), self.connection)))
        with self.connection() as connection:
            connection.execute('INSERT INTO shell.actor(actor_id) VALUES(%s)', (self.actor,))
        reply = self.client.post('/session/sign-in', json={'trader': self.actor, 'secret': 'fixture-only'})
        self.assertEqual(reply.status_code, 200)
        self.assertIn('durai_session', reply.cookies)

    def post(self, label='R-1'):
        # Sample: Post label R-1 with key K-1. Read the HTTP response.
        return self.client.post('/api/receipts', json={'label': label}, headers={'Idempotency-Key': 'K-1'})

    def test_repeated_post_replays_the_receipt(self):
        # Sample: Post key K-1 twice. Check the same receipt and one stored row.
        one = self.post()
        two = self.post()
        self.assertEqual((one.status_code, two.status_code), (201, 201))
        self.assertEqual(one.json(), two.json())
        self.assertEqual(len(self.client.get('/api/receipts').json()['items']), 1)

    def test_get_request_never_changes_the_session(self):
        # Sample: Send three prefetch requests. Check the same cookie and a signed-in trader.
        token = self.client.cookies['durai_session']
        for path, status in (('/session', 200), ('/session/sign-out', 405), ('/api/receipts', 200)):
            reply = self.client.get(path, headers={'Purpose': 'prefetch'})
            self.assertEqual(reply.status_code, status)
            self.assertNotIn('set-cookie', reply.headers)
            self.assertEqual(self.client.cookies['durai_session'], token)
        self.assertEqual(self.client.get('/session').json()['trader'], self.actor)

    def test_outbox_and_audit_share_the_write(self):
        # Sample: Post one receipt. Read its event and audit row. Publish the event.
        identity = self.post().json()['receipt_id']
        with self.connection() as connection:
            for table in ('outbox', 'audit'):
                statement = psycopg.sql.SQL('SELECT count(*) FROM shell.{} WHERE receipt_id=%s').format(psycopg.sql.Identifier(table))
                self.assertEqual(connection.execute(statement, (identity,)).fetchone()['count'], 1)
            self.assertGreaterEqual(publish_outbox(connection), 1)

    def test_idempotency_key_conflict(self):
        # Sample: Post R-1. Post R-2 with the same key. Check 409 and one receipt.
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post('R-2').json(), {'error': {'code': 'idempotency_conflict'}})
        self.assertEqual(len(self.client.get('/api/receipts').json()['items']), 1)

    def test_mounted_shell_applies_idempotency_to_domain_writes(self):
        # Sample: Mount the shell at /api. Post sign-in and a receipt with no key. Check 200 and 400.
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from apps.shell.api.app import Settings, create_app

        app = FastAPI()
        app.mount('/api', create_app(Settings(self.actor, 'fixture-only', False), self.connection))
        with TestClient(app) as client:
            login = client.post('/api/session/sign-in', json={'trader': self.actor, 'secret': 'fixture-only'})
            self.assertEqual(login.status_code, 200)
            receipt = client.post('/api/api/receipts', json={'label': 'R-1'})
            self.assertEqual(receipt.json(), {'error': {'code': 'invalid_idempotency_key'}})
            self.assertEqual(receipt.status_code, 400)
