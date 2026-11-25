"""Sample: Run deploy_next_build.py up --workspace W-4 --project durai-app-deepseek-r3 --port 54370. Check http://127.0.0.1:54370/ answers 200 and the sign-in page lists the traders."""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy_blind_build as base  # noqa: E402

NODE_IMAGE = 'durai-eval-node:1'
NODE_BIN = '/opt/durai/node_modules/.bin'
MOUNT = '/opt/durai/app'
SKIP = ('node_modules', '.next', '.vitest', '__pycache__')
API_START = (
    "python -c 'import importlib, importlib.util; "
    "m = importlib.import_module(\"src.app\") if importlib.util.find_spec(\"src.app\") else None; "
    "print(m.prepare(), flush=True) if m and hasattr(m, \"prepare\") else None' "
    "&& exec python -m src.api"
)
WEB_START = NODE_BIN + '/next build && exec ' + NODE_BIN + '/next start -H 0.0.0.0 -p 8000'


def web_name(project):
    # Sample: Project durai-app-r3. Return container durai-app-r3-web.
    return project + '-web'


def copy_for_web(workspace, project):
    # Sample: Workspace W-4, project durai-app-r3. Copy the app to private/deploy/durai-app-r3/web without node_modules and .next. Return that folder.
    target = base.PRIVATE / project / 'web'
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(workspace, target, ignore=shutil.ignore_patterns(*SKIP))
    return target


def up(workspace, project, port, wait=240):
    # Sample: Workspace W-4, project durai-app-r3, port 54370. Start the database, the API with the example data, and the Next.js server in the API network. Return status 200.
    base.check_project(project)
    root = Path(workspace).resolve()
    if not (root / 'package.json').is_file() or not (root / 'src' / 'api.py').is_file():
        raise ValueError('Workspace needs package.json and src/api.py')
    name = base.names(project)
    label = ['--label', 'com.durai.owner=' + project]
    db_env, app_env = base.secret_files(project)
    if not base.exists('network', name['network']):
        base.docker('network', 'create', *label, name['network'])
    if not base.exists('container', name['db']):
        started = base.docker('run', '-d', '--name', name['db'], *label, '--restart', 'unless-stopped', '--network', name['network'],
                              '--network-alias', 'db', '--env-file', str(db_env), '-v', name['volume'] + ':/var/lib/postgresql/data',
                              base.DATABASE_IMAGE)
        if started.returncode:
            raise RuntimeError('Database failed: ' + started.stderr.strip()[-300:])
    else:
        base.docker('start', name['db'])
    while base.docker('exec', name['db'], 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres').returncode:
        pass
    base.docker('rm', '-f', web_name(project), name['app'])
    api = base.docker('run', '-d', '--name', name['app'], *label, '--restart', 'unless-stopped', '--network', name['network'],
                      '--read-only', '--cap-drop', 'ALL', '--user', '65534:65534', '--tmpfs', '/tmp:rw,nosuid,size=64m',
                      '--env-file', str(app_env), '--mount', 'type=bind,source=' + str(root) + ',target=/workspace,readonly',
                      '-w', '/workspace', '-p', '127.0.0.1:' + str(int(port)) + ':8000',
                      base.IMAGE, 'sh', '-c', API_START)
    if api.returncode:
        raise RuntimeError('API failed: ' + api.stderr.strip()[-300:])
    folder = copy_for_web(root, project)
    web = base.docker('run', '-d', '--name', web_name(project), *label, '--restart', 'unless-stopped',
                      '--network', 'container:' + name['app'], '--cap-drop', 'ALL', '--tmpfs', '/tmp:rw,nosuid,size=256m',
                      '-e', 'HOME=/tmp', '-e', 'NEXT_TELEMETRY_DISABLED=1',
                      '--mount', 'type=bind,source=' + str(folder) + ',target=' + MOUNT, '-w', MOUNT,
                      NODE_IMAGE, 'sh', '-c', WEB_START)
    if web.returncode:
        raise RuntimeError('Web failed: ' + web.stderr.strip()[-300:])
    status = base.probe(port, wait)
    if status != 200:
        logs = base.docker('logs', '--tail', '30', web_name(project))
        raise RuntimeError('Web did not answer 200. Log tail:\n' + (logs.stdout + logs.stderr)[-1500:])
    return {'project': project, 'url': 'http://127.0.0.1:' + str(int(port)), 'status': status}


def down(project):
    # Sample: Project durai-app-r3. Remove the web, API and database containers and the network. Keep the volume.
    base.check_project(project)
    name = base.names(project)
    base.docker('rm', '-f', web_name(project), name['app'], name['db'])
    base.docker('network', 'rm', name['network'])
    return {'project': project, 'removed': True, 'volumeKept': True}


def main(argv=None):
    # Sample: Run up --workspace W-4 --project durai-app-r3 --port 54370. Print one JSON line. Exit 0.
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('up', 'down'))
    parser.add_argument('--workspace')
    parser.add_argument('--project', required=True)
    parser.add_argument('--port', type=int, default=54370)
    arguments = parser.parse_args(argv)
    try:
        result = up(arguments.workspace, arguments.project, arguments.port) if arguments.action == 'up' else down(arguments.project)
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
