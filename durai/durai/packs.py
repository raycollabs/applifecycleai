import copy
import json
import re
from pathlib import Path
from psycopg.types.json import Jsonb
from .common import canonical, digest, Conflict, audit
from .db import transaction
from .files import export_package, import_package

LAYERS = ('DESIGN', 'ENTITY', 'DAO', 'SERVICE', 'UI', 'TEST')
TYPES = ('KNOWLEDGE', 'MEMORY', 'PROCEDURE')
ORDER = ('DESIGN_READY', 'EXECUTION_SHELL_READY', 'BASELINE_TEST_FAILURE_CONFIRMED',
         'IMPLEMENTATION_READY', 'WAITING_FOR_REVIEW')


def validate_pack(document):
    # Sample: Load G-1 with 18 cells. Change one record digest. Reject the pack.
    fmt = document.get('format')
    if fmt not in ('durai-ontology-pack/v1', 'durai-ontology-pack/v2'):
        raise ValueError('Unsupported ontology pack format')
    for key in ('id', 'version'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', document.get(key, '')):
            raise ValueError('Invalid pack identity')
    if document.get('kind') not in ('GENERIC', 'APPLICATION'):
        raise ValueError('Invalid store kind')
    if fmt == 'durai-ontology-pack/v2':
        tier = document.get('tier', 'FREE')
        if tier not in ('FREE', 'PAID', 'ENTERPRISE', 'COMMUNITY'):
            raise ValueError('Invalid pack tier')
    records = document['records']
    if not records or len(records) > 5000:
        raise ValueError('Pack record budget differs')
    seen, cells = set(), set()
    for record in records:
        if record['id'] in seen or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,200}', record['id']):
            raise ValueError('Duplicate or invalid record identity')
        seen.add(record['id'])
        if record['layer'] not in LAYERS or record['type'] not in TYPES:
            raise ValueError('Invalid layer or ontology type')
        cells.add((record['layer'], record['type']))
        if record['digest'] != digest(record['body']):
            raise Conflict('Record digest differs')
        if document['kind'] == 'GENERIC' and record['type'] == 'MEMORY' and record['body'].get('contentKind') not in ('SCHEMA', 'SYNTHETIC_EXAMPLE'):
            raise ValueError('Generic memory must be a schema or synthetic example')
    if (document['kind'] == 'GENERIC' or fmt == 'durai-ontology-pack/v1') and cells != {(layer, kind) for layer in LAYERS for kind in TYPES}:
        raise ValueError('Complete pack requires all 18 cells')
    for edge in document.get('edges', []):
        if set(edge) != {'from', 'to', 'relation'} or edge['from'] not in seen or edge['to'] not in seen:
            raise ValueError('Unresolved ontology relationship')
    procedures = [r for r in records if r['id'] == 'generation.sequence']
    if document['kind'] == 'GENERIC' and (not procedures or tuple(procedures[0]['body']['gates']) != ORDER):
        raise ValueError('Generic pack must retain the approved gate sequence')
    if any(key in document for key in ('credentials', 'commands', 'grants', 'handlers')):
        raise ValueError('Pack content cannot grant execution authority')
    if len(canonical(document)) > 8 * 1024 * 1024:
        raise ValueError('Pack exceeds the local content budget')
    return digest(document)


def make_record(identity, layer, kind, body):
    # Sample: Seal the DESIGN knowledge body. Keep its identity and content digest.
    return {'id': identity, 'layer': layer, 'type': kind, 'body': body, 'digest': digest(body)}


