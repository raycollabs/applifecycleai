"""Sample: Confirm the two-share defect before dispatch. Check four protected results."""
import json
from .common import digest, Conflict
from .engineering import Engineering
from .generation import ROOT
from .worker_contract import verify_contract


def prepare_repair(source, contract, database='durai_local', runner=None):
    # Sample: Run 2 lots of 100 on the baseline. Record 2 before model dispatch.
    from .sandbox import DockerRunner
    verify_contract(contract, database)
    engine = Engineering(database)
    run_id = engine.create(contract['bindingId'], contract['specification'],
                           {'baselineDigest': digest(source), 'workerContractDigest': contract['digest'],
                            'interface': 'historical-position-producer/v1'}, 'REPLICA')
    try:
        runner = runner or DockerRunner(ROOT / 'private/docker-workspaces')
        suite = json.loads((ROOT / 'tests/protected/f101.json').read_bytes())['cases']
        engine.record(run_id, 'DESIGN_READY', engine.evidence(run_id, briefDigest=contract['digest']))
        compile(source, 'baseline.py', 'exec')
        observed = runner.execute(source, digest(source), suite[0]['input']).get('shares')
        if type(observed) is not int:
            raise Conflict('Repair shell did not return integer shares')
        engine.record(run_id, 'EXECUTION_SHELL_READY', engine.evidence(run_id, compiled=True,
                      smokePassed=True, baselineDigest=digest(source)))
        red_case, red_observed = None, None
        for case in suite:
            value = runner.execute(source, digest(source), case['input']).get('shares')
            if type(value) is not int:
                raise Conflict('Repair baseline failed its execution contract')
            if value != case['expectedShares']:
                red_case, red_observed = case, value
                break
        if red_case is None:
            raise Conflict('Repair baseline has no declared business defect')
        engine.record(run_id, 'BASELINE_TEST_FAILURE_CONFIRMED', engine.evidence(run_id,
            category='BUSINESS_ASSERTION', baselineDigest=digest(source), suiteDigest=digest(suite),
            assertion='Declared position goal: ' + red_case['id'], expected=red_case['expectedShares'], observed=red_observed), 'durai_evaluator')
        engine.record(run_id, 'IMPLEMENTATION_READY', engine.evidence(run_id, suiteDigest=digest(suite)))
        return engine, run_id, runner, suite
    except Exception as error:
        engine.record(run_id, 'FAILED', engine.evidence(run_id, errorType=type(error).__name__))
        raise


def finish_repair(engine, run_id, source, runner, suite):
    # Sample: Check opening 200 and correction 100. Wait for review after all four cases.
    checks = []
    for case in suite:
        observed = runner.execute(source, digest(source), case['input']).get('shares')
        checks.append({'goal': case['id'], 'expected': case['expectedShares'], 'observed': observed,
                       'passed': type(observed) is int and observed == case['expectedShares']})
    if not all(check['passed'] for check in checks):
        return engine.record(run_id, 'FAILED', engine.evidence(run_id, errorType='BusinessEvaluationFailed', checks=checks))
    return engine.record(run_id, 'WAITING_FOR_REVIEW', engine.evidence(run_id, suiteDigest=digest(suite),
                         candidateDigest=digest(source), verdict='PASS', checks=checks), 'durai_evaluator')
