"""Sample: Import receipt dev-loop-task-tx-funds-v14-deepseek-flash-cap50-final-r1.json. Read 1 run, 1 attempt, its cost, and the model scorecard."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
from psycopg.types.json import Jsonb
from .common import Conflict
from .db import ROOT, connect, transaction

MIGRATIONS = ROOT / 'migrations/bench'
WRITER = 'durai_evaluator'
READER = 'durai_runtime'
STRATEGIES = ('SINGLE', 'SUBAGENTS', 'TEAM')
REQUIRED = ('runId', 'outcome', 'attempts', 'model', 'provider', 'fixture')
LABEL = re.compile(r'^dev-loop-(?P<fixture>.+?)-(?P<label>(?:deepseek|glm|zai|z-ai|gemini|mistral|ling|relace|nemotron|local|opus|sonnet|haiku|fable|gpt)[\w.-]*)\.json$')


def prepare_bench(database='durai_local'):
    # Sample: Prepare durai_test. Apply bench_0001.sql once. Run it again. Check no second apply.
    applied = []
    with connect('postgres', database) as admin:
        admin.execute('CREATE TABLE IF NOT EXISTS durai_core.migration(name text PRIMARY KEY, digest text NOT NULL, applied_at timestamptz NOT NULL DEFAULT clock_timestamp())')
        admin.execute("SELECT pg_advisory_xact_lock(hashtext('durai-migration'))")
        for path in sorted(MIGRATIONS.glob('*.sql')):
            name, digest = 'bench/' + path.name, hashlib.sha256(path.read_bytes()).hexdigest()
            before = admin.execute('SELECT digest FROM durai_core.migration WHERE name=%s', (name,)).fetchone()
            if before and before['digest'] != digest:
                raise ValueError('Applied migration changed: ' + name)
            if not before:
                admin.execute(path.read_text(encoding='utf-8'), prepare=False)
                admin.execute('INSERT INTO durai_core.migration(name,digest) VALUES(%s,%s)', (name, digest))
            applied.append({'name': name, 'sha256': digest})
    return applied


def add_profile(profile_id, provider, model, endpoint_kind, settings=None, database='durai_local', role='execution'):
    # Sample: Add profile deepseek-flash-direct for provider deepseek. Add it again. Check one row.
    with transaction(WRITER, database) as db:
        db.execute('INSERT INTO durai_bench.model_profile(id,provider,model,endpoint_kind,settings,role) VALUES(%s,%s,%s,%s,%s,%s) '
                   'ON CONFLICT DO NOTHING', (profile_id, provider, model, endpoint_kind, Jsonb(settings or {}), role))
    return profile_id


def add_price(row, database='durai_local'):
    # Sample: Add DeepSeek Flash 0.30 input, 0.006 cached, 1.20 output from 2026-11-01, AGGREGATOR, with 2 source URLs.
    keys = ('provider', 'model', 'effective_from', 'period', 'verification', 'input_per_million', 'cached_input_per_million',
            'output_per_million', 'currency', 'sources', 'checked_at')
    value = dict({'period': 'standard', 'currency': 'USD', 'cached_input_per_million': None}, **row)
    with transaction(WRITER, database) as db:
        db.execute('INSERT INTO durai_bench.price(' + ','.join(keys) + ') VALUES(' + ','.join(['%s'] * len(keys)) + ') ON CONFLICT DO NOTHING',
                   tuple(Jsonb(value[key]) if key == 'sources' else value[key] for key in keys))


def task_case(fixture, root=ROOT):
    # Sample: Read fixture task-tx-funds-v14-plain. Return id task-tx-funds-v14-plain, family transaction-v14, case funds, variant plain.
    config_path = root / 'templates' / fixture / 'task.json'
    config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
    if fixture.startswith('task-tx-') and config.get('case'):
        return {'id': fixture, 'family': 'transaction-v14', 'case_name': config['case'], 'variant': config.get('variant', 'contract'),
                'template': fixture}
    return {'id': fixture, 'family': 'single-task', 'case_name': fixture, 'variant': 'default', 'template': fixture}


def label_of(path):
    # Sample: Read dev-loop-task-tx-funds-v14-plain-deepseek-flash-cap50-final-r1.json. Return deepseek-flash-cap50-final-r1.
    match = LABEL.match(Path(path).name)
    if not match:
        raise ValueError('Receipt name has no model label: ' + Path(path).name)
    return match.group('label')


def check_receipt(receipt):
    # Sample: Read a receipt without attempts. Return the reason "missing attempts".
    missing = [key for key in REQUIRED if key not in receipt]
    if missing:
        return 'missing ' + ', '.join(missing)
    if not isinstance(receipt['attempts'], list):
        return 'attempts is not a list'
    for attempt in receipt['attempts']:
        if not isinstance(attempt, dict) or not isinstance(attempt.get('usage', {}), dict):
            return 'attempt shape differs'
    return None


def attempt_rows(receipt, timings):
    # Sample: Read a receipt with attempts FAIL and PASS. Return rows 1 and 2 with calls, tokens, and the failed goals on row 1 only when the run failed.
    rows = []
    ledger = ((receipt['attempts'][-1].get('usage') or {}).get('modelCalls') or 0) if receipt['attempts'] else 0
    measured = sum(((item.get('efficiency') or {}).get('metrics') or {}).get('modelCalls') or 0 for item in receipt['attempts'])
    verdict = receipt.get('verdict') if isinstance(receipt.get('verdict'), dict) else {}
    goals = sorted({goal for item in verdict.get('results', []) for goal in item.get('failedGoals') or []})
    fault = next((item.get('fault') for item in verdict.get('results', []) if item.get('fault')), None) or {}
    for number, attempt in enumerate(receipt['attempts'], 1):
        metrics = ((attempt.get('efficiency') or {}).get('metrics') or {})
        last = number == len(receipt['attempts'])
        rows.append({'number': number, 'verdict': attempt.get('verdict'), 'model_calls': metrics.get('modelCalls'),
                     'tool_calls': metrics.get('toolCalls'), 'read_calls': metrics.get('readCalls'), 'query_calls': metrics.get('queryCalls'),
                     'edit_calls': metrics.get('editCalls'), 'refused_writes': metrics.get('refusedWrites'),
                     'calls_before_first_write': metrics.get('callsBeforeFirstWrite'), 'input_tokens': metrics.get('inputCount'),
                     'cached_input_tokens': metrics.get('cachedInputCount'), 'usage_complete': not (last and ledger > measured), 'output_tokens': metrics.get('outputCount'),
                     'reasoning_tokens': metrics.get('reasoningCount'), 'pack_characters': metrics.get('packCharacters'),
                     'worker_seconds': timings.get('workers', {}).get(attempt.get('attemptId')),
                     'failed_goals': goals if last and receipt['outcome'] != 'PASSED' else [],
                     'fault_origin': fault.get('origin') if last and receipt['outcome'] != 'PASSED' else None,
                     'reason_code': fault.get('reasonCode') if last and receipt['outcome'] != 'PASSED' else None})
    return rows


def provider_totals(receipt):
    # Sample: Read providerRequests of 2 requests with 600,000 and 400,000 input tokens. Return input 1,000,000. Return None when the list is absent or empty.
    requests = receipt.get('providerRequests')
    if not isinstance(requests, list) or not requests or not all(isinstance(item, dict) for item in requests):
        return None
    total = lambda key: sum(item.get(key) or 0 for item in requests)
    return (total('inputTokens'), total('cachedInputTokens'), total('outputTokens'), total('reasoningTokens'))


def run_timings(run_id, database='durai_local'):
    # Sample: Read events of run R-1 from 19:43:21 to 19:45:11 with attempt A-1 claimed at 19:43:21 and measured at 19:45:02. Return 110 and 101 seconds.
    with transaction(READER, database) as db:
        db.execute('SET TRANSACTION READ ONLY')
        rows = db.execute('SELECT kind,payload,created_at FROM durai_coordination.event WHERE run_id=%s ORDER BY sequence', (run_id,)).fetchall()
    if not rows:
        return {}
    claimed, workers = {}, {}
    for row in rows:
        attempt = (row['payload'] or {}).get('attemptId') if isinstance(row['payload'], dict) else None
        if row['kind'] == 'ATTEMPT_CLAIMED' and attempt:
            claimed.setdefault(attempt, row['created_at'])
        if row['kind'] == 'WORKER_EFFICIENCY' and attempt in claimed:
            workers[attempt] = round((row['created_at'] - claimed[attempt]).total_seconds(), 3)
    return {'started': rows[0]['created_at'], 'finished': rows[-1]['created_at'], 'workers': workers}


def parse_timings(timings):
    # Sample: Read started "2026-11-20T10:00:00+00:00". Return the same instant as a datetime.
    value = dict(timings)
    for key in ('started', 'finished'):
        if isinstance(value.get(key), str):
            value[key] = datetime.fromisoformat(value[key])
    return value


def commit_of(path, root=ROOT):
    # Sample: Read a committed receipt. Return the 40-character commit that added it, or None for an uncommitted file.
    try:
        result = subprocess.run(['git', '-c', 'safe.directory=*', 'log', '--diff-filter=A', '-1', '--format=%H', '--', str(path)],
                                cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if re.fullmatch(r'[a-f0-9]{40}', value) else None


def import_receipt(path, profile_id, database='durai_local', timing_database='durai_local', pack=None, advisor_profile=None, root=ROOT, timings=None):
    # Sample: Import the funds receipt twice. Return "imported" then "unchanged". Change one byte. Raise Conflict.
    path = Path(path)
    content = path.read_bytes()
    receipt_digest = hashlib.sha256(content.replace(b'\r\n', b'\n')).hexdigest()
    receipt = json.loads(content)
    reason = check_receipt(receipt)
    if reason:
        return {'state': 'INVALID', 'path': path.name, 'reason': reason}
    strategy = receipt.get('strategy') or 'SINGLE'
    if strategy not in STRATEGIES:
        return {'state': 'INVALID', 'path': path.name, 'reason': 'strategy is not one of ' + ', '.join(STRATEGIES)}
    endpoint = receipt.get('endpoint') or receipt.get('providerEndpoint') or None
    provider = provider_totals(receipt) or (None,) * 4
    timings = parse_timings(timings) if timings is not None else (run_timings(receipt['runId'], timing_database) if timing_database else {})
    case = task_case(receipt['fixture'], root)
    advisor = receipt.get('advisor') if isinstance(receipt.get('advisor'), dict) else None
    tokens, latencies = ((advisor or {}).get('tokens') or {}), ((advisor or {}).get('latencies') or [])
    advisor_profile = ((advisor or {}).get('profile') or {}).get('id') or advisor_profile if advisor else advisor_profile
    totals = (advisor.get('calls'), tokens.get('input'), tokens.get('output'), tokens.get('reasoning'), round(sum(latencies), 3),
              Jsonb(advisor.get('states') or [])) if advisor else (None,) * 6
    binding = (advisor or {}).get('binding') or ((receipt.get('binding') or {}).get('id') if isinstance(receipt.get('binding'), dict) else None)
    passed = next((number for number, attempt in enumerate(receipt['attempts'], 1) if attempt.get('verdict') == 'PASS'), None)
    usage = receipt['attempts'][-1].get('usage', {}) if receipt['attempts'] else {}
    relative = path.resolve().relative_to(root.resolve()).as_posix() if path.resolve().is_relative_to(root.resolve()) else path.name
    with transaction(WRITER, database) as db:
        db.execute('INSERT INTO durai_bench.task_case(id,family,case_name,variant,template) VALUES(%(id)s,%(family)s,%(case_name)s,%(variant)s,%(template)s) '
                   'ON CONFLICT DO NOTHING', case)
        inserted = db.execute(
            'INSERT INTO durai_bench.run(run_id,task_case_id,label,model_profile_id,binding,pack,advisor_profile,outcome,pass_attempt,model_calls,'
            'stop_reason,task_digest,retries,receipt_path,receipt_digest,git_commit,started_at,finished_at,advisor_calls,advisor_input_tokens,'
            'advisor_output_tokens,advisor_reasoning_tokens,advisor_seconds,advisor_states,strategy,endpoint,provider_input_tokens,'
            'provider_cached_input_tokens,provider_output_tokens,provider_reasoning_tokens) VALUES(' + ','.join(['%s'] * 30) + ') '
            'ON CONFLICT (run_id) DO NOTHING RETURNING run_id',
            (receipt['runId'], case['id'], label_of(path), profile_id, binding,
             Jsonb(pack or {}), advisor_profile, receipt['outcome'], passed, usage.get('modelCalls', 0),
             (receipt.get('stop') or {}).get('reasonCode') if isinstance(receipt.get('stop'), dict) else None, receipt.get('taskDigest'),
             receipt.get('clientRetries') if isinstance(receipt.get('clientRetries'), int) else None, relative, receipt_digest,
             commit_of(path, root), timings.get('started'), timings.get('finished'), *totals, strategy, endpoint, *provider)).fetchone()
        if not inserted:
            stored = db.execute('SELECT receipt_digest FROM durai_bench.run WHERE run_id=%s', (receipt['runId'],)).fetchone()
            if stored['receipt_digest'] != receipt_digest:
                raise Conflict('Stored run has a different receipt digest: ' + receipt['runId'])
            return {'state': 'UNCHANGED', 'path': path.name, 'runId': receipt['runId']}
        for row in attempt_rows(receipt, timings):
            db.execute('INSERT INTO durai_bench.attempt(run_id,' + ','.join(row) + ') VALUES(%s,' + ','.join(['%s'] * len(row)) + ')',
                       (receipt['runId'], *row.values()))
    return {'state': 'IMPORTED', 'path': path.name, 'runId': receipt['runId'], 'attempts': len(receipt['attempts'])}


def backfill_provider_usage(database, root_paths):
    # Sample: Planner runs this with the postgres admin login only (the evaluator login has no UPDATE grant): backfill_provider_usage('durai_local', [Path('<plans>/durai_local_repo')]).
    # Sample: Run R-1 has providerRequests of 1,000,000 input and NULL provider columns. Fill them from receipts/R-1.json under the root. A second call returns an empty report.
    # Sample: Receipt bytes differ from the stored digest. Skip the run and report DIGEST_MISMATCH.
    roots = [Path(item) for item in root_paths]
    report = []
    with connect('postgres', database) as admin:
        rows = admin.execute('SELECT run_id,receipt_path,receipt_digest FROM durai_bench.run WHERE provider_input_tokens IS NULL ORDER BY run_id').fetchall()
        for row in rows:
            found = next((root / row['receipt_path'] for root in roots if (root / row['receipt_path']).is_file()), None)
            if found is None:
                report.append({'runId': row['run_id'], 'state': 'MISSING_RECEIPT'})
                continue
            content = found.read_bytes()
            if hashlib.sha256(content.replace(b'\r\n', b'\n')).hexdigest() != row['receipt_digest']:
                report.append({'runId': row['run_id'], 'state': 'DIGEST_MISMATCH', 'path': str(found)})
                continue
            totals = provider_totals(json.loads(content))
            if totals is None:
                continue
            admin.execute('UPDATE durai_bench.run SET provider_input_tokens=%s,provider_cached_input_tokens=%s,provider_output_tokens=%s,'
                          'provider_reasoning_tokens=%s WHERE run_id=%s AND provider_input_tokens IS NULL', (*totals, row['run_id']))
            report.append({'runId': row['run_id'], 'state': 'FILLED'})
    return report


def add_experiment(experiment, database='durai_local'):
    # Sample: Add experiment pack-order with baseline r2 and treatment order-r1 on transaction-v14. Add it again. Check one row.
    value = dict({'variants': ['contract', 'plain'], 'decision': None, 'result': {}, 'baseline_variants': None, 'treatment_variants': None}, **experiment)
    with transaction(WRITER, database) as db:
        db.execute('INSERT INTO durai_bench.experiment(id,question,plan_file,baseline_labels,treatment_labels,family,variants,decision,result,baseline_variants,treatment_variants) '
                   'VALUES(%(id)s,%(question)s,%(plan_file)s,%(baseline_labels)s,%(treatment_labels)s,%(family)s,%(variants)s,%(decision)s,%(result)s,'
                   '%(baseline_variants)s,%(treatment_variants)s) '
                   'ON CONFLICT DO NOTHING', dict(value, result=Jsonb(value['result'])))


def scorecard(database='durai_local', family=None):
    # Sample: Read the scorecard for transaction-v14. Return one row per model profile with pass rate and cost per pass.
    with transaction(READER, database) as db:
        db.execute('SET TRANSACTION READ ONLY')
        return db.execute('SELECT * FROM durai_bench.model_scorecard WHERE %s::text IS NULL OR family=%s ORDER BY family, model_profile_id',
                          (family, family)).fetchall()


def compare(experiment_id, database='durai_local'):
    # Sample: Compare experiment pack-order on 22 pairs. Return both-pass, treatment-only, baseline-only, and both-fail counts.
    with transaction(READER, database) as db:
        db.execute('SET TRANSACTION READ ONLY')
        rows = db.execute('SELECT * FROM durai_bench.experiment_pairs WHERE experiment_id=%s ORDER BY case_name, variant', (experiment_id,)).fetchall()
    pairs = [row for row in rows if row['baseline_pass'] is not None and row['treatment_pass'] is not None]
    count = lambda base, treat: sum(1 for row in pairs if bool(row['baseline_pass']) is base and bool(row['treatment_pass']) is treat)
    return {'experimentId': experiment_id, 'pairs': len(pairs), 'bothPass': count(True, True), 'treatmentOnly': count(False, True),
            'baselineOnly': count(True, False), 'bothFail': count(False, False), 'rows': rows}
