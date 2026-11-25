"""Sample: Run the workspace tests against a disposable PostgreSQL 17 on a private network. Return passed 1 and failed 1. Remove the containers and the network."""
from contextlib import contextmanager
import json
import re
import secrets
import subprocess
import time
from pathlib import Path

TEST_IMAGE = 'durai-eval-python:1'
DATABASE_IMAGE = 'postgres:17-alpine'
TIMEOUT_SECONDS = 300
OUTPUT_TAIL = 4000
URL_VARIABLE = 'DURAI_BUILD_DATABASE_URL'
APP_WAIT_SECONDS = 60
APP_PORT = 8000
BODY_LIMIT = 2000
PROBE = ('import json,sys,urllib.request\n'
         'try:\n'
         '    r=urllib.request.urlopen("http://127.0.0.1:"+sys.argv[1]+"/",timeout=3)\n'
         '    print(json.dumps({"status":r.status,"body":r.read(4000).decode("utf-8","replace")}))\n'
         'except Exception as e:\n'
         '    print(json.dumps({"status":None,"body":type(e).__name__}))\n')


def docker_available():
    # Sample: Docker daemon answers docker version. Return True. Daemon is off. Return False.
    try:
        return subprocess.run(['docker', 'version', '--format', '{{.Server.Version}}'], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def parse_counts(output):
    # Sample: Output "Ran 2 tests ... FAILED (failures=1)". Return passed 1 and failed 1. Output "Ran 3 tests ... OK". Return passed 3 and failed 0.
    ran = re.findall(r'^Ran (\d+) tests? in', output, re.MULTILINE)
    total = int(ran[-1]) if ran else 0
    failed = 0
    summary = re.findall(r'^FAILED \(([^)]*)\)', output, re.MULTILINE)
    if summary:
        failed = sum(int(count) for count in re.findall(r'(?:failures|errors)=(\d+)', summary[-1]))
    return {'passed': max(total - failed, 0), 'failed': failed}


@contextmanager
def sandbox():
    # Sample: Open a sandbox. Get a private network and a ready postgres:17-alpine with alias db. Leave the block. Check the containers and the network are gone.
    ident = secrets.token_hex(6)
    names = {'network': 'durai-build-net-' + ident, 'database': 'durai-build-db-' + ident,
             'runner': 'durai-build-run-' + ident, 'app': 'durai-build-app-' + ident}
    password = secrets.token_hex(16)
    url = 'postgresql://postgres:' + password + '@db:5432/postgres'
    label = ['--label', 'com.durai.owner=blind-build']

    def run(*args, timeout=60):
        return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)

    try:
        created = run('network', 'create', '--internal', *label, names['network'])
        if created.returncode:
            raise RuntimeError('Docker network failed: ' + created.stderr.strip()[-300:])
        started = run('run', '-d', '--name', names['database'], *label, '--network', names['network'], '--network-alias', 'db',
                      '-e', 'POSTGRES_PASSWORD=' + password, '--tmpfs', '/var/lib/postgresql/data:rw,size=512m',
                      DATABASE_IMAGE, timeout=120)
        if started.returncode:
            raise RuntimeError('Database container failed: ' + started.stderr.strip()[-300:])
        deadline = time.monotonic() + 90
        while run('exec', names['database'], 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres').returncode:
            if time.monotonic() > deadline:
                raise RuntimeError('Database did not become ready')
            time.sleep(1)
        yield {'names': names, 'url': url, 'password': password, 'label': label, 'run': run}
    finally:
        subprocess.run(['docker', 'rm', '-f', names['runner'], names['app'], names['database']], capture_output=True, timeout=60)
        subprocess.run(['docker', 'network', 'rm', names['network']], capture_output=True, timeout=60)


def container_flags(root, url, extra_env=()):
    # Sample: Workspace D:/w. Return flags for a read-only root, no capabilities, user 65534, a read-only bind of D:/w on /workspace, and the database URL.
    flags = ['--read-only', '--cap-drop', 'ALL', '--user', '65534:65534', '--tmpfs', '/tmp:rw,nosuid,size=64m', '-e', 'HOME=/tmp',
             '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONPATH=/workspace', '-e', URL_VARIABLE + '=' + url]
    for item in extra_env:
        flags += ['-e', item]
    return flags + ['--mount', 'type=bind,source=' + root + ',target=/workspace,readonly', '-w', '/workspace']


def combine_counts(first, second):
    # Sample: Python 2 passed 0 failed and Node 1 passed 1 failed. Return passed 3, failed 1 and exitCode 1.
    codes = [code for code in (first.get('exitCode'), second.get('exitCode')) if code != 0]
    return {'passed': first['passed'] + second['passed'], 'failed': first['failed'] + second['failed'],
            'exitCode': codes[0] if codes else 0, 'timedOut': first['timedOut'] or second['timedOut'],
            'output': (first['output'] + '\n' + second['output'])[-OUTPUT_TAIL:]}


def run_workspace_tests(workspace, seconds=TIMEOUT_SECONDS):
    # Sample: Workspace with tests/test_a.py (1 pass, 1 fail) and no package.json. Return passed 1, failed 1. Add package.json with one passing vitest test: return passed 2, failed 1.
    python = run_python_tests(workspace, seconds)
    if not (Path(workspace) / 'package.json').exists():
        return python
    from .build_run_node import run_node_tests
    return combine_counts(python, run_node_tests(workspace, seconds))


def run_python_tests(workspace, seconds=TIMEOUT_SECONDS):
    # Sample: Workspace has tests/test_a.py with one passing and one failing test. Return passed 1 and failed 1 and exitCode 1.
    root = str(Path(workspace).resolve())
    with sandbox() as box:
        try:
            done = subprocess.run(
                ['docker', 'run', '--rm', '--name', box['names']['runner'], *box['label'], '--network', box['names']['network'],
                 *container_flags(root, box['url']), TEST_IMAGE, 'python', '-m', 'unittest', 'discover', '-s', 'tests'],
                capture_output=True, text=True, timeout=seconds)
        except subprocess.TimeoutExpired as error:
            text = (error.stdout or b'') + (error.stderr or b'')
            text = text.decode('utf-8', 'replace') if isinstance(text, bytes) else text
            return {'passed': 0, 'failed': 0, 'exitCode': None, 'timedOut': True, 'output': text[-OUTPUT_TAIL:]}
        output = (done.stdout or '') + (done.stderr or '')
        return {**parse_counts(output), 'exitCode': done.returncode, 'timedOut': False,
                'output': output.replace(box['password'], '***')[-OUTPUT_TAIL:]}


def check_workspace_app(workspace, seconds=APP_WAIT_SECONDS, port=APP_PORT):
    # Sample: Workspace with package.json and app/page.tsx. Return status 200 from the Next.js image. Workspace with src/app.py only. Return status 200 from the Python image.
    if (Path(workspace) / 'package.json').exists():
        from .build_run_node import check_node_app
        return check_node_app(workspace, seconds=max(seconds, 240), port=port)
    return check_python_app(workspace, seconds, port)


def check_python_app(workspace, seconds=APP_WAIT_SECONDS, port=APP_PORT):
    # Sample: Workspace src/app.py serves ok on DURAI_APP_PORT. Return status 200 and body ok. Remove every container.
    root = str(Path(workspace).resolve())
    with sandbox() as box:
        names = box['names']
        started = box['run']('run', '-d', '--name', names['app'], *box['label'], '--network', names['network'],
                             *container_flags(root, box['url'], ['DURAI_APP_PORT=' + str(port)]),
                             TEST_IMAGE, 'python', '-m', 'src.app', timeout=120)
        if started.returncode:
            return {'status': None, 'body': '', 'started': False,
                    'output': 'App container failed: ' + started.stderr.strip()[-500:]}
        deadline = time.monotonic() + seconds
        result = {'status': None, 'body': ''}
        while time.monotonic() < deadline:
            state = box['run']('inspect', '-f', '{{.State.Running}}', names['app'])
            if state.stdout.strip() != 'true':
                break
            probe = box['run']('exec', names['app'], 'python', '-c', PROBE, str(port), timeout=30)
            try:
                result = json.loads(probe.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                result = {'status': None, 'body': ''}
            if result.get('status') == 200:
                break
            time.sleep(1)
        logs = box['run']('logs', '--tail', '40', names['app'])
        output = ((logs.stdout or '') + (logs.stderr or '')).replace(box['password'], '***')[-OUTPUT_TAIL:]
        return {'status': result.get('status'), 'body': (result.get('body') or '')[:BODY_LIMIT], 'started': True,
                'output': output}
