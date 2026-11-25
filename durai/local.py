"""Local Durai operator. Owns only durai-local resources and private state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / 'private'
IMAGE = 'sha256:7c2e75b32428228f33541f6312cf126feef4943fbbd5c30b3af667d70f8d3523'
CONTAINER = 'durai-local-db'
PORT = 54410


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + '\n', encoding='utf-8')


def config():
    path = PRIVATE / 'local.json'
    if not path.exists():
        save(path, {'passwords': {role: secrets.token_hex(24) for role in
             ('postgres', 'durai_runtime', 'durai_foreign', 'durai_reviewer',
              'durai_release', 'durai_evaluator')},
             'reviewKey': secrets.token_urlsafe(32), 'apiKey': secrets.token_urlsafe(32)})
    return json.loads(path.read_text(encoding='utf-8'))


def run(args, **kwargs):
    result = subprocess.run(args, cwd=ROOT, text=True, encoding='utf-8',
                            errors='replace', capture_output=True, **kwargs)
    if result.returncode:
        message = result.stdout + result.stderr
        for value in config()['passwords'].values():
            message = message.replace(value, '[REDACTED]')
        raise RuntimeError(message)
    return result.stdout.strip()


def python_path():
    return PRIVATE / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def deps():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, UV_CACHE_DIR=str(PRIVATE / 'uv-cache'))
    if not python_path().exists():
        print(run(['uv', 'venv', str(PRIVATE / 'venv'), '--python', sys.executable], env=env))
    print(run(['uv', 'pip', 'sync', '--python', str(python_path()), '--require-hashes',
               str(ROOT / 'requirements.lock')], env=env, timeout=600))


def up():
    cfg = config()
    env_file = PRIVATE / 'db.env'
    env_file.write_text('POSTGRES_PASSWORD=' + cfg['passwords']['postgres'] +
                        '\nPOSTGRES_DB=postgres\nPGDATA=/var/lib/postgresql/data/pgdata\n',
                        encoding='utf-8')
    found = run(['docker', 'ps', '-a', '--filter', 'name=^/' + CONTAINER + '$',
                 '--format', '{{.Names}}'])
    if not found:
        run(['docker', 'run', '-d', '--name', CONTAINER,
             '--label', 'com.durai.owner=local-trading', '--restart', 'unless-stopped',
             '-p', f'127.0.0.1:{PORT}:5432', '--env-file', str(env_file),
             '--mount', 'type=volume,source=durai-local-pgdata,target=/var/lib/postgresql/data',
             IMAGE, 'postgres', '-D', '/var/lib/postgresql/data/pgdata',
             '-c', 'shared_preload_libraries=age', '-c', 'listen_addresses=*'])
    else:
        label = run(['docker', 'inspect', '--format', '{{index .Config.Labels "com.durai.owner"}}', CONTAINER])
        actual = run(['docker', 'inspect', '--format', '{{.Image}}', CONTAINER])
        if label != 'local-trading' or actual != IMAGE:
            raise ValueError('Existing container identity differs. Preserve it.')
        run(['docker', 'start', CONTAINER])
    for _ in range(60):
        ready = subprocess.run(['docker', 'exec', CONTAINER, 'pg_isready', '-U', 'postgres'],
                               capture_output=True)
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Durai database did not become ready; inspect its private container logs.')
    # Use the venv for all database operations. Do not change pilot roles or databases.
    print(run([str(python_path()), str(ROOT / 'manage.py'), 'prepare'], timeout=120))


def start(port):
    endpoint = f'http://127.0.0.1:{port}/health'
    try:
        with urllib.request.urlopen(endpoint, timeout=2) as response:
            state = json.load(response)
        if state.get('application') != 'durai-trading-local':
            raise ValueError('The requested port belongs to another application')
        print('Durai trading sample is already running at ' + endpoint.removesuffix('/health'))
        return
    except OSError:
        pass
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP |
             subprocess.CREATE_BREAKAWAY_FROM_JOB) if os.name == 'nt' else 0
    with (logs / 'server.stdout.log').open('ab') as out, (logs / 'server.stderr.log').open('ab') as err:
        process = subprocess.Popen([str(python_path()), str(ROOT / 'manage.py'), 'serve', '--port', str(port)],
                                   cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   creationflags=flags, start_new_session=os.name != 'nt')
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError('Local server exited. Read durai/logs/server.stderr.log.')
        try:
            with urllib.request.urlopen(endpoint, timeout=1) as response:
                if json.load(response).get('application') == 'durai-trading-local':
                    save(PRIVATE / 'server.json', {'pid': process.pid, 'port': port,
                                                   'command': 'durai/manage.py serve'})
                    print('Durai trading sample: ' + endpoint.removesuffix('/health'))
                    return
        except OSError:
            time.sleep(.2)
    raise RuntimeError('Local server did not become ready')


def restart(port):
    record_path = PRIVATE / 'server.json'
    if record_path.exists():
        record = json.loads(record_path.read_text(encoding='utf-8'))
        if record['port'] != port:
            raise ValueError('The saved server belongs to another port')
        pid = int(record['pid'])
        script_path = str(ROOT / 'manage.py').replace("'", "''")
        script = fr"""$ErrorActionPreference='Stop'
