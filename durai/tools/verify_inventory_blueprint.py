"""Sample: Run the isolated blueprint checks. Store the receipt under the active primary plan."""
import argparse
from datetime import datetime, timezone
from contextlib import redirect_stderr
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    # Sample: Check the disposable server port. Run the declared suite without a paid provider call.
    parser=argparse.ArgumentParser()
    parser.add_argument('--focused',action='store_true')
    args=parser.parse_args()
    target=json.loads((ROOT/'private/context-isolation.json').read_text())
    if target['host']!='127.0.0.1' or target['port']==54410 or not target['name'].startswith('durai-eval-db-'):
        raise ValueError('Refuse a shared blueprint target')
    ports=subprocess.check_output(['docker','port',target['name'],'5432/tcp'],text=True)
    if '127.0.0.1:'+str(target['port']) not in ports:
        raise ValueError('Disposable blueprint server differs')
    os.environ.update(DURAI_DB_HOST=target['host'],DURAI_DB_PORT=str(target['port']),DURAI_SETTINGS=target['settings'])
    from durai.db import prepare,use_server
    from durai.common import digest,uid
    from durai.context_store import ContextStore
    prepare('durai_test')
    paths=[ROOT/p for p in ('durai/inventory.py','blueprints/inventory/v1.sql','tests/test_inventory_blueprint.py',
        'durai/reservations.py','blueprints/reservations/v1.sql','tests/test_reservation_blueprint.py',
        'durai/outbox.py','durai/inbox.py','durai/idempotency.py',
        'blueprints/events/v1.sql','blueprints/events/reference.sql','tests/test_event_blueprint.py',
        'blueprints/idempotency/v1.sql','blueprints/idempotency/reference.sql','tests/test_idempotency_blueprint.py')]
    before={p.relative_to(ROOT).as_posix():digest(p.read_bytes()) for p in paths}
    modules=['test_inventory_blueprint']
    if not args.focused:
        modules+=['test_reservation_blueprint','test_event_blueprint','test_idempotency_blueprint','test_architecture_defaults','test_ontology_phase1','test_context_store']
    sys.path.insert(0,str(ROOT/'tests'))
    suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
    identity='inventory-blueprint-'+uid(); log=ROOT/'private'/(identity+'.log')
    started=time.monotonic()
    with log.open('w',encoding='utf-8') as stream, redirect_stderr(stream):
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    after={name:digest((ROOT/name).read_bytes()) for name in before}
    receipt={'state':'PASS' if result.wasSuccessful() and before==after else 'FAIL','testsRun':result.testsRun,
        'skipped':len(result.skipped),'errors':[test.id() for test,detail in result.errors],
        'failures':[test.id() for test,detail in result.failures],'modules':modules,
        'seconds':round(time.monotonic()-started,3),'checkedAt':datetime.now(timezone.utc).isoformat(),
        'server':target['name'],'fileDigests':after,'sourceUnchangedDuringTests':before==after,
        'paidModelCalls':0,'broker':'injected test adapter'}
    path=ROOT/'receipts'/(identity+'.json')
    path.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    with use_server('127.0.0.1',54410,ROOT/'private/local.json'):
        reference=ContextStore(role='durai_evaluator').verify_artifact('receipt',path.relative_to(ROOT).as_posix(),digest(path.read_bytes()))
        store=ContextStore(); plan='plan_durai_phase3_inventory_2026-11-18'
        store.append(plan,'evidence',{'references':[reference]},identity+':evidence')
        store.append(plan,'progress',{'step':'Qualify the inventory blueprint',
            'result':receipt['state']+': '+str(result.testsRun)+' tests.','evidence':[reference]},identity+':progress')
    print(json.dumps({'receipt':str(path),'log':str(log),**{k:receipt[k] for k in ('state','testsRun','seconds','errors','failures')}}))
    return 0 if receipt['state']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
