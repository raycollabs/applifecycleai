"""Sample: Run two graph jobs with 40 HTTP attempts. Save each receipt. Stop before candidate adoption."""
import hashlib
import ctypes
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.qualification import *
from durai.batch_provider import BatchCounters, BatchProviderModel
from durai.db import connect
from durai.plans_dir import plans_dir
from durai.packs import PackStore, generic_pack
from durai.policy_store import record_policies
from durai.worker_contract import resolve_contract
from langchain_openai import ChatOpenAI

OUTPUT = ROOT / 'private/batch-20261118'
OUTPUT.mkdir(parents=True, exist_ok=True)
RECEIPT = OUTPUT / 'receipt.json'
STATE = {'status': 'PREFLIGHT', 'jobs': [], 'adopted': False, 'operatingSchemaApplied': False}

def save():
    # Sample: Save job A before starting B. Replace the public status with the same receipt.
    value = json.dumps(STATE, indent=2)
    temporary = RECEIPT.with_suffix('.tmp')
    temporary.write_text(value, encoding='utf-8')
    temporary.replace(RECEIPT)
    plans_dir() / 'durai-batch-status.json'.write_text(value, encoding='utf-8')

def scripted_proofs():
    # Sample: Edit the module shell with its reference. Check the review state with zero external calls.
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_workspace_worker import SequenceModel
    packs = PackStore('durai_test')
    reference = packs.install(generic_pack('8'))
    record_policies(reference, 'durai_test')
    identity = 'batch-v8-scripted-proof-' + uid()[:8]
    packs.bind(identity, [reference], 'SYNTHETIC_LOCAL')
    binding = {'bindingId': identity, 'contractDigest': resolve_contract(identity, database='durai_test')['digest']}
    for part in ('module', 'migration'):
        name = 'task-graph-tenancy-' + part + '-v8'
        folder = TEMPLATES / name
        config = fixture_config(folder)
        path = config['module']
        base = fixture_files(folder)
        reference = load(folder / 'reference')
        steps = [('edit_file', {'file_path': path, 'old_string': base[path].decode(), 'new_string': reference[path].decode()})]
        result = run_job(name, lambda attempt: SequenceModel(steps=steps, replies=[]),
                         OUTPUT / ('proof-' + part), candidate_root=OUTPUT / 'proof-candidates', binding=binding)
        (OUTPUT / ('proof-' + part + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        if result['outcome'] != 'PASSED' or result['attempts'][-1]['state'] != 'WAITING_FOR_REVIEW':
            raise RuntimeError('SCRIPTED_PROOF_FAILED_' + part)
    STATE['scriptedProofs'] = 'PASS'
    save()

def schema_check():
    # Sample: Compare each checked-in migration with its operating receipt. Refuse core_0012 in the operating database.
    with connect('postgres', 'durai_local') as connection:
        rows = connection.execute('SELECT name,digest FROM durai_core.migration').fetchall()
    applied = {row['name']: row['digest'] for row in rows}
    if 'core_0012.sql' in applied:
        raise RuntimeError('OPERATING_SCHEMA_BOUNDARY')
    for path in (ROOT / 'migrations/core').glob('*.sql'):
        if applied.get(path.name) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise RuntimeError('OPERATING_MIGRATION_MISMATCH_' + path.name)
    STATE['operatingMigrationDigests'] = applied
    save()

def composition():
    # Sample: Combine the generated Python and SQL bytes. Run the protected suite and a dump-and-restore check.
    module = '/workspace/durai/application_policy_graph.py'
    migration = '/workspace/migrations/core/core_0012.sql'
    store = CandidateStore(ROOT / 'private/candidates')
    generated = [store.restore(**job['package']) for job in STATE['jobs']]
    base = fixture_files(TEMPLATES / 'task-graph-tenancy-module-v8')
    files = dict(base)
    files[module] = generated[0][module]
    files[migration] = generated[1][migration]
    suite = load(TEMPLATES / 'task-graph-tenancy-module-v8/protected')
    evaluator = SandboxEvaluator(OUTPUT / 'composition')
    spec = fixture_spec('task-graph-tenancy-module-v8', 'durai_test', evaluator.image, evaluator.database_image)
    spec['binding'] = STATE['binding']
    spec['writePaths'] = [module, migration]
    spec.pop('editScope', None)
    from durai.task_contract import build_task
    verdict = evaluator.evaluate(build_task(spec)['task'], files, suite, baseline=base)
    STATE['composition'] = {'verdict': verdict, 'generatedDigests': {
        path: hashlib.sha256(files[path]).hexdigest() for path in (module, migration)}}
    save()
    if verdict['verdict'] != 'PASS':
        return
    from graph_candidate_checks import check_candidate
    STATE['composition']['operatorChecks'] = check_candidate(files, suite, evaluator)
    sealed = build_task(spec)['task']
    STATE['composition']['package'] = store.retain('composed-graph-' + uid(), sealed, files)
    STATE['composition']['review'] = 'WAITING_FOR_HUMAN_REVIEW'
    save()

def run_job(name, model_factory, scratch, database='durai_test', decision=None, budget=None, candidate_root=None, binding=None):
    # Sample: Run fixture task-7-1a-policy-catalog with a scripted model. Return the outcome, attempts, and module text.
    scratch = Path(scratch)
    ledger, store, evaluator = TaskLedger(database), CandidateStore(candidate_root or ROOT / 'private/candidates'), SandboxEvaluator(scratch)
    providers = Providers()
    providers.register(CAPABILITY, 'deepagents@' + DeepAgentsWorker.version,
                       lambda model, folder, attempt_id: DeepAgentsWorker(model, folder, attempt_id, database=database))
    folder = TEMPLATES / name
    spec = task_b_spec(database, evaluator.image) if name == 'task-b-change-manifest' else \
        fixture_spec(name, database, evaluator.image, evaluator.database_image)
    spec['budget'].update(budget or {})
    if binding:
        spec['binding'] = binding
    module = spec['deliverables'][0]
    builder = build_loop(ledger, providers, evaluator, store, FixtureSources(database, folder), model_factory, scratch / 'work')
    run_id = name + '-' + uid()
    config = {'configurable': {'thread_id': run_id}}
    with development_graph(builder, database) as graph:
        state = graph.invoke({'run_id': run_id, 'spec': spec, 'current': pins(spec)}, config)
        if decision and '__interrupt__' in state:
            state = graph.invoke(__import__('langgraph.types', fromlist=['Command']).Command(resume=decision), config)
    attempts = []
    for entry in state.get('history', []):
        row = ledger.get(entry['attemptId'])
        restored = store.restore(**entry['package']) if entry.get('package') else None
        attempts.append({'attemptId': entry['attemptId'], 'verdict': entry['verdict'], 'state': row['state'],
                         'verdictDigest': entry.get('verdictDigest'), 'package': entry.get('package'),
                         'restoredManifest': manifest(restored)['digest'] if restored else None,
                         'usage': ledger.usage(row['budget_id'])})
    passing = state.get('package')
    restored = store.restore(**passing) if passing else None
    return {'runId': run_id, 'outcome': state['outcome'], 'taskDigest': state['task']['digest'],
            'attempts': attempts, 'verdict': state.get('verdict'), 'package': passing,
            'fixture': name, 'module': restored[module].decode() if restored else None,
            'events': [row['kind'] for row in replay(run_id, database=database)],
            'policyViews': [row['payload'] for row in replay(run_id, database=database)
                            if row['kind'] == 'POLICY_VIEW_DELIVERED'],
            'distinctBudgetUsage': {identity: ledger.usage(identity) for identity in
                                    {ledger.get(entry['attemptId'])['budget_id'] for entry in state.get('history', [])}}}


def main():
    # Sample: Check local proofs, bind v8 for this batch, then run module A and migration B without a fallback.
    if (OUTPUT / 'physical.sqlite').exists() and BatchCounters(OUTPUT / 'physical.sqlite', 0).status()['total']:
        print('Existing batch receipt retained. No provider call started.')
        return
    schema_check()
    scripted_proofs()
    if '--proof-only' in sys.argv:
        STATE['status'] = 'PREFLIGHT_PASS'
        save()
        return
    counters = BatchCounters(OUTPUT / 'physical.sqlite', datetime.fromisoformat('2026-11-18T14:30:00-06:00').timestamp())
    if counters.status()['total']:
        raise RuntimeError('BATCH_ALREADY_STARTED_USE_EXISTING_RECEIPT')
    settings = json.loads((ROOT / 'private/deepseek.json').read_text())
    if settings['model'] != 'deepseek-flash' or settings['baseUrl'].rstrip('/') != 'https://api.deepseek.com':
        raise RuntimeError('PROVIDER_CONFIGURATION_MISMATCH')
    STATE['provider'] = {'baseUrl': settings['baseUrl'], 'model': settings['model'], 'sdkRetries': 0,
                         'maxOutputTokens': 32768, 'timeoutSeconds': 180, 'fallback': False,
                         'totalPhysicalCap': 40, 'perJobPhysicalCap': 20}
    packs = PackStore('durai_local')
    reference = packs.install(generic_pack('8'))
    record_policies(reference, 'durai_local')
    identity = 'batch-20261118-v8-' + uid()[:8]
    packs.bind(identity, [reference], 'SYNTHETIC_LOCAL')
    binding = {'bindingId': identity, 'contractDigest': resolve_contract(identity, database='durai_local')['digest']}
    STATE['binding'] = binding
    STATE['status'] = 'RUNNING'
    save()
    for part in ('module', 'migration'):
        if counters.status()['blocked']:
            break
        def factory(attempt):
            # Sample: Create one DeepSeek Flash client with SDK retries off and one shared physical counter.
            inner = ChatOpenAI(model=settings['model'], api_key=settings['apiKey'], base_url=settings['baseUrl'],
                               temperature=1, top_p=.95, max_tokens=32768, timeout=180,
                               max_retries=0, streaming=True, stream_usage=False)
            return BatchProviderModel(inner=inner, counters=counters, job=part)
        result = run_job('task-graph-tenancy-' + part + '-v8', factory, OUTPUT / part,
                         database='durai_local', budget={'modelCalls': 20}, binding=binding)
        STATE['jobs'].append(result)
        STATE['physical'] = counters.status()
        save()
        output = json.dumps(result.get('verdict') or {})
        if any(marker in output for marker in ('Cannot connect to the Docker daemon', 'error during connect',
                                                'Docker infrastructure', 'protected suite differs', 'image is not pinned')):
            STATE['status'] = 'STOPPED_INFRASTRUCTURE'
            save()
            return
    if len(STATE['jobs']) == 2 and all(job['outcome'] == 'PASSED' for job in STATE['jobs']):
        composition()
    STATE['physical'] = counters.status()
    STATE['status'] = 'STOPPED_PROVIDER' if STATE['physical']['blocked'] else 'COMPLETE_REVIEW_ONLY'
    save()

if __name__ == '__main__':
    lock = OUTPUT / 'owner.lock'
    try:
        owner = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit('The batch owner lock is present. No provider call started.')
    try:
        os.write(owner, str(os.getpid()).encode())
        if os.name == 'nt':
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        try:
            main()
        except Exception as error:
            STATE['status'] = 'STOPPED_ERROR'
            STATE['errorClass'] = type(error).__name__
            if (OUTPUT / 'physical.sqlite').exists():
                STATE['physical'] = BatchCounters(OUTPUT / 'physical.sqlite', 0).status()
            (OUTPUT / 'operator-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
            save()
            raise SystemExit(1)
    finally:
        if os.name == 'nt':
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        os.close(owner)
        lock.unlink()