def generic_pack(version='7'):
    # Sample: Read the approved engineering policy. Produce every layer/type cell.
    root = Path(__file__).resolve().parents[1]
    if version not in ('1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15'):
        raise ValueError('Unsupported generic policy version')
    policy = json.loads((root / ('design/engineering-philosophy-v' + version + '.json')).read_text(encoding='utf-8'))
    records = []
    for layer in LAYERS:
        for kind in TYPES:
            body = {'layer': layer, 'responsibility': policy['typeResponsibilities'][kind]}
            if kind == 'KNOWLEDGE':
                body['policies'] = policy['policies']
            elif kind == 'MEMORY':
                body.update(contentKind='SCHEMA', required=['goal', 'sourceDigest', 'contextDigest', 'policyDigest', 'evidence'])
            else:
                body.update(inputs=['specification', 'effectiveBinding'], outputs=['versionedArtifact', 'verificationEvidence'],
                            failureRoute='NEEDS_CLARIFICATION', trustedHandler='engineering-v1')
            records.append(make_record(layer.lower() + '.' + kind.lower(), layer, kind, body))
    records.append(make_record('generation.sequence', 'DESIGN', 'PROCEDURE',
                   {'gates': list(ORDER), 'scope': 'SYNTHETIC_LOCAL', 'releaseApprovalRequired': True}))
    if int(version) >= 9:
        records.append(make_record('decision.edit-guard', 'DESIGN', 'PROCEDURE',
                                   dict(policy['decisionPoints']['edit-guard'], audience=['control-plane'], releaseAuthority=False)))
    if int(version) >= 10:
        records.append(make_record('decision.failure-triage', 'DESIGN', 'PROCEDURE',
                                   dict(policy['decisionPoints']['failure-triage'], audience=['control-plane'],
                                        releaseAuthority=False)))
    if int(version) >= 14:
        records.append(make_record('decision.context-pack', 'DESIGN', 'PROCEDURE',
                                   dict(policy['decisionPoints']['context-pack'], audience=['control-plane'],
                                        releaseAuthority=False)))
    if int(version) >= 15:
        records.append(make_record('decision.execution-strategy', 'DESIGN', 'PROCEDURE',
                                   dict(policy['decisionPoints']['execution-strategy'], audience=['control-plane'],
                                        releaseAuthority=False)))
    return {'format': 'durai-ontology-pack/v1', 'id': 'durai.software-generation', 'version': version,
            'kind': 'GENERIC', 'reviewState': 'USER_APPROVED_DESIGN', 'records': records,
            'edges': [{'from': 'generation.sequence', 'to': layer.lower() + '.procedure', 'relation': 'GOVERNS'} for layer in LAYERS],
            'provenance': {'policyId': policy['id'], 'policyDigest': digest(policy), 'authority': policy['authority']}}


