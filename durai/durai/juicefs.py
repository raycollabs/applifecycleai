"""Basic local JuiceFS integration. Metadata and file bytes have separate volumes.

The FUSE clients are trusted infrastructure, not candidate sandboxes. They use
only a dedicated metadata login/database. Full Git/crash/HA qualification remains
a separate gate.
"""
import json
from pathlib import Path
import secrets
import subprocess
import time
from psycopg import sql
from .db import connect
from .common import digest, uid

ROOT = Path(__file__).resolve().parents[1]
TAG = 'juicedata/mount:ce-v1.3.1'
META_URL = 'postgres://durai_juicefs@host.docker.internal:54410/durai_juicefs?sslmode=disable&search_path=public'


def command(args, *, input_text=None, timeout=120):
    result = subprocess.run(args, input=input_text, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout)
    if result.returncode:
        raise RuntimeError('JuiceFS command failed: ' + result.stderr[-1500:])
    return result.stdout.strip()


def qualify():
    private = ROOT / 'private'
    private.mkdir(parents=True, exist_ok=True)
    env_file = private / 'juicefs.env'
    if not env_file.exists():
        env_file.write_text('META_PASSWORD=' + secrets.token_hex(24) + '\n', encoding='utf-8')
    password = env_file.read_text(encoding='utf-8').strip().split('=', 1)[1]
    with connect('postgres', 'postgres', autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname='durai_juicefs'").fetchone():
            admin.execute(sql.SQL('CREATE ROLE durai_juicefs LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Literal(password)))
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname='durai_juicefs'").fetchone():
            admin.execute('CREATE DATABASE durai_juicefs OWNER durai_juicefs TEMPLATE template0')
        admin.execute('REVOKE ALL ON DATABASE durai_juicefs FROM PUBLIC')
        admin.execute('GRANT CONNECT ON DATABASE durai_juicefs TO durai_juicefs')
    lock = ROOT / 'juicefs-image.lock.json'
    if lock.exists():
        image = json.loads(lock.read_text(encoding='utf-8'))['imageId']
    else:
        print('Pulling the versioned JuiceFS client image...', flush=True)
        command(['docker', 'pull', TAG], timeout=300)
        image = command(['docker', 'image', 'inspect', TAG, '--format', '{{.Id}}'])
        lock.write_text(json.dumps({'tag': TAG, 'imageId': image,
                                    'source': 'https://juicefs.com/docs/community/juicefs_on_docker/'}, indent=2) + '\n', encoding='utf-8')
    mounts = ['--mount', 'type=volume,source=durai-juicefs-bytes,target=/data']
    base = ['docker', 'run', '--rm', '--env-file', str(env_file), *mounts, '--entrypoint', 'juicefs', image]
    command([*base, 'format', '--storage', 'file', '--bucket', '/data', META_URL, 'durai-local-files'], timeout=90)
    clients = []
    try:
        for suffix in ('a', 'b'):
            name = 'durai-juicefs-' + suffix + '-' + uid()[:8]
            command(['docker', 'run', '-d', '--name', name, '--label', 'com.durai.owner=local-juicefs-proof',
                     '--cap-add', 'SYS_ADMIN', '--device', '/dev/fuse', '--security-opt', 'apparmor:unconfined',
                     '--env-file', str(env_file), *mounts, '--entrypoint', 'juicefs', image,
                     'mount', '--cache-size', '0', '--attr-cache', '0', '--entry-cache', '0', META_URL, '/mnt/jfs'])
            clients.append(name)
        for name in clients:
            for attempt in range(30):
                probe = subprocess.run(['docker', 'exec', name, 'test', '-f', '/mnt/jfs/.control'], capture_output=True)
                if probe.returncode == 0:
                    break
                time.sleep(.2)
            else:
                raise RuntimeError('JuiceFS mount did not become ready')
        run_key = uid()
        first = '/mnt/jfs/skill-' + run_key
        renamed = first + '-renamed'
        payload = 'F-101: use 200 canonical shares for 2 lots of 100.'
        command(['docker', 'exec', '-i', clients[0], 'sh', '-c', 'cat > "$1"', 'sh', first], input_text=payload)
        observed = command(['docker', 'exec', clients[1], 'cat', first])
        if observed != payload:
            raise ValueError('Second JuiceFS client did not read the exact bytes')
        command(['docker', 'exec', clients[0], 'mv', first, renamed])
        command(['docker', 'exec', clients[0], 'chmod', '640', renamed])
        observed = command(['docker', 'exec', clients[1], 'cat', renamed])
        permissions = command(['docker', 'exec', clients[1], 'stat', '-c', '%a', renamed])
        if observed != payload or permissions != '640':
            raise ValueError('JuiceFS rename/permission contract differs')
        receipt = {'state': 'BASIC_CONTRACT_PASS', 'provider': 'juicefs', 'imageId': image,
                   'version': command([*base, 'version']), 'metadata': 'dedicated PostgreSQL database durai_juicefs',
                   'byteBackend': 'file backend on Docker named volume durai-juicefs-bytes',
                   'checks': ['create', 'second-client-read', 'rename', 'mode-640', 'second-client-read-after-rename'],
                   'fixturePath': renamed, 'fixtureDigest': digest(payload.encode()),
                   'productionQualified': False, 'recoveryBoundary': 'Metadata database and file-byte volume require joint backup.'}
        (ROOT / 'receipts/juicefs.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        return receipt
    finally:
        for name in clients:
            # Remove only the exact ephemeral containers created by this invocation.
            command(['docker', 'rm', '-f', name])
