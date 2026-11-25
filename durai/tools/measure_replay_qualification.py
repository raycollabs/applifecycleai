"""Sample: Pause standby replay, commit 64 holds, and verify the restored balance after replay."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import sys
import time

from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from durai.common import digest, uid
from durai.context_store import ContextStore
from durai.db import connect, use_server
from durai.db_sandbox import IMAGE, docker
from durai.inventory import change_hold, hold_units, read_stock
from verify_protected_transactions import isolated_target, PLAN


def standby_shell(name, command, timeout=60):
    # Sample: Run pg_is_in_recovery on the disposable standby.
    return docker('exec','-u','postgres',name,'sh','-ec',command,timeout=timeout)


def write_container(name, path, content):
    # Sample: Write the disposable replication configuration and keep its bytes private.
    subprocess.run(['docker','exec','-i','-u','postgres',name,'sh','-ec',
                    'umask 077; cat > "$1.next"; mv "$1.next" "$1"','write',path],
                   input=content,check=True,timeout=20)


def standby_query(name, query):
    # Sample: Read the standby recovery state through its local socket.
    return docker('exec','-u','postgres',name,'psql','-U','supabase_admin','-d','durai_test',
                  '-Atq','-v','ON_ERROR_STOP=1','-c',query,timeout=15)


def poll(check, seconds=45):
    # Sample: Poll a standby until its receiver connects or the timer expires.
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(.15)
    raise TimeoutError('Replication observation timed out')


def replay_row(reader, name):
    # Sample: Read the WAL byte distance for the designated replica.
    row = reader.execute("""
        SELECT application_name,state,replay_lsn::text,
               pg_wal_lsn_diff(pg_current_wal_lsn(),replay_lsn)::bigint AS lag
        FROM pg_stat_replication WHERE application_name=%s AND replay_lsn IS NOT NULL
    """,(name,)).fetchone()
    return row if row and row['state']=='streaming' else None


def mixed_workload():
    # Sample: Hold units on four items. Confirm half and release half.
    prefix = 'replay.'+uid().replace('-','')
    items = [prefix+'.'+str(i) for i in range(4)]
    with connect('postgres','durai_test') as db:
        for item in items:
            db.execute("INSERT INTO durai_inventory.stock(application_id,item_id,capacity,available) VALUES ('trading',%s,16,16)",
                       (item,))

    def work(index):
        # Sample: Hold one unit and finish its order. Record both command times.
        start = time.perf_counter()
        result = hold_units(items[index%4],1,600,uid())['outcome']
        held = time.perf_counter()
        if result['result'] != 'HELD':
            raise ValueError('Synthetic hold was unexpectedly unavailable')
        action = 'confirm' if (index//4)%2==0 else 'release'
        finished = change_hold(action,result['hold']['holdId'],1,uid())['outcome']
        if finished['result'] != ('CONFIRMED' if action=='confirm' else 'RELEASED'):
            raise ValueError('Synthetic transition differs')
        end = time.perf_counter()
        return [(held-start)*1000,(end-held)*1000]

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        samples = sorted(sample for pair in pool.map(work,range(64)) for sample in pair)
    elapsed = time.perf_counter()-start
    for item in items:
        if read_stock(item) != {'capacity':16,'available':8,'held':0,'allocated':8}:
            raise ValueError('Mixed stock totals differ')
    return prefix, {'items':4,'holds':64,'confirms':32,'releases':32,'commands':128,'concurrency':8,
                    'elapsedSeconds':round(elapsed,4),'commandsPerSecond':round(128/elapsed,2),
                    'medianMs':round(statistics.median(samples),2),'p95Ms':round(samples[121],2)}


def primary_fingerprint(reader, prefix):
    # Sample: Compare the four stock rows and the matching hold rows.
    query = """
        SELECT kind, body FROM (
          SELECT 'stock' AS kind,to_jsonb(s)::text AS body FROM durai_inventory.stock s
            WHERE application_id='trading' AND item_id LIKE %s
          UNION ALL
          SELECT 'hold',to_jsonb(h)::text FROM durai_inventory.item_hold h
            WHERE application_id='trading' AND item_id LIKE %s
        ) data ORDER BY kind,body
    """
    rows = reader.execute(query,(prefix+'.%',prefix+'.%')).fetchall()
    return query, [[r['kind'],r['body']] for r in rows]


def measure_replay(target, workload=mixed_workload, fingerprint=primary_fingerprint, expected_rows=68):
    # Sample: Start a physical standby and check the paused and resumed replay positions.
    primary = target['name']
    networks = json.loads(docker('inspect','--format','{{json .NetworkSettings.Networks}}',primary))
    private = [name for name in networks if name.startswith('durai-eval-net-')
               and docker('network','inspect','--format','{{.Internal}}',name)=='true']
    if len(private)!=1:
        raise ValueError('Disposable server lacks an isolated network')
    name = 'durai-standby-'+uid().replace('-','')[:12]
    replicator = name.replace('-','_')
    secret = secrets.token_hex(24)
    hba = '/var/lib/postgresql/data/pgdata/pg_hba.conf'
    original = subprocess.check_output(['docker','exec',primary,'cat',hba],timeout=20)
    started = False
    changed = False
    role_created = False
    try:
        docker('run','-d','--pull','never','--name',name,'--network',private[0],
               '--label','com.durai.owner=task-evaluator','--tmpfs','/var/lib/postgresql/data:rw,size=512m',
               '--entrypoint','sleep',IMAGE,'infinity')
        started = True
        addresses = json.loads(docker('inspect','--format','{{json .NetworkSettings.Networks}}',name))
        address = addresses[private[0]]['IPAddress']
        if not address or any(ch not in '0123456789.' for ch in address):
            raise ValueError('Standby address is unavailable')
        with connect('postgres','postgres',autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE ROLE {} WITH LOGIN REPLICATION PASSWORD {}').format(
                sql.Identifier(replicator),sql.Literal(secret)))
        role_created = True
        entry = ('host replication '+replicator+' '+address+'/32 scram-sha-256\n').encode('ascii')
        changed = True
        write_container(primary,hba,entry+original)
        with connect('postgres','postgres',autocommit=True) as admin:
            admin.execute('SELECT pg_reload_conf()')
        write_container(name,'/tmp/durai-repl-pass',
                        ('durai-db:5432:*:'+replicator+':'+secret+'\n').encode('ascii'))
        docker('exec','-u','0',name,'chown','postgres:postgres','/var/lib/postgresql/data')
        standby_shell(name,'pg_basebackup -d "host=durai-db port=5432 connect_timeout=5 user='+replicator+
                      ' application_name='+name+' passfile=/tmp/durai-repl-pass" '
                      '-D /var/lib/postgresql/data/pgdata --checkpoint=fast -X stream -R -w',timeout=90)
        standby_shell(name,'pg_ctl -D /var/lib/postgresql/data/pgdata -l /tmp/durai-standby.log '
                      '-o "-c shared_preload_libraries=age -c hot_standby=on" -w start')
        if standby_query(name,'SELECT pg_is_in_recovery()')!='t':
            raise ValueError('Replica is not in recovery')
        with connect('postgres','durai_test',autocommit=True) as reader:
            poll(lambda:replay_row(reader,name))
            standby_query(name,'SELECT pg_wal_replay_pause()')
            poll(lambda:standby_query(name,'SELECT pg_get_wal_replay_pause_state()')=='paused')
            try:
                prefix, sample = workload()
                end_lsn = reader.execute('SELECT pg_current_wal_lsn()::text AS pos').fetchone()['pos']
                paused = replay_row(reader,name)
                if not paused or paused['lag']<=0:
                    raise ValueError('Paused replica showed no WAL backlog')
            finally:
                standby_query(name,'SELECT pg_wal_replay_resume()')
            start = time.perf_counter()
            poll(lambda:reader.execute('SELECT EXISTS(SELECT 1 FROM pg_stat_replication '
                                       'WHERE replay_lsn >= %s::pg_lsn AND application_name=%s) AS caught',(end_lsn,name)).fetchone()['caught'])
            catch_up = time.perf_counter()-start
            after = replay_row(reader,name)
            query, origin = fingerprint(reader,prefix)
            with reader.cursor() as cursor:
                fixed = sql.SQL(query).format()
                pattern = sql.Literal(prefix+'.%').as_string(cursor)
                command = 'SELECT json_build_array(kind,body) FROM ('+fixed.as_string(cursor).replace('%s',pattern)+') point ORDER BY kind,body'
            replica = [json.loads(line) for line in standby_query(name,command).splitlines()]
            if origin != replica or len(origin)!=expected_rows:
                raise ValueError('Replay data digests differ')
        return {'sample':sample,'pausedWalLagBytes':paused['lag'],'replayCatchUpSeconds':round(catch_up,3),
                'afterWalLagBytes':after['lag'],'verifiedRows':len(origin),'replayedDigest':digest(replica)}
    finally:
        if started:
            docker('rm','-f',name)
        if changed:
            write_container(primary,hba,original)
        if changed or role_created:
            with connect('postgres','postgres',autocommit=True) as admin:
                admin.execute('SELECT pg_reload_conf()')
                if role_created:
                    admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(replicator)))


def main():
    # Sample: Measure isolated WAL replay and retain the operating receipt.
    target = isolated_target()
    os.environ.update(DURAI_DB_HOST=target['host'],DURAI_DB_PORT=str(target['port']),DURAI_SETTINGS=target['settings'])
    result = measure_replay(target)
    data = {'state':'PASS','server':target['name'],'scriptDigest':digest(Path(__file__).read_bytes()),**result}
    path = ROOT/'receipts'/('transaction-replay-'+uid()+'.json')
    path.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8',newline='\n')
    with use_server('127.0.0.1',54410,ROOT/'private/local.json'):
        ref = ContextStore(role='durai_evaluator').verify_artifact('receipt',path.relative_to(ROOT).as_posix(),digest(path.read_bytes()))
        ContextStore().append(PLAN,'evidence',{'references':[ref]},path.stem)
    print(json.dumps({'receipt':str(path),**result}))


if __name__=='__main__':
    main()
