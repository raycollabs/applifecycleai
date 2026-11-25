"""Sample: Export fixture F-1. Restore its exact rows in new database R-1. Keep activation disabled."""
from datetime import datetime
import base64
import json
from pathlib import Path
from psycopg import sql
from psycopg.types.json import Jsonb
from .common import canonical, digest, uid, Conflict
from .db import ROOT, connect, transaction, prepare
from .files import export_package, import_package
from .ontology import project_trading_rule
from .context_store import artifact_root

# Sample: Restore version repaired-v3 before run R-1. Check that R-1 references the restored version.
TABLES = [
    'durai_agents.definition', 'durai_agents.version', 'durai_agents.run',
    'durai_files.manifest', 'durai_coordination.kv', 'durai_coordination.quota',
    'durai_coordination.operation', 'durai_coordination.event', 'dev_trading.rule',
    'app_trading.trade', 'durai_improvement.finding', 'durai_governance.evaluation',
    'durai_governance.approval', 'durai_agents.release', 'durai_governance.target', 'durai_governance.activation',
    'durai_governance.audit', 'durai_context.plan', 'durai_context.reference',
    'durai_context.record', 'durai_context.status_revision']


def encode(value):
    if isinstance(value, (bytes, memoryview)):
        return {'$bytes': base64.b64encode(value).decode()}
    if isinstance(value, datetime):
        return {'$timestamp': value.isoformat()}
    if isinstance(value, dict):
        return {key: encode(child) for key, child in value.items()}
    if isinstance(value, list):
        return [encode(child) for child in value]
    return value


def snapshot(database):
    # Sample: Export a context record. Rebuild its generated search terms after restore.
    tables = {}
    with transaction(database=database) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        for name in TABLES:
            rows = db.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(*name.split('.')))).fetchall()
            if name == 'durai_context.record':
                for row in rows:
                    row.pop('terms', None)
            tables[name] = sorted([encode(row) for row in rows], key=lambda row: canonical(row))
    return {'format': 'durai-synthetic-recovery/v1', 'tables': tables,
            'scope': 'Trading synthetic fixture. No credentials, checkpoint tables, active leases, or external production data.'}


def export_fixture(path, database):
    # Sample: Export a context receipt reference. Include its original bytes under the content digest.
    data = snapshot(database)
    files = {'database/fixture.json': canonical(data)}
    for row in data['tables']['durai_agents.version']:
        source = base64.b64decode(row['source_bytes']['$bytes'])
        if digest(source) != row['digest']:
            raise Conflict('Source digest differs before snapshot')
        files['sources/' + row['digest'] + '.py'] = source
    for row in data['tables']['durai_context.reference']:
        artifact = artifact_root() / row['digest']
        if not artifact.is_file() or artifact.is_symlink() or digest(artifact.read_bytes()) != row['digest']:
            raise Conflict('Context artifact unavailable before snapshot')
        files['context-evidence/' + row['digest']] = artifact.read_bytes()
    return export_package(path, files, {'databaseDigest': digest(data), 'ontologyVersion': '1',
                                        'recoveryPolicy': 'READ_ONLY_RESTORED_FIXTURE'})


def restore_fixture(path, expected_digest):
    # Sample: Restore F-1 into a fresh database. Remove its bootstrap audit rows and insert the exact source audit history.
    files, manifest = import_package(path, expected_digest)
    data = json.loads(files['database/fixture.json'])
    if digest(data) != manifest['bindings']['databaseDigest'] or list(data['tables'].keys()) != sorted(TABLES):
        raise Conflict('Database snapshot contract differs')
    database = 'durai_restore_' + uid().replace('-', '')[:12]
    with connect('postgres', 'postgres', autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(database)))
    prepare(database)
    for row in data['tables']['durai_context.reference']:
        content = files.get('context-evidence/' + row['digest'])
        if content is None or digest(content) != row['digest']:
            raise Conflict('Context artifact snapshot differs')
        artifact = artifact_root() / row['digest']
        artifact.parent.mkdir(parents=True, exist_ok=True)
        try:
            with artifact.open('xb') as output:
                output.write(content)
        except FileExistsError:
            if artifact.is_symlink() or digest(artifact.read_bytes()) != row['digest']:
                raise Conflict('Existing context artifact differs') from None
    with transaction('postgres', database) as db:
        db.execute('TRUNCATE durai_governance.audit')
        for name in TABLES:
            for row in data['tables'][name]:
                columns, values = [], []
                for key, value in row.items():
                    columns.append(sql.Identifier(key))
                    if isinstance(value, dict) and set(value) == {'$bytes'}:
                        value = base64.b64decode(value['$bytes'])
                    elif isinstance(value, dict) and set(value) == {'$timestamp'}:
                        value = datetime.fromisoformat(value['$timestamp'])
                    elif isinstance(value, (dict, list)):
                        value = Jsonb(value)
                    values.append(value)
                db.execute(sql.SQL('INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES({})').format(
                    sql.Identifier(*name.split('.')), sql.SQL(',').join(columns),
                    sql.SQL(',').join(sql.Placeholder() for _ in values)), values)
    restored = snapshot(database)
    if restored != data:
        raise Conflict('Restored SQL snapshot differs')
    graph = project_trading_rule(database)
    # Sample: Restore F-1. Revoke release-role connections and runtime writes. Check that activation stays disabled.
    with transaction('postgres', database) as db:
        for role in ('durai_release', 'durai_reviewer', 'durai_evaluator'):
            db.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM {}').format(sql.Identifier(database), sql.Identifier(role)))
        for schema in {name.split('.')[0] for name in TABLES}:
            db.execute(sql.SQL('REVOKE INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} FROM durai_runtime,durai_foreign').format(sql.Identifier(schema)))
    return {'database': database, 'packageDigest': expected_digest,
            'databaseDigest': digest(restored), 'tables': len(TABLES),
            'graph': graph, 'state': 'RESTORED_FIXTURE', 'activationAuthority': 'DISABLED'}
