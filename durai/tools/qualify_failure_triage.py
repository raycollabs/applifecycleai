"""Sample: Qualify fixture task-failure-triage in durai_test. Write a receipt with RED, GREEN, and a scripted review run."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from durai.common import digest  # noqa: E402
from durai.db import prepare  # noqa: E402
from durai.execution_events import replay  # noqa: E402
from durai.harness import prepare_checkpoints  # noqa: E402
from durai.qualification import TEMPLATES, fixture_files, fixture_spec, load, run_fixture  # noqa: E402
from durai.task_contract import build_task  # noqa: E402
from durai.task_evaluator import SandboxEvaluator  # noqa: E402
from durai.workspace_manifest import manifest  # noqa: E402
from test_workspace_worker import SequenceModel  # noqa: E402

NAME, MODULE, DATABASE = 'task-failure-triage', '/workspace/failure_triage_rules.py', 'durai_test'


def failed_goals(verdict):
    # Sample: Read a unittest output with "FAIL: test_trusted_fixture_fault_stops". Return that test name.
    output = next(item['output'] for item in verdict['results'] if item['command'] == 'python-unittest')
    return sorted(line.split(' ')[1] for line in output.splitlines() if line.startswith(('FAIL: ', 'ERROR: ')))


def qualify():
    # Sample: Evaluate shell S-1 and reference R-1. Run one scripted edit. Return receipt fields with 0 external model calls.
    prepare(DATABASE)
    prepare_checkpoints(DATABASE)
    folder = TEMPLATES / NAME
    base, suite, reference = fixture_files(folder), load(folder / 'protected'), load(folder / 'reference')
    with tempfile.TemporaryDirectory(prefix='durai-triage-qualify-') as scratch:
        evaluator = SandboxEvaluator(Path(scratch))
        task = build_task(fixture_spec(NAME, DATABASE, evaluator.image))['task']
        red = evaluator.evaluate(task, base, suite, baseline=base)
        green = evaluator.evaluate(task, dict(base, **reference), suite, baseline=base)
        steps = [('edit_file', {'file_path': MODULE, 'old_string': base[MODULE].decode(),
                                'new_string': reference[MODULE].decode()})]
        run = run_fixture(NAME, lambda attempt: SequenceModel(steps=steps, replies=[]), Path(scratch), DATABASE,
                          candidate_root=Path(scratch) / 'candidates')
    decisions = [row['payload'] for row in replay(run['runId'], database=DATABASE) if row['kind'] == 'DECISION_RECORDED']
    return {'fixture': NAME, 'database': DATABASE, 'binding': task['binding'], 'taskObjectiveDigest': digest(task['objective']),
            'shellManifest': manifest(base)['digest'], 'referenceManifest': manifest(dict(base, **reference))['digest'],
            'suiteDigest': manifest(suite)['digest'],
            'red': {'verdict': red['verdict'], 'digest': red['digest'], 'failedGoals': failed_goals(red),
                    'states': {item['command']: item['state'] for item in red['results']}},
            'green': {'verdict': green['verdict'], 'digest': green['digest'],
                      'states': {item['command']: item['state'] for item in green['results']}},
            'scriptedRun': {'runId': run['runId'], 'outcome': run['outcome'], 'attemptState': run['attempts'][-1]['state'],
                            'externalModelCalls': 0, 'scriptedModelCalls': run['attempts'][-1]['usage'].get('modelCalls'),
                            'package': run['package'],
                            'decisions': [{key: row[key] for key in ('point', 'choice', 'reasons', 'ruleDigest', 'featuresDigest')}
                                          for row in decisions]}}


if __name__ == '__main__':
    receipt = qualify()
    target = ROOT / 'receipts/failure-triage-fixture-qualification.json'
    target.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'red': receipt['red']['failedGoals'], 'green': receipt['green']['verdict'],
                      'run': receipt['scriptedRun']['outcome'], 'state': receipt['scriptedRun']['attemptState']}))
