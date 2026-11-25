"""Linux container execution for candidate programs with no network or secrets."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
from .common import digest, uid, Conflict

ROOT = Path(__file__).resolve().parents[1]
TAG = 'python:3.13.7-slim-bookworm'


def prepare_image():
    path = ROOT / 'sandbox-image.lock.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))['imageId']
    print('Pulling the versioned Python sandbox image...', flush=True)
    subprocess.run(['docker', 'pull', TAG], check=True, capture_output=True, timeout=300)
    image = subprocess.check_output(['docker', 'image', 'inspect', TAG, '--format', '{{.Id}}'], text=True).strip()
    path.write_text(json.dumps({'tag': TAG, 'imageId': image,
                                'execution': 'non-root, network disabled, read-only root, no capabilities'}, indent=2) + '\n', encoding='utf-8')
    return image


class DockerRunner:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        lock = ROOT / 'sandbox-image.lock.json'
        if not lock.exists():
            raise ValueError('Prepare the sandbox image before candidate execution')
        self.image = json.loads(lock.read_text(encoding='utf-8'))['imageId']

    def execute(self, source, expected_digest, value):
        if digest(source) != expected_digest:
            raise Conflict('Candidate source digest differs')
        with tempfile.TemporaryDirectory(prefix='candidate-', dir=self.root) as folder:
            Path(folder, 'program.py').write_bytes(source)
            name = 'durai-sandbox-' + uid()
            command = ['docker', 'run', '--rm', '--name', name,
                       '--label', 'com.durai.owner=candidate-sandbox', '--network', 'none',
                       '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                       '--pids-limit', '32', '--memory', '128m', '--cpus', '0.5',
                       '--user', '65534:65534', '--log-driver', 'none',
                       '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
                       '--mount', 'type=bind,source=' + folder + ',target=/workspace,readonly',
                       '-i', self.image, 'python', '-I', '-S', '/workspace/program.py']
            # Bound host pipe output. Kill the exact container on deadline/overflow.
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
            import threading
            output, overflow = bytearray(), threading.Event()

            def read_output():
                while chunk := process.stdout.read(4096):
                    if len(output) + len(chunk) > 65536:
                        overflow.set()
                        break
                    output.extend(chunk)

            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            try:
                process.stdin.write(json.dumps(value).encode())
                process.stdin.close()
                deadline = time.monotonic() + 20
                while process.poll() is None:
                    if overflow.is_set() or time.monotonic() > deadline:
                        raise ValueError('Candidate exceeded output or time budget')
                    time.sleep(.05)
                reader.join(timeout=2)
                if overflow.is_set() or process.returncode:
                    raise ValueError('Candidate execution failed its contract')
                return json.loads(output)
            finally:
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)
                process.wait(timeout=10)
                reader.join(timeout=2)
                process.stdout.close()


def qualify():
    runner = DockerRunner(ROOT / 'private/docker-workspaces')
    source = b'''import os,json,socket
result={"uid":os.getuid(),"secretNames":[k for k in os.environ if any(x in k for x in ["PASSWORD","TOKEN","API_KEY","DSN","REVIEW_KEY"])]}
try:
    open('/root-write-test','w').write('x')
    result['rootWritable']=True
except OSError:
    result['rootWritable']=False
try:
    open('/workspace/program.py','a').write('x')
    result['sourceWritable']=True
except OSError:
    result['sourceWritable']=False
print(json.dumps(result))
'''
    result = runner.execute(source, digest(source), {})
    if result != {'uid': 65534, 'secretNames': [], 'rootWritable': False, 'sourceWritable': False}:
        raise ValueError('Linux sandbox boundary differs')
    receipt = {'state': 'PASS', 'provider': 'docker-linux', 'imageId': runner.image,
               'checks': result, 'network': 'none', 'capabilities': 'none',
               'limits': {'memoryMiB': 128, 'cpu': .5, 'pids': 32, 'seconds': 20, 'outputBytes': 65536},
               'scope': 'Local container boundary; independent VM/kernel-escape qualification remains pending.'}
    (ROOT / 'receipts/sandbox.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt
