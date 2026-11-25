"""Sample: Verify the isolated context database. Run the regression suite and store an evidence reference."""
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
    # Sample: Supply the disposable server descriptor. Refuse the shared port before any database write.
    parser = argparse.ArgumentParser()
    parser.add_argument('--environment', default=str(ROOT / 'private/context-isolation.json'))
    parser.add_argument('--pattern', default='test*.py')
    args = parser.parse_args()
    descriptor = Path(args.environment).resolve()
    if not descriptor.is_relative_to(ROOT / 'private'):
        raise ValueError('The environment descriptor must be private to this worktree')
    target = json.loads(descriptor.read_text())
    if target['host'] != '127.0.0.1' or int(target['port']) == 54410 or not target['name'].startswith('durai-eval-db-'):
        raise ValueError('Refuse a shared or nonlocal test database')
    published = subprocess.run(['docker', 'port', target['name'], '5432/tcp'], capture_output=True, text=True, check=True).stdout
    if '127.0.0.1:' + str(target['port']) not in published:
        raise ValueError('The disposable server port differs')
    os.environ.update(DURAI_DB_HOST=target['host'], DURAI_DB_PORT=str(target['port']), DURAI_SETTINGS=target['settings'])
    from durai.db import prepare
    from durai.trading import seed
    from durai.common import digest, uid
    from durai.context_store import ContextStore
    prepare('durai_test')
    prepare('durai_local')
    seed('durai_local')
    run_id = 'context-verify-' + uid()
    started = time.monotonic()
    log = ROOT / 'private' / (run_id + '.log')
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern=args.pattern)
    with log.open('w', encoding='utf-8') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    source_paths = [*sorted((ROOT / 'durai').glob('context_*.py')), ROOT / 'durai/development_loop.py', ROOT / 'durai/recovery.py',
                    *sorted((ROOT / 'migrations/core').glob('*.sql')), *sorted((ROOT / 'migrations/retired').glob('*.sql'))]
    receipt = {'state': 'PASS' if result.wasSuccessful() else 'FAIL', 'testsRun': result.testsRun,
        'errors': [test.id() for test, detail in result.errors], 'failures': [test.id() for test, detail in result.failures],
        'skipped': len(result.skipped), 'seconds': round(time.monotonic() - started, 3), 'checkedAt': datetime.now(timezone.utc).isoformat(),
        'server': target['name'], 'paidModelCalls': 0, 'realEmbeddingQualityQualified': False,
        'fileDigests': {str(path.relative_to(ROOT)).replace('\\', '/'): digest(path.read_bytes()) for path in source_paths}}
    path = ROOT / 'receipts' / (run_id + '.json')
    path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    reference = ContextStore('durai_test', 'durai_evaluator').verify_artifact('receipt', str(path.relative_to(ROOT)).replace('\\', '/'), digest(path.read_bytes()))
    store = ContextStore('durai_test')
    plan_id = 'plan_durai_context_store_2026-11-18'
    if store.read(plan_id):
        store.append(plan_id, 'evidence', {'references': [reference]}, run_id + ':evidence')
        store.append(plan_id, 'progress', {'step': 'Run ' + args.pattern, 'result': receipt['state'] + ': ' + str(result.testsRun) + ' tests.',
            'evidence': [reference]}, run_id + ':progress')
    print(json.dumps({'receipt': str(path), 'log': str(log), **{k: receipt[k] for k in ('state', 'testsRun', 'seconds', 'errors', 'failures')}}))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
