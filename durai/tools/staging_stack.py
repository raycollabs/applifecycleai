"""Sample: Start stack durai-stage-0a1b2c3d4e5f, export its outcomes, snapshot it, and remove it."""
import argparse
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from durai.common import digest
from durai.db_sandbox import IMAGE as POSTGRES_IMAGE, ROLES

PROJECT = re.compile(r'durai-stage-[0-9a-f]{12}')
SEAT = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}')
STAGING = ROOT/'private'/'staging'
REDIS_IMAGE = 'sha256:e7b0b929eb5b1e7b18f4662e190ceeac481b72e35dfede063b09a4ce8beac79f'
CADDY_IMAGE = 'sha256:5813f7eb9b94fac4970f9f576695fe1ed1d53d41586fd3eaa350caf7c4627d9c'
DATA = '/var/lib/postgresql/data'
PGDATA = DATA+'/pgdata'
DB = 'durai_test'
STREAM = 'durai-reservation-staging'
SERVICES = ('pg-primary','pg-replica','broker','api','relay','consumer','proxy')


def python_image():
    # Sample: Read the pinned Python image ID from staging-image.lock.json.
    return json.loads((ROOT/'staging-image.lock.json').read_text(encoding='utf-8'))['imageId']


def check_project(project):
    # Sample: Accept "durai-stage-0a1b2c3d4e5f". Refuse "prod-reservations".
    if not isinstance(project,str) or not PROJECT.fullmatch(project):
        raise ValueError('Refuse a project that is not durai-stage-<12 hex>')
    return project


def new_project():
    # Sample: Return a new project name such as durai-stage-3f9a0c71be42.
    return 'durai-stage-'+secrets.token_hex(6)


def make_secrets(folder, token=None):
    # Sample: Write token, Redis password, and database passwords into folder/secrets. Return the values.
    place = Path(folder)/'secrets'
    place.mkdir(parents=True,exist_ok=True)
    passwords = {role:secrets.token_hex(24) for role in ROLES}
    values = {'token':token or secrets.token_hex(24),'redis':secrets.token_hex(24),'replication':secrets.token_hex(24)}
    files = {
        'token':values['token'],
        'redis':values['redis'],
        'admin_settings':json.dumps({'passwords':passwords}),
        'runtime_settings':json.dumps({'passwords':{'durai_runtime':passwords['durai_runtime']}}),
        'db.env':'POSTGRES_PASSWORD='+passwords['postgres']+'\nPOSTGRES_DB=postgres\nPGDATA='+PGDATA+'\n',
        'replica.env':'PGPASSWORD='+values['replication']+'\n',
    }
    for name,text in files.items():
        (place/name).write_text(text,encoding='utf-8',newline='\n')
    return {**values,'passwords':passwords}


def posix(path):
    # Sample: Turn D:\\work\\a into D:/work/a for the compose file.
    return str(path).replace('\\','/')


def caddyfile(port):
    # Sample: Serve https://127.0.0.1:8443 and https://localhost:8443 with the Caddy internal CA. Send requests to api port 8080.
    return ('{\n\tadmin off\n\tdefault_sni 127.0.0.1\n\tlocal_certs\n\tskip_install_trust\n\tauto_https disable_redirects\n}\n'
            '127.0.0.1:'+str(port)+', localhost:'+str(port)+' {\n\ttls internal\n\treverse_proxy api:8080\n}\n')


