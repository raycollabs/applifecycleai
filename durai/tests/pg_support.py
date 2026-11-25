"""Sample: Call start_postgres. Get a connect function for a disposable PostgreSQL 17 container. Call stop_postgres. Check the container is gone."""
import secrets
import subprocess
import time

import psycopg

IMAGE = 'postgres:17-alpine'
_state = {}


PGVECTOR_IMAGE = 'pgvector/pgvector:pg17'


def image_ready(image):
    # Sample: Image pgvector/pgvector:pg17 is pulled. Return True. It is missing. Return False.
    try:
        return subprocess.run(['docker', 'image', 'inspect', image], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def docker_ready():
    # Sample: The Docker daemon answers. Return True. Docker is off. Return False.
    try:
        return subprocess.run(['docker', 'version', '--format', '{{.Server.Version}}'], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


AGE_CANDIDATES = (('apache/age:release_PG17_1.6.0', 'postgres'), ('trade-local/supabase-postgres-age:17.6.1.136-age1.7.0', 'supabase_admin'))


def age_image():
    # Sample: Env DURAI_AGE_IMAGE names an image and DURAI_AGE_USER its superuser. Return both. Without env, return the first pulled image of AGE_CANDIDATES. Return None when none is pulled.
    import os
    if os.environ.get('DURAI_AGE_IMAGE'):
        return os.environ['DURAI_AGE_IMAGE'], os.environ.get('DURAI_AGE_USER', 'postgres')
    for image, user in AGE_CANDIDATES:
        if image_ready(image):
            return image, user
    return None


def start_postgres(image=IMAGE, user='postgres'):
    # Sample: Start postgres:17-alpine (or the given image) on a random local port with a memory disk. Wait for pg_isready. Return a function that opens a connection as the given superuser.
    if 'connect' in _state:
        return _state['connect']
    name = 'durai-ports-test-' + secrets.token_hex(4)
    password = secrets.token_hex(8)
    started = subprocess.run(['docker', 'run', '-d', '--name', name, '--label', 'com.durai.owner=ports-test', '-e', 'POSTGRES_PASSWORD=' + password,
                              '--tmpfs', '/var/lib/postgresql/data:rw,size=256m', '-p', '127.0.0.1::5432', image],
                             capture_output=True, text=True, timeout=180)
    if started.returncode:
        raise RuntimeError('Postgres container failed: ' + started.stderr.strip()[-200:])
    port = subprocess.run(['docker', 'port', name, '5432/tcp'], capture_output=True, text=True, timeout=30).stdout.strip().rsplit(':', 1)[-1]
    deadline = time.monotonic() + 90
    while subprocess.run(['docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], capture_output=True).returncode:
        if time.monotonic() > deadline:
            raise RuntimeError('Postgres did not become ready')
        time.sleep(1)
    url = 'postgresql://%s:%s@127.0.0.1:%s/postgres' % (user, password, port)
    for _ in range(30):
        try:
            psycopg.connect(url, connect_timeout=3).close()
            break
        except psycopg.OperationalError:
            time.sleep(1)
    _state.update(name=name, connect=lambda: psycopg.connect(url, autocommit=False))
    return _state['connect']


def stop_postgres():
    # Sample: Remove the container started by start_postgres. Check docker ps shows no durai-ports-test container.
    if 'name' in _state:
        subprocess.run(['docker', 'rm', '-f', _state['name']], capture_output=True, timeout=60)
        _state.clear()
