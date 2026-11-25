"""Sample: Start a disposable database and run the session context tests."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.common import digest
from durai.db import use_server
from durai.db_sandbox import EphemeralDatabase, docker


def main():
    # Sample: Run the session tests on a new local port. Save the synthetic test result.
    private = ROOT / 'private'
    private.mkdir(exist_ok=True)
    with EphemeralDatabase(private) as target:
        if target.port == 54410 or not target.name.startswith('durai-eval-db-'):
            raise ValueError('Refuse the operating database')
        docker('network', 'connect', 'bridge', target.name)
        os.environ['DURAI_SESSION_ISOLATED'] = '1'
        with use_server('127.0.0.1', target.port, target.settings_path):
            suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_session_*.py')
            result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.wasSuccessful() and result.testsRun > 0 and not result.skipped
    files = ('migrations/session_context/session_context_0001.sql', 'durai/session_context.py',
             'durai/session_host.py', 'durai/session_summarizer.py', 'tests/test_session_context.py',
             'tests/test_session_summarizer.py', 'tests/test_session_caps.py', 'tests/test_session_trial.py', 'tests/test_session_compaction.py', 'tests/test_session_search.py', 'tests/test_session_view.py', 'durai/context_delivery.py', 'durai/context_pack.py', 'durai/session_compaction.py', 'migrations/session_context/session_context_0002.sql', 'tools/run_session_summary_trial.py', 'durai/session_settings.py', 'tools/verify_session_context.py')
    receipt = {'state': 'PASS' if passed else 'FAIL', 'testsRun': result.testsRun, 'skipped': len(result.skipped),
               'isolated': True, 'paidModelCalls': 0, 'checkedAt': datetime.now(timezone.utc).isoformat(),
               'fileDigests': {file: digest((ROOT / file).read_bytes()) for file in files}}
    path = ROOT / 'receipts/session-context-host-20261120.json'
    path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