def render_compose(project, folder, port=8443):
    # Sample: Build the model for project P-1. Publish only proxy 127.0.0.1:8443. Name secrets by file path.
    check_project(project)
    place = Path(folder)
    secret = lambda name:{'source':name,'target':name,'mode':0o444}
    mounts = [{'type':'bind','source':posix(ROOT/name),'target':'/app/'+name,'read_only':True}
              for name in ('durai','tools','blueprints','migrations')]
    service_secrets = {'api':['token','runtime_settings'],'relay':['runtime_settings','redis'],
                       'consumer':['runtime_settings','redis'],'migrate':['admin_settings']}

    def python(name, command, **extra):
        # Sample: Define the non-root read-only Python service "relay" on the private network.
        environment = {'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':'/app:/app/tools','HOME':'/tmp',
                       'DURAI_DB_HOST':'pg-primary','DURAI_DB_PORT':'5432','DURAI_BROKER':'redis-stream',
                       'DURAI_REDIS_HOST':'broker','DURAI_REDIS_PASSWORD_FILE':'/run/secrets/redis',
                       'DURAI_TOKEN_FILE':'/run/secrets/token',
                       'DURAI_SETTINGS':'/run/secrets/'+('admin_settings' if name=='migrate' else 'runtime_settings')}
        return {'image':python_image(),'pull_policy':'never','container_name':project+'-'+name,'command':command,
                'read_only':True,'user':'65534:65534','cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],
                'tmpfs':['/tmp:rw,nosuid,size=16m'],'environment':environment,'volumes':mounts,
                'secrets':[secret(item) for item in service_secrets[name]],'networks':['backend'],**extra}

    entry = '/app/tools/reservation_staging_services.py'
    primary_command = ['postgres','-D',PGDATA,'-c','shared_preload_libraries=age','-c','listen_addresses=*',
                       '-c','wal_level=replica','-c','max_wal_senders=4','-c','wal_keep_size=256MB']
    replica_script = ('chown postgres:postgres '+DATA+' && if [ ! -f '+PGDATA+'/PG_VERSION ]; then '
                      'gosu postgres pg_basebackup -d "host=pg-primary port=5432 user=durai_repl application_name=replica1 '
                      'connect_timeout=5" -D '+PGDATA+' -X stream -R -w --checkpoint=fast; fi && exec gosu postgres postgres '
                      '-D '+PGDATA+' -c shared_preload_libraries=age -c hot_standby=on -c listen_addresses=*')
    ready = ['CMD-SHELL',"psql -h 127.0.0.1 -U supabase_admin -d postgres -Atqc 'SELECT 1'"]
    services = {
        'pg-primary':{'image':POSTGRES_IMAGE,'pull_policy':'never','container_name':project+'-pg-primary',
                      'command':primary_command,'env_file':[posix(place/'secrets'/'db.env')],
                      'volumes':[{'type':'volume','source':'pg-primary-data','target':DATA}],'networks':['backend'],
                      'healthcheck':{'test':ready,'interval':'2s','timeout':'5s','retries':60}},
        'pg-replica':{'image':POSTGRES_IMAGE,'pull_policy':'never','container_name':project+'-pg-replica',
                      'entrypoint':['sh','-ec',replica_script],'env_file':[posix(place/'secrets'/'replica.env')],
                      'volumes':[{'type':'volume','source':'pg-replica-data','target':DATA}],'networks':['backend'],
                      'healthcheck':{'test':ready,'interval':'2s','timeout':'5s','retries':60}},
        'broker':{'image':REDIS_IMAGE,'pull_policy':'never','container_name':project+'-broker',
                  'command':['sh','-c','exec redis-server --requirepass "$$(cat /run/secrets/redis)" '
                             '--appendonly yes --appendfsync always --save ""'],
                  'secrets':[secret('redis')],'volumes':[{'type':'volume','source':'broker-data','target':'/data'}],
                  'networks':['backend'],
                  'healthcheck':{'test':['CMD-SHELL','redis-cli -a "$$(cat /run/secrets/redis)" --no-auth-warning ping | grep -q PONG'],
                                 'interval':'2s','timeout':'5s','retries':30}},
        'api':python('api',['python',entry,'api'],restart='unless-stopped',
                     healthcheck={'test':['CMD','python','-c',"import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)"],
                                  'interval':'2s','timeout':'5s','retries':30}),
        'relay':python('relay',['python',entry,'relay'],restart='unless-stopped'),
        'consumer':python('consumer',['python',entry,'consumer'],restart='unless-stopped'),
        'migrate':python('migrate',['python','/app/tools/staging_admin.py','schema'],profiles=['tools']),
        'proxy':{'image':CADDY_IMAGE,'pull_policy':'never','container_name':project+'-proxy',
                 'command':['caddy','run','--config','/etc/caddy/Caddyfile','--adapter','caddyfile'],
                 'volumes':[{'type':'bind','source':posix(place/'Caddyfile'),'target':'/etc/caddy/Caddyfile','read_only':True},
                            {'type':'volume','source':'proxy-data','target':'/data'}],
                 'ports':[{'target':port,'published':str(port),'host_ip':'127.0.0.1','protocol':'tcp'}],
                 'networks':['backend','edge']},
    }
    return {'name':project,'services':services,
            'networks':{'backend':{'internal':True},'edge':{}},
            'volumes':{name:{} for name in ('pg-primary-data','pg-replica-data','broker-data','proxy-data')},
            'secrets':{name:{'file':posix(place/'secrets'/name)} for name in ('token','redis','admin_settings','runtime_settings')}}


