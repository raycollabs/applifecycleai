"""Sample: Race 20 retries of K-1. Keep one order and one retained outcome."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import json
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
import psycopg
from psycopg.types.json import Jsonb
from durai.common import Conflict, digest, uid
from durai.db import ROOT, connect, server, transaction
from durai.idempotency import MAX_BYTES, execute_request

DATABASE = 'durai_test'
FILES = ['blueprints/idempotency/v1.sql', 'blueprints/idempotency/reference.sql']


def install_fixture():
    # Sample: Check the disposable port. Apply v1 once. Refuse a changed source digest on replay.
    target = json.loads((ROOT/'private/context-isolation.json').read_text())
    active = server()
    if (target['host']!='127.0.0.1' or target['port']==54410 or not target['name'].startswith('durai-eval-db-') or
            active['port']!=target['port'] or active['host']!=target['host']):
        raise ValueError('Blueprint tests require their isolated server')
    ports = subprocess.check_output(['docker','port',target['name'],'5432/tcp'],text=True)
    if '127.0.0.1:'+str(target['port']) not in ports:
        raise ValueError('Blueprint server identity differs')
    source = digest({name:digest((ROOT/name).read_bytes()) for name in FILES})
    with connect('postgres', DATABASE) as db:
        exists = db.execute("SELECT to_regnamespace('durai_idempotency') AS id").fetchone()['id']
        if exists is None:
            for name in FILES:
                db.execute((ROOT/name).read_text(),prepare=False)
            db.execute('INSERT INTO durai_idempotency.schema_version VALUES(%s,%s)',('v1',source))
        else:
            row = db.execute('SELECT source_digest FROM durai_idempotency.schema_version WHERE name=%s',('v1',)).fetchone()
            if not row or row['source_digest']!=source:
                raise ValueError('Applied blueprint differs. Use a fresh isolated server.')
    return source


def order_handler(db, payload):
    # Sample: Record 100 units as order O-1. Return its retained ID and state.
    identity = uid()
    external = payload.get('externalIdentity')
    state = 'PENDING_EXTERNAL' if external else 'RECORDED'
    db.execute('INSERT INTO durai_idempotency.example_order(id,units,state,external_identity) VALUES(%s,%s,%s,%s)',
        (identity,payload['units'],state,external))
    return {'orderId':identity,'units':payload['units'],'state':state,'externalIdentity':external}


class IdempotencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Install only the isolated schema. Keep primary business tables unchanged.
        install_fixture()

    def setUp(self):
        # Sample: Give this test a new operation and request key. Keep prior test rows intact.
        self.operation = 'order.create.'+uid()
        self.key = 'K-'+uid()

    def execute(self, payload=None, handler=order_handler, role='durai_runtime', key=None, operation=None):
        # Sample: Apply K-1 for 100 units through a trusted local handler.
        return execute_request(operation or self.operation,self.key if key is None else key,
            {'units':100} if payload is None else payload,handler,DATABASE,role)

    def count(self, role='durai_runtime'):
        # Sample: Count retained requests in this operation under the selected tenant.
        with transaction(role,DATABASE) as db:
            return db.execute('SELECT count(*) AS n FROM durai_idempotency.request WHERE operation=%s',(self.operation,)).fetchone()['n']

    def test_equal_retry_returns_retained_outcome(self):
        # Sample: Apply K-1 for 100 units twice. Check one order ID and one replay.
        one = self.execute({'units':100,'reference':'O-1'})
        two = self.execute({'reference':'O-1','units':100},handler=lambda db,p:self.fail('Duplicate handler call'))
        self.assertEqual(one['outcome'],two['outcome'])
        self.assertFalse(one['replayed']); self.assertTrue(two['replayed'])
        self.assertEqual(self.count(),1)

    def test_changed_payload_is_rejected(self):
        # Sample: Retain K-1 for 100 units. Reuse K-1 for 200 units. Check refusal and the original result.
        one = self.execute()
        with self.assertRaisesRegex(Conflict,'different payload'):
            self.execute({'units':200})
        self.assertEqual(self.execute()['outcome'],one['outcome'])

    def test_concurrent_duplicate_has_one_effect(self):
        # Sample: Send K-1 from 20 callers. Check one handler call, one order, and equal outcomes.
        gate = threading.Barrier(20)
        calls = []
        def apply():
            # Sample: Wait for 20 callers. Submit the same 100-unit command.
            gate.wait(timeout=15)
            def handler(db,payload):
                # Sample: Hold the winning claim for 100 milliseconds. Record one order.
                calls.append(True); time.sleep(.1)
                return order_handler(db,payload)
            return self.execute(handler=handler)
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(lambda _:apply(),range(20)))
        self.assertEqual(len(calls),1)
        self.assertEqual(sum(not r['replayed'] for r in results),1)
        self.assertTrue(all(r['outcome']==results[0]['outcome'] for r in results))
        self.assertEqual(self.count(),1)
        with transaction('durai_runtime',DATABASE) as db:
            self.assertEqual(db.execute('SELECT count(*) AS n FROM durai_idempotency.example_order WHERE id=%s',
                (results[0]['outcome']['orderId'],)).fetchone()['n'],1)

    def test_wait_timeout_preserves_original_identity(self):
        # Sample: Hold K-1. Time out one duplicate wait. Complete K-1 and retry with the same identity.
        ready, release = threading.Event(), threading.Event()
        def hold(db,payload):
            # Sample: Hold the winning claim until the duplicate wait has stopped.
            ready.set()
            if not release.wait(timeout=10): raise TimeoutError('Sample holder timed out')
            return order_handler(db,payload)
        @contextmanager
        def short_wait(role,database):
            # Sample: Limit this duplicate connection to a 100-millisecond lock wait.
            with transaction(role,database) as db:
                db.execute("SET lock_timeout='100ms'")
                yield db
        with ThreadPoolExecutor(max_workers=1) as pool:
            winner = pool.submit(self.execute,handler=hold)
            try:
                self.assertTrue(ready.wait(timeout=5))
                with patch('durai.idempotency.transaction',short_wait):
                    with self.assertRaises(psycopg.errors.LockNotAvailable):
                        self.execute(handler=lambda db,p:self.fail('Timed out duplicate effect'))
            finally:
                release.set()
            result = winner.result(timeout=5)
        retry = self.execute(handler=lambda db,p:self.fail('Repeated committed effect'))
        self.assertTrue(retry['replayed'])
        self.assertEqual(retry['outcome'],result['outcome'])
        self.assertEqual(self.count(),1)

    def test_lost_response_does_not_repeat_effect(self):
        # Sample: Commit K-1 and discard its response. Retry. Check no new order.
        committed = self.execute()
        def lost_response():
            # Sample: Lose the client response after the database commit.
            raise ConnectionError('Sample response was lost')
        with self.assertRaises(ConnectionError): lost_response()
        retry = self.execute(handler=lambda db,p:self.fail('Repeated effect'))
        self.assertEqual(retry['outcome'],committed['outcome'])
        self.assertTrue(retry['replayed'])

    def test_handler_failure_rolls_back_claim_and_effect(self):
        # Sample: Insert an order and raise before completion. Check no claim or order. Retry successfully.
        inserted = []
        def fail(db,payload):
            # Sample: Insert order O-1. Stop before retaining its outcome.
            result = order_handler(db,payload); inserted.append(result['orderId'])
            raise RuntimeError('Sample failure before commit')
        with self.assertRaises(RuntimeError): self.execute(handler=fail)
        self.assertEqual(self.count(),0)
        with transaction('durai_runtime',DATABASE) as db:
            self.assertFalse(db.execute('SELECT id FROM durai_idempotency.example_order WHERE id=%s',(inserted[0],)).fetchone())
        self.assertFalse(self.execute()['replayed'])

    def test_connection_loss_allows_safe_recovery(self):
        # Sample: Close an uncommitted worker connection after its order insert. Retry with a new connection.
        inserted = []
        def lose(db,payload):
            # Sample: Insert O-1 and close its connection before completion.
            result = order_handler(db,payload); inserted.append(result['orderId']); db.close()
            return result
        with self.assertRaises(psycopg.Error): self.execute(handler=lose)
        self.assertEqual(self.count(),0)
        retry = self.execute()
        self.assertNotEqual(retry['outcome']['orderId'],inserted[0])
        with transaction('durai_runtime',DATABASE) as db:
            self.assertFalse(db.execute('SELECT id FROM durai_idempotency.example_order WHERE id=%s',(inserted[0],)).fetchone())

    def test_tenant_scope_is_enforced(self):
        # Sample: Use K-1 in trading and foreign tenants. Check independent outcomes and no foreign read.
        one = self.execute(); two = self.execute(role='durai_foreign')
        self.assertNotEqual(one['outcome']['orderId'],two['outcome']['orderId'])
        self.assertFalse(two['replayed'])
        with transaction('durai_foreign',DATABASE) as db:
            self.assertFalse(db.execute('SELECT id FROM durai_idempotency.example_order WHERE id=%s',
                (one['outcome']['orderId'],)).fetchone())
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with transaction('durai_foreign',DATABASE) as db:
                db.execute('INSERT INTO durai_idempotency.request(application_id,operation,request_key,request_digest,request_body) '
                    'VALUES(%s,%s,%s,%s,%s)',('trading',self.operation,uid(),'0'*64,Jsonb({'units':1})))

    def test_operation_scope_is_enforced(self):
        # Sample: Use K-1 for reserve and release operations. Check two intended effects.
        one = self.execute(); two = self.execute(operation=self.operation+'.release')
        self.assertNotEqual(one['outcome']['orderId'],two['outcome']['orderId'])
        self.assertFalse(two['replayed'])

    def test_payload_hash_is_not_the_identity(self):
        # Sample: Send equal bodies with K-1 and K-2. Check two separate order IDs.
        one = self.execute(); two = self.execute(key=self.key+'-second')
        self.assertEqual(one['requestDigest'],two['requestDigest'])
        self.assertNotEqual(one['outcome']['orderId'],two['outcome']['orderId'])
        self.assertEqual(self.count(),2)

    def test_digest_collision_does_not_accept_changed_payload(self):
        # Sample: Force equal hashes for 100 and 200 units. Reuse K-1. Check payload mismatch refusal.
        with patch('durai.idempotency.request_digest',return_value='a'*64):
            self.execute()
            with self.assertRaises(Conflict): self.execute({'units':200})

    def test_inputs_and_outcome_are_bounded(self):
        # Sample: Refuse an empty key, non-finite amount, null text, and oversized result. Check no partial order.
        for value in ({'units':float('nan')},{'units':1,'text':'x'*(MAX_BYTES+1)},{'units':1,'text':'\x00'},{1:'bad-key'}):
            with self.assertRaises(ValueError): self.execute(value)
        with self.assertRaises(ValueError): self.execute(key='')
        inserted = []
        def oversized(db,payload):
            # Sample: Insert O-1. Return an oversized result. Check transaction rollback.
            result = order_handler(db,payload); inserted.append(result['orderId'])
            return {'text':'x'*(MAX_BYTES+1)}
        with self.assertRaises(ValueError): self.execute(handler=oversized)
        self.assertEqual(self.count(),0)
        with transaction('durai_runtime',DATABASE) as db:
            self.assertFalse(db.execute('SELECT id FROM durai_idempotency.example_order WHERE id=%s',(inserted[0],)).fetchone())

    def test_unfinished_claim_cannot_commit(self):
        # Sample: Insert a claim without an outcome. Commit. Check refusal and no retained claim.
        with self.assertRaises(psycopg.errors.CheckViolation):
            with closing(connect('durai_runtime',DATABASE)) as db:
                with db:
                    db.execute('INSERT INTO durai_idempotency.request(operation,request_key,request_digest,request_body) '
                        'VALUES(%s,%s,%s,%s)',(self.operation,self.key,'0'*64,Jsonb({'units':1})))
        self.assertEqual(self.count(),0)

    def test_completed_result_cannot_change(self):
        # Sample: Complete K-1. Attempt to overwrite or delete it. Check refusal and the retained outcome.
        one = self.execute()
        with self.assertRaises(psycopg.errors.CheckViolation):
            with transaction('durai_runtime',DATABASE) as db:
                db.execute('UPDATE durai_idempotency.request SET outcome=%s WHERE operation=%s AND request_key=%s',
                    (Jsonb({'state':'CHANGED'}),self.operation,self.key))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with transaction('durai_runtime',DATABASE) as db:
                db.execute('DELETE FROM durai_idempotency.request WHERE operation=%s AND request_key=%s',(self.operation,self.key))
        self.assertEqual(self.execute()['outcome'],one['outcome'])

    def test_terminal_business_failure_is_retained(self):
        # Sample: Retain DECLINED for K-1. Retry. Check the same decline and no new handler call.
        one = self.execute(handler=lambda db,p:{'state':'DECLINED','code':'CAPACITY_UNAVAILABLE'})
        two = self.execute(handler=lambda db,p:self.fail('Repeated declined command'))
        self.assertEqual(one['outcome'],two['outcome']); self.assertTrue(two['replayed'])
        self.assertEqual(self.count(),1)

    def test_external_intent_retains_original_identity(self):
        # Sample: Commit unresolved intent P-1. Retry K-1. Check P-1 and no new provider attempt.
        identity = 'P-'+uid()
        one = self.execute({'units':100,'externalIdentity':identity})
        two = self.execute({'units':100,'externalIdentity':identity},handler=lambda db,p:self.fail('New external attempt'))
        self.assertEqual(one['outcome'],two['outcome'])
        self.assertEqual(two['outcome']['state'],'PENDING_EXTERNAL')
        self.assertEqual(two['outcome']['externalIdentity'],identity)


if __name__=='__main__':
    unittest.main()
