"""Replay-safe verified creation of local portable byte archives."""
from .common import digest, Conflict
from .files import export_package, import_package


def ensure_archive(path, files, bindings):
    # Sample: Reuse the same sealed source archive. Reject an altered retained file.
    expected = digest({'format': 'durai-sqlite-package/v1', 'bindings': bindings,
                       'files': {name: digest(data) for name, data in sorted(files.items())}})
    if path.exists():
        restored, _ = import_package(path, expected)
        if restored != files:
            raise Conflict('Retained archive differs from the pinned source bytes')
        return expected
    return export_package(path, files, bindings)
