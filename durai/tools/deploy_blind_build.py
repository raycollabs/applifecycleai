"""Sample: Run deploy_blind_build.py up --workspace W-1 --project durai-app-deepseek-r2 --port 54360. Check http://127.0.0.1:54360/ answers 200 and the password is never printed."""
import argparse
import json
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = re.compile(r'^durai-app-[a-z0-9-]{3,40}$')
IMAGE = 'durai-eval-python:1'
DATABASE_IMAGE = 'postgres:17-alpine'
APP_PORT = 8000
PRIVATE = ROOT / 'private' / 'deploy'


def check_project(project):
    # Sample: Check durai-app-deepseek-r2. Return it. Check trade-pilot. Raise ValueError.
    if not isinstance(project, str) or not PROJECT.fullmatch(project):
        raise ValueError('Project name must match ^durai-app-[a-z0-9-]{3,40}$')
    return project


def docker(*args, timeout=120):
    # Sample: Call docker version. Return the finished process with text output.
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)


def names(project):
    # Sample: Project durai-app-r2. Return container durai-app-r2-app, durai-app-r2-db, network durai-app-r2-net, volume durai-app-r2-pgdata.
    return {'app': project + '-app', 'db': project + '-db', 'network': project + '-net', 'volume': project + '-pgdata'}


def secret_files(project):
    # Sample: Project durai-app-r2. Return private/deploy/durai-app-r2 with db.env and app.env, created once with a random password.
    folder = PRIVATE / project
    folder.mkdir(parents=True, exist_ok=True)
    db_env, app_env = folder / 'db.env', folder / 'app.env'
    if not db_env.exists():
        password = secrets.token_hex(16)
        db_env.write_text('POSTGRES_PASSWORD=' + password + '\n', encoding='utf-8', newline='\n')
        app_env.write_text('DURAI_APP_PORT=' + str(APP_PORT) + '\nDURAI_BUILD_DATABASE_URL=postgresql://postgres:' + password
                           + '@db:5432/postgres\nHOME=/tmp\nPYTHONDONTWRITEBYTECODE=1\nPYTHONPATH=/workspace\n',
                           encoding='utf-8', newline='\n')
    return db_env, app_env


def probe(port, seconds):
    # Sample: App answers 200 on 127.0.0.1:54360 within 90 seconds. Return 200. No answer. Return None.
    deadline = time.monotonic() + seconds
    while True:
        try:
            with urllib.request.urlopen('http://127.0.0.1:' + str(port) + '/', timeout=3) as response:
                return response.status
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return None
        time.sleep(1)


def exists(kind, name):
    # Sample: Container durai-app-r2-db exists. Return True. Missing. Return False.
    return docker(kind, 'inspect', name).returncode == 0


def up(workspace, project, port, wait=90):
    # Sample: Workspace W-1, project durai-app-r2, port 54360. Start the database with a named volume, start the app on 127.0.0.1:54360, wait for 200. Return status 200.
    check_project(project)
    root = Path(workspace).resolve()
    if not (root / 'src' / 'app.py').is_file():
        raise ValueError('Workspace has no src/app.py')
    if not 1024 <= int(port) <= 65535:
        raise ValueError('Port must be between 1024 and 65535')
    name = names(project)
    label = ['--label', 'com.durai.owner=' + project]
    if exists('volume', name['volume']) and not (PRIVATE / project / 'db.env').exists():
        raise RuntimeError('Volume exists without its password file; run down --purge-data first')
    db_env, app_env = secret_files(project)
    if not exists('network', name['network']):
        made = docker('network', 'create', *label, name['network'])
        if made.returncode:
            raise RuntimeError('Network failed: ' + made.stderr.strip()[-300:])
    if not exists('container', name['db']):
        started = docker('run', '-d', '--name', name['db'], *label, '--restart', 'unless-stopped', '--network', name['network'],
                         '--network-alias', 'db', '--env-file', str(db_env), '-v', name['volume'] + ':/var/lib/postgresql/data',
                         DATABASE_IMAGE)
        if started.returncode:
            raise RuntimeError('Database failed: ' + started.stderr.strip()[-300:])
    else:
        docker('start', name['db'])
    deadline = time.monotonic() + 90
    while docker('exec', name['db'], 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres').returncode:
        if time.monotonic() > deadline:
            raise RuntimeError('Database did not become ready')
        time.sleep(1)
    docker('rm', '-f', name['app'])
    started = docker('run', '-d', '--name', name['app'], *label, '--restart', 'unless-stopped', '--network', name['network'],
                     '--read-only', '--cap-drop', 'ALL', '--user', '65534:65534', '--tmpfs', '/tmp:rw,nosuid,size=64m',
                     '--env-file', str(app_env), '--mount', 'type=bind,source=' + str(root) + ',target=/workspace,readonly',
                     '-w', '/workspace', '-p', '127.0.0.1:' + str(int(port)) + ':' + str(APP_PORT),
                     IMAGE, 'python', '-m', 'src.app')
    if started.returncode:
        raise RuntimeError('App failed: ' + started.stderr.strip()[-300:])
    status = probe(port, wait)
    if status != 200:
        logs = docker('logs', '--tail', '30', name['app'])
        raise RuntimeError('App did not answer 200 within ' + str(wait) + ' seconds. Log tail:\n' + (logs.stdout + logs.stderr)[-1500:])
    return {'project': project, 'url': 'http://127.0.0.1:' + str(int(port)), 'status': status}


def status(project, port=None):
    # Sample: Project durai-app-r2 is running. Return container states running and, with port 54360, HTTP status 200.
    check_project(project)
    name = names(project)
    result = {'project': project}
    for key in ('app', 'db'):
        found = docker('inspect', '-f', '{{.State.Status}}', name[key])
        result[key] = found.stdout.strip() if found.returncode == 0 else 'missing'
    result['volume'] = 'present' if exists('volume', name['volume']) else 'missing'
    if port:
        result['http'] = probe(port, 3)
    return result


def down(project, purge=False):
    # Sample: Project durai-app-r2. Remove app and database containers and the network. Keep the volume. With purge remove the volume and the private folder. Project trade-pilot. Raise ValueError and call no docker command.
    check_project(project)
    name = names(project)
    docker('rm', '-f', name['app'], name['db'])
    docker('network', 'rm', name['network'])
    if purge:
        docker('volume', 'rm', name['volume'])
        folder = PRIVATE / project
        for item in folder.glob('*') if folder.is_dir() else []:
            item.unlink()
        if folder.is_dir():
            folder.rmdir()
    return {'project': project, 'removed': True, 'volumeKept': not purge}


def main(argv=None):
    # Sample: Run status --project durai-app-r2 --port 54360. Print one JSON line. Exit 0.
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('up', 'status', 'down'))
    parser.add_argument('--workspace')
    parser.add_argument('--project', required=True)
    parser.add_argument('--port', type=int, default=54360)
    parser.add_argument('--wait', type=int, default=90)
    parser.add_argument('--purge-data', action='store_true')
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == 'up':
            if not arguments.workspace:
                raise ValueError('up needs --workspace')
            result = up(arguments.workspace, arguments.project, arguments.port, arguments.wait)
        elif arguments.action == 'status':
            result = status(arguments.project, arguments.port)
        else:
            result = down(arguments.project, arguments.purge_data)
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
