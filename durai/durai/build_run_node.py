"""Sample: Run the Next.js workspace tests in durai-eval-node:1 with no network. Return passed 1 and failed 1 for one passing and one failing vitest test."""
from contextlib import nullcontext
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

import os

NODE_IMAGE = os.environ.get('DURAI_EVAL_NODE_IMAGE', 'durai-eval-node:1')
NODE_BIN = '/opt/durai/node_modules/.bin'
MOUNT = '/opt/durai/app'
SKIP = ('node_modules', '.next', '.vitest', '__pycache__', '.env', '.env.local')
OUTPUT_TAIL = 4000
APP_PORT = 3000
APP_WAIT_SECONDS = 240
BODY_LIMIT = 2000
PROBE = ("fetch('http://127.0.0.1:{port}/').then(async (r) => {{ const t = await r.text(); "
         "console.log(JSON.stringify({{status: r.status, body: t.slice(0, {limit})}})); }})"
         ".catch((e) => console.log(JSON.stringify({{status: null, body: String(e)}})));")


def copy_workspace(workspace, target):
    # Sample: Copy app/page.tsx and tests/smoke.test.ts from D:/w to a new folder. Leave out node_modules, .next and .vitest. Return the new folder.
    shutil.copytree(workspace, target, ignore=shutil.ignore_patterns(*SKIP), dirs_exist_ok=True)
    return target


def run_node_tests(workspace, seconds=300):
    # Sample: Workspace with one passing and one failing vitest test. Return passed 1, failed 1, exitCode 1 and timedOut False.
    with tempfile.TemporaryDirectory(prefix='durai-node-test-') as folder:
        copy = copy_workspace(workspace, Path(folder, 'app'))
        command = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                   '--tmpfs', '/tmp:rw,nosuid,size=256m', '-e', 'HOME=/tmp', '-e', 'NEXT_TELEMETRY_DISABLED=1',
                   '--mount', 'type=bind,source=' + str(copy) + ',target=' + MOUNT, '-w', MOUNT,
                   NODE_IMAGE, NODE_BIN + '/vitest', 'run', '--reporter=json']
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=seconds)
        except subprocess.TimeoutExpired as error:
            text = (error.stdout or '') if isinstance(error.stdout, str) else ''
            return {'passed': 0, 'failed': 0, 'exitCode': None, 'timedOut': True, 'output': text[-OUTPUT_TAIL:]}
        report = Path(copy, '.vitest', 'json', 'output.json')
        counts = {'passed': 0, 'failed': 0}
        if report.exists():
            data = json.loads(report.read_text(encoding='utf-8'))
            counts = {'passed': int(data.get('numPassedTests', 0)), 'failed': int(data.get('numFailedTests', 0))}
        output = ((done.stdout or '') + (done.stderr or ''))[-OUTPUT_TAIL:]
        return {**counts, 'exitCode': done.returncode, 'timedOut': False, 'output': output}


API_IMAGE = os.environ.get('DURAI_EVAL_PYTHON_IMAGE', 'durai-eval-python:a3-20261124')
TRADER = 'TR-BEN'
SESSION_FETCH = """
const q = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const form = q.form ? new FormData() : undefined;
if (form) for (const [key, value] of Object.entries(q.form)) form.append(key, value);
fetch(new URL(q.path, 'http://127.0.0.1:' + q.port), {
  method: q.method, headers: q.headers, body: form, redirect: 'manual', signal: AbortSignal.timeout(8000)
}).then(async response => {
  console.log(JSON.stringify({status: response.status, body: (await response.text()).slice(0, 1000000),
    cookies: response.headers.getSetCookie(), location: response.headers.get('location')}));
}).catch(() => process.exitCode = 1);
"""
SHELL_INIT = ("from shell_api.database import connect, migrate; "
              "from pathlib import Path; "
              "c=connect(); migrate(c); migrate(c,Path('src/schema'),'domain'); c.execute('INSERT INTO shell.actor(actor_id) VALUES(%s) ON CONFLICT DO NOTHING', "
              "('TR-BEN',)); c.commit(); c.close()")
LEGACY_INIT = 'from src import app; app.prepare()'


