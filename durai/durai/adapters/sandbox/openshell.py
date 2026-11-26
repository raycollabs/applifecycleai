"""Sample: Make OpenShellSandbox with the openshell command. Create a sandbox with an egress allow-list. Run python3 inside. Keys stay as placeholders."""
import math
import os
import shlex
import subprocess
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

from ...common import digest
from ...egress_policy import build_policy, endpoint_argument, parse_allow, to_yaml
from ...ports.errors import InvalidPath, NotFound, TelemetryEnabled, ToolMissing, Unsupported
from .base import SandboxBase, run_bounded

TELEMETRY = 'OPENSHELL_TELEMETRY_ENABLED'
WORKDIR = '/tmp/work'
DEFAULT_IMAGE = 'nvcr.io/nvidia/base/ubuntu:24.04'
READY_SECONDS = 120
TIMEOUT_EXIT = 124


def telemetry_off(environ):
    # Sample: Variable unset or false returns True. Variable true returns False.
    return str(environ.get(TELEMETRY, 'false')).strip().lower() in ('false', '0', 'no', 'off')


def cli_path(path, cli):
    # Sample: With the wsl prefix, C:\Users\a\p.yaml becomes /mnt/c/Users/a/p.yaml. Without it the path stays the same.
    if os.path.basename(cli[0]).lower() in ('wsl', 'wsl.exe') and len(str(path)) > 1 and str(path)[1] == ':':
        windows = PureWindowsPath(str(path))
        return '/mnt/' + windows.drive[0].lower() + '/' + '/'.join(windows.parts[1:])
    return str(path)


