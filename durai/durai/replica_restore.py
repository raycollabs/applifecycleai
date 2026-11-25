"""Restore a sealed synthetic replica into a new local folder."""
import json
from pathlib import Path
import tempfile
from .common import canonical, digest, Conflict
from .files import safe_path
from .replica import verify_replica


def restore_replica(package, destination, expected_digest):
    # Sample: Restore the sealed two-lot baseline. Check bytes before publication.
    package = Path(package).resolve()
    destination = Path(destination).absolute()
    manifest_path = package / 'manifest.json'
    if manifest_path.is_symlink():
        raise Conflict('Replica manifest must be a regular file')
    manifest = json.loads(manifest_path.read_bytes())
    if digest(manifest) != expected_digest or manifest.get('format') != 'durai-application/v1':
        raise Conflict('Replica package manifest digest or format differs')
    names = manifest.get('files', {})
    required = {'baseline/program.py', 'candidate/program.py', 'replica.json', 'comparison.json'}
    if set(names) != required:
        raise Conflict('Replica package path set differs')
    actual = {p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}
    if actual != required | {'manifest.json'}:
        raise Conflict('Replica package contains missing or extra files')
    files = {}
    for name, checksum in names.items():
        safe_path(name)
        path = package / name
        if path.is_symlink() or not path.resolve().is_relative_to(package):
            raise Conflict('Replica file containment differs')
        data = path.read_bytes()
        if digest(data) != checksum:
            raise Conflict('Replica file digest differs')
        files[name] = data
    replica = json.loads(files['replica.json'])
    verify_replica(files['baseline/program.py'], replica)
    comparison = json.loads(files['comparison.json'])
    if digest(files['candidate/program.py']) != comparison.get('candidateDigest'):
        raise Conflict('Replica candidate digest differs')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    if not destination.parent.is_dir():
        raise ValueError('Restore parent must exist')
    # Publish only verified bytes. Never execute restored code or restore credentials.
    with tempfile.TemporaryDirectory(prefix='replica-restore-', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'bundle'
        staging.mkdir()
        for name, data in files.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (staging / 'manifest.json').write_bytes(canonical(manifest))
        staging.rename(destination)
    return {'state': 'RESTORED', 'manifestDigest': expected_digest,
            'outputDirectory': str(destination), 'filesRestored': len(files),
            'executionStarted': False, 'approvalCreated': False,
            'scope': 'Synthetic single-process package; no database or volume restore'}
