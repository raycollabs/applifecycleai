"""Sample: Verify worker controls. Install G-2 without replacing G-1 bindings."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from durai.common import digest
from durai.engineering import check_walkthroughs
from durai.generation import ROOT
from durai.local_factory import preserve_target
from durai.packs import PackStore, generic_pack
from durai.worker_contract import resolve_contract


class Results(unittest.TextTestResult):
    def addSuccess(self,test):
        # Sample: Save one passing function name. Keep model messages outside the receipt.
        super().addSuccess(test)
        if not hasattr(self,'goals'):
            self.goals=[]
        self.goals.append({'function':test.id(),'state':'PASS'})


def main():
    # Sample: Install G-2 under a new binding. Check that the approved target does not change.
    before=preserve_target()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    tests=unittest.TextTestRunner(verbosity=1,resultclass=Results).run(suite)
    if not tests.wasSuccessful():
        raise RuntimeError('Worker foundation qualification failed')
    store=PackStore()
    old_reference=next(ref for ref in store.discover() if ref['scope']=='*' and ref['id']=='durai.software-generation' and ref['version']=='1')
    old=store.get(old_reference)
    reference=store.install(generic_pack())
    binding=store.bind('foundation-generic-v2',[reference],'SYNTHETIC_LOCAL')
    bindings={'foundation-generic-v2':binding['digest']}
    try:
        prior=store.resolve('exchange-synthetic')
    except KeyError:
        prior=None
    if prior:
        applications=[ref for ref in prior['document']['packs'] if store.get(ref)['kind']=='APPLICATION']
        trading=store.bind('foundation-exchange-v2',[reference,*applications],'SYNTHETIC_LOCAL')
        resolve_contract('foundation-exchange-v2')
        bindings['foundation-exchange-v2']=trading['digest']
    assert store.get(old_reference)==old
    assert digest(old)==digest(generic_pack('1'))
    assert before==preserve_target()
    coverage={}
    files=['durai/comment_policy.py','durai/worker_contract.py','durai/worker_harness.py','durai/repair_readiness.py',
           'durai/packs.py','durai/engineering.py','durai/generation.py','durai/coding_worker.py',
           'durai/model_fallbacks.py','durai/replica.py','tests/test_worker_foundation.py','tests/test_adapters.py',
           'templates/position-app/program.py','templates/position-app/server.py','templates/position-app/test_business.py',
           'tools/qualify_worker_foundation.py']
    for name in files:
        coverage[name]=check_walkthroughs((ROOT/name).read_bytes())['functions']
    receipt={'state':'PASS','date':'2026-11-16','scope':'Four local ontology and Deep Agents foundation controls',
             'focusedTestsPassed':sum(goal['function'].startswith(('test_worker_foundation.', 'test_adapters.')) for goal in tests.goals),'testGoals':tests.goals,
             'fullRegressionTestsPassed':tests.testsRun,'fullRegressionNote':'Recorded by this qualification run.',
             'genericPack':reference,'previousGenericPack':old_reference,'previousPackUnchanged':True,
             'newBindings':bindings,'existingTargetTradesAndApprovalsUnchanged':True,
             'originalStateDigest':digest(before),'walkthroughCoverage':coverage,
             'effectiveContractDigest':resolve_contract('foundation-generic-v2')['digest'],
             'externalModelCalls':0,'limits':['Qualified position-domain adapter only',
                'Human review required for full ASD-STE100 compliance',
                'Shared agent cost budgets and general planner qualification remain separate work']}
    (ROOT/'receipts/worker-foundation.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in receipt.items() if k!='testGoals'},indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