class OpenShellSandbox(SandboxBase):
    ISOLATED = True
    SUPPORTS_EGRESS = True

    def __init__(self, cli=None, image=DEFAULT_IMAGE, providers=(), allow=(), environ=None, runner=run_bounded, spawn=subprocess.Popen):
        # Sample: Read the command from DURAI_OPENSHELL_CLI or use openshell. Refuse a telemetry flag that is on. Force the flag off for every call.
        super().__init__()
        environ = os.environ if environ is None else environ
        if not telemetry_off(environ):
            raise TelemetryEnabled(TELEMETRY + ' must be false')
        command = cli or environ.get('DURAI_OPENSHELL_CLI') or 'openshell'
        self.cli = list(command) if isinstance(command, (list, tuple)) else shlex.split(command, posix=os.name != 'nt')
        self.image, self.providers, self.allow, self.runner = image, tuple(providers), [parse_allow(item) for item in allow], runner
        self.spawn = spawn
        self.env = dict(environ)
        self.env[TELEMETRY] = 'false'
        self.env['WSLENV'] = ':'.join(filter(None, [self.env.get('WSLENV'), TELEMETRY]))

    def call(self, args, stdin=b'', seconds=60, cap=65536):
        # Sample: Run openshell sandbox get S-1. Return exit code, output and reason. A missing command raises ToolMissing.
        try:
            return self.runner(self.cli + list(args), stdin, seconds, cap, env=self.env)
        except FileNotFoundError:
            raise ToolMissing('The openshell command is not installed: ' + self.cli[0]) from None

    def create(self, spec, key):
        # Sample: Create with key K-1. Start the sandbox with the policy file. Wait for phase Ready. A failed start removes the sandbox and the key.
        if key in self.create_keys:
            return self.create_keys[key]
        sandbox_id = super().create(spec, key)
        box = self.boxes[sandbox_id]
        try:
            self.launch(sandbox_id, box, spec)
        except BaseException:
            self.create_keys.pop(key, None)
            self.release(box)
            box['state'] = 'DESTROYED'
            raise
        return sandbox_id

    def launch(self, sandbox_id, box, spec):
        # Sample: Write policy.yaml in the box folder. Start openshell sandbox create in the background with sleep. Poll get until Ready. Make /tmp/work.
        policy = Path(box['folder']) / 'policy.yaml'
        policy.write_text(to_yaml(build_policy(self.allow)), encoding='utf-8')
        command = self.cli + ['sandbox', 'create', '--name', sandbox_id, '--from', self.image, '--policy', cli_path(str(policy), self.cli)]
        for provider in self.providers:
            command += ['--provider', provider]
        if 'cpus' in spec:
            command += ['--cpu', str(spec['cpus'])]
        if 'memoryMiB' in spec:
            command += ['--memory', str(int(spec['memoryMiB'])) + 'Mi']
        command += ['--', 'sleep', '86400']
        try:
            box['process'] = self.spawn(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self.env)
        except FileNotFoundError:
            raise ToolMissing('The openshell command is not installed: ' + self.cli[0]) from None
        end, last = time.monotonic() + READY_SECONDS, b''
        while time.monotonic() < end:
            if box['process'].poll() is not None:
                raise RuntimeError('openshell sandbox create ended before Ready (exit ' + str(box['process'].returncode) + ')')
            code, last, _ = self.call(['sandbox', 'get', sandbox_id], seconds=20)
            if b'Phase: Ready' in last:
                break
            time.sleep(1)
        else:
            raise TimeoutError('Sandbox did not reach Ready: ' + last.decode('utf-8', 'replace')[-200:])
        code, output, _ = self.call(['sandbox', 'exec', '-n', sandbox_id, '--no-tty', '--no-login-shell', '--', 'mkdir', '-p', WORKDIR], seconds=30)
        if code != 0:
            raise RuntimeError('Could not make the work folder: ' + output.decode('utf-8', 'replace')[-200:])

    def run(self, box, argv, stdin, seconds):
        # Sample: Run python -I -c "print(200)". Send it as python3. Pass the deadline as --timeout. Exit 124 means deadline. Output over the cap means output-limit.
        name = self.name_of(box)
        argv = ['python3' if index == 0 and part == 'python' else part for index, part in enumerate(argv)]
        args = ['sandbox', 'exec', '-n', name, '--no-tty', '--no-login-shell', '--workdir', WORKDIR,
                '--timeout', str(max(1, math.ceil(seconds))), '--'] + argv
        code, output, reason = self.call(args, stdin, seconds + 10, box['spec']['outputBytes'])
        if reason is None and code == TIMEOUT_EXIT:
            reason = 'deadline'
        return code, output, reason

    def name_of(self, box):
        # Sample: Return the id of the box, such as sbx-1a2b. The sandbox carries the same name in the gateway.
        return next(key for key, value in self.boxes.items() if value is box)

    def remote_path(self, path):
        # Sample: Path program.py returns /tmp/work/program.py. Paths ../x and /etc/x raise InvalidPath.
        pure = PurePosixPath(path.replace('\\', '/'))
        if not path or pure.is_absolute() or '..' in pure.parts or ':' in pure.parts[0]:
            raise InvalidPath(path)
        return WORKDIR + '/' + '/'.join(pure.parts)

    def write_file(self, sandbox_id, path, data):
        # Sample: Write program.py. Send the bytes on stdin to a shell that makes the folder and writes the file. Record FILE_WRITTEN.
        self.get(sandbox_id)
        target = self.remote_path(path)
        code, output, _ = self.call(['sandbox', 'exec', '-n', sandbox_id, '--no-tty', '--no-login-shell', '--', 'sh', '-c',
                                     'mkdir -p "$(dirname "$1")" && cat > "$1"', 'sh', target], data, 60)
        if code != 0:
            raise RuntimeError('Write failed: ' + output.decode('utf-8', 'replace')[-200:])
        self.note(sandbox_id, 'FILE_WRITTEN', {'path': path, 'digest': digest(data)})

    def read_file(self, sandbox_id, path):
        # Sample: Read program.py with cat. A missing file raises NotFound.
        self.get(sandbox_id)
        code, output, _ = self.call(['sandbox', 'exec', '-n', sandbox_id, '--no-tty', '--no-login-shell', '--', 'cat', self.remote_path(path)], seconds=60, cap=16 * 1024 * 1024)
        if code != 0:
            raise NotFound(path)
        return output

    def apply_policy(self, sandbox_id, policy, key):
        # Sample: Apply {"network": "none"}. Return. Apply {"network": {"allow": ["example.com:443"]}}. Run openshell policy update with --wait. Anything else raises Unsupported.
        self.get(sandbox_id)
        network = policy.get('network') if isinstance(policy, dict) else None
        if network == 'none':
            return
        if not isinstance(network, dict) or set(policy) != {'network'} or set(network) != {'allow'}:
            raise Unsupported('This adapter applies only network none or a network allow-list')
        for item in network['allow']:
            entry = parse_allow(item)
            args = ['policy', 'update', sandbox_id, '--add-endpoint', endpoint_argument(entry), '--rule-name', 'egress_' + digest(key)[:8], '--wait']
            for path in entry['binaries']:
                args += ['--binary', path]
            code, output, _ = self.call(args, seconds=90)
            if code != 0:
                raise RuntimeError('Policy update failed: ' + output.decode('utf-8', 'replace')[-300:])
        self.note(sandbox_id, 'POLICY_APPLIED', {'allow': [parse_allow(item)['host'] + ':' + str(parse_allow(item)['port']) for item in network['allow']]})

    def release(self, box):
        # Sample: Delete the sandbox in the gateway. Stop the background create process.
        try:
            name = self.name_of(box)
            self.call(['sandbox', 'delete', name], seconds=60)
        except (StopIteration, ToolMissing):
            pass
        process = box.get('process')
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if process is not None and process.stdout:
            process.stdout.close()
