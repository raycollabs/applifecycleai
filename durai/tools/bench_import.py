"""Sample: Export timings of 141 receipts from durai_local. Import the receipts into durai_bench with 2 price rows and 2 experiments."""
import argparse
import fnmatch
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.bench_store import LABEL, add_experiment, add_price, add_profile, compare, import_receipt, run_timings, scorecard  # noqa: E402

PRICES = [
    {'provider': 'deepseek', 'model': 'deepseek-flash', 'period': 'peak', 'input_per_million': 0.30, 'cached_input_per_million': 0.006,
     'output_per_million': 1.20, 'sources': ['https://www.morphllm.com/deepseek-api', 'https://benchlm.ai/deepseek/api-pricing',
                                             'https://www.nxcode.io/resources/news/deepseek-api-pricing-complete-guide-2026']},
    {'provider': 'z.ai', 'model': 'glm-5.3', 'period': 'standard', 'input_per_million': 1.40, 'cached_input_per_million': 0.26,
     'output_per_million': 4.40, 'sources': ['https://aipricecompare.org/api/glm-api-pricing.html', 'https://www.maximem.ai/blog/glm-api-pricing-2026']},
]
ADVISOR_PROFILE = {'id': 'deepseek-flash-think-low', 'provider': 'deepseek', 'model': 'deepseek-flash', 'endpoint_kind': 'direct',
                   'settings': {'thinking': True, 'reasoningEffort': 'low'}}
PAID_PLAN = 'plan_durai_transaction_paid_runs_v14_2026-11-19.md'
EXPERIMENTS = [
    {'id': 'pack-order', 'question': 'Does relevance order of pack files help?', 'plan_file': 'plan_durai_pack_relevance_order_2026-11-20.md',
     'baseline_labels': ['deepseek-flash-cap50-r2'], 'treatment_labels': ['deepseek-flash-cap50-order-r1'], 'family': 'transaction-v14',
     'decision': 'keep relevance order'},
    {'id': 'final-text', 'question': 'Do the task text fixes help?', 'plan_file': PAID_PLAN,
     'baseline_labels': ['deepseek-flash-cap50-order-r1'], 'treatment_labels': ['deepseek-flash-cap50-final-r1'], 'family': 'transaction-v14',
     'result': {'treatment_task_text_commit': 'e96d4b8', 'note': 'Inventory objective changed later at commit e92adda.'}},
    {'id': 'contract-vs-plain-r2', 'question': 'Does the contract text beat the plain text?', 'plan_file': PAID_PLAN,
     'baseline_labels': ['deepseek-flash-cap50-r2'], 'treatment_labels': ['deepseek-flash-cap50-r2'], 'family': 'transaction-v14',
     'baseline_variants': ['plain'], 'treatment_variants': ['contract']},
    {'id': 'contract-vs-plain-final', 'question': 'Does the contract text beat the plain text?', 'plan_file': PAID_PLAN,
     'baseline_labels': ['deepseek-flash-cap50-final-r1'], 'treatment_labels': ['deepseek-flash-cap50-final-r1'], 'family': 'transaction-v14',
     'baseline_variants': ['plain'], 'treatment_variants': ['contract']},
]


def receipts(root=ROOT, only='dev-loop-*.json'):
    # Sample: List receipts/dev-loop-*.json under a root. Return 141 paths in name order. With only "*advsmoke*" return the matching paths.
    return sorted(path for path in (Path(root) / 'receipts').glob('dev-loop-*.json') if fnmatch.fnmatch(path.name, only))


def profile_of(receipt):
    # Sample: Read provider deepseek and model deepseek-flash. Return id deepseek-flash-direct, endpoint direct.
    provider, model = str(receipt.get('provider')), str(receipt.get('model'))
    local = 'local' in provider
    endpoint = 'local' if local else ('router' if '/' in model else 'direct')
    name = model.rsplit('/', 1)[-1] if local else model
    slug = re.sub(r'[^a-z0-9._-]+', '-', name.lower()).strip('-.') or 'unknown'
    return {'id': (slug + '-' + endpoint)[:79], 'provider': provider, 'model': model, 'endpoint_kind': endpoint}


