"""Digest-checked portable SQLite packages and pinned native fixture execution."""
from contextlib import closing
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import sys
import tempfile
from .common import canonical, digest, Conflict


def safe_path(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '\\' in value or ':' in value or not path.parts:
        raise ValueError('Package path must be relative and contained')
    return path


def export_package(path, files, bindings):
    manifest = {'format': 'durai-sqlite-package/v1', 'bindings': bindings,
                'files': {name: digest(data) for name, data in sorted(files.items())}}
    for name in files:
        safe_path(name)
    # A new package avoids truncating an existing recovery artifact.
    if Path(path).exists():
        raise FileExistsError(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE manifest(document BLOB NOT NULL, digest TEXT NOT NULL)')
        db.execute('CREATE TABLE files(path TEXT PRIMARY KEY, content BLOB NOT NULL)')
        db.execute('INSERT INTO manifest VALUES(?,?)', (canonical(manifest), digest(manifest)))
        db.executemany('INSERT INTO files VALUES(?,?)', files.items())
    return digest(manifest)


def import_package(path, expected_digest):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT document,digest FROM manifest').fetchall()
        if len(rows) != 1:
            raise Conflict('Package must have one manifest')
        document, checksum = rows[0]
        manifest = json.loads(document)
        if digest(manifest) != checksum or checksum != expected_digest:
            raise Conflict('Package manifest digest differs')
        if manifest.get('format') != 'durai-sqlite-package/v1':
            raise ValueError('Unsupported package format')
        files = dict(db.execute('SELECT path,content FROM files').fetchall())
        if set(files) != set(manifest['files']):
            raise Conflict('Package path set differs')
        for name, data in files.items():
            safe_path(name)
            if digest(data) != manifest['files'][name]:
                raise Conflict('Package file digest differs')
        return files, manifest


class NativeFixtureRunner:
    """Separate process for trusted synthetic fixtures; not an adversarial OS boundary.

    Candidate work receives no DB, model, or reviewer variables. Linux container
    sandboxes must be used before running untrusted generated programs.
    """
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def execute(self, source, expected_digest, value):
        if digest(source) != expected_digest:
            raise Conflict('Workspace artifact digest differs')
        with tempfile.TemporaryDirectory(prefix='fixture-', dir=self.root) as folder:
            program = Path(folder, 'program.py')
            program.write_bytes(source)
            env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'COMSPEC') if key in os.environ}
            env.update(TEMP=folder, TMP=folder, PYTHONIOENCODING='utf-8')
            result = subprocess.run([sys.executable, '-I', '-S', str(program)],
                                    input=json.dumps(value), text=True, capture_output=True,
                                    cwd=folder, env=env, timeout=10)
            if result.returncode:
                raise ValueError('Fixture execution failed')
            if len(result.stdout) > 65536:
                raise ValueError('Fixture output exceeds its contract')
            return json.loads(result.stdout)
