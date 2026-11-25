"""Sample: Run the isolated ontology suite. Store its receipt under the primary ontology plan."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    # Sample: Check the disposable server port. Run all tests without a paid model call.
    parser = argparse.ArgumentParser()
    parser.add_argument('--pattern', default='test*.py')
    args = parser.parse_args()
    target = json.loads((ROOT / 'private/context-isolation.json').read_text())
    if target['host'] != '127.0.0.1' or target['port'] == 54410 or not target['name'].startswith('durai-eval-db-'):
        raise ValueError('Refuse a shared test target')
    ports = subprocess.run(['docker', 'port', target['name'], '5432/tcp'], capture_output=True, text=True, check=True).stdout
    if '127.0.0.1:' + str(target['port']) not in ports:
        raise ValueError('Disposable server identity differs')
    os.environ.update(DURAI_DB_HOST=target['host'], DURAI_DB_PORT=str(target['port']), DURAI_SETTINGS=target['settings'])
    from durai.db import prepare, use_server
    from durai.trading import seed
    from durai.common import digest, uid
    from durai.context_store import ContextStore
    prepare('durai_test')
    prepare('durai_local')
    seed('durai_local')
    before = {str(p.relative_to(ROOT)).replace('\\', '/'): digest(p.read_bytes()) for p in sorted((ROOT / 'durai').glob('*.py'))}
    identity = 'ontology-phase1-' + uid()
    log = ROOT / 'private' / (identity + '.log')
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern=args.pattern)
    with log.open('w', encoding='utf-8') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after = {name: digest((ROOT / name).read_bytes()) for name in before}
    success = result.wasSuccessful() and before == after
    receipt = {'state': 'PASS' if success else 'FAIL', 'testsRun': result.testsRun, 'skipped': len(result.skipped),
        'errors': [test.id() for test, detail in result.errors], 'failures': [test.id() for test, detail in result.failures],
        'seconds': round(time.monotonic() - started, 3), 'checkedAt': datetime.now(timezone.utc).isoformat(),
        'server': target['name'], 'sourceUnchangedDuringTests': before == after, 'fileDigests': after,
        'paidModelCalls': 0, 'primaryOntologyInstalls': 0}
    path = ROOT / 'receipts' / (identity + '.json')
    path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    with use_server('127.0.0.1', 54410, ROOT / 'private/local.json'):
        verifier = ContextStore(role='durai_evaluator')
        reference = verifier.verify_artifact('receipt', str(path.relative_to(ROOT)).replace('\\', '/'), digest(path.read_bytes()))
        store = ContextStore()
        plan = 'plan_durai_transaction_pattern_ontology_2026-11-18'
        store.append(plan, 'evidence', {'references': [reference]}, identity + ':evidence')
        store.append(plan, 'progress', {'step': 'Qualify ontology Phase 1 with ' + args.pattern,
            'result': receipt['state'] + ': ' + str(result.testsRun) + ' tests.', 'evidence': [reference]}, identity + ':progress')
    print(json.dumps({'receipt': str(path), 'log': str(log), **{k: receipt[k] for k in ('state', 'testsRun', 'seconds', 'errors', 'failures')}}))
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
