"""Sample: Run 64 local orders, recover two interrupted workers, and verify backup and replica results."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import sys
from threading import Barrier
import time

from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from durai.common import digest, uid
from durai.context_store import ContextStore
from durai.db import connect, use_server
from durai.db_sandbox import EphemeralDatabase, docker
from measure_replay_qualification import measure_replay
from reservation_pilot_api import request, serve
from reservation_pilot_broker import Broker, STREAM, consume_entries, relay

PLAN = 'plan_durai_reservation_staging_pilot_2026-11-19'
DB = 'durai_test'
TABLES = (('durai_reservations','seat'),('durai_reservations','seat_order'),
          ('durai_idempotency','request'),('durai_events','stream'),('durai_events','outbox'),
          ('durai_staging','effect'))


def backup(container, label):
    # Sample: Create an isolated PostgreSQL archive and record its digest privately.
    value = subprocess.check_output(['docker','exec',container,'pg_dump','-U','supabase_admin','-Fc',DB],timeout=60)
    if not value.startswith(b'PGDMP'):
        raise ValueError('Invalid backup archive')
    path = ROOT/'private'/('reservation-'+label+'-'+uid()+'.dump')
    path.write_bytes(value)
    return value


def restore(container, value):
    # Sample: Restore an archive to a fresh durai_restore database and keep the duration.
    name = 'durai_restore_'+uid().replace('-','')[:12]
    with connect('postgres','postgres',autocommit=True) as db:
        db.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
    started = time.perf_counter()
    job = subprocess.run(['docker','exec','-i',container,'pg_restore','-U','supabase_admin','--exit-on-error',
                          '--single-transaction','-d',name],input=value,capture_output=True,timeout=120)
    seconds = round(time.perf_counter()-started,3)
    if job.returncode:
        (ROOT/'private'/('restore-'+name+'.log')).write_bytes(job.stderr)
        raise ValueError('Staging archive restore failed')
    return name,seconds


def snapshot(database):
    # Sample: Read the order, request, event, and effect counts in one snapshot.
    with connect('postgres',database) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        output = {}
        for schema,table in TABLES:
            rows = db.execute(sql.SQL('SELECT to_jsonb(t)::text AS payload FROM {} t ORDER BY 1')
                              .format(sql.Identifier(schema,table))).fetchall()
            output[schema+'.'+table] = {'rows':len(rows),'digest':digest([r['payload'] for r in rows])}
        return output


def apply_schema():
    # Sample: Apply the pinned idempotency, event, seat, and effect schemas to the empty staging target.
    names = ('idempotency/v1.sql','events/v1.sql','reservations/v1.sql','reservations/staging.sql')
    with connect('postgres',DB) as db:
        for name in names:
            db.execute((ROOT/'blueprints'/name).read_text(),prepare=False)
    return {name:digest((ROOT/'blueprints'/name).read_bytes()) for name in names}


def seed(seats):
    # Sample: Register A1 as a synthetic seat for the trading tenant.
    with connect('postgres',DB) as db:
        for seat in seats:
            db.execute('INSERT INTO durai_reservations.seat VALUES (%s,%s)',('trading',seat))


def hold(base, token, seat, ttl=600, key=None):
    # Sample: Submit a hold command and retain its HTTP body and response.
    body = {'seatId':seat,'ttlSeconds':ttl,'requestKey':key or uid()}
    code,result = request(base,'/holds',token,body)
    if code!=200:
        raise ValueError('Hold command failed')
    return body,result


def move(base, token, order, action):
    # Sample: Confirm O-1 through HTTP with revision 1.
    code,result = request(base,'/holds/'+action,token,
                          {'orderId':order['orderId'],'revision':order['revision'],'requestKey':uid()})
    if code!=200:
        raise ValueError('Transition command failed')
    return result['outcome']


def exercise_api(base, token):
    # Sample: Race the last seat and exercise all three terminal transitions over HTTP.
    seats = ['pilot.last','pilot.release','pilot.expire']
    seed(seats)
    gate = Barrier(2)

    def last(_):
        # Sample: Wait for both callers and reserve the last seat.
        gate.wait(timeout=10)
        return hold(base,token,seats[0])

    if request(base,'/holds','wrong',{'seatId':seats[0]})[0]!=401:
        raise ValueError('Authorization boundary failed')
    with ThreadPoolExecutor(2) as pool:
        contenders = list(pool.map(last,range(2)))
    if sorted(r['outcome']['result'] for _,r in contenders)!=['HELD','SEAT_UNAVAILABLE']:
        raise ValueError('Last-seat race failed')
    body,winner = next((b,r) for b,r in contenders if r['outcome']['result']=='HELD')
    response = request(base,'/holds',token,body)
    if response[0]!=200 or not response[1]['replayed'] or response[1]['outcome']!=winner['outcome']:
        raise ValueError('Hold retry differs')
    order = winner['outcome']['order']
    confirmed = move(base,token,order,'confirm')['order']
    if request(base,'/orders/'+order['orderId'],token)!=(200,confirmed) or confirmed['orderStatus']!='CONFIRMED':
        raise ValueError('Confirmed order read differs')
    other = hold(base,token,seats[1])[1]['outcome']['order']
    if move(base,token,other,'release')['order']['orderStatus']!='CANCELLED':
        raise ValueError('Release state differs')
    expiring = hold(base,token,seats[2],1)[1]['outcome']['order']
    with connect('durai_runtime',DB) as db:
        db.execute('SELECT pg_sleep(GREATEST(0,extract(epoch FROM (%s::timestamptz-clock_timestamp()))))',
                   (expiring['expiresAt'],))
    if move(base,token,expiring,'expire')['order']['orderStatus']!='EXPIRED':
        raise ValueError('Expiry state differs')
    return winner['outcome']['event']


def child(name, action, stream=None):
    # Sample: Start a separate relay or consumer process and check its expected exit.
    options = [sys.executable,str(Path(__file__).resolve()),'--worker',action,'--broker',name]
    if stream:
        options.extend(['--order-stream',stream])
    return subprocess.run(options,timeout=40,capture_output=True,text=True)


def exercise_restarts(broker, event):
    # Sample: Lose both acknowledgment boundaries and verify one PostgreSQL projection effect.
    first = child(broker.name,'relay-stop',event['streamId'])
    if first.returncode!=71:
        raise ValueError('Relay did not stop after publishing: '+first.stderr[-500:])
    docker('restart',broker.name)
    broker.wait_ready()
    if broker.run('XLEN',STREAM)!=1:
        raise ValueError('Redis append-only restart lost the event')
    time.sleep(1.1)
    next_relay = child(broker.name,'relay',event['streamId'])
    if next_relay.returncode or json.loads(next_relay.stdout)['relayed']!=1:
        raise ValueError('Replacement relay failed')
    entries = broker.run('XRANGE',STREAM,'-','+')
    if len(entries)!=2 or any(json.loads(fields[1])['eventId']!=event['eventId'] for _,fields in entries):
        raise ValueError('Retry changed broker event identity')
    first_consumer = child(broker.name,'consumer-stop')
    if first_consumer.returncode!=72 or broker.pending()!=1:
        raise ValueError('Consumer did not stop after committing: '+first_consumer.stderr[-500:])
    recovered = consume_entries(broker,'replacement',recover=True)
    duplicate = consume_entries(broker,'replacement')
    if len(duplicate)!=1 or not duplicate[0]['replayed']:
        raise ValueError('Republished event repeated its local effect')
    if len(recovered)!=1 or not recovered[0]['replayed']:
        raise ValueError('Pending entry did not resume with its retained outcome')
    with connect('durai_runtime',DB) as db:
        effects = db.execute('SELECT count(*) AS n FROM durai_staging.effect WHERE event_id=%s',
                             (event['eventId'],)).fetchone()['n']
    if effects!=1:
        raise ValueError('Projection applied twice')
    return {'relayExit':71,'consumerExit':72,'brokerRestartRetainedEntry':True,'duplicateEntries':2,
            'pendingEntryRecovered':True,'effects':effects}


def backlog():
    # Sample: Read the unconfirmed event count and oldest age from PostgreSQL.
    with connect('durai_runtime',DB) as db:
        row = db.execute("""
            SELECT count(*) AS count, COALESCE(EXTRACT(epoch FROM clock_timestamp()-min(created_at)),0) AS age
            FROM durai_events.outbox WHERE state<>'PUBLISHED'
        """).fetchone()
    return {'count':row['count'],'oldestSeconds':round(float(row['age']),3)}


def drain(broker):
    # Sample: Publish and consume until the staged outbox and stream both drain.
    start = time.perf_counter()
    for _ in range(32):
        count = relay(broker)
        while consume_entries(broker,'projection'):
            pass
        if not count:
            break
    if backlog()['count'] or broker.pending():
        raise ValueError('Pilot event backlog did not drain')
    return round(time.perf_counter()-start,3)


def workload(base, token, broker):
    # Sample: Hold 64 seats in eight clients, then confirm or release each seat.
    prefix = 'load.'+uid().replace('-','')
    seats = [prefix+'.'+str(index) for index in range(64)]
    seed(seats)

    def work(index):
        # Sample: Time one hold and one terminal HTTP request.
        start = time.perf_counter()
        held = hold(base,token,seats[index])[1]['outcome']
        middle = time.perf_counter()
        end = move(base,token,held['order'],'confirm' if index%2==0 else 'release')
        expected = 'CONFIRMED' if index%2==0 else 'CANCELLED'
        if end['order']['orderStatus']!=expected:
            raise ValueError('Workload transition differs')
        return [(middle-start)*1000,(time.perf_counter()-middle)*1000]

    start = time.perf_counter()
    with ThreadPoolExecutor(8) as pool:
        samples = sorted(t for row in pool.map(work,range(64)) for t in row)
    elapsed = time.perf_counter()-start
    queued = backlog()
    if queued['count']!=128:
        raise ValueError('Expected exactly one event per HTTP command')
    drain_seconds = drain(broker)
    return prefix,{'commands':128,'clients':8,'heldOrders':64,'confirmedOrders':32,'releasedOrders':32,
                   'elapsedSeconds':round(elapsed,4),'commandsPerSecond':round(128/elapsed,2),
                   'medianMs':round(statistics.median(samples),2),'p95Ms':round(samples[121],2),
                   'beforeDrain':queued,'afterDrain':backlog(),'drainSeconds':drain_seconds}


def fingerprint(reader, prefix):
    # Sample: Compare all workload orders, events, and consumer effects on the standby.
    query = """
        WITH orders AS (
            SELECT * FROM durai_reservations.seat_order WHERE application_id='trading' AND seat_id LIKE %s
        ), events AS (
            SELECT e.* FROM durai_events.outbox e JOIN orders o
              ON (e.application_id,e.stream_id)=(o.application_id,'order.'||o.order_id::text)
        ), effects AS (
            SELECT p.* FROM durai_staging.effect p JOIN events e
              ON (p.application_id,p.event_id)=(e.application_id,e.event_id)
        )
        SELECT kind,body FROM (
            SELECT 'order' AS kind,to_jsonb(o)::text AS body FROM orders o
            UNION ALL SELECT 'event',to_jsonb(e)::text FROM events e
            UNION ALL SELECT 'effect',to_jsonb(p)::text FROM effects p
        ) payload ORDER BY kind,body
    """
    rows = reader.execute(query,(prefix+'.%',)).fetchall()
    return query,[[row['kind'],row['body']] for row in rows]


def perform():
    # Sample: Run staging on a fresh database and a pinned private broker.
    scratch = ROOT/'private'
    scratch.mkdir(exist_ok=True)
    ctx = ContextStore()
    status = ctx.read(PLAN,'status')
    if status['value']['phase']=='1':
        ctx.set_status(PLAN,dict(status['value'],phase='2',nextAction='Run the local staging stack and repeat the recovery checks.'),
                   status['revision'],'staging-execution')
    image = docker('image','inspect','redis:7.4-alpine','--format','{{.Id}}')
    with EphemeralDatabase(str(scratch)) as db:
        docker('network','connect','bridge',db.name)
        db.wait()
        target = {'name':db.name,'port':db.port,'settings':str(db.settings_path.resolve()),'host':'127.0.0.1'}
        os.environ.update(DURAI_DB_HOST=target['host'],DURAI_DB_PORT=str(db.port),DURAI_SETTINGS=target['settings'])
        descriptor = scratch/'reservation-staging-target.json'
        descriptor.write_text(json.dumps(target),encoding='utf-8')
        broker = Broker('durai-stage-redis-'+uid().replace('-','')[:12])
        try:
            with connect('postgres',DB) as connection:
                connection.execute("CREATE TABLE public.pilot_baseline (marker text PRIMARY KEY)")
                connection.execute("INSERT INTO public.pilot_baseline VALUES ('before-seat-schema')")
            pre = backup(db.name,'baseline')
            schemas = apply_schema()
            broker.start(image,db.network)
            token = secrets.token_hex(24)
            with serve(token) as endpoint:
                event = exercise_api(endpoint,token)
                fault = exercise_restarts(broker,event)
                initial_drain = drain(broker)
                replay = measure_replay(target,lambda:workload(endpoint,token,broker),fingerprint,320)
            origin = snapshot(DB)
            if origin['durai_staging.effect']['rows']!=134:
                raise ValueError('Expected one effect for each of 134 unique events')
            after = backup(db.name,'completed')
            kept,restore_seconds = restore(db.name,after)
            if snapshot(kept)!=origin:
                raise ValueError('Completed staging restore differs')
            baseline,rollback_seconds = restore(db.name,pre)
            with connect('postgres',baseline) as check:
                row = check.execute("""
                    SELECT (SELECT marker FROM public.pilot_baseline) AS marker,
                           to_regnamespace('durai_reservations') IS NULL
                           AND to_regnamespace('durai_staging') IS NULL
                           AND to_regnamespace('durai_idempotency') IS NULL
                           AND to_regnamespace('durai_events') IS NULL AS prior_schema
                """).fetchone()
                if row!={'marker':'before-seat-schema','prior_schema':True}:
                    raise ValueError('Pre-change rollback restore differs')
            return {'state':'PASS','broker':'Redis Streams','brokerImageId':image,'databaseImageId':docker(
                    'inspect',db.name,'--format','{{.Image}}'),'schemas':schemas,
                    'api':{'lastSeatWinner':1,'retryRetained':True,'confirmed':True,'released':True,'expired':True,
                           'unauthorizedRefused':True},
                    'restarts':fault,'initialDrainSeconds':initial_drain,'replica':replay,
                    'restore':{'archiveDigest':digest(after),'sections':origin,'seconds':restore_seconds},
                    'rollback':{'archiveDigest':digest(pre),'priorSchemaVerified':True,'seconds':rollback_seconds}}
        finally:
            subprocess.run(['docker','rm','-fv',broker.name],capture_output=True,timeout=35)
            descriptor.unlink(missing_ok=True)


def main():
    # Sample: Run the complete controller or the selected disposable worker action.
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker',choices=['relay-stop','relay','consumer-stop'])
    parser.add_argument('--broker')
    parser.add_argument('--order-stream')
    args = parser.parse_args()
    if args.worker:
        target = json.loads((ROOT/'private/reservation-staging-target.json').read_text())
        if (target['host']!='127.0.0.1' or target['port']==54410
                or str(target['port'])!=os.environ.get('DURAI_DB_PORT')
                or target['settings']!=os.environ.get('DURAI_SETTINGS')):
            raise ValueError('Refuse a non-pilot worker target')
        broker = Broker(args.broker)
        if args.worker=='consumer-stop':
            consume_entries(broker,'stopped',lose_ack=True)
        else:
            print(json.dumps({'relayed':relay(broker,stream=args.order_stream,lose_ack=args.worker=='relay-stop')}))
        return
    result = perform()
    path = ROOT/'receipts'/('reservation-staging-'+uid()+'.json')
    result['fileDigests']={name:digest((ROOT/name).read_bytes()) for name in (
        'tools/run_reservation_staging.py','tools/reservation_pilot_api.py','tools/reservation_pilot_broker.py',
        'tools/measure_replay_qualification.py','durai/reservations.py','durai/inbox.py','durai/outbox.py')}
    path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
    with use_server('127.0.0.1',54410,ROOT/'private/local.json'):
        ref = ContextStore(role='durai_evaluator').verify_artifact('receipt',path.relative_to(ROOT).as_posix(),digest(path.read_bytes()))
        ContextStore().append(PLAN,'evidence',{'references':[ref]},path.stem)
    print(json.dumps({'receipt':path.as_posix(),'state':result['state'],'replica':result['replica'],
                      'restoreSeconds':result['restore']['seconds'],'rollbackSeconds':result['rollback']['seconds']}))


if __name__=='__main__':
    main()
