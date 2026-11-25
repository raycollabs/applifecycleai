"""Synthetic replica qualification with durable replay and sealed recovery."""
import json
from pathlib import Path
import tempfile
from .common import canonical, digest, Conflict
from .engineering import Engineering, check_walkthroughs
from .generation import ROOT, DEFAULT_SPEC, write_bundle, verify_bundle
from .replica_manifest import capture, verify
from .replica_requests import ReplicaRequests


def qualify(binding_id, key, database='durai_local', runner=None):
    # Sample: Run K-1 for two lots of 100. Return the saved result on replay.
    files, manifest = capture(binding_id, database)
    requests = ReplicaRequests(database)
    owner, prior = requests.claim(key, {'manifestDigest': manifest['id']})
    if prior is not None:
        if prior['state'] == 'LOST' and prior.get('runId'):
            lost_engine = Engineering(database)
            if lost_engine.get(prior['runId'])['state'] not in ('FAILED', 'WAITING_FOR_REVIEW'):
                lost_engine.record(prior['runId'], 'FAILED', lost_engine.evidence(prior['runId'], errorType='OwnerDeadlineExpired'))
        return prior
    engine, run_id = Engineering(database), None
    try:
        run_id = engine.create(binding_id, DEFAULT_SPEC, manifest, 'REPLICA')
        requests.attach(key, owner, run_id)
        if engine.get(run_id)['binding_digest'] != manifest['bindingDigest']:
            raise Conflict('Replica binding changed during capture')
        from .sandbox import DockerRunner
        runner = runner or DockerRunner(ROOT / 'private/docker-workspaces')
        if getattr(runner, 'image', None) != manifest['imageId']:
            raise Conflict('Replica runner image differs')
        verify(files, manifest, binding_id)
        source, candidate = files['baseline/program.py'], files['candidate/program.py']
        for content in (source, candidate):
            check_walkthroughs(content)
            compile(content, 'position.py', 'exec')
        engine.record(run_id, 'DESIGN_READY', engine.evidence(run_id, briefDigest=manifest['id']))
        smoke = runner.execute(source, digest(source), {'commands': []})
        if smoke != {'trades': [], 'netShares': 0}:
            raise Conflict('Replica shell smoke failed')
        engine.record(run_id, 'EXECUTION_SHELL_READY', engine.evidence(run_id, compiled=True, smokePassed=True, baselineDigest=digest(source)))
        suite = json.loads(files['fixtures.json'])
        red = runner.execute(source, digest(source), suite[0]['input'])
        if red.get('netShares') != 2:
            raise Conflict('Replica baseline must reproduce the two-share defect')
        engine.record(run_id, 'BASELINE_TEST_FAILURE_CONFIRMED', engine.evidence(run_id,
            category='BUSINESS_ASSERTION', baselineDigest=digest(source), suiteDigest=digest(suite),
            assertion='2 lots of 100 produce 200 shares', expected=200, observed=2), 'durai_evaluator')
        engine.record(run_id, 'IMPLEMENTATION_READY', engine.evidence(run_id, suiteDigest=digest(suite)))
        checks = []
        for case in suite:
            before = runner.execute(source, digest(source), case['input'])
            after = runner.execute(candidate, digest(candidate), case['input'])
            field = 'category' if 'expectedError' in case else 'netShares'
            expected = case.get('expectedError', case.get('expected'))
            checks.append({'goal': case['goal'], 'baseline': before.get(field), 'observed': after.get(field),
                           'expected': expected, 'passed': after.get(field) == expected})
        verdict = {'suiteDigest': digest(suite), 'candidateDigest': digest(candidate), 'checks': checks,
                   'verdict': 'PASS' if all(item['passed'] for item in checks) else 'FAIL'}
        if verdict['verdict'] != 'PASS':
            raise Conflict('Replica business comparison failed')
        verify(files, manifest, binding_id)
        files.update({'replica.json': canonical(manifest), 'comparison.json': canonical(verdict)})
        folder = ROOT / 'private/phase1-replicas' / run_id
        sealed = write_bundle(folder, files)
        engine.record(run_id, 'WAITING_FOR_REVIEW', engine.evidence(run_id, **verdict), 'durai_evaluator')
        result = {'state': 'WAITING_FOR_REVIEW', 'runId': run_id, 'replicaDigest': manifest['id'],
                  'manifest': sealed, 'packageDigest': digest(sealed), 'outputDirectory': str(folder),
                  'comparison': verdict, 'approvalCreated': False, 'provider': manifest['provider'],
                  'limits': manifest['limits']}
    except Exception as error:
        if run_id and engine.get(run_id)['state'] not in ('FAILED', 'WAITING_FOR_REVIEW'):
            engine.record(run_id, 'FAILED', engine.evidence(run_id, errorType=type(error).__name__))
        result = {'state': 'FAILED', 'runId': run_id, 'errorType': type(error).__name__, 'approvalCreated': False}
    return requests.finish(key, owner, result)


def restore(package, destination, expected_digest, requester):
    # Sample: Restore six sealed files for app A. Reject changed bytes or app B.
    package, destination = Path(package).resolve(), Path(destination).absolute()
    sealed = verify_bundle(package)
    required = {'baseline/program.py', 'candidate/program.py', 'schema.sql', 'fixtures.json', 'replica.json', 'comparison.json'}
    if digest(sealed) != expected_digest or set(sealed['files']) != required:
        raise Conflict('Replica package identity differs')
    files = {name: (package / name).read_bytes() for name in required}
    if any(digest(data) != sealed['files'][name] for name, data in files.items()):
        raise Conflict('Replica package changed during restore')
    manifest = json.loads(files['replica.json'])
    verify({name: data for name, data in files.items() if name not in ('replica.json', 'comparison.json')}, manifest, requester)
    verdict = json.loads(files['comparison.json'])
    if verdict.get('candidateDigest') != digest(files['candidate/program.py']) or verdict.get('suiteDigest') != digest(json.loads(files['fixtures.json'])):
        raise Conflict('Replica comparison identity differs')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(prefix='phase1-restore-', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'bundle'
        write_bundle(staging, files)
        staging.rename(destination)
    return {'state': 'RESTORED', 'packageDigest': expected_digest, 'filesRestored': len(files),
            'outputDirectory': str(destination), 'executionStarted': False, 'approvalCreated': False}