$owned = Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'
if ($owned) {{
 if ($owned.CommandLine -notlike '*{script_path}*serve*--port {port}*') {{ throw 'Saved PID identity differs. Preserve it.' }}
 $children = Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}'
 foreach ($child in $children) {{
  if ($child.Name -eq 'conhost.exe' -and $child.ExecutablePath -eq (Join-Path $env:SystemRoot 'System32\conhost.exe')) {{ continue }}
  if ($child.CommandLine -notlike '*{script_path}*serve*--port {port}*') {{ throw 'Unexpected child process. Preserve it.' }}
  Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
 }}
 Stop-Process -Id {pid} -ErrorAction SilentlyContinue
}}
exit 0"""
        run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script])
    start(port)


def main():
    # Sample: Run ontology-status. Forward the read-only request to the private runtime.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['deps', 'up', 'test', 'demo', 'factory', 'status', 'serve', 'start', 'restart', 'approve', 'rollback', 'juicefs', 'sandbox', 'repair', 'verify', 'model-status', 'model-setup', 'model-fallbacks', 'model-provider-setup', 'model-provider-key', 'model-catalog', 'model-anthropic-setup', 'ontology-install', 'ontology-status', 'ontology-bind', 'ontology-export', 'ontology-import', 'greenfield', 'replica', 'five-phase'])
    parser.add_argument('--port', type=int, default=54420)
    parser.add_argument('--run-id')
    parser.add_argument('--spec', help='JSON specification for the qualified synthetic position adapter')
    parser.add_argument('--binding', help='Pinned ontology application binding identity')
    parser.add_argument('--model', action='store_true', help='Use the configured model for greenfield candidate coding')
    parser.add_argument('--with-trading', action='store_true', help='Also install the optional trading reference catalog')
    parser.add_argument('--capture-live', action='store_true', help='Read-only capture of this PC trading ontology, including private memory')
    parser.add_argument('--pack-file', help='Pack document, binding document, or exact pack reference JSON')
    parser.add_argument('--archive', help='Portable ontology SQLite archive')
    parser.add_argument('--digest', help='Expected archive manifest digest')
    parser.add_argument('--output', help='New export archive path')
    parser.add_argument('--fallback-model', action='append', default=[], help='Ordered primary-provider fallback ID; up to four')
    parser.add_argument('--provider', help='Optional provider profile ID')
    parser.add_argument('--base-url', help='Provider OpenAI-compatible API endpoint')
    parser.add_argument('--model-id', action='append', default=[], help='Provider role model ID; up to five')
    parser.add_argument('--role', choices=['coding', 'evaluation', 'design', 'context'], default='coding')
    parser.add_argument('--key-env', help='Provider-specific API key environment variable name')
    parser.add_argument('--disabled', action='store_true', help='Save a provider profile without enabling it')
    args = parser.parse_args()
    if args.action == 'deps':
        deps()
    elif args.action == 'up':
        up()
    elif args.action == 'start':
        start(args.port)
    elif args.action == 'restart':
        restart(args.port)
    elif args.action == 'verify':
        raise SystemExit(subprocess.call([str(python_path()), str(ROOT / 'tools/final_check.py')], cwd=ROOT))
    else:
        command = [str(python_path()), str(ROOT / 'manage.py'), args.action]
        if args.action == 'serve':
            command += ['--port', str(args.port)]
        if args.run_id:
            command += ['--run-id', args.run_id]
        if args.spec:
            command += ['--spec', str(Path(args.spec).resolve())]
        if args.binding:
            command += ['--binding', args.binding]
        if args.model:
            command += ['--model']
        for enabled, name in [(args.with_trading, '--with-trading'), (args.capture_live, '--capture-live')]:
            if enabled:
                command.append(name)
        for value, name in [(args.pack_file, '--pack-file'), (args.archive, '--archive'), (args.digest, '--digest'), (args.output, '--output')]:
            if value:
                command += [name, str(Path(value).resolve()) if name != '--digest' else value]
        for value in args.fallback_model:
            command += ['--fallback-model', value]
        for value in args.model_id:
            command += ['--model-id', value]
        for value, name in [(args.provider, '--provider'), (args.base_url, '--base-url'), (args.key_env, '--key-env')]:
            if value:
                command += [name, value]
        command += ['--role', args.role]
        if args.disabled:
            command.append('--disabled')
        # Stream long verification output and retain the child exit code.
        raise SystemExit(subprocess.call(command, cwd=ROOT))


if __name__ == '__main__':
    main()