def boot_api(box, copy, mode, secret):
    # Sample: Start the shell API beside scratch database db. Return its private origin setting.
    from .build_run_tests import container_flags
    if mode == 'shell':
        flags = ['PGHOST=db', 'PGDATABASE=postgres', 'PGUSER=postgres', 'PGPASSWORD=' + box['password'],
                 'DURAI_SHELL_TRADER=' + TRADER, 'DURAI_SHELL_SIGNIN_SECRET=' + secret,
                 'DURAI_SHELL_COOKIE_SECURE=0']
        command = ('python -c "' + SHELL_INIT.replace('"', '\\"') + '" && '
                   'exec python -m uvicorn --factory shell_api.app:create_app --host 0.0.0.0 --port 8001')
        env = ['-e', 'DURAI_SHELL_API_ORIGIN=http://shell-api:8001', '-e', 'DURAI_SHELL_COOKIE_SECURE=0']
    else:
        flags = []
        command = 'python -c "' + LEGACY_INIT + '" && exec python -m src.api'
        env = ['-e', 'TRADE_BOOK_API_URL=http://shell-api:8001']
    result = box['run']('run', '-d', '--name', box['names']['app'], '--network', box['names']['network'],
                        '--network-alias', 'shell-api', *container_flags(str(copy), box['url'], flags),
                        API_IMAGE, 'sh', '-c', command, timeout=90)
    if result.returncode:
        raise RuntimeError('API container did not start')
    return env


def node_fetch(name, port, path, *, method, headers=None, form=None):
    # Sample: Request /positions. Return its status, visible HTML, and cookie headers.
    payload = {'port': port, 'path': path, 'method': method, 'headers': headers or {}, 'form': form}
    reply = subprocess.run(['docker', 'exec', '-i', name, 'node', '-e', SESSION_FETCH],
                           input=json.dumps(payload), capture_output=True, encoding='utf-8', timeout=15)
    if reply.returncode:
        return {'status': 0, 'body': '', 'cookies': []}
    try:
        return json.loads(reply.stdout.strip())
    except ValueError:
        return {'status': 0, 'body': '', 'cookies': []}


def check_node_app(workspace, seconds=APP_WAIT_SECONDS, port=APP_PORT):
    # Sample: Open a Next.js app with build screens. Sign in and read the session check in the result.
    from functools import partial
    from .build_run_tests import sandbox
    from .build_web_rules import check_sign_in
    import secrets

    workspace = Path(workspace)
    mode = 'shell' if (workspace / 'shell_api' / 'app.py').is_file() else (
        'legacy' if (workspace / 'src' / 'api.py').is_file() else None)
    with tempfile.TemporaryDirectory(prefix='durai-node-app-') as folder:
        copy = copy_workspace(workspace, Path(folder, 'app'))
        name = 'durai-node-app-' + uuid.uuid4().hex[:10]
        start = f'{NODE_BIN}/next build && exec {NODE_BIN}/next start -H 0.0.0.0 -p {port}'
        with sandbox() if mode else nullcontext() as box:
            secret = secrets.token_urlsafe(24)
            network = box['names']['network'] if box else 'none'
            api_env = boot_api(box, copy, mode, secret) if box else []
            try:
                started = subprocess.run(['docker', 'run', '-d', '--name', name, '--network', network, '--cap-drop', 'ALL',
                                          '--tmpfs', '/tmp:rw,nosuid,size=256m', '-e', 'HOME=/tmp', '-e', 'NEXT_TELEMETRY_DISABLED=1',
                                          *api_env, '--mount', 'type=bind,source=' + str(copy) + ',target=' + MOUNT, '-w', MOUNT,
                                          NODE_IMAGE, 'sh', '-c', start], capture_output=True, encoding='utf-8', timeout=120)
                if started.returncode:
                    return {'status': None, 'body': '', 'started': False, 'output': 'App container did not start'}
                deadline = time.monotonic() + seconds
                fetch = partial(node_fetch, name, port)
                response = {'status': 0, 'body': ''}
                while time.monotonic() < deadline:
                    state = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', name], capture_output=True, text=True, timeout=15)
                    if state.stdout.strip() != 'true':
                        break
                    response = fetch('/', method='GET')
                    if response['status'] == 200:
                        break
                    time.sleep(2)
                if response['status'] == 200 and mode:
                    behavior = check_sign_in(fetch, TRADER, secret if mode == 'shell' else '')
                    return {'status': 200 if behavior['passed'] else 422, 'body': '', 'started': True,
                            'output': json.dumps(behavior, sort_keys=True), 'behavior': behavior}
                logs = subprocess.run(['docker', 'logs', '--tail', '20', name], capture_output=True, encoding='utf-8', timeout=15)
                output = (logs.stdout + logs.stderr)[-OUTPUT_TAIL:].replace(secret, '***')
                if box:
                    output = output.replace(box['password'], '***')
                return {'status': response['status'] or None, 'body': response['body'][:BODY_LIMIT],
                        'started': True, 'output': output}
            finally:
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
