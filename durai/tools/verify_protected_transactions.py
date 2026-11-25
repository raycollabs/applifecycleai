"""Sample: Run the shell and reference pairs in isolated evaluator databases."""
import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from durai.common import digest, uid
from durai.context_store import ContextStore
from durai.db import use_server
from durai.packs import PackStore
from durai.policy_store import record_policies
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator
from durai.workspace_manifest import manifest
from durai.worker_contract import resolve_contract

PLAN = 'plan_durai_phase4_5_completion_2026-11-19'
FIXTURE = ROOT/'qualification/phase4'
BINDING = 'foundation-exchange-v2'
MODULES = ('__init__','common','db','idempotency','outbox','inbox','reservations','inventory',
           'payment_intents','payment_funds','settlement','journal','job_progress','coordination',
           'object_download','location_search')
SCHEMAS = ('idempotency/v1.sql','idempotency/reference.sql','events/v1.sql','events/reference.sql',
           'reservations/v1.sql','inventory/v1.sql','payments/v1.sql','payments/v2.sql','transactions/v1.sql')


def isolated_target():
    # Sample: Check the test port and refuse the primary database port.
    target = json.loads((ROOT/'private/context-isolation.json').read_text())
    if target['host']!='127.0.0.1' or target['port']==54410 or not target['name'].startswith('durai-eval-db-'):
        raise ValueError('Refuse a shared qualification target')
    port = subprocess.check_output(['docker','port',target['name'],'5432/tcp'],text=True)
    if '127.0.0.1:'+str(target['port']) not in port:
        raise ValueError('Disposable server mapping differs')
    return target


def prepare_binding(target):
    # Sample: Copy the adopted references into the disposable control database.
    with use_server('127.0.0.1',54410,ROOT/'private/local.json'):
        primary = PackStore()
        binding = primary.resolve(BINDING)
        original = binding['document']['packs']
        packs = [primary.get(ref) for ref in original]
    os.environ.update(DURAI_DB_HOST=target['host'],DURAI_DB_PORT=str(target['port']),DURAI_SETTINGS=target['settings'])
    store = PackStore('durai_test')
    refs = [store.install(pack,ref['scope']) for pack,ref in zip(packs,original)]
    if refs != original:
        raise ValueError('Copied pack pins differ')
    isolated = store.bind(BINDING,refs,binding['document']['mode'])
    if isolated['digest'] != binding['digest']:
        raise ValueError('Copied binding digest differs')
    for ref in refs:
        record_policies(ref,'durai_test')
    return resolve_contract(BINDING,database='durai_test')['digest']


def make_shell(source, symbols):
    # Sample: Keep execute_request's signature and replace only its body with a runnable stub.
    source = source.decode('utf-8')
    lines = source.splitlines(keepends=True)
    nodes = [n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in symbols]
    if len(nodes) != len(symbols):
        raise ValueError('A requested function is unavailable')
    for node in sorted(nodes,key=lambda n:n.lineno,reverse=True):
        start = node.body[0].lineno-1
        lines[start:node.end_lineno] = ["    raise NotImplementedError('Complete the public task')\n"]
    return ''.join(lines).encode('utf-8')


