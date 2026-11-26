"""Sample: Make LocalProcessSandbox for tests. Run python -c "print(200)" in a temp folder. It gives no isolation. Never use it for candidate code."""
import os
import sys

from .base import SandboxBase, run_bounded


class LocalProcessSandbox(SandboxBase):
    ISOLATED = False

    def run(self, box, argv, stdin, seconds):
        # Sample: Replace the word python with this interpreter. Pass only PATH and SYSTEMROOT. Run in the sandbox folder.
        command = [sys.executable if part == 'python' and index == 0 else part for index, part in enumerate(argv)]
        env = {name: os.environ[name] for name in ('PATH', 'SYSTEMROOT') if name in os.environ}
        return run_bounded(command, stdin, seconds, box['spec']['outputBytes'], env=env, cwd=box['folder'])