def export_timings(out, root=ROOT):
    # Sample: Read 141 receipts. Write {runId: {started, finished, workers}} for each run found in durai_local events.
    result = {}
    for path in receipts(root):
        data = json.loads(path.read_text(encoding='utf-8'))
        run_id = data.get('runId') if isinstance(data, dict) else None
        found = run_timings(run_id, 'durai_local') if run_id else {}
        if found:
            result[run_id] = {'started': found['started'].isoformat(), 'finished': found['finished'].isoformat(), 'workers': found['workers']}
    Path(out).write_text(json.dumps(result, indent=1), encoding='utf-8')
    print('timings', len(result), 'of', len(receipts(root)), 'receipts ->', out)


def import_all(database, timings, root=ROOT, only='dev-loop-*.json'):
    # Sample: Import 121 receipts. Return counts by state plus the skipped list with reasons.
    counts, skipped, invalid = {}, [], []
    for path in receipts(root, only):
        if not LABEL.match(path.name):
            skipped.append((path.name, 'name has no model label'))
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        profile = profile_of(data)
        add_profile(profile['id'], profile['provider'], profile['model'], profile['endpoint_kind'], database=database)
        try:
            result = import_receipt(path, profile['id'], database, None, root=Path(root), timings=timings.get(data.get('runId'), {}))
        except Exception as error:
            result = {'state': 'ERROR', 'path': path.name, 'reason': type(error).__name__ + ': ' + str(error)}
        counts[result['state']] = counts.get(result['state'], 0) + 1
        if result['state'] in ('INVALID', 'ERROR'):
            invalid.append((path.name, result['reason']))
    return counts, skipped, invalid


def import_main(args):
    # Sample: Import into durai_test with timings.json, prices, and experiments. Print counts, INVALID receipts, scorecard, and comparisons.
    timings = json.loads(Path(args.timings).read_text(encoding='utf-8'))
    add_profile(ADVISOR_PROFILE['id'], ADVISOR_PROFILE['provider'], ADVISOR_PROFILE['model'], ADVISOR_PROFILE['endpoint_kind'],
                ADVISOR_PROFILE['settings'], args.database, 'advisor')
    counts, skipped, invalid = import_all(args.database, timings, Path(args.root).resolve(), args.only)
    print('imported', counts)
    for name, reason in skipped:
        print('SKIPPED', name, reason)
    for name, reason in invalid:
        print('INVALID', name, reason)
    if args.prices:
        for row in PRICES:
            add_price(dict(row, effective_from='2026-11-01', verification='AGGREGATOR', checked_at='2026-11-20'), args.database)
    if args.experiments:
        for experiment in EXPERIMENTS:
            add_experiment(experiment, args.database)
    for row in scorecard(args.database):
        print('SCORECARD', json.dumps(row, default=str))
    if args.experiments:
        for experiment in EXPERIMENTS:
            result = compare(experiment['id'], args.database)
            print('COMPARE', experiment['id'], json.dumps({key: value for key, value in result.items() if key != 'rows'}))


def main():
    # Sample: Run "export-timings --out t.json". Write t.json. Run "import --database durai_test --timings t.json". Print the scorecard.
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export-timings')
    export.add_argument('--out', required=True)
    export.add_argument('--root', default=str(ROOT))
    load = commands.add_parser('import')
    load.add_argument('--database', required=True)
    load.add_argument('--timings', required=True)
    load.add_argument('--root', default=str(ROOT))
    load.add_argument('--only', default='dev-loop-*.json')
    load.add_argument('--prices', action='store_true')
    load.add_argument('--experiments', action='store_true')
    args = parser.parse_args()
    export_timings(args.out, Path(args.root).resolve()) if args.command == 'export-timings' else import_main(args)


if __name__ == '__main__':
    main()
