"""Sample: Evaluate the shell and the trusted reference of 22 v14 transaction tasks with no model. Build each pack. Write one receipt."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.common import digest, uid
from durai.context_pack import build_pack
from durai.error_contract import unnamed_errors
from durai.qualification import TEMPLATES, fixture_config, fixture_files, fixture_spec, fixture_suite
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator
from durai.workspace_manifest import manifest, visible

EXPECTED_SHELL = {'database-setup': 'PASS', 'python-compile': 'PASS', 'python-unittest': 'FAIL', 'comment-policy': 'PASS'}


def protected_lines(suite):
    # Sample: Read test_protected.py and seed 99.sql. Return their stripped lines with 25 or more characters.
    keep = [path for path in suite if path.endswith('/test_protected.py') or path.endswith('/99.sql')]
    return {line.strip() for path in keep for line in suite[path].decode('utf-8').splitlines() if len(line.strip()) >= 25}


def leaks(files, suite, pack_text):
    # Sample: Put a protected test line in a workspace file. Return that line.
    lines = protected_lines(suite)
    text = '\n'.join(content.decode('utf-8', 'replace') for content in files.values())
    return sorted(line for line in lines if line in text or line in pack_text)


def assess(name, evaluator, database):
    # Sample: Evaluate task-tx-journal-v14. Return shell FAIL with test_balance_and_reversal, reference PASS, and pack facts.
    folder = TEMPLATES / name
    config = fixture_config(folder)
    spec = fixture_spec(name, database, evaluator.image, evaluator.database_image)
    built = build_task(spec)
    if built['state'] != 'READY':
        return {'template': name, 'state': 'FAIL', 'reason': 'task not ready: ' + json.dumps(built.get('missing'))}
    task = built['task']
    files, suite = fixture_files(folder), fixture_suite(folder)
    trusted = dict(files, **{path: (ROOT / path.removeprefix('/workspace/')).read_bytes() for path in config['editScope']})
    pack = build_pack(task, visible(task, files), 60000, [config['failingAcceptanceGoal']])
    found = leaks(files, suite, pack['text'])
    before = evaluator.evaluate(task, files, suite, baseline=files)
    after = evaluator.evaluate(task, trusted, suite, baseline=files)
    shell_checks = {item['command']: item['state'] for item in before['results']}
    reference_checks = {item['command']: item['state'] for item in after['results']}
    unit = next((item for item in before['results'] if item['command'] == 'python-unittest'), {})
    checks = {'disjoint': files.keys().isdisjoint(suite), 'noLeak': not found,
              'errorsNamed': not unnamed_errors(task['objective'], suite),
              'shellFails': before['verdict'] == 'FAIL' and shell_checks == EXPECTED_SHELL,
              'goalNamed': config['failingAcceptanceGoal'] in unit.get('output', ''),
              'stubRaised': 'NotImplementedError' in unit.get('output', ''),
              'referencePasses': after['verdict'] == 'PASS',
              'writePathsFirst': set(pack['included'][:len(task['writePaths'])]) == set(task['writePaths']),
              'packLimit': pack['characters'] <= 60000}
    return {'template': name, 'case': config['case'], 'variant': config['variant'],
            'state': 'PASS' if all(checks.values()) else 'FAIL', 'checks': checks, 'leaks': found[:5],
            'taskDigest': task['digest'], 'workspaceDigest': manifest(files)['digest'], 'suiteDigest': after['suiteDigest'],
            'shellVerdictDigest': before['digest'], 'referenceVerdictDigest': after['digest'], 'imageId': after['imageId'],
            'shellChecks': shell_checks, 'referenceChecks': reference_checks,
            'pack': {'digest': pack['digest'], 'characters': pack['characters'], 'included': len(pack['included']),
                     'cut': pack['cut']}}


def main(names=None, database='durai_test'):
    # Sample: Run 22 templates. Write receipts/transaction-fixtures-v14-dry-run-<id>.json with 22 PASS rows and 0 model calls.
    names = names or sorted(item.name for item in TEMPLATES.glob('task-tx-*-v14*'))
    rows = []
    with tempfile.TemporaryDirectory(prefix='durai-tx-dry-', dir=ROOT / 'private') as folder:
        evaluator = SandboxEvaluator(folder)
        for name in names:
            row = assess(name, evaluator, database)
            rows.append(row)
            print(json.dumps({key: row.get(key) for key in ('template', 'state', 'checks', 'reason')}), flush=True)
    receipt = {'state': 'PASS' if rows and all(row['state'] == 'PASS' for row in rows) else 'FAIL',
               'templates': len(rows), 'paidModelCalls': 0, 'binding': 'foundation-generic-v14', 'rows': rows}
    path = ROOT / 'receipts' / ('transaction-fixtures-v14-dry-run-' + uid()[:8] + '.json')
    path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'receipt': path.relative_to(ROOT).as_posix(), 'digest': digest(path.read_bytes()), 'state': receipt['state']}))
    return 0 if receipt['state'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:] or None))
