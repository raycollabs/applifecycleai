"""Sample: Prepare durai_test. Apply samples_0001.sql once. Run it again. Check no second apply."""
import hashlib
from .db import ROOT, connect

MIGRATIONS = ROOT / 'migrations/samples'


def prepare_samples(database='durai_local'):
    # Sample: Prepare durai_test. Apply samples_0001.sql once. Run it again. Check no second apply.
    applied = []
    with connect('postgres', database) as admin:
        admin.execute('CREATE TABLE IF NOT EXISTS durai_core.migration(name text PRIMARY KEY, digest text NOT NULL, applied_at timestamptz NOT NULL DEFAULT clock_timestamp())')
        admin.execute("SELECT pg_advisory_xact_lock(hashtext('durai-migration'))")
        for path in sorted(MIGRATIONS.glob('*.sql')):
            name, digest = 'samples/' + path.name, hashlib.sha256(path.read_bytes()).hexdigest()
            before = admin.execute('SELECT digest FROM durai_core.migration WHERE name=%s', (name,)).fetchone()
            if before and before['digest'] != digest:
                raise ValueError('Applied migration changed: ' + name)
            if not before:
                admin.execute(path.read_text(encoding='utf-8'), prepare=False)
                admin.execute('INSERT INTO durai_core.migration(name,digest) VALUES(%s,%s)', (name, digest))
            applied.append({'name': name, 'sha256': digest})
    return applied
