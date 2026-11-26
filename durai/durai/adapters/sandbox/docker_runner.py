"""Sample: Make DockerSandbox. Run python -c "print(200)" in a container with no network, a read-only root and no capabilities."""
import json
import subprocess

from ...common import uid
from ...sandbox import ROOT
from .base import SandboxBase, run_bounded

LOCK = ROOT / 'sandbox-image.lock.json'


def image_ready():
    # Sample: Return the pinned image id when the lock file exists and Docker holds the image. Return None otherwise.
    if not LOCK.exists():
        return None
    image = json.loads(LOCK.read_text(encoding='utf-8'))['imageId']
    try:
        done = subprocess.run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return image if done.returncode == 0 else None


class DockerSandbox(SandboxBase):
    ISOLATED = True

    def __init__(self):
        # Sample: Read the pinned image id. Raise ValueError that names the prepare step when the image is missing.
        super().__init__()
        self.image = image_ready()
        if self.image is None:
            raise ValueError('Prepare the sandbox image before candidate execution')

    def command(self, box, name, argv):
        # Sample: Build docker run with network none, read-only root, no capabilities, 32 pids, 128 MiB, 0.5 CPU, user 65534 and the workspace mounted read-only.
        spec = box['spec']
        return ['docker', 'run', '--rm', '--name', name, '--label', 'com.durai.owner=sandbox-port', '--network', 'none',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--pids-limit', str(int(spec['pids'])), '--memory', str(int(spec['memoryMiB'])) + 'm', '--cpus', str(spec['cpus']),
                '--user', '65534:65534', '--log-driver', 'none', '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m',
                '--mount', 'type=bind,source=' + box['folder'] + ',target=/workspace,readonly', '-w', '/workspace',
                '-i', self.image, *argv]

    def run(self, box, argv, stdin, seconds):
        # Sample: Start a named container. On deadline or output overflow remove that exact container.
        name = 'durai-sbx-' + uid().replace('-', '')[:12]

        def kill():
            # Sample: Try docker rm -f for 45 seconds. On a timeout try docker kill, then docker rm -f for 60 seconds. Never raise.
            for command, seconds in ((['docker', 'rm', '-f', name], 45), (['docker', 'kill', name], 30), (['docker', 'rm', '-f', name], 60)):
                try:
                    done = subprocess.run(command, capture_output=True, timeout=seconds)
                except (subprocess.TimeoutExpired, OSError):
                    continue
                if command[1] == 'rm' and done.returncode == 0:
                    return

        try:
            return run_bounded(self.command(box, name, argv), stdin, seconds, box['spec']['outputBytes'], kill=kill)
        finally:
            kill()
