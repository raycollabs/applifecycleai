import json
from pathlib import Path
from .common import canonical, digest, Conflict
from .engineering import Engineering, check_walkthroughs
from .generation import DEFAULT_SPEC, write_bundle, verify_bundle
from .registry import Registry

ROOT = Path(__file__).resolve().parents[1]


def capture_replica(database='durai_local'):
    # Sample: Capture historical broken-v1 and 2 lots of 100. Preserve its registered bytes.
    baseline = Registry(database).get('broken-v1')
    snapshot = {'lots': 2, 'lot_size': 100}
    image = json.loads((ROOT / 'sandbox-image.lock.json').read_text(encoding='utf-8'))['imageId']
    manifest = {'format': 'durai-system-replica/v1', 'sourceVersion': baseline['id'],
                'sourceDigest': baseline['digest'], 'snapshot': snapshot, 'snapshotDigest': digest(snapshot),
                'inputSchema': {'lots': 'signed integer', 'lot_size': 'positive integer', 'correction_lots': 'optional signed integer'},
                'topology': ['synthetic-JSON-producer'], 'imageId': image, 'network': 'none',
                'fidelity': 'Historical single-process synthetic producer and selected trade inputs',
                'omissions': ['production topology', 'live financial data', 'broker integration', 'HA and throughput claims'],
                'liveSourceModified': False}
    if digest(baseline['source_bytes']) != manifest['sourceDigest']:
        raise Conflict('Registered replica source digest differs')
    return bytes(baseline['source_bytes']), manifest


def verify_replica(source, manifest):
    # Sample: Change one source byte or snapshot lot size. Refuse replica execution.
    if manifest.get('format') != 'durai-system-replica/v1' or digest(source) != manifest['sourceDigest'] or digest(manifest['snapshot']) != manifest['snapshotDigest']:
        raise Conflict('Replica source or snapshot differs')
    return True


def candidate_source():
    # Sample: Repair booking and correction in a new version. Preserve historical source.
    source = (ROOT / 'apps/trading-demo/fixtures/repaired.py').read_text(encoding='utf-8')
    import ast
    tree = ast.parse(source)
    if ast.get_docstring(tree):
        source = '\n'.join(source.splitlines()[tree.body[0].end_lineno:]) + '\n'
    source = source.replace('def book(lots, lot_size):\n', 'def book(lots, lot_size):\n    # Sample: Book 2 lots of 100. Return 200 shares.\n')
    source = source.replace('def correct(shares, lots, lot_size):\n', 'def correct(shares, lots, lot_size):\n    # Sample: Remove 1 lot of 100 from 200 shares. Return 100 shares.\n')
    check_walkthroughs(source)
    return source.encode('utf-8')


def evolve(binding_id, database='durai_local', runner=None):
    # Sample: Recreate the 2-share defect in a replica. Repair it without touching the target.
    from .sandbox import DockerRunner
    source, manifest = capture_replica(database)
    verify_replica(source, manifest)
    engine = Engineering(database)
    run_id = engine.create(binding_id, DEFAULT_SPEC, manifest, 'REPLICA')
    runner = runner or DockerRunner(ROOT / 'private/docker-workspaces')
    cases = [{'goal': 'opening', 'input': manifest['snapshot'], 'expected': 200},
             {'goal': 'correction', 'input': dict(manifest['snapshot'], correction_lots=1), 'expected': 100},
             {'goal': 'short', 'input': {'lots': -2, 'lot_size': 100}, 'expected': -200},
             {'goal': 'unit-lot-regression', 'input': {'lots': 7, 'lot_size': 1}, 'expected': 7}]
    try:
        engine.record(run_id, 'DESIGN_READY', engine.evidence(run_id, briefDigest=digest({'manifest': manifest,
            'goal': 'Repair booking and correction to use canonical shares', 'rules': DEFAULT_SPEC})))
        compile(source, 'replica.py', 'exec')
        baseline = runner.execute(source, digest(source), manifest['snapshot'])
        engine.record(run_id, 'EXECUTION_SHELL_READY', engine.evidence(run_id, compiled=True,
                      smokePassed=isinstance(baseline.get('shares'), int), baselineDigest=digest(source)))
        if baseline['shares'] == 200:
            raise Conflict('Replica baseline has no intended quantity defect')
        engine.record(run_id, 'BASELINE_TEST_FAILURE_CONFIRMED', engine.evidence(run_id,
            category='BUSINESS_ASSERTION', baselineDigest=digest(source), suiteDigest=digest(cases),
            assertion='2 lots of 100 produce 200 shares', expected=200, observed=baseline['shares']), 'durai_evaluator')
        engine.record(run_id, 'IMPLEMENTATION_READY', engine.evidence(run_id, suiteDigest=digest(cases)))
        candidate = candidate_source()
        checks = []
        for case in cases:
            before = runner.execute(source, digest(source), case['input'])['shares']
            after = runner.execute(candidate, digest(candidate), case['input'])['shares']
            checks.append({'goal': case['goal'], 'baseline': before, 'expected': case['expected'],
                           'observed': after, 'passed': after == case['expected']})
        verdict = {'suiteDigest': digest(cases), 'candidateDigest': digest(candidate), 'checks': checks,
                   'verdict': 'PASS' if all(item['passed'] for item in checks) else 'FAIL'}
        if verdict['verdict'] != 'PASS':
            raise Conflict('Replica candidate did not meet the business goals')
        verify_replica(Registry(database).get('broken-v1')['source_bytes'], manifest)
        files = {'baseline/program.py': source, 'candidate/program.py': candidate,
                 'replica.json': canonical(manifest), 'comparison.json': canonical(verdict)}
        folder = ROOT / 'private/replicas' / run_id
        sealed = write_bundle(folder, files)
        verify_bundle(folder)
        engine.record(run_id, 'WAITING_FOR_REVIEW', engine.evidence(run_id, **verdict), 'durai_evaluator')
        return {'state': 'WAITING_FOR_REVIEW', 'runId': run_id, 'mode': 'REPLICA',
                'replicaDigest': digest(manifest), 'manifest': sealed, 'outputDirectory': str(folder),
                'comparison': verdict, 'originalSourceUnchanged': True, 'approvalCreated': False,
                'generationMethod': 'authored-domain-repair-adapter', 'fidelity': manifest['fidelity']}
    except Exception as error:
        if engine.get(run_id)['state'] not in ('FAILED', 'WAITING_FOR_REVIEW'):
            engine.record(run_id, 'FAILED', engine.evidence(run_id, errorType=type(error).__name__))
        raise
