"""Sample: Run a blocked seats task and a scripted passing task on disposable PostgreSQL."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from durai.blueprint_patterns import transaction_pack
from durai.common import digest
from durai.db import use_server
from durai.db_sandbox import EphemeralDatabase, docker
from durai.execution_events import replay
from durai.packs import generic_pack
from durai.prompt_quality import BINDING, PACK_VERSION, REVISION
from durai.qualification import TEMPLATES, fixture_files, run_fixture
from test_workspace_worker import SequenceModel
from tools.build_transaction_patterns import install

FIXTURE = 'task-tx-seats-v14-plain'
DATABASE = 'durai_test'


def refuse_model(attempt):
    # Sample: Refuse a provider factory call when revision 2 is absent.
    raise AssertionError('Model factory was called before the prompt gate.')


def scripted(attempt):
    # Sample: Give the seats shell its qualified implementation through one edit tool call.
    path = '/workspace/durai/reservations.py'
    shell = fixture_files(TEMPLATES / FIXTURE)[path].decode('utf-8')
    trusted = (ROOT / path.removeprefix('/workspace/')).read_text(encoding='utf-8')
    return SequenceModel(steps=[('edit_file', {'file_path': path, 'old_string': shell,
                                               'new_string': trusted})], replies=[])


def main():
    # Sample: Install the candidate on a disposable server. Check the fact audit and protected result.
    output = Path(sys.argv[1])
    private = ROOT / 'private'
    private.mkdir(exist_ok=True)
    with EphemeralDatabase(private) as target:
        if target.port == 54410 or not target.name.startswith('durai-eval-db-'):
            raise ValueError('Refuse an operating database')
        docker('network', 'connect', 'bridge', target.name)
        target.wait()
        environment = {'DURAI_DB_HOST': '127.0.0.1', 'DURAI_DB_PORT': str(target.port),
                       'DURAI_SETTINGS': str(target.settings_path)}
        with patch.dict(os.environ, environment), use_server('127.0.0.1', target.port, target.settings_path):
            scratch = target.folder / 'quality'
            blocked = run_fixture(FIXTURE, refuse_model, scratch / 'blocked', DATABASE,
                                  candidate_root=scratch / 'candidates')
            if blocked['outcome'] != 'BLOCKED' or blocked['stop']['reasonCode'] != 'PROMPT_BLUEPRINT_UNAVAILABLE':
                raise AssertionError('Task did not stop before spending')
            install(generic_pack('14'), DATABASE, binding=BINDING)
            installed = install(transaction_pack(generic_pack('14')), DATABASE, rebind=True)
            if installed['bindingRevision'] != REVISION or installed['reference']['version'] != PACK_VERSION:
                raise AssertionError('Disposable binding differs from the worker pin')
            result = run_fixture(FIXTURE, scripted, scratch / 'passing', DATABASE,
                                 candidate_root=scratch / 'candidates')
            events = replay(result['runId'], database=DATABASE)
            audits = [event['payload'] for event in events if event['kind'] == 'PROMPT_FACT_AUDIT']
            if result['outcome'] != 'PASSED' or len(audits) != 1 or audits[0]['state'] != 'PASS':
                raise AssertionError('Protected result or worker fact audit failed: ' + str(result['outcome']))
    receipt = {'state': 'PASS', 'fixture': FIXTURE, 'isolated': True, 'paidModelCalls': 0,
               'blockedOutcome': blocked['outcome'], 'blockedReason': blocked['stop']['reasonCode'],
               'protectedVerdict': result['verdict']['verdict'], 'suiteDigest': result['verdict']['suiteDigest'],
               'packVersion': PACK_VERSION, 'bindingRevision': REVISION, 'audit': audits[0]}
    receipt['digest'] = digest(receipt)
    with output.open('x', encoding='utf-8', newline='\n') as file:
        file.write(json.dumps(receipt, indent=2) + '\n')
    print('PASS. One block before model creation and one scripted protected pass.')


if __name__ == '__main__':
    main()
