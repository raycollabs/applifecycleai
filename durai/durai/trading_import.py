"""Read-only trading ontology capture and reversible application-pack import."""
import json
from pathlib import Path
import subprocess
from .common import canonical, digest, Conflict
from .packs import make_record, validate_pack
from .archives import ensure_archive

ROOT = Path(__file__).resolve().parents[1]
APPLICATION = '848256bd-e6a8-5907-9368-df4e523cbd93'
TABLES = {
    'ontology_meta': ['application', 'document', 'profile_version', 'release_version', 'template_version',
                      'artifact_slot', 'artifact_revision', 'artifact_dependency'],
    'quality': ['deployment_profile', 'requirement'],
    'solution_catalog': ['codebase_version', 'component_use', 'composition_version', 'solution_version'],
    'evidence': ['assertion', 'assertion_evidence', 'source', 'source_version', 'span'],
    'agent_runtime': ['procedure_version', 'memory_observation'],
    'learning': ['artifact', 'deployment_profile', 'solution', 'memory_event'],
}
EXCLUDED = ['credentials and role bindings', 'review approvals and release authority',
            'active work, leases, sessions, and worker ownership', 'financial production rows',
            'external downloaded source bytes without redistribution review']


def source_snapshot():
    # Sample: Export all ontology rows for the known trading app in one read-only snapshot.
    fields = []
    for schema, tables in TABLES.items():
        for table in tables:
            name = schema + '.' + table
            fields.append("'%s',COALESCE((SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text) FROM %s t WHERE application_id='%s'),'[]'::jsonb)" % (name, name, APPLICATION))
    fields.extend([
        "'graph.vertices',COALESCE((SELECT jsonb_agg(jsonb_build_object('sourceId',id::text,'label',tableoid::regclass::text,'properties',properties::text::jsonb) ORDER BY id) FROM trade_ontology._ag_label_vertex WHERE properties::text::jsonb->>'application_id'='%s'),'[]'::jsonb)" % APPLICATION,
        "'graph.edges',COALESCE((SELECT jsonb_agg(jsonb_build_object('sourceId',e.id::text,'from',e.start_id::text,'to',e.end_id::text,'label',e.tableoid::regclass::text,'properties',e.properties::text::jsonb) ORDER BY e.id) FROM trade_ontology._ag_label_edge e WHERE e.start_id IN(SELECT id FROM trade_ontology._ag_label_vertex WHERE properties::text::jsonb->>'application_id'='%s') AND e.end_id IN(SELECT id FROM trade_ontology._ag_label_vertex WHERE properties::text::jsonb->>'application_id'='%s')),'[]'::jsonb)" % (APPLICATION, APPLICATION),
    ])
    query = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL search_path=ag_catalog,public; SET LOCAL statement_timeout='30s'; SELECT jsonb_build_object(" + ','.join(fields) + '); COMMIT;'
    result = subprocess.run(['docker', 'exec', '-i', 'trade-local-db', 'psql', '-X', '-q', '-U',
                             'supabase_admin', '-d', 'postgres', '-At', '-v', 'ON_ERROR_STOP=1'],
                            input=query, capture_output=True, text=True, encoding='utf-8', timeout=45)
    if result.returncode:
        raise RuntimeError('Read-only source ontology export failed: ' + result.stderr[:400])
    return json.loads(result.stdout)


def reference_files(catalog):
    # Sample: Resolve internal/dao/store.go under the pilot. Keep exact bytes by digest.
    names = set()
    for artifact in catalog['artifacts']:
        names.update(artifact['body'].get('sourceFiles', []))
    for rule in catalog['rules']:
        names.update(rule.get('evidence', []))
    files, missing = {}, []
    base = (ROOT.parent / 'apps/trade-pilot').resolve()
    for name in sorted(names):
        path = (base / name).resolve()
        if not path.is_relative_to(base) or not path.is_file() or path.is_symlink():
            missing.append(name)
        else:
            files['source/' + name] = path.read_bytes()
    return files, missing


def trading_pack(snapshot=None):
    # Sample: Reconcile 18 live cells with the catalog. Preserve earlier rows separately.
    path = ROOT / 'design/reference-packs/exchange-trading/implementation-source.json'
    catalog = json.loads(path.read_text(encoding='utf-8'))
    records, edges = [], []
    for artifact in catalog['artifacts']:
        records.append(make_record('catalog.' + artifact['id'], artifact['layer'], artifact['kind'], artifact))
    for rule in catalog['rules']:
        records.append(make_record('rule.' + rule['id'], rule['layer'], rule['kind'], rule))
        for neighbor in rule.get('neighbors', []):
            edges.append({'from': 'rule.' + rule['id'], 'to': 'rule.' + neighbor, 'relation': 'RELATED_RULE'})
    for solution in catalog['solutions']:
        records.append(make_record('solution.' + solution['id'], 'DESIGN', 'KNOWLEDGE', solution))
    records.append(make_record('sample.position-domain', 'SERVICE', 'KNOWLEDGE', {
        'quantityUnit': 'shares', 'signedLots': True, 'correctionDirection': 'REMOVE_LOTS',
        'quantityExpression': 'lots * lot_size', 'correctionExpression': 'current_shares - delta_lots * lot_size',
        'scope': 'Synthetic position adapter; not the full exchange catalog implementation',
        'provenance': 'Existing Durai quantity-unit/1 and correction-unit/1 sample contracts'}))
    reconciliation = {'catalogArtifacts': 18, 'catalogRules': 30, 'catalogSolutions': 3, 'liveExport': snapshot is not None}
    if snapshot is not None:
        live = snapshot['learning.artifact']
        current = [r for r in live if r['profile'] == catalog['profile']]
        expected = {(r['id'], r['digest']) for r in catalog['artifacts']}
        observed = {(r['id'], r['content_digest']) for r in current}
        if observed != expected:
            raise Conflict('Live current artifacts differ from the source catalog')
        reconciliation['sourceCounts'] = {name: len(rows) for name, rows in snapshot.items()}
        reconciliation['snapshotDigest'] = digest(snapshot)
        vertices = {}
        slots = {row['id']: row for row in snapshot.get('ontology_meta.artifact_slot', [])}
        source_ids = {}
        for index, vertex in enumerate(snapshot['graph.vertices']):
            vertices[vertex['sourceId']] = 'source.graph.vertex.' + str(index)
        for name, rows in snapshot.items():
            for index, row in enumerate(rows):
                dimensions = slots.get(row.get('slot_id'), row)
                if name == 'graph.vertices':
                    dimensions = row.get('properties', {})
                layer = dimensions.get('layer', 'DESIGN').upper()
                layer = layer if layer in ('DESIGN', 'ENTITY', 'DAO', 'SERVICE', 'UI', 'TEST') else 'DESIGN'
                default_kind = 'MEMORY' if 'memory' in name else ('PROCEDURE' if name == 'agent_runtime.procedure_version' else 'KNOWLEDGE')
                kind = dimensions.get('kind', dimensions.get('ontology_type', default_kind)).upper()
                kind = kind if kind in ('KNOWLEDGE', 'MEMORY', 'PROCEDURE') else 'KNOWLEDGE'
                identity = ('source.graph.vertex.' + str(index)) if name == 'graph.vertices' else ('source.' + name + '.' + str(index))
                records.append(make_record(identity, layer, kind, {'sourceTable': name, 'sourceRow': row,
                    'sourceDigest': digest(row), 'restoredAuthority': False}))
                if 'id' in row:
                    source_ids[(name, row['id'])] = identity
                if name == 'graph.edges':
                    edges.append({'from': vertices[row['from']], 'to': vertices[row['to']],
                                  'relation': row['label'].split('.')[-1].strip('"')})
        for row in snapshot.get('ontology_meta.artifact_dependency', []):
            edges.append({'from': source_ids[('ontology_meta.artifact_revision', row['artifact_revision_id'])],
                          'to': source_ids[('ontology_meta.artifact_revision', row['upstream_revision_id'])], 'relation': 'DEPENDS_ON'})
        for row in snapshot.get('ontology_meta.artifact_revision', []):
            origin = source_ids[('ontology_meta.artifact_revision', row['id'])]
            for field, target_table, relation in [('slot_id', 'ontology_meta.artifact_slot', 'HAS_SLOT'),
                                                  ('document_id', 'ontology_meta.document', 'HAS_DOCUMENT'),
                                                  ('template_id', 'ontology_meta.template_version', 'USES_TEMPLATE')]:
                if (target_table, row[field]) in source_ids:
                    edges.append({'from': origin, 'to': source_ids[(target_table, row[field])], 'relation': relation})
    files, missing = reference_files(catalog)
    provenance = {'mapperVersion': 'exchange-pack-adapter/3', 'sourceApplication': APPLICATION, 'sourceProfile': catalog['profile'],
                  'sourceReleaseState': catalog['releaseState'], 'sourceCatalogDigest': digest(path.read_bytes()),
                  'reconciliation': reconciliation, 'exclusions': EXCLUDED,
                  'fileDigests': {name: digest(data) for name, data in files.items()}, 'missingFiles': missing}
    version = '1-' + digest(provenance)[:12]
    document = {'format': 'durai-ontology-pack/v1', 'id': 'durai.app.exchange-trading', 'version': version,
                'kind': 'APPLICATION', 'reviewState': 'IMPLEMENTED_UNREVIEWED', 'records': records,
                'edges': edges, 'provenance': provenance}
    validate_pack(document)
    return document, files


def import_trading(store, capture_live=True):
    # Sample: Capture the pilot without writes. Install a reference pack and byte archive.
    snapshot = source_snapshot() if capture_live else None
    document, files = trading_pack(snapshot)
    reference = store.install(document)
    folder = ROOT / 'private/ontology-imports' / reference['digest']
    folder.mkdir(parents=True, exist_ok=True)
    if snapshot is not None:
        (folder / 'source-snapshot.json').write_bytes(canonical(snapshot))
    archive = folder / 'source-files.sqlite'
    archive_digest = ensure_archive(archive, files, {'packDigest': reference['digest']})
    # Snapshot/allowed memories stay private. Public receipts contain counts and digests only.
    return {'reference': reference, 'provenance': document['provenance'],
            'sourceFileArchive': str(archive), 'sourceFileArchiveDigest': archive_digest,
            'projection': store.project(reference), 'reviewState': document['reviewState'],
            'liveSourceModified': False}
