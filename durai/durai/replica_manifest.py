"""Pinned synthetic position inputs and an explicit provider scope."""
import json
from .common import canonical, digest, Conflict
from .engineering import validate_domain
from .generation import ROOT, DEFAULT_SPEC, scaffold_source
from .packs import PackStore
from .protected.position_suite import cases

PROVIDER = 'synthetic-position-fixture/v1'
SCHEMA = b'CREATE TABLE trade(id TEXT PRIMARY KEY,lot_size INTEGER NOT NULL,shares INTEGER NOT NULL)'


def fixture_data(scope, requester):
    # Sample: Read two-lot fixtures for app A. Refuse a request from app B.
    if not scope or requester != scope:
        raise Conflict('Replica provider scope differs')
    return canonical(cases())


def capture(binding_id, database='durai_local'):
    # Sample: Pin position source, table schema, sealed inputs, and ontology G-1.
    binding = PackStore(database).resolve(binding_id)
    if binding['document']['mode'] != 'SYNTHETIC_LOCAL':
        raise Conflict('Replica requires a synthetic binding')
    for reference in binding['document']['packs']:
        pack = PackStore(database).get(reference)
        for record in pack['records']:
            if pack['kind'] == 'APPLICATION' and record['id'] == 'sample.position-domain':
                if any(record['body'].get(k) != DEFAULT_SPEC[k] for k in ('quantityUnit', 'signedLots', 'correctionDirection')):
                    raise Conflict('Replica domain rules differ')
    candidate = (ROOT / 'templates/position-app/program.py').read_bytes()
    if SCHEMA not in candidate:
        raise Conflict('Replica schema differs from the pinned source')
    files = {'baseline/program.py': scaffold_source(candidate), 'candidate/program.py': candidate,
             'schema.sql': SCHEMA, 'fixtures.json': fixture_data(binding_id, binding_id)}
    image = json.loads((ROOT / 'sandbox-image.lock.json').read_text())['imageId']
    manifest = {'format': 'durai-phase1-replica/v1', 'scope': binding_id,
                'provider': PROVIDER, 'imageId': image, 'network': 'none',
                'bindingDigest': binding['digest'], 'bindingRevision': binding['revision'],
                'packs': binding['document']['packs'], 'domainRules': validate_domain(DEFAULT_SPEC),
                'files': {name: digest(data) for name, data in files.items()},
                'topology': ['synthetic-position-sqlite'], 'substitutes': [],
                'limits': ['Synthetic fixtures only', 'SQLite schema is recreated for each request',
                           'No production topology, credentials, broker, or volume snapshot']}
    manifest['id'] = digest(manifest)
    verify(files, manifest, binding_id)
    return files, manifest


def verify(files, manifest, requester):
    # Sample: Change one schema byte or provider ID. Refuse before the runner starts.
    body = {key: value for key, value in manifest.items() if key != 'id'}
    if manifest.get('format') != 'durai-phase1-replica/v1' or manifest.get('id') != digest(body):
        raise Conflict('Replica manifest identity differs')
    if manifest.get('scope') != requester or manifest.get('provider') != PROVIDER or manifest.get('network') != 'none':
        raise Conflict('Replica provider, scope, or network differs')
    required = {'baseline/program.py', 'candidate/program.py', 'schema.sql', 'fixtures.json'}
    if set(files) != required or set(manifest.get('files', {})) != required:
        raise Conflict('Replica input path set differs')
    if any(digest(data) != manifest['files'][name] for name, data in files.items()):
        raise Conflict('Replica input digest differs')
    if files['schema.sql'] != SCHEMA or SCHEMA not in files['candidate/program.py']:
        raise Conflict('Replica schema source differs')
    if files['fixtures.json'] != fixture_data(requester, requester):
        raise Conflict('Replica sealed business cases differ')
    if not manifest.get('packs') or not manifest.get('bindingDigest') or not manifest.get('imageId'):
        raise Conflict('Replica policy or runner identity is missing')
    return True