def docker(*args, timeout=120, check=True, run=subprocess.run, **options):
    # Sample: Run docker ps with a 120 second limit. Return the finished process.
    try:
        return run(['docker',*args],capture_output=True,timeout=timeout,check=check,**options)
    except subprocess.CalledProcessError as error:
        raise RuntimeError('docker '+' '.join(args[:3])+' failed: '+(error.stderr or b'').decode(errors='replace')[-600:]) from None


def compose(project, *args, timeout=180, check=True, run=subprocess.run, **options):
    # Sample: Run docker compose restart relay for project P-1.
    check_project(project)
    return docker('compose','-p',project,'-f',posix(STAGING/project/'compose.json'),*args,timeout=timeout,check=check,run=run,**options)


def psql(project, query, database=DB, timeout=30):
    # Sample: Read SELECT 1 from the primary as the superuser. Return "1".
    check_project(project)
    result = docker('exec','-i',project+'-pg-primary','psql','-U','supabase_admin','-d',database,'-Atq','-v','ON_ERROR_STOP=1',
                    timeout=timeout,input=query.encode())
    return result.stdout.decode().strip()


def wait_until(check, seconds, what):
    # Sample: Poll for 90 seconds until the replica streams. Raise TimeoutError when it does not.
    end = time.monotonic()+seconds
    while time.monotonic()<end:
        value = check()
        if value:
            return value
        time.sleep(.5)
    raise TimeoutError('Timed out waiting for '+what)


def seat_sql(seats):
    # Sample: Insert seats A1 and A2 into tenant "trading". Refuse a seat name with a quote.
    if any(not SEAT.fullmatch(seat) for seat in seats):
        raise ValueError('Invalid seat identifier')
    values = ','.join("('trading','"+seat+"')" for seat in seats)
    return 'INSERT INTO durai_reservations.seat(application_id,seat_id) VALUES '+values+';'


def image_ids():
    # Sample: Return the five pinned image IDs used by the stack.
    return {'postgres':POSTGRES_IMAGE,'redis':REDIS_IMAGE,'python':python_image(),'caddy':CADDY_IMAGE}


def up(load_seats=300, port=8443, run_id=None):
    # Sample: Start the stack with 300 load seats. Return its project, profile path, and CA file path.
    project = new_project()
    folder = STAGING/project
    folder.mkdir(parents=True)
    values = make_secrets(folder)
    run = run_id or str(uuid4())
    tag = 'stg.'+run.replace('-','')[:8]
    seats = {'canary':tag+'.canary','race':tag+'.race','rehearsal':tag+'.rehearsal',
             'load':[tag+'.load.'+str(index) for index in range(load_seats)]}
    (folder/'compose.json').write_text(json.dumps(render_compose(project,folder,port),indent=2),encoding='utf-8',newline='\n')
    (folder/'Caddyfile').write_text(caddyfile(port),encoding='utf-8',newline='\n')
    try:
        compose(project,'up','-d','--wait','--wait-timeout','150','pg-primary','broker',timeout=200)
        subnet = docker('network','inspect',project+'_backend','--format','{{(index .IPAM.Config 0).Subnet}}').stdout.decode().strip()
        if not re.fullmatch(r'[0-9.]+/[0-9]+',subnet):
            raise ValueError('Backend subnet is unavailable')
        psql(project,"CREATE ROLE durai_repl WITH LOGIN REPLICATION PASSWORD '"+values['replication']+"'",'postgres')
        hba = PGDATA+'/pg_hba.conf'
        original = docker('exec',project+'-pg-primary','cat',hba).stdout
        docker('exec','-i','-u','postgres',project+'-pg-primary','sh','-ec','umask 077; cat > "$1.next"; mv "$1.next" "$1"','write',hba,
               input=('host replication durai_repl '+subnet+' scram-sha-256\n').encode()+original)
        psql(project,'SELECT pg_reload_conf()','postgres')
        schemas = json.loads(compose(project,'--profile','tools','run','--rm','--no-deps','-T','migrate',timeout=240).stdout.decode().strip().splitlines()[-1])
        psql(project,seat_sql([seats['canary'],seats['race'],seats['rehearsal'],*seats['load']]))
        canary = json.loads(compose(project,'--profile','tools','run','--rm','--no-deps','-T','migrate','python','/app/tools/staging_admin.py',
                                    'canary',seats['canary'],'canary:'+run).stdout.decode().strip().splitlines()[-1])
        compose(project,'up','-d','--wait','--wait-timeout','150',timeout=250)
        wait_until(lambda:psql(project,"SELECT 1 FROM pg_stat_replication WHERE state='streaming' AND application_name='replica1'")=='1',90,'replica stream')
        authority = folder/'caddy-root.crt'
        wait_until(lambda:docker('cp',project+'-proxy:/data/caddy/pki/authorities/local/root.crt',str(authority),check=False).returncode==0,60,'proxy root certificate')
    except BaseException:
        down(project)
        raise
    profile = {'baseUrl':'https://127.0.0.1:'+str(port),'canaryOrderId':canary['orderId'],'canarySeatId':seats['canary'],
               'raceSeatId':seats['race'],'loadSeats':seats['load'],'runId':run,'clients':8,'requestsPerSecond':50,
               'holdSeconds':30,'p95LimitMs':300,'queueAgeLimitSeconds':5,'replicaLagLimitBytes':1048576}
    (folder/'profile.json').write_text(json.dumps(profile,indent=2),encoding='utf-8',newline='\n')
    (folder/'stack.json').write_text(json.dumps({'project':project,'port':port,'images':image_ids(),'schemas':schemas,
                                                 'rehearsalSeat':seats['rehearsal'],'subnet':subnet},indent=2),encoding='utf-8',newline='\n')
    return {'project':project,'profile':str(folder/'profile.json'),'ca':str(authority),'token':str(folder/'secrets'/'token')}


