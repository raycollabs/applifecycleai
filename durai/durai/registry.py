"""Agent identities, immutable packages, and scope-filtered discovery."""
from psycopg.types.json import Jsonb
from .common import audit, digest, Conflict
from .db import transaction


class Registry:
    def __init__(self, database='durai_local', role='durai_runtime'):
        self.database, self.role = database, role

    def define(self, agent_id, name, capabilities):
        with transaction(self.role, self.database) as db:
            db.execute('INSERT INTO durai_agents.definition VALUES(durai_core.application_id(),%s,%s,%s) ON CONFLICT DO NOTHING',
                       (agent_id, name, Jsonb(capabilities)))
            row = db.execute('SELECT * FROM durai_agents.definition WHERE id=%s', (agent_id,)).fetchone()
            if not row or row['name'] != name or row['capabilities'] != capabilities:
                raise Conflict('Definition identity differs')

    def register(self, agent_id, version_id, source, manifest):
        checksum = digest(source)
        bound = dict(manifest, sourceDigest=checksum, agentId=agent_id,
                     versionId=version_id, artifactType='python-trading-program/v1')
        with transaction(self.role, self.database) as db:
            before = db.execute('SELECT digest,manifest FROM durai_agents.version WHERE id=%s', (version_id,)).fetchone()
            if before:
                if before['digest'] != checksum or before['manifest'] != bound:
                    raise Conflict('Registered version is immutable')
                return checksum
            db.execute('INSERT INTO durai_agents.version(application_id,id,agent_id,digest,manifest,source_bytes) VALUES(durai_core.application_id(),%s,%s,%s,%s,%s)',
                       (version_id, agent_id, checksum, Jsonb(bound), source))
            audit(db, 'REGISTER_VERSION', {'versionId': version_id, 'digest': checksum})
        return checksum

    def get(self, version_id):
        with transaction(self.role, self.database) as db:
            row = db.execute('SELECT * FROM durai_agents.version WHERE id=%s', (version_id,)).fetchone()
        if row is None:
            raise KeyError('Version is unavailable in this scope')
        row['source_bytes'] = bytes(row['source_bytes'])
        if digest(row['source_bytes']) != row['digest']:
            raise Conflict('Stored source digest differs')
        return row

    def discover(self, capability):
        with transaction(self.role, self.database) as db:
            return db.execute('SELECT d.id,d.name,v.id AS version_id,v.digest,v.manifest FROM durai_agents.definition d JOIN durai_agents.version v ON (v.application_id,v.agent_id)=(d.application_id,d.id) WHERE d.capabilities ? %s ORDER BY v.created_at',
                              (capability,)).fetchall()