class PackStore:
    def __init__(self, database='durai_local', role='durai_runtime'):
        # Sample: Use the trading runtime login. Keep foreign pack content invisible.
        self.database, self.role = database, role

    def install(self, document, scope=None):
        # Sample: Install G-1 twice. Keep one immutable pack and reject changed replay.
        if self.role != 'durai_runtime':
            raise PermissionError('Pack writes require the local operator identity')
        checksum = validate_pack(document)
        with transaction(self.role, self.database) as reader:
            application = reader.execute('SELECT durai_core.application_id() AS id').fetchone()['id']
        scope = (scope or '*') if document['kind'] == 'GENERIC' else (scope or application)
        if scope != '*' and scope != application:
            raise ValueError('Operator cannot install another application through this scope')
        with transaction('postgres', self.database) as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('pack:' + scope + ':' + document['id'],))
            prior = db.execute('SELECT digest FROM durai_ontology.pack WHERE scope=%s AND id=%s AND version=%s',
                               (scope, document['id'], document['version'])).fetchone()
            if prior:
                if prior['digest'] != checksum:
                    raise Conflict('Pack version is immutable')
            else:
                db.execute('INSERT INTO durai_ontology.pack(scope,id,version,kind,digest,document) VALUES(%s,%s,%s,%s,%s,%s)',
                           (scope, document['id'], document['version'], document['kind'], checksum, Jsonb(document)))
                for record in document['records']:
                    db.execute('INSERT INTO durai_ontology.record VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                               (scope, document['id'], document['version'], record['id'], record['layer'], record['type'], record['digest'], Jsonb(record)))
        with transaction(self.role, self.database) as db:
            audit(db, 'ONTOLOGY_INSTALL', {'scope': scope, 'id': document['id'], 'version': document['version'], 'digest': checksum})
        return {'scope': scope, 'id': document['id'], 'version': document['version'], 'digest': checksum}

    def get(self, reference):
        # Sample: Resolve the exact G-1 digest. Refuse an altered or invisible version.
        with transaction(self.role, self.database) as db:
            row = db.execute('SELECT document,digest FROM durai_ontology.pack WHERE scope=%s AND id=%s AND version=%s',
                             (reference['scope'], reference['id'], reference['version'])).fetchone()
        if not row:
            raise KeyError('Pack unavailable in this scope')
        if row['digest'] != reference['digest'] or validate_pack(row['document']) != row['digest']:
            raise Conflict('Pinned pack digest differs')
        return row['document']

    def discover(self):
        # Sample: List packs as app B. Return generic packs and only app B packs.
        with transaction(self.role, self.database) as db:
            return db.execute('SELECT scope,id,version,kind,digest FROM durai_ontology.pack ORDER BY scope,id,version').fetchall()

    def bind(self, identity, references, mode='REFERENCE_ONLY'):
        # Sample: Bind G-1 and T-1 for a synthetic app. Keep earlier binding revisions.
        if self.role != 'durai_runtime':
            raise PermissionError('Binding writes require the local operator identity')
        if mode not in ('REFERENCE_ONLY', 'SYNTHETIC_LOCAL'):
            raise ValueError('Only reference or isolated synthetic execution is supported')
        documents = [self.get(ref) for ref in references]
        if len({p['id'] for p in documents}) != len(documents) or sum(p['kind'] == 'GENERIC' for p in documents) != 1:
            raise Conflict('Bind one generic pack and at most one version of each pack')
        document = {'packs': references, 'mode': mode, 'releaseAuthority': False}
        with transaction('durai_reviewer', self.database) as db:
            app = db.execute('SELECT durai_core.application_id() AS id').fetchone()['id']
            db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (app + ':' + identity,))
            rows = db.execute('SELECT revision,digest FROM durai_ontology.binding WHERE id=%s ORDER BY revision DESC LIMIT 1', (identity,)).fetchone()
            if rows and rows['digest'] == digest(document):
                return self.resolve(identity)
            revision = rows['revision'] + 1 if rows else 1
            db.execute('INSERT INTO durai_ontology.binding VALUES(%s,%s,%s,%s,%s,DEFAULT)',
                       (app, identity, revision, Jsonb(document), digest(document)))
            audit(db, 'ONTOLOGY_BIND', {'id': identity, 'revision': revision, 'digest': digest(document)})
        return self.resolve(identity)

    def resolve(self, identity, revision=None):
        # Sample: Resolve revision 1 after revision 2 is bound. Keep the old exact pins.
        with transaction(self.role, self.database) as db:
            row = db.execute('SELECT * FROM durai_ontology.binding WHERE id=%s AND (%s::bigint IS NULL OR revision=%s) ORDER BY revision DESC LIMIT 1',
                             (identity, revision, revision)).fetchone()
        if not row:
            raise KeyError('Binding unavailable in this scope')
        for reference in row['document']['packs']:
            self.get(reference)
        return row

    def fork(self, reference, identity, version, record_id, body):
        # Sample: Change a comment rule in G-2. Preserve G-1 and record its provenance.
        document = copy.deepcopy(self.get(reference))
        document.update(id=identity, version=version, reviewState='UNREVIEWED',
                        provenance={'forkOf': reference, 'reviewRequired': True})
        matches = [r for r in document['records'] if r['id'] == record_id]
        if len(matches) != 1:
            raise KeyError('Fork target record is unavailable')
        matches[0].update(body=body, digest=digest(body))
        with transaction(self.role, self.database) as db:
            scope = db.execute('SELECT durai_core.application_id() AS id').fetchone()['id']
        return self.install(document, scope)

    def export(self, reference, path):
        # Sample: Export G-1 to a new SQLite archive. Seal the full path set.
        return export_package(path, {'ontology/pack.json': canonical(self.get(reference))}, {'pack': reference})

    def import_archive(self, path, expected_digest):
        # Sample: Change one exported byte. Reject it before installing any record.
        path = Path(path)
        if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Ontology archive exceeds its local byte or path budget')
        files, manifest = import_package(path, expected_digest)
        if set(files) != {'ontology/pack.json'}:
            raise Conflict('Ontology archive path set differs')
        document = json.loads(files['ontology/pack.json'])
        if validate_pack(document) != manifest['bindings']['pack']['digest']:
            raise Conflict('Ontology archive binding differs')
        with transaction(self.role, self.database) as db:
            application = db.execute('SELECT durai_core.application_id() AS id').fetchone()['id']
        scope = '*' if document in (generic_pack('1'), generic_pack('2')) else application
        return self.install(document, scope)

    def search(self, reference, text):
        # Sample: Search T-1 for correction. Search only the authorized pinned pack.
        self.get(reference)
        if not text or len(text) > 200:
            raise ValueError('Search requires 1 to 200 characters')
        with transaction(self.role, self.database) as db:
            return db.execute('SELECT id,layer,type,digest,document FROM durai_ontology.record WHERE scope=%s AND pack_id=%s AND version=%s AND document::text ILIKE %s ORDER BY id LIMIT 50',
                              (reference['scope'], reference['id'], reference['version'], '%' + text + '%')).fetchall()

    def project(self, reference):
        # Sample: Project all G-1 records and GOVERNS edges. Compare canonical identities.
        document = self.get(reference)
        prefix = reference['scope'] + ':' + reference['id'] + ':' + reference['version'] + ':'
        with transaction('postgres', self.database) as db:
            db.execute('SET search_path=ag_catalog,public')
            for record in document['records']:
                params = {'key': prefix + record['id'], 'scope': reference['scope'], 'pack': reference['digest'], 'digest': record['digest']}
                db.execute("SELECT * FROM ag_catalog.cypher('durai_pack_graph', $$ MERGE(n:Record {key:$key}) SET n.pack=$pack,n.scope=$scope,n.digest=$digest RETURN n.key $$,%s::ag_catalog.agtype) AS(value ag_catalog.agtype)",
                           (json.dumps(params),), prepare=True).fetchone()
            for edge in document.get('edges', []):
                params = {'a': prefix + edge['from'], 'b': prefix + edge['to'], 'relation': edge['relation']}
                db.execute("SELECT * FROM ag_catalog.cypher('durai_pack_graph', $$ MATCH(a:Record {key:$a}),(b:Record {key:$b}) MERGE(a)-[r:LINK {relation:$relation}]->(b) RETURN r.relation $$,%s::ag_catalog.agtype) AS(value ag_catalog.agtype)",
                           (json.dumps(params),), prepare=True).fetchone()
            params = json.dumps({'pack': reference['digest'], 'scope': reference['scope']})
            rows = db.execute("SELECT value::text,checksum::text FROM ag_catalog.cypher('durai_pack_graph', $$ MATCH(n:Record) WHERE n.pack=$pack AND n.scope=$scope RETURN n.key,n.digest $$,%s::ag_catalog.agtype) AS(value ag_catalog.agtype,checksum ag_catalog.agtype)",
                              (params,), prepare=True).fetchall()
            links = db.execute("SELECT a::text,b::text,relation::text FROM ag_catalog.cypher('durai_pack_graph', $$ MATCH(a:Record)-[r:LINK]->(b:Record) WHERE a.pack=$pack AND a.scope=$scope RETURN a.key,b.key,r.relation $$,%s::ag_catalog.agtype) AS(a ag_catalog.agtype,b ag_catalog.agtype,relation ag_catalog.agtype)",
                               (params,), prepare=True).fetchall()
        expected_records = {(prefix + r['id'], r['digest']) for r in document['records']}
        actual_records = {(r['value'], r['checksum']) for r in rows}
        expected_links = {(prefix + e['from'], prefix + e['to'], e['relation']) for e in document.get('edges', [])}
        actual_links = {(r['a'], r['b'], r['relation']) for r in links}
        if actual_records != expected_records or actual_links != expected_links:
            raise Conflict('Graph identities, digests, or relationships differ')
        return {'packDigest': reference['digest'], 'records': len(rows), 'edges': len(actual_links), 'authority': 'durai_ontology.pack', 'fullIdentityAndDigestComparison': True}