def down(project, run=subprocess.run):
    # Sample: Remove the containers, networks, and volumes of durai-stage-0a1b2c3d4e5f. Refuse "prod-reservations".
    check_project(project)
    folder = STAGING/project
    arguments = ['-f',posix(folder/'compose.json')] if (folder/'compose.json').exists() else []
    run(['docker','compose','-p',project,*arguments,'--profile','tools','down','-v','--remove-orphans','--timeout','20'],
        capture_output=True,timeout=240)
    left = run(['docker','ps','-a','--filter','label=com.docker.compose.project='+project,'--format','{{.Names}}'],
               capture_output=True,timeout=30).stdout
    shutil.rmtree(folder/'secrets',ignore_errors=True)
    return left.decode().split() if isinstance(left,bytes) else []


def service(project, action, name):
    # Sample: Stop service relay of project P-1. Refuse service "pg-primary".
    if action not in ('stop','start','restart') or name not in ('relay','consumer','api','broker'):
        raise ValueError('Use stop, start, or restart on relay, consumer, api, or broker')
    compose(project,action,name,timeout=90)


def sample(project):
    # Sample: Read oldest unpublished outbox age 0.8 seconds and replica lag 2048 bytes.
    row = psql(project,"SELECT COALESCE(extract(epoch FROM max(clock_timestamp()-created_at) FILTER (WHERE state<>'PUBLISHED')),0)||'|'||"
               "COALESCE((SELECT max(pg_wal_lsn_diff(pg_current_wal_lsn(),replay_lsn)) FROM pg_stat_replication WHERE state='streaming'),-1) "
               'FROM durai_events.outbox').split('|')
    return float(row[0]),int(float(row[1]))


class Sampler:
    def __init__(self, project, interval=0.25):
        # Sample: Sample project P-1 every 0.25 seconds in a background thread.
        self.project, self.interval = project, interval
        self.stop, self.rows, self.errors = threading.Event(), [], 0
        self.worker = threading.Thread(target=self.loop,daemon=True)

    def loop(self):
        # Sample: Record one reading, then wait 0.25 seconds. Count a failed reading.
        while not self.stop.is_set():
            try:
                self.rows.append(sample(self.project))
            except Exception:
                self.errors += 1
            self.stop.wait(self.interval)

    def __enter__(self):
        # Sample: Start the sample thread.
        self.worker.start()
        return self

    def __exit__(self, kind, value, trace):
        # Sample: Stop the thread and wait up to 20 seconds for it.
        self.stop.set()
        self.worker.join(20)
        return False

    def result(self):
        # Sample: Return the maximum queue age and replica lag of all readings.
        if not self.rows:
            raise ValueError('No metric samples were collected')
        return {'samples':len(self.rows),'failedSamples':self.errors,'maxQueueAgeSeconds':round(max(r[0] for r in self.rows),3),
                'maxReplicaLagBytes':max(r[1] for r in self.rows)}


def snapshot(project, label):
    # Sample: Write a pg_dump archive of the primary to the project folder. Return its path and digest.
    check_project(project)
    value = docker('exec',project+'-pg-primary','pg_dump','-U','supabase_admin','-Fc',DB,timeout=120).stdout
    if not value.startswith(b'PGDMP'):
        raise ValueError('Invalid snapshot archive')
    path = STAGING/project/('snapshot-'+label+'.dump')
    path.write_bytes(value)
    return {'path':str(path),'digest':digest(value),'bytes':len(value)}


