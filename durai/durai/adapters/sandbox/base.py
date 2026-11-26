"""Sample: Subclass SandboxBase. Write run(box, argv, stdin, seconds). The base keeps keys, files, events and states."""
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath

from ...common import digest, uid
from ...ports.errors import InvalidPath, InvalidSpec, NotFound, Unsupported

DEFAULTS = {'seconds': 20, 'outputBytes': 65536, 'memoryMiB': 128, 'cpus': 0.5, 'pids': 32}
SAFE_POLICIES = ({'network': 'none'},)


def run_bounded(command, stdin, seconds, cap, kill=None, env=None, cwd=None):
    # Sample: Run python -c "print(200)" with cap 1000 and 5 seconds. Return (0, b"200\n", None). A process over the cap returns reason output-limit. A slow one returns reason deadline.
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=cwd)
    output, overflow = bytearray(), threading.Event()

    def read_output():
        # Sample: Read 4096 bytes at a time. Stop at the cap and set the overflow flag.
        while chunk := process.stdout.read(4096):
            room = cap - len(output)
            output.extend(chunk[:max(room, 0)])
            if len(chunk) > room:
                overflow.set()
                break

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    reason = None
    try:
        try:
            process.stdin.write(stdin)
            process.stdin.close()
        except OSError:
            pass
        end = time.monotonic() + seconds
        while process.poll() is None:
            if overflow.is_set():
                reason = 'output-limit'
                break
            if time.monotonic() > end:
                reason = 'deadline'
                break
            time.sleep(.02)
    finally:
        if process.poll() is None:
            if kill:
                kill()
            process.kill()
        process.wait(timeout=10)
        reader.join(timeout=2)
        process.stdout.close()
    if reason is None and overflow.is_set():
        reason = 'output-limit'
    return process.returncode, bytes(output), reason


class SandboxBase:
    def __init__(self):
        # Sample: Start with no sandbox, no create key and no destroy key.
        self.boxes, self.create_keys, self.destroy_keys = {}, {}, set()

    def normalize(self, spec):
        # Sample: Spec {"seconds": 5} returns the defaults with seconds 5. Spec {"bogus": 1} or seconds 0 raises InvalidSpec.
        if not isinstance(spec, dict) or set(spec) - set(DEFAULTS):
            raise InvalidSpec('Spec has unknown fields: ' + ', '.join(sorted(set(spec) - set(DEFAULTS))) if isinstance(spec, dict) else 'Spec must be a dict')
        merged = dict(DEFAULTS, **spec)
        if any(not isinstance(value, (int, float)) or value <= 0 for value in merged.values()):
            raise InvalidSpec('Spec limits must be positive numbers')
        return merged

    def note(self, sandbox_id, kind, payload):
        # Sample: Record EXEC_ENDED for sbx-1 as event 3 with the digest of its payload.
        box = self.boxes[sandbox_id]
        box['events'].append({'sequence': len(box['events']) + 1, 'kind': kind, 'payload': payload, 'digest': digest(payload)})

    def get(self, sandbox_id):
        # Sample: Return the live sandbox sbx-1. A destroyed or unknown id raises NotFound.
        box = self.boxes.get(sandbox_id)
        if box is None or box['state'] == 'DESTROYED':
            raise NotFound(sandbox_id)
        return box

    def create(self, spec, key):
        # Sample: Create with key K-1. Return sbx-a. Create again with K-1. Return sbx-a. Key K-2 returns sbx-b.
        if key in self.create_keys:
            return self.create_keys[key]
        spec = self.normalize(spec)
        sandbox_id = 'sbx-' + uid().replace('-', '')[:12]
        self.boxes[sandbox_id] = {'spec': spec, 'folder': tempfile.mkdtemp(prefix='durai-sbx-'), 'state': 'RUNNING', 'events': []}
        self.create_keys[key] = sandbox_id
        self.note(sandbox_id, 'CREATED', {'spec': spec})
        return sandbox_id

    def safe_path(self, box, path):
        # Sample: Path program.py returns folder/program.py. Paths ../x, /etc/x and a:/x raise InvalidPath.
        pure = PurePosixPath(path.replace('\\', '/'))
        if not path or pure.is_absolute() or '..' in pure.parts or ':' in pure.parts[0]:
            raise InvalidPath(path)
        return Path(box['folder']).joinpath(*pure.parts)

    def write_file(self, sandbox_id, path, data):
        # Sample: Write program.py with 20 bytes. Record FILE_WRITTEN with the file digest.
        target = self.safe_path(self.get(sandbox_id), path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.note(sandbox_id, 'FILE_WRITTEN', {'path': path, 'digest': digest(data)})

    def read_file(self, sandbox_id, path):
        # Sample: Read program.py. Return its bytes. Read missing.py. Raise NotFound.
        target = self.safe_path(self.get(sandbox_id), path)
        if not target.is_file():
            raise NotFound(path)
        return target.read_bytes()

    def apply_policy(self, sandbox_id, policy, key):
        # Sample: Apply {"network": "none"}. Return. Apply an allow-list. Raise Unsupported, because this adapter cannot enforce it.
        self.get(sandbox_id)
        if policy not in SAFE_POLICIES:
            raise Unsupported('This adapter cannot enforce the policy')

    def events(self, sandbox_id):
        # Sample: Return the events of sbx-1, also after destroy. An unknown id raises NotFound.
        if sandbox_id not in self.boxes:
            raise NotFound(sandbox_id)
        return list(self.boxes[sandbox_id]['events'])

    def exec(self, sandbox_id, argv, stdin=b'', deadline=None):
        # Sample: Run python -c "print(200)". Return SUCCEEDED, exit code 0, output b"200\n". A timeout returns FAILED with reason deadline.
        box = self.get(sandbox_id)
        seconds = deadline or box['spec']['seconds']
        self.note(sandbox_id, 'EXEC_STARTED', {'argv': list(argv)})
        code, output, reason = self.run(box, list(argv), stdin, seconds)
        state = 'SUCCEEDED' if code == 0 and reason is None else 'FAILED'
        result = {'state': state, 'exitCode': code, 'output': output, 'truncated': reason == 'output-limit', 'reason': reason}
        self.note(sandbox_id, 'EXEC_ENDED', {'state': state, 'exitCode': code, 'reason': reason, 'outputDigest': digest(output)})
        return result

    def destroy(self, sandbox_id, key):
        # Sample: Destroy sbx-1. Record DESTROYED once. Destroy it again with another key. Return and record nothing.
        if sandbox_id not in self.boxes:
            raise NotFound(sandbox_id)
        box = self.boxes[sandbox_id]
        if box['state'] == 'DESTROYED' or key in self.destroy_keys:
            return
        self.destroy_keys.add(key)
        self.release(box)
        shutil.rmtree(box['folder'], ignore_errors=True)
        box['state'] = 'DESTROYED'
        self.note(sandbox_id, 'DESTROYED', {})

    def release(self, box):
        # Sample: Adapters with outside resources, such as a container, remove them here. The base does nothing.
        return None
