"""Sample: Create a team folder from base journal.py. Write test_journal.py. After a restart open the folder. Check both files and the base digest."""
import json
import shutil
import time
from pathlib import Path
from .common import Conflict
from .strategy_subagents import as_bytes, package_digest

MARKER = '.durai-workspace.json'


class WorkspaceLost(Conflict):
    pass


class DiskWorkspace:
    def __init__(self, root, base=None, reopen=False):
        # Sample: Create folder R-1 from base files and record the base digest in a marker file. With reopen, require the folder and a matching marker. Else raise WORKSPACE_LOST.
        self.root = Path(root)
        if reopen:
            marker = self.root / MARKER
            if not self.root.is_dir() or not marker.is_file():
                raise WorkspaceLost('WORKSPACE_LOST: folder ' + str(self.root) + ' or its marker is missing')
            self.base_digest = json.loads(marker.read_text(encoding='utf-8'))['baseDigest']
            if base is not None and package_digest({path: as_bytes(value) for path, value in base.items()}) != self.base_digest:
                raise WorkspaceLost('WORKSPACE_LOST: base digest of ' + str(self.root) + ' differs from the step base')
            return
        base = {path: as_bytes(value) for path, value in (base or {}).items()}
        if self.root.exists() and any(self.root.iterdir()):
            raise WorkspaceLost('WORKSPACE_LOST: folder ' + str(self.root) + ' already holds files')
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_digest = package_digest(base)
        for path, value in base.items():
            self.write(path, value)
        (self.root / MARKER).write_text(json.dumps({'baseDigest': self.base_digest}), encoding='utf-8')

    def write(self, path, value):
        # Sample: Write /workspace/journal.py with "x = 2". Read it back as b"x = 2".
        if '..' in path.split('/'):
            raise Conflict('Workspace path escapes the folder: ' + path)
        target = self.root / path.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(as_bytes(value))

    def read(self, path):
        # Sample: Read /workspace/journal.py after a write of "x = 2". Return b"x = 2".
        return (self.root / path.lstrip('/')).read_bytes()

    def files(self):
        # Sample: Return every file under the folder except the marker, keyed as /workspace/journal.py.
        return {'/' + item.relative_to(self.root).as_posix(): item.read_bytes()
                for item in sorted(self.root.rglob('*')) if item.is_file() and item.name != MARKER}

    def missing_base(self, base):
        # Sample: Read base journal.py and test_journal.py. Return the base paths that are no longer files in the folder.
        return sorted(path for path in base if not (self.root / path.lstrip('/')).is_file())

    def keep(self):
        # Sample: Mark folder R-1 as kept after a failed team run. Write keptAt into the marker. Files and base digest stay unchanged.
        marker = self.root / MARKER
        data = json.loads(marker.read_text(encoding='utf-8'))
        data['keptAt'] = time.time_ns()
        marker.write_text(json.dumps(data), encoding='utf-8')

    def close(self):
        # Sample: Remove the folder of the step when the team reaches a terminal state.
        shutil.rmtree(self.root, ignore_errors=True)