def restore(project, path):
    # Sample: Stop the services, replace durai_test with the archive, and start the services again.
    check_project(project)
    value = Path(path).read_bytes()
    if not value.startswith(b'PGDMP'):
        raise ValueError('Invalid snapshot archive')
    for name in ('api','relay','consumer'):
        service(project,'stop',name)
    started = time.perf_counter()
    psql(project,'DROP DATABASE IF EXISTS '+DB+' WITH (FORCE); CREATE DATABASE '+DB+" TEMPLATE template0 ENCODING 'UTF8'; "
         'REVOKE ALL ON DATABASE '+DB+' FROM PUBLIC; '+' '.join('GRANT CONNECT ON DATABASE '+DB+' TO '+role+';' for role in ROLES if role!='postgres'),
         'postgres',timeout=60)
    job = docker('exec','-i',project+'-pg-primary','pg_restore','-U','supabase_admin','--exit-on-error','--single-transaction','-d',DB,
                 input=value,timeout=180,check=False)
    if job.returncode:
        raise ValueError('Snapshot restore failed: '+job.stderr.decode()[-300:])
    return {'seconds':round(time.perf_counter()-started,3),'archiveDigest':digest(value)}


def export_outcomes(project, run_id):
    # Sample: Read outcome digest, event ID, and effect count for every request key with prefix run_id.
    check_project(project)
    if not re.fullmatch(r'[0-9a-f-]{36}',run_id):
        raise ValueError('Use the run UUID')
    text = psql(project,"SELECT COALESCE(json_object_agg(request_key,json_build_object('outcome',outcome,'effects',"
                "(SELECT count(*) FROM durai_staging.effect e WHERE e.event_id=(r.outcome->'event'->>'eventId')::uuid))),'{}'::json) "
                "FROM durai_idempotency.request r WHERE state='COMPLETED' AND request_key LIKE '"+run_id+":%'")
    result = {}
    for key,row in json.loads(text).items():
        event = row['outcome'].get('event')
        result[key] = {'outcomeDigest':digest(row['outcome']),'eventId':event['eventId'] if event else None,
                       'effectCount':row['effects']}
    return result


def drain(project, seconds=120):
    # Sample: Wait until every outbox event is published and every event has an effect. Return the wait time.
    started = time.perf_counter()
    query = ("SELECT (SELECT count(*) FROM durai_events.outbox WHERE state<>'PUBLISHED')||'|'||"
             "(SELECT count(*) FROM durai_events.outbox o WHERE NOT EXISTS (SELECT 1 FROM durai_staging.effect e WHERE e.event_id=o.event_id))")
    wait_until(lambda:psql(project,query)=='0|0',seconds,'outbox and effects to drain')
    return round(time.perf_counter()-started,3)


def redis(project, *args):
    # Sample: Run XLEN on the stream of project P-1 and read 3 as JSON.
    check_project(project)
    out = docker('exec',project+'-broker','sh','-c','exec redis-cli -2 -a "$(cat /run/secrets/redis)" --no-auth-warning --json "$@"',
                 'sh',*args,timeout=30).stdout.decode()
    return json.loads(out)


def pending(project):
    # Sample: Read 0 entries that still need acknowledgment for group "projection".
    return redis(project,'XPENDING',STREAM,'projection')[0]


def rewind(project):
    # Sample: Move group "projection" to the first stream entry so every event is delivered again.
    return redis(project,'XGROUP','SETID',STREAM,'projection','0')


def one_off(project, role, timeout=90):
    # Sample: Run "relay-stop" once on the private network. Read exit code 71 after Redis accepts the event.
    check_project(project)
    name = {'relay-stop':'relay','consumer-stop':'consumer'}[role]
    return compose(project,'run','--rm','--no-deps','-T',name,'python','/app/tools/reservation_staging_services.py',role,
                   timeout=timeout,check=False).returncode


def free_port():
    # Sample: Ask the system for a free loopback port such as 54123.
    import socket
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        return probe.getsockname()[1]


def main():
    # Sample: Run "up", "down PROJECT", or "restart PROJECT relay" from the command line.
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command',required=True)
    sub.add_parser('up')
    for name in ('down','stop','start','restart'):
        item = sub.add_parser(name)
        item.add_argument('project')
        if name!='down':
            item.add_argument('service')
    args = parser.parse_args()
    if args.command=='up':
        print(json.dumps(up()))
    elif args.command=='down':
        print(json.dumps({'remaining':down(args.project)}))
    else:
        service(args.project,args.command,args.service)


if __name__=='__main__':
    main()
