"""Sample: Start database E-1 on internal network N-1. Apply Durai migrations. Remove E-1 and N-1 after the verdict."""
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
from .common import uid
from .db import connect, prepare, use_server

IMAGE = 'sha256:7c2e75b32428228f33541f6312cf126feef4943fbbd5c30b3af667d70f8d3523'
ROLES = ('postgres', 'durai_runtime', 'durai_foreign', 'durai_reviewer', 'durai_release', 'durai_evaluator')
DATABASE = 'durai_test'


def docker(*args, timeout=120):
    # Sample: Run docker network ls. Return its text output. Raise on a failed command.
    return subprocess.run(['docker', *args], capture_output=True, text=True, check=True, timeout=timeout).stdout.strip()


class EphemeralDatabase:
    def __init__(self, scratch=None, owned=()):
        # Sample: Name database E-1 and network N-1 from one random ID. Keep owned migration core_0010.sql out of the trusted set. Create no container yet.
        self.owned = frozenset(owned)
        ident = uid().replace('-', '')[:12]
        self.name, self.network = 'durai-eval-db-' + ident, 'durai-eval-net-' + ident
        self.folder = Path(tempfile.mkdtemp(prefix='durai-evaldb-', dir=scratch))
        self.settings_path, self.port = self.folder / 'settings.json', None
        self.candidate_path = self.folder / 'candidate.json'

    def __enter__(self):
        # Sample: Create N-1. Start E-1 with tmpfs data and random passwords. Apply migrations. Remove E-1 from the bridge network.
        passwords = {role: secrets.token_hex(24) for role in ROLES}
        self.settings_path.write_text(json.dumps({'passwords': passwords}), encoding='utf-8')
        self.candidate_path.write_text(json.dumps({'passwords': {role: value for role, value in passwords.items()
                                                                 if role != 'postgres'}}), encoding='utf-8')
        env_file = self.folder / 'db.env'
        env_file.write_text('POSTGRES_PASSWORD=' + passwords['postgres'] + '\nPOSTGRES_DB=postgres\n'
                            'PGDATA=/var/lib/postgresql/data/pgdata\n', encoding='utf-8')
        try:
            docker('network', 'create', '--internal', '--label', 'com.durai.owner=task-evaluator', self.network)
            docker('run', '-d', '--name', self.name, '--label', 'com.durai.owner=task-evaluator',
                   '-p', '127.0.0.1::5432', '--env-file', str(env_file), '--tmpfs', '/var/lib/postgresql/data:rw,size=512m',
                   IMAGE, 'postgres', '-D', '/var/lib/postgresql/data/pgdata',
                   '-c', 'shared_preload_libraries=age', '-c', 'listen_addresses=*')
            env_file.unlink()
            docker('network', 'connect', '--alias', 'durai-db', self.network, self.name)
            self.port = int(docker('port', self.name, '5432/tcp').splitlines()[0].rsplit(':', 1)[1])
            self.wait()
            with use_server('127.0.0.1', self.port, self.settings_path):
                prepare(DATABASE, self.owned)
                from .harness import prepare_checkpoints
                prepare_checkpoints(DATABASE)
            docker('network', 'disconnect', 'bridge', self.name)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def wait(self, seconds=120):
        # Sample: Poll E-1 every second for 120 seconds. Return after one successful login as supabase_admin.
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                with use_server('127.0.0.1', self.port, self.settings_path):
                    with connect('postgres', 'postgres') as connection:
                        connection.execute('SELECT 1')
                return
            except Exception:
                time.sleep(1)
        raise TimeoutError('Ephemeral database did not become ready')

    def apply(self, files, suite, image, seconds=120, candidate=()):
        # Sample: Add candidate migration core_0010.sql and seed 001_packs.sql. Run them as superuser on N-1. Return exit code 0. Fail core_0010.sql. Return exit code 4.
        from .db import ROOT
        from .common import Conflict
        from .process_owner import OwnedProcess
        if not self.settings_path.exists():
            raise RuntimeError('Ephemeral database is sealed')
        runner = self.folder / 'runner'
        (runner / 'durai').mkdir(parents=True)
        (runner / 'durai/__init__.py').write_bytes(b'')
        (runner / 'durai/db.py').write_bytes((ROOT / 'durai/db.py').read_bytes())
        shutil.copytree(ROOT / 'migrations/core', runner / 'migrations/core', ignore=lambda folder, names: sorted(self.owned & set(names)))
        for path, content in files.items():
            if path.startswith('/workspace/migrations/core/') and path.endswith('.sql') and path.count('/') == 4:
                (runner / 'migrations/core' / path.rsplit('/', 1)[1]).write_bytes(content)
        (runner / 'seed').mkdir()
        for path, content in suite.items():
            if path.startswith('/workspace/tests/seed/') and path.endswith('.sql') and path.count('/') == 4:
                (runner / 'seed' / path.rsplit('/', 1)[1]).write_bytes(content)
        script = ("import json, pathlib, sys, traceback\nfrom durai.db import connect, prepare\n"
                  "candidate = set(json.loads(sys.argv[1]))\n"
                  "names = sorted(item.name for item in pathlib.Path('/runner/migrations/core').glob('*.sql'))\n"
                  "def stop(error, code):\n"
                  "    traceback.print_exc()\n"
                  "    sys.exit(code if isinstance(error, ValueError) or getattr(error, 'sqlstate', None) else 6)\n"
                  "for index, name in enumerate(names):\n"
                  "    try:\n"
                  "        prepare('durai_test', set(names[index + 1:]))\n"
                  "    except Exception as error:\n"
                  "        stop(error, 4 if name in candidate else 3)\n"
                  "for seed in sorted(pathlib.Path('/runner/seed').glob('*.sql')):\n"
                  "    try:\n"
                  "        with connect('postgres', 'durai_test') as connection:\n"
                  "            connection.execute(seed.read_text(encoding='utf-8'), prepare=False)\n"
                  "    except Exception as error:\n"
                  "        stop(error, 5)\n"
                  "print('applied')\n")
        names = sorted(path.rsplit('/', 1)[1] for path in candidate
                       if path.startswith('/workspace/migrations/core/') and path.endswith('.sql') and path.count('/') == 4)
        name = 'durai-eval-runner-' + uid()[:12]
        process = OwnedProcess(['docker', 'run', '--rm', '--name', name, '--label', 'com.durai.owner=task-evaluator',
                                '--network', self.network, '--read-only', '--cap-drop', 'ALL', '--user', '65534:65534',
                                '--tmpfs', '/tmp:rw,nosuid,size=16m', '-e', 'PYTHONPYCACHEPREFIX=/tmp/pyc', '-e', 'HOME=/tmp',
                                '-e', 'DURAI_DB_HOST=durai-db', '-e', 'DURAI_DB_PORT=5432', '-e', 'DURAI_SETTINGS=/run/durai/admin.json',
                                '--mount', 'type=bind,source=' + str(self.settings_path) + ',target=/run/durai/admin.json,readonly',
                                '--mount', 'type=bind,source=' + str(runner) + ',target=/runner,readonly',
                                '-w', '/runner', image, 'python', '-c', script, json.dumps(names)])
        try:
            try:
                code = process.wait(seconds)
            except Conflict:
                return {'exitCode': None, 'output': process.text()[-4000:], 'timedOut': True}
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
            shutil.rmtree(runner, ignore_errors=True)
        return {'exitCode': code, 'output': process.text()[-4000:]}

    def seal(self):
        # Sample: Delete the superuser settings of E-1. Keep the candidate settings.
        if self.settings_path.exists():
            self.settings_path.unlink()

    def candidate_args(self):
        # Sample: Return docker options for candidate C-1: network N-1, host durai-db, port 5432, and settings mounted read-only.
        return ['--network', self.network, '-e', 'DURAI_DB_HOST=durai-db', '-e', 'DURAI_DB_PORT=5432',
                '-e', 'DURAI_SETTINGS=/run/durai/settings.json',
                '--mount', 'type=bind,source=' + str(self.candidate_path) + ',target=/run/durai/settings.json,readonly']

    def networks(self):
        # Sample: Inspect E-1 after setup. Return only network N-1.
        return sorted(json.loads(docker('inspect', self.name, '--format', '{{json .NetworkSettings.Networks}}')))

    def __exit__(self, kind, value, trace):
        # Sample: Remove container E-1 and network N-1. Delete the throwaway settings. Ignore parts that do not exist.
        subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True, timeout=60)
        subprocess.run(['docker', 'network', 'rm', self.network], capture_output=True, timeout=60)
        shutil.rmtree(self.folder, ignore_errors=True)
        return False
