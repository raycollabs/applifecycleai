"""Sample: Hold 20 units, measure the requests, and restore the isolated database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from durai.common import digest, uid
from durai.context_store import ContextStore
from durai.coordination import Coordination
from durai.db import connect, transaction, use_server
from durai.inventory import hold_units, read_stock
from durai.job_progress import queue_job
from durai.payment_intents import create_intent, dispatch_charge

DATABASE = 'durai_test'
PLAN = 'plan_durai_phase4_5_local_qualification_2026-11-18'
TABLES = (
    ('durai_inventory','stock'), ('durai_inventory','item_hold'), ('durai_idempotency','request'),
    ('durai_reservations','seat_order'), ('durai_events','outbox'), ('durai_payments','intent'),
    ('durai_payments','funds'), ('durai_payments','funds_operation'), ('durai_transactions','settlement'),
    ('durai_transactions','journal'), ('durai_transactions','object_grant'), ('durai_transactions','location'),
    ('durai_coordination','job'),
)


def sample_inventory():
    # Sample: Reserve 20 units with four workers. Check that no units remain available.
    item = 'ops.'+uid()
    with connect('postgres',DATABASE) as db:
        db.execute('INSERT INTO durai_inventory.stock(application_id,item_id,capacity,available) VALUES (%s,%s,20,20)',
                   ('trading',item))

    def run(_):
        # Sample: Hold a single unit and measure the command duration.
        started = time.perf_counter()
        outcome = hold_units(item,1,600,uid())['outcome']
        if outcome['result'] != 'HELD':
            raise ValueError('Synthetic stock result differs')
        return (time.perf_counter()-started)*1000

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        times = list(pool.map(run,range(20)))
    elapsed = time.perf_counter()-started
    if read_stock(item) != {'capacity':20,'available':0,'held':20,'allocated':0}:
        raise ValueError('Synthetic stock balance differs')
    return {'commands':20,'concurrency':4,'elapsedSeconds':round(elapsed,4),
            'commandsPerSecond':round(20/elapsed,2),'medianMs':round(statistics.median(times),2),
            'p95Ms':round(sorted(times)[18],2),'maximumMs':round(max(times),2)}


class MissingProvider:
    def charge(self, intent):
        # Sample: Lose the provider connection for the synthetic payment.
        raise TimeoutError('Sample provider response was lost')


def inspect_health():
    # Sample: Check an unresolved payment, a stale owner, and a waiting lock on the test database.
    identity = create_intent('ops.'+uid(),100,'USD',uid())['outcome']['intent']['operationId']
    dispatch_charge(identity,MissingProvider())
    job = queue_job(uid())['outcome']['jobId']
    Coordination(DATABASE).claim(job,'ops-worker',seconds=1)
    with transaction(database=DATABASE) as db:
        db.execute('SELECT pg_sleep(1.05)')
        signals = db.execute("""
            SELECT (SELECT count(*) FROM durai_events.outbox WHERE state<>'PUBLISHED') AS queued,
                   (SELECT COALESCE(EXTRACT(epoch FROM (clock_timestamp()-min(created_at))),0)
                        FROM durai_events.outbox WHERE state<>'PUBLISHED') AS queue_age_seconds,
                   (SELECT count(*) FROM durai_events.outbox WHERE attempts>0) AS attempted,
                   (SELECT count(*) FROM durai_events.outbox WHERE attempts>1) AS retried,
                   (SELECT count(*) FROM durai_payments.intent WHERE state='UNCERTAIN')
                    +(SELECT count(*) FROM durai_payments.funds_operation WHERE state='UNCERTAIN') AS unresolved,
                   (SELECT count(*) FROM durai_transactions.settlement s,
                        jsonb_array_elements(s.report->'findings') f WHERE f->>'state'<>'MATCHED') AS settlement_differences,
                   (SELECT count(*) FROM durai_coordination.job
                        WHERE state='RUNNING' AND lease_until<=clock_timestamp()) AS expired_owners
        """).fetchone()
    signals['queue_age_seconds'] = float(signals['queue_age_seconds'])
    signals['retried_event_fraction'] = signals['retried']/signals['attempted'] if signals['attempted'] else None
    if not signals['unresolved'] or not signals['expired_owners'] or not signals['queued']:
        raise ValueError('Synthetic signal sample is incomplete')

    name = 'durai-lock-probe-'+uid()

    def wait_on_job():
        # Sample: Wait on the locked synthetic job row from a separate session.
        with connect('durai_runtime',DATABASE,application_name=name) as db:
            db.execute("SET lock_timeout='5s'")
            return db.execute('SELECT id FROM durai_coordination.job WHERE id=%s FOR UPDATE',(job,)).fetchone()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction(database=DATABASE) as first:
            first.execute('SELECT id FROM durai_coordination.job WHERE id=%s FOR UPDATE',(job,))
            future = pool.submit(wait_on_job)
            deadline = time.monotonic()+3
            blocked = False
            with connect('postgres',DATABASE,autocommit=True) as admin:
                while not blocked and time.monotonic()<deadline:
                    blocked = admin.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=%s "
                                            "AND wait_event_type='Lock') AS waiting",(name,)).fetchone()['waiting']
                    if not blocked:
                        time.sleep(.03)
        if not blocked or future.result(timeout=5)['id'] != job:
            raise ValueError('Controlled lock wait was not observed')

    with connect('postgres',DATABASE) as db:
        replica = db.execute('SELECT count(*) AS count, max(pg_wal_lsn_diff(pg_current_wal_lsn(),replay_lsn)) AS lag '
                             'FROM pg_stat_replication').fetchone()
    signals.update(controlled_lock_wait_observed=blocked,replica_count=replica['count'],
                   replica_lag_bytes=int(replica['lag']) if replica['lag'] is not None else None)
    return signals


def fingerprint(database):
    # Sample: Read ordered rows from both databases. Compare counts and content digests.
    result = {}
    with connect('postgres',database) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        for schema, table in TABLES:
            query = sql.SQL('SELECT to_jsonb(t)::text AS row FROM {} t ORDER BY 1').format(sql.Identifier(schema,table))
            rows = [r['row'] for r in db.execute(query)]
            result[schema+'.'+table] = {'rows':len(rows),'digest':digest(rows)}
    return result


def restore_and_compare(container):
    # Sample: Restore the isolated backup into a new database and check exact row digests.
    fresh = 'durai_restore_'+uid().replace('-','')[:12]
    origin = fingerprint(DATABASE)
    started = time.perf_counter()
    archive = subprocess.check_output(['docker','exec',container,'pg_dump','-U','supabase_admin','-Fc',DATABASE],timeout=60)
    backup_seconds = time.perf_counter()-started
    if not archive.startswith(b'PGDMP'):
        raise ValueError('Backup archive format differs')
    backup = ROOT/'private'/('ops-'+fresh+'.dump')
    backup.write_bytes(archive)
    with connect('postgres','postgres',autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(fresh)))
    started = time.perf_counter()
    restored = subprocess.run(['docker','exec','-i',container,'pg_restore','-U','supabase_admin',
                               '--exit-on-error','--single-transaction','-d',fresh],
                              input=archive,capture_output=True,timeout=120)
    restore_seconds = time.perf_counter()-started
    if restored.returncode:
        (ROOT/'private'/('ops-'+fresh+'-restore.log')).write_bytes(restored.stderr)
        raise ValueError('Isolated restore failed. Read the private restore log.')
    result = fingerprint(fresh)
    if result != origin:
        raise ValueError('Restored table counts or digests differ')
    return {'database':fresh,'backupDigest':digest(archive),'backupBytes':len(archive),
            'backupSeconds':round(backup_seconds,3),'restoreSeconds':round(restore_seconds,3),'tables':result}


def main():
    # Sample: Check the isolated server identity and retain one operating receipt.
    target = json.loads((ROOT/'private/context-isolation.json').read_text())
    if target['host']!='127.0.0.1' or target['port']==54410 or not target['name'].startswith('durai-eval-db-'):
        raise ValueError('Refuse a shared operating target')
    ports = subprocess.check_output(['docker','port',target['name'],'5432/tcp'],text=True)
    if '127.0.0.1:'+str(target['port']) not in ports:
        raise ValueError('Disposable server differs')
    os.environ.update(DURAI_DB_HOST=target['host'],DURAI_DB_PORT=str(target['port']),DURAI_SETTINGS=target['settings'])
    report = {'state':'PASS','server':target['name'],'checkedAt':datetime.now(timezone.utc).isoformat(),
              'scriptDigest':digest(Path(__file__).read_bytes()),'inventory':sample_inventory(),'signals':inspect_health(),
              'recovery':restore_and_compare(target['name'])}
    path = ROOT/'receipts'/('transaction-operating-'+uid()+'.json')
    path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n')
    with use_server('127.0.0.1',54410,ROOT/'private/local.json'):
        ref = ContextStore(role='durai_evaluator').verify_artifact('receipt',path.relative_to(ROOT).as_posix(),digest(path.read_bytes()))
        ContextStore().append(PLAN,'evidence',{'references':[ref]},path.stem)
    print(json.dumps({'receipt':str(path),'inventory':report['inventory'],'signals':report['signals'],
                      'restoreSeconds':report['recovery']['restoreSeconds'],'tables':len(TABLES)}))


if __name__=='__main__':
    main()
