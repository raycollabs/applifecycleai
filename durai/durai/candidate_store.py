"""Sample: Retain attempt T-1 1 with a.py. Restore it by digest. Check the same a.py bytes."""
from pathlib import Path
import re
from .common import Conflict
from .files import export_package, import_package
from .workspace_manifest import manifest


class CandidateStore:
    def __init__(self, root):
        # Sample: Open the store at private/candidates. Create the folder.
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def retain(self, attempt_id, task, files):
        # Sample: Retain attempt T-1 1 with a.py and asset.bin. Return the package path and digest.
        name = re.sub(r'[^A-Za-z0-9_.\-]', '_', attempt_id) + '.sqlite'
        path = self.root / name
        content = {'files' + key: value for key, value in files.items()}
        checksum = export_package(path, content, {'format': 'durai-candidate/v1', 'attemptId': attempt_id,
                                                  'taskDigest': task['digest'], 'manifestDigest': manifest(files)['digest']})
        return {'path': str(path), 'digest': checksum}

    def restore(self, path, digest):
        # Sample: Restore the T-1 1 package with digest D-1. Return /workspace/a.py. Refuse digest D-2.
        files, document = import_package(path, digest)
        restored = {name[len('files'):]: value for name, value in files.items()}
        if manifest(restored)['digest'] != document['bindings']['manifestDigest']:
            raise Conflict('Candidate manifest differs')
        return restored
