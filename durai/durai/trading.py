"""F-101 discovery binds the business rule, stored row, source, and context."""
from pathlib import Path
from psycopg.types.json import Jsonb
from .common import audit, digest, event, uid
from .coordination import Coordination
from .db import transaction
from .registry import Registry

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'apps/trading-demo/fixtures'
RULE = {'id': 'quantity-unit', 'version': '1', 'unit': 'shares', 'expression': 'shares = lots * lot_size'}
CORRECTION_RULE = {'id': 'correction-unit', 'version': '1', 'unit': 'shares',
                   'semantics': 'A positive correction_lots removes that many lots from the current position. Preserve subtraction and signed-input behavior.',
                   'expression': 'corrected_shares = shares - correction_lots * lot_size'}


def seed(database='durai_local'):
    registry = Registry(database)
    registry.define('trading-producer', 'Synthetic trade booking producer', ['trade.book', 'trade.correct'])
    for name, filename in [('broken-v1', 'broken.py'), ('quantity-only-v2', 'quantity-only.py'), ('repaired-v3', 'repaired.py')]:
        source = (FIXTURES / filename).read_bytes()
        registry.register('trading-producer', name, source,
                          {'fixture': 'F-101', 'sourceRevision': digest(source),
                           'generationMethod': 'authored-synthetic-fixture', 'ontologyVersion': '1'})
    with transaction(database=database) as db:
        db.execute('INSERT INTO dev_trading.rule VALUES(durai_core.application_id(),%s,%s,%s) ON CONFLICT DO NOTHING',
                   (RULE['id'], RULE['version'], Jsonb(RULE)))
        db.execute('INSERT INTO dev_trading.rule VALUES(durai_core.application_id(),%s,%s,%s) ON CONFLICT DO NOTHING',
                   (CORRECTION_RULE['id'], CORRECTION_RULE['version'], Jsonb(CORRECTION_RULE)))
        db.execute("INSERT INTO app_trading.trade VALUES(durai_core.application_id(),'T-101',2,100,2,'broken-v1') ON CONFLICT DO NOTHING")
    return registry


def discover(database='durai_local', run_id=None):
    run_id = run_id or uid()
    coordinator = Coordination(database)
    run = coordinator.run(run_id)
    if run['checkpoint'].get('findingId'):
        return run_id, run['checkpoint']['findingId']
    registry = Registry(database)
    with transaction(database=database) as db:
        row = db.execute("SELECT * FROM app_trading.trade WHERE id='T-101' FOR SHARE").fetchone()
        rule = db.execute("SELECT document FROM dev_trading.rule WHERE id='quantity-unit' AND version='1'").fetchone()['document']
        if row['stored_shares'] == row['lots'] * row['lot_size']:
            raise ValueError('No quantity defect is present')
        version = registry.get(row['source_version'])
        context = {'rule': rule, 'sourceDigest': version['digest'], 'snapshotDigest': digest(row),
                   'sourceMapping': {'producer': 'trading-producer.book', 'column': 'app_trading.trade.stored_shares'},
                   'observedWrite': {'tradeId': row['id'], 'sourceVersion': row['source_version'],
                                     'storedShares': row['stored_shares']}}
        context['correctionRule'] = db.execute("SELECT document FROM dev_trading.rule WHERE id='correction-unit' AND version='1'").fetchone()['document']
        context['executionEnvironment'] = {
            'requirementsDigest': digest((ROOT / 'requirements.lock').read_bytes()),
            'instructionsDigest': digest(b'Store booking and correction quantities in canonical shares.'),
            'authority': 'PostgreSQL', 'candidateProvider': 'docker-linux',
            'nativeProviderScope': 'trusted synthetic fixtures only'}
        image_lock = ROOT / 'sandbox-image.lock.json'
        if image_lock.exists():
            import json
            context['executionEnvironment']['candidateImage'] = json.loads(image_lock.read_text(encoding='utf-8'))['imageId']
        context_digest = digest(context)
        db.execute('INSERT INTO durai_files.manifest VALUES(durai_core.application_id(),%s,%s) ON CONFLICT DO NOTHING',
                   (context_digest, Jsonb(context)))
        finding_id = 'F-101-' + run_id
        evidence = {'trade': row, 'expectedShares': row['lots'] * row['lot_size'],
                    'observedShares': row['stored_shares'], 'ontologyId': rule['id'],
                    'ontologyVersion': rule['version'], 'sourceRevision': version['digest'],
                    'snapshotDigest': digest(row), 'contextManifest': context_digest,
                    'query': "SELECT lots * lot_size AS expected, stored_shares FROM app_trading.trade WHERE id='T-101'"}
        db.execute("INSERT INTO durai_improvement.finding VALUES(durai_core.application_id(),%s,%s,'OPEN',%s)",
                   (finding_id, run_id, Jsonb(evidence)))
        db.execute('UPDATE durai_agents.run SET checkpoint=%s WHERE id=%s',
                   (Jsonb({'findingId': finding_id, 'stage': 'DISCOVERED'}), run_id))
        event(db, run_id, 'FINDING_DISCOVERED', {'findingId': finding_id, 'expectedShares': 200, 'observedShares': 2})
        audit(db, 'DISCOVER_FINDING', {'findingId': finding_id, 'contextManifest': context_digest})
    return run_id, finding_id
