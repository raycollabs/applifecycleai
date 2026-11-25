"""Sample: Run Task B with scripted models in durai_test. Write receipts/dev-loop-task-b.json with outcome PASSED."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from durai.db import prepare
from durai.harness import prepare_checkpoints
from durai.qualification import ROOT, run_fixture, scripted_steps


def scripted(attempt):
    # Sample: Return the scripted model for attempt 2 of Task B.
    from test_workspace_worker import SequenceModel
    return SequenceModel(steps=scripted_steps(attempt), replies=[])


def live(attempt):
    # Sample: Return the first configured coding route. Make no call before the loop reserves budget.
    from durai.coding_worker import configured_model
    return configured_model()


def main():
    # Sample: Parse --live with --max-model-calls 20 and --accept-spend. Refuse --live without both limits.
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', default='task-b-change-manifest', help='Task fixture folder under templates')
    parser.add_argument('--database', help='durai_local for live runs; durai_test for scripted runs')
    parser.add_argument('--live', action='store_true', help='Use the configured coding model route')
    parser.add_argument('--max-model-calls', type=int, help='Required with --live; 1 to 40')
    parser.add_argument('--accept-spend', action='store_true', help='Required with --live')
    args = parser.parse_args()
    if args.live and not (args.accept_spend and args.max_model_calls and 1 <= args.max_model_calls <= 40):
        raise SystemExit('Live mode needs --accept-spend and --max-model-calls from 1 to 40')
    args.database = args.database or ('durai_local' if args.live else 'durai_test')
    if not args.live and args.fixture != 'task-b-change-manifest':
        raise SystemExit('Scripted mode exists only for Task B; other fixtures run scripted checks in the test suite')
    if args.live and args.database == 'durai_test':
        raise SystemExit('Live runs keep evidence in durai_local; the test suite clears durai_test')
    prepare(args.database)
    prepare_checkpoints(args.database)
    budget = {'modelCalls': args.max_model_calls} if args.live else None
    result = run_fixture(args.fixture, live if args.live else scripted, Path(tempfile.mkdtemp(prefix='durai-' + args.fixture + '-')),
                         args.database, budget=budget)
    for item in [result] + result['attempts']:
        if item.get('package'):
            item['package'] = dict(item['package'], path=Path(item['package']['path']).name)
    receipt = {'date': '2026-11-16', 'mode': 'live' if args.live else 'scripted', 'database': args.database,
               'state': 'PASS' if result['outcome'] == 'PASSED' else 'FAIL',
               'scope': args.fixture + ' through the LangGraph loop, Deep Agents adapter, protected Docker evaluator, and review boundary. '
                        'A passing run does not authorize release.', **result}
    stem = 'task-b' if args.fixture == 'task-b-change-manifest' else args.fixture
    name = 'dev-loop-' + stem + ('-live.json' if args.live else '.json')
    (ROOT / 'receipts' / name).write_text(json.dumps(receipt, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'state': receipt['state'], 'outcome': result['outcome'],
                      'attempts': [(item['verdict'], item['state']) for item in result['attempts']]}))
    return 0 if receipt['state'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