def assess(case, trusted, seeds, evaluator, contract_digest):
    # Sample: Run one protected inventory test. Record the source and suite digests.
    scope = {'/workspace/durai/'+m+'.py':s for m,s in case['scope'].items()}
    baseline = dict(trusted)
    for name,symbols in scope.items():
        baseline[name] = make_shell(trusted[name],symbols)
    suite = dict(seeds, **{
        '/workspace/tests/__init__.py':b'',
        '/workspace/tests/support.py':(FIXTURE/'protected/support.py').read_bytes(),
        '/workspace/tests/test_protected.py':(FIXTURE/'protected'/(case['id']+'.py')).read_bytes(),
    })
    if not baseline.keys().isdisjoint(suite):
        raise ValueError('Protected files are in the worker input')
    module = next(iter(scope))
    spec = {'applicationId':'trading','taskId':'TASK-TX-'+case['id']+'-'+uid()[:8],'kind':'FEATURE',
            'profile':'software','objective':case['objective']+' Read /workspace/public-contract.md.','editScope':scope,'sourceRevision':manifest(baseline)['digest'],
            'binding':{'bindingId':BINDING,'contractDigest':contract_digest},'readPaths':['/workspace/**'],
            'writePaths':list(scope),'deniedPaths':['/workspace/tests/**'],
            'validationCommands':['python-compile','python-unittest','comment-policy'],
            'protectedSuite':{'id':'H-TX-'+case['id'],'digest':manifest(suite)['digest']},
            'environment':{'imageId':evaluator.database_image,'database':'ephemeral-postgres-age'},
            'runtime':{'name':'deepagents','version':'reference-qualification-v1'},
            'budget':{'modelCalls':1,'toolCalls':20,'seconds':120,'attempts':1},'deliverables':[module],
            'readiness':{'shellRunnable':True,'failingAcceptanceGoal':case['goal']}}
    built = build_task(spec)
    if built['state'] != 'READY':
        raise ValueError('Public task is incomplete')
    task = built['task']
    before = evaluator.evaluate(task,baseline,suite,baseline=baseline)
    after = evaluator.evaluate(task,trusted,suite,baseline=baseline)
    baseline_checks = {r['command']:r['state'] for r in before['results']}
    reference_checks = {r['command']:r['state'] for r in after['results']}
    failure = next(r for r in before['results'] if r['command']=='python-unittest')
    passed = (before['verdict']=='FAIL' and after['verdict']=='PASS'
              and baseline_checks=={'database-setup':'PASS','python-compile':'PASS','python-unittest':'FAIL','comment-policy':'PASS'}
              and case['goal'] in failure.get('output','') and 'NotImplementedError' in failure.get('output',''))
    return {'case':case['id'],'state':'PASS' if passed else 'FAIL','taskDigest':task['digest'],
            'shellDigest':before['candidateManifest'],'referenceDigest':after['candidateManifest'],
            'suiteDigest':after['suiteDigest'],'imageId':after['imageId'],
            'shellVerdictDigest':before['digest'],'referenceVerdictDigest':after['digest'],
            'shellChecks':baseline_checks,'referenceChecks':reference_checks,'protectedFilesInWorkerInput':False}, before, after


def main():
    # Sample: Run the selected cases and store one evidence receipt for each plan.
    parser = argparse.ArgumentParser()
    parser.add_argument('--case',action='append')
    args = parser.parse_args()
    contracts = json.loads((FIXTURE/'contracts.json').read_text())
    target = isolated_target()
    contract_digest = prepare_binding(target)
    trusted = {'/workspace/durai/'+m+'.py':(ROOT/'durai'/(m+'.py')).read_bytes() for m in MODULES}
    trusted['/workspace/public-contract.md'] = (FIXTURE/'public-contract.md').read_bytes()
    seeds = {'/workspace/tests/seed/'+str(i).zfill(2)+'.sql':(ROOT/'blueprints'/n).read_bytes()
             for i,n in enumerate(SCHEMAS)}
    seeds['/workspace/tests/seed/99.sql'] = (FIXTURE/'protected/seed.sql').read_bytes()
    selected = [case for case in contracts if not args.case or case['id'] in args.case]
    if not selected or args.case and set(args.case) != {case['id'] for case in selected}:
        raise ValueError('Unknown qualification case')
    identity = 'protected-transactions-'+uid()
    rows = []
    with tempfile.TemporaryDirectory(prefix='durai-protected-',dir=ROOT/'private') as folder:
        evaluator = SandboxEvaluator(folder)
        for case in selected:
            row, before, after = assess(case,trusted,seeds,evaluator,contract_digest)
            rows.append(row)
            (ROOT/'private'/(identity+'-'+case['id']+'.json')).write_text(
                json.dumps({'shell':before,'reference':after},indent=2),encoding='utf-8')
            print(json.dumps({'case':case['id'],'state':row['state'],'referenceChecks':row['referenceChecks']}),flush=True)
    receipt = {'state':'PASS' if all(r['state']=='PASS' for r in rows) else 'FAIL',
               'bindingId':BINDING,'contractDigest':contract_digest,'cases':rows,'paidModelCalls':0}
    path = ROOT/'receipts'/(identity+'.json')
    path.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8',newline='\n')
    with use_server('127.0.0.1',54410,ROOT/'private/local.json'):
        ref = ContextStore(role='durai_evaluator').verify_artifact('receipt',path.relative_to(ROOT).as_posix(),digest(path.read_bytes()))
        ContextStore().append(PLAN,'evidence',{'references':[ref]},identity)
    print(json.dumps({'receipt':str(path),'state':receipt['state'],'cases':len(rows)}),flush=True)
    return 0 if receipt['state']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
