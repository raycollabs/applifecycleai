"""Sample: Materialize asset.bin with bytes 00 FF 0D 0A. Capture it. Check the same bytes and digest."""
from pathlib import Path
from .common import Conflict, digest
from .task_contract import check_path, matches


def visible(task, files):
    # Sample: Read /workspace/** and deny /workspace/tests/**. Return a.py and omit tests/holdout.py.
    return {path: content for path, content in files.items()
            if matches(path, task['readPaths']) and not matches(path, task['deniedPaths'])}


def manifest(files):
    # Sample: Hash a.py and asset.bin. Return two file digests and one manifest digest.
    entries = {path: digest(content) for path, content in sorted(files.items())}
    return {'files': entries, 'digest': digest(entries)}


def materialize(root, files):
    # Sample: Write /workspace/a.py with CRLF into an empty root. Check the same bytes on disk.
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise Conflict('Workspace root must be empty')
    for path, content in files.items():
        check_path(path)
        if not isinstance(content, bytes):
            raise Conflict('Workspace content must be bytes: ' + path)
        target = root / path.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return manifest(files)


def capture(root):
    # Sample: Capture a root with a.py and asset.bin. Return both as /workspace paths. Refuse a symlink.
    root = Path(root).resolve()
    files = {}
    for item in sorted(root.rglob('*')):
        if item.is_symlink():
            raise Conflict('Workspace contains a symbolic link: ' + item.relative_to(root).as_posix())
        if item.is_file():
            files['/' + item.relative_to(root).as_posix()] = item.read_bytes()
    return files


def verify(root, expected):
    # Sample: Capture a root after adding extra.py. Compare with the earlier manifest. Refuse it.
    actual = manifest(capture(root))
    if actual['digest'] != expected['digest']:
        changed = sorted(set(actual['files'].items()) ^ set(expected['files'].items()))
        raise Conflict('Workspace manifest differs: ' + ', '.join(sorted({path for path, _ in changed})))
    return actual
