"""Run local replica acceptance and retain explicit scope and evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.common import digest, uid
from durai.engineering import check_walkthroughs
from durai.generation import ROOT
from durai.local_factory import preserve_target
from durai.packs import PackStore, generic_pack
from durai.trading_import import trading_pack
from durai.replica_phase1 import qualify, restore
from durai.sandbox import DockerRunner


class Results(unittest.TextTestResult):
    def startTest(self, test):
        # Sample: Keep the exact function identity for each acceptance goal.
        super().startTest(test)
        if not hasattr(self, 'goals'):
            self.goals = []

    def addSuccess(self, test):
        # Sample: Record one passing goal without a private failure payload.
        super().addSuccess(test)
        self.goals.append({'function': test.id(), 'state': 'PASS'})


def main():
    # Sample: Prove the synthetic replica and compare the approved target before and after.
    before = preserve_target()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    tests = unittest.TextTestRunner(verbosity=1, resultclass=Results).run(suite)
    if not tests.wasSuccessful():
        raise RuntimeError('Durai acceptance tests failed')
    store = PackStore('durai_test')
    generic = store.install(generic_pack())
    document, _ = trading_pack()
    domain = store.install(document)
    binding, key = 'phase1-docker-' + uid(), uid()
    store.bind(binding, [generic, domain], 'SYNTHETIC_LOCAL')
    runner = DockerRunner(ROOT / 'private/phase1-docker-workspaces')
    result = qualify(binding, key, 'durai_test', runner)
    if result['state'] != 'WAITING_FOR_REVIEW':
        raise RuntimeError('Docker replica proof failed')
    with patch.object(runner, 'execute', side_effect=AssertionError('Replay executed source')):
        replay = qualify(binding, key, 'durai_test', runner)
    assert replay == result
    with tempfile.TemporaryDirectory(prefix='phase1-recovery-') as temporary:
        destination = Path(temporary) / 'replica'
        restored = restore(result['outputDirectory'], destination, result['packageDigest'], binding)
        inputs = json.loads((destination / 'fixtures.json').read_bytes())
        candidate = (destination / 'candidate/program.py').read_bytes()
        checks = []
        for case in inputs:
            observed = runner.execute(candidate, digest(candidate), case['input'])
            field = 'category' if 'expectedError' in case else 'netShares'
            expected = case.get('expectedError', case.get('expected'))
            assert observed.get(field) == expected
            checks.append({'goal': case['goal'], 'observed': observed.get(field), 'passed': True})
    probe = b'''import json,socket
s=socket.socket()
s.settimeout(2)
try:
    s.connect(("192.0.2.1",443))
    blocked=False
except OSError:
    blocked=True
finally:
    s.close()
print(json.dumps({"outboundBlocked":blocked}))
'''
    assert runner.execute(probe, digest(probe), {}) == {'outboundBlocked': True}
    after = preserve_target()
    assert before == after
    coverage = {}
    for name in ('durai/replica_manifest.py', 'durai/replica_requests.py', 'durai/replica_phase1.py',
                 'tools/replica_phase1.py', 'tools/qualify_replica_phase1.py', 'tests/test_replica_phase1.py'):
        coverage[name] = check_walkthroughs((ROOT / name).read_bytes())['functions']
    receipt = {'state': 'PASS', 'date': '2026-11-16', 'phase': 'Replica Phase 1',
               'scope': 'Synthetic single-process position application with recreated SQLite schema and fixtures',
               'testsPassed': tests.testsRun, 'testGoals': tests.goals,
               'run': result, 'sameKeyReplayUnchanged': True, 'restoredFiles': restored['filesRestored'],
               'restoredBusinessChecks': checks, 'outboundBlocked': True,
               'existingTargetUnchanged': True, 'originalStateDigest': digest(before),
               'newLocalApprovals': 0, 'walkthroughCoverage': coverage,
               'limits': ['No production database or volume snapshot', 'No multi-service topology or HA qualification',
                          'No automatic retry after owner loss', 'No production performance or large-context claim']}
    (ROOT / 'receipts/replica-phase1.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({key: value for key, value in receipt.items() if key not in ('testGoals', 'run')}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
