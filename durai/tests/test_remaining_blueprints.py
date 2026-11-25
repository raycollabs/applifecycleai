"""Sample: Reconcile a charge and refund. Post, reverse, and inspect the journal."""
import unittest

import test_capture_refunds_blueprint as funds_fixture

from durai.common import Conflict, digest, uid
from durai.coordination import Coordination
from durai.db import ROOT, connect, transaction
from durai.job_progress import cancel_job, queue_job, report_progress, status
from durai.journal import post_journal, read_journal, reverse_journal
from durai.location_search import nearby
from durai.object_download import download
from durai.payment_funds import balances, create_operation, dispatch, register_direct_payment
from durai.payment_intents import create_intent, dispatch_charge
from durai.settlement import reconcile_statement

DATABASE = 'durai_test'


class RemainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Install the payment dependency and apply the remaining blueprint once.
        funds_fixture.CaptureRefundTests.setUpClass()
        source = (ROOT/'blueprints/transactions/v1.sql').read_bytes()
        checksum = digest(source)
        with connect('postgres',DATABASE) as db:
            if db.execute("SELECT to_regnamespace('durai_transactions') AS id").fetchone()['id'] is None:
                db.execute(source.decode(),prepare=False)
                db.execute('INSERT INTO durai_transactions.schema_version VALUES (1,%s)',(checksum,))
            elif db.execute('SELECT source_digest FROM durai_transactions.schema_version').fetchone()['source_digest'] != checksum:
                raise ValueError('Applied transaction blueprint differs')

    def payment(self, role='durai_runtime'):
        # Sample: Record a confirmed 1000-unit charge for merchant A.
        payment = create_intent('order.'+uid(),1000,'USD',uid(),role=role)['outcome']['intent']['operationId']
        result = dispatch_charge(payment,funds_fixture.payment_fixture.SampleProvider(),role=role)
        register_direct_payment(payment,'merchant-a',role=role)
        return result

    def line(self, result, kind, amount, fee=0):
        # Sample: Report a payment of 1000 and a 10-unit fee. Check a net of 990.
        return {'operationId':result['operationId'],'kind':kind,'currency':'USD','amountMinor':amount,
                'feeMinor':fee,'netMinor':(-amount if kind=='REFUND' else amount)-fee,
                'providerReference':result['providerReference']}

    def test_statement_reconciliation(self):
        # Sample: Match a charge, mismatch a refund, and retain one missing and one unexpected line.
        payment = self.payment()
        payment_id = payment['operationId']
        provider = funds_fixture.FundsProvider()
        refunds = []
        for amount in (600,200,100):
            operation = create_operation('REFUND',payment_id,amount,uid())['outcome']['operation']['operationId']
            refunds.append(dispatch(operation,provider))
        expected = [('CHARGE',payment_id)]+[('REFUND',r['operationId']) for r in refunds]
        unexpected = dict(refunds[0],operationId=uid(),providerReference='other.'+uid())
        statement = [self.line(payment,'CHARGE',1000,10), self.line(refunds[0],'REFUND',600),
                     self.line(refunds[1],'REFUND',100),self.line(unexpected,'REFUND',10)]
        name = uid()
        report = reconcile_statement('merchant-a',name,expected,statement)
        self.assertCountEqual([r['state'] for r in report['findings']],['MATCHED','MATCHED','MISMATCH','MISSING','UNEXPECTED'])
        self.assertEqual(reconcile_statement('merchant-a',name,expected[::-1],statement[::-1]),report)
        with self.assertRaises(Conflict):
            reconcile_statement('merchant-a',name,expected,statement[:-1])
        self.assertEqual(balances(payment_id),{'captured':1000,'refunded':900,'refund_reserved':0})

    def test_statement_scope_and_invalid_data(self):
        # Sample: Refuse a confirmed charge from another tenant, a duplicate line, and a bad net.
        foreign = self.payment('durai_foreign')
        with self.assertRaises(Conflict):
            reconcile_statement('merchant-a',uid(),[('CHARGE',foreign['operationId'])],[])
        line = self.line(foreign,'CHARGE',1000)
        for statement in ([line,line],[dict(line,netMinor=999)]):
            with self.assertRaises(ValueError):
                reconcile_statement('merchant-a',uid(),[],statement)

    def test_journal_balance_and_reversal(self):
        # Sample: Post 100 and -100. Reverse both lines. Refuse a second reversal and an unequal sum.
        lines = [{'account':'cash','signedMinor':100},{'account':'sales','signedMinor':-100}]
        key = uid()
        original = post_journal('USD',lines,key)['outcome']
        self.assertEqual(post_journal('USD',lines,key)['outcome'],original)
        identity = original['journalId']
        reverse = reverse_journal(identity,uid())['outcome']
        self.assertEqual(reverse['reverses'],identity)
        self.assertEqual([x['signedMinor'] for x in reverse['lines']],[-100,100])
        self.assertEqual(read_journal(identity),original)
        with self.assertRaises(Conflict):
            reverse_journal(identity,uid())
        with self.assertRaises(Conflict):
            post_journal('USD',[lines[0],dict(lines[1],signedMinor=-90)],uid())
        with self.assertRaises(KeyError):
            read_journal(identity,role='durai_foreign')

    def test_job_progress_and_cancellation(self):
        # Sample: Queue J-1, claim it, report 25 percent, and cancel it.
        key = uid()
        job = queue_job(key)['outcome']['jobId']
        self.assertEqual(queue_job(key)['outcome']['jobId'],job)
        service = Coordination(DATABASE)
        generation = service.claim(job,'worker-a')
        report_progress(job,'worker-a',generation,25)
        report_progress(job,'worker-a',generation,25)
        self.assertEqual(status(job)['progress']['percent'],25)
        with self.assertRaises(Conflict):
            report_progress(job,'worker-a',generation,10)
        cancel_key = uid()
        cancelled = cancel_job(job,cancel_key)['outcome']
        self.assertEqual(cancel_job(job,cancel_key)['outcome'],cancelled)
        self.assertEqual(status(job)['state'],'CANCELLED')
        with self.assertRaises(Conflict):
            report_progress(job,'worker-a',generation,50)
        with self.assertRaises(Conflict):
            service.commit(job,'worker-a',generation,{})
        with self.assertRaises(KeyError):
            status(job,role='durai_foreign')
        with transaction(database=DATABASE) as db:
            kinds = db.execute('SELECT event_type FROM durai_events.outbox WHERE stream_id=%s ORDER BY sequence',
                               ('job.'+job,)).fetchall()
        self.assertEqual([r['event_type'] for r in kinds],['job.queued','job.progress','job.cancelled'])

    def test_object_download_authority_and_expiry(self):
        # Sample: Give subject A a 60-second link. Refuse subject B without signing.
        identity = uid()
        with connect('postgres',DATABASE) as db:
            db.execute('INSERT INTO durai_transactions.object_grant VALUES (%s,%s,%s,%s)',
                       ('trading','subject-a',identity,'synthetic/object'))
        signed = []

        def sign(key, ttl):
            # Sample: Record the key and duration received by the signer.
            signed.append((key,ttl))
            return 'https://storage.example/object?token=sample'

        self.assertEqual(download('subject-a',identity,sign)['expiresInSeconds'],60)
        with self.assertRaises(KeyError):
            download('subject-b',identity,sign)
        with self.assertRaises(KeyError):
            download('subject-a',identity,sign,role='durai_foreign')
        with self.assertRaises(ValueError):
            download('subject-a',identity,sign,ttl_seconds=301)
        self.assertEqual(signed,[('synthetic/object',60)])

    def test_location_radius_and_scope(self):
        # Sample: Search 1000 meters around the date line. Include the nearest local point.
        near, farther, far, foreign = (uid() for _ in range(4))
        identities = [near,farther,far,foreign]

        def clean():
            # Sample: Remove only the four locations created by this test.
            with connect('postgres',DATABASE) as db:
                db.execute('DELETE FROM durai_transactions.location WHERE location_id=ANY(%s)',(identities,))

        self.addCleanup(clean)
        with connect('postgres',DATABASE) as db:
            latitude = db.execute('SELECT COALESCE(max(latitude),-2)+2 AS lat FROM durai_transactions.location').fetchone()['lat']
            for tenant, identity, longitude in [('trading',near,-179.999),('trading',farther,-179.996),
                                                ('trading',far,-179.9),('foreign',foreign,179.999)]:
                db.execute('INSERT INTO durai_transactions.location VALUES (%s,%s,%s,%s)',(tenant,identity,latitude,longitude))
        rows = nearby(latitude,179.999,1000)
        self.assertEqual([r['location_id'] for r in rows],[near,farther])
        self.assertEqual(nearby(latitude,179.999,1000,limit=1),rows[:1])
        self.assertEqual(nearby(latitude,179.999,rows[0]['distance_meters'],limit=1),rows[:1])
        with self.assertRaises(ValueError):
            nearby(91,0,1000)
