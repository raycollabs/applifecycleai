"""Sample: Make LocalFolderBlobStore on a folder. Put b"abc" at a/b.bin. Get b"abc". The digest equals the SHA-256 of b"abc"."""
import hashlib
import json
from pathlib import Path, PurePosixPath

from ...ports.errors import Conflict, InvalidPath, NotFound

KEY_FILE = '.durai-blob-keys.json'


class LocalFolderBlobStore:
    def __init__(self, root):
        # Sample: Open the folder. Create it when it is missing. Read the applied keys when the key file exists.
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        key_path = self.root / KEY_FILE
        self.keys = json.loads(key_path.read_text(encoding='utf-8')) if key_path.exists() else {}

    def target(self, path):
        # Sample: Path a/b.bin returns root/a/b.bin. Path ../x or /x or a path with a drive raises InvalidPath.
        clean = PurePosixPath(str(path).replace('\\', '/'))
        if not str(path).strip() or clean.is_absolute() or '..' in clean.parts or ':' in clean.parts[0]:
            raise InvalidPath(str(path))
        return self.root.joinpath(*clean.parts)

    def put(self, path, data, key):
        # Sample: Put b"abc" at a/b.bin with key B-1. Return the SHA-256. Put it again with key B-1. Return the same digest. Put b"xyz" at a/b.bin with key B-2. Raise Conflict.
        if key in self.keys:
            return self.keys[key]
        target = self.target(path)
        digest = hashlib.sha256(data).hexdigest()
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise Conflict(str(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.keys[key] = digest
        (self.root / KEY_FILE).write_text(json.dumps(self.keys), encoding='utf-8')
        return digest

    def get(self, path):
        # Sample: Get a/b.bin. Return b"abc". Get x/none.bin. Raise NotFound.
        target = self.target(path)
        if not target.is_file():
            raise NotFound(str(path))
        return target.read_bytes()

    def presign(self, path, seconds):
        # Sample: Presign a/b.bin for 60 seconds. Return a file link that ends with ?expires=60. Presign x/none.bin. Raise NotFound.
        target = self.target(path)
        if not target.is_file():
            raise NotFound(str(path))
        return target.resolve().as_uri() + '?expires=' + str(int(seconds))
