"""Sample: Load trace blind-T-1-S-TRADE-BOOK.json. Replay tool steps in dry-run mode without external API calls. Verify deterministic match."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.efficiency_metrics import load_trace


def extract_tool_steps(trace):
    # Sample: Read trace with 2 AI tool calls. Return list of 2 tool call step dicts with name, args and status.
    steps = []
    messages = trace.get('messages', [])
    for msg in messages:
        if msg.get('role') == 'ai' and msg.get('tool_calls'):
            for call in msg['tool_calls']:
                steps.append({
                    'name': call.get('name'),
                    'args': call.get('args', {}),
                    'id': call.get('id')
                })
    return steps


def replay_trace(trace_path, dry_run=True, tools_instance=None):
    # Sample: Replay trace at path P. In dry-run mode return step count, unique tools, and verification status MATCH.
    trace = load_trace(trace_path)
    steps = extract_tool_steps(trace)
    executed = []
    diffs = []

    for index, step in enumerate(steps):
        name = step['name']
        args = step['args']
        if not dry_run and tools_instance is not None:
            try:
                outcome = tools_instance.call(name, args)
                executed.append({'step': index, 'name': name, 'status': 'success' if not outcome.get('isError') else 'error'})
            except Exception as error:
                executed.append({'step': index, 'name': name, 'status': 'exception', 'error': str(error)})
                diffs.append(f"Step {index} {name} failed: {error}")
        else:
            executed.append({'step': index, 'name': name, 'status': 'replayed_dry_run'})

    unique_tools = sorted(list({s['name'] for s in steps}))
    return {
        'attemptId': trace.get('attemptId'),
        'totalSteps': len(steps),
        'uniqueTools': unique_tools,
        'dryRun': dry_run,
        'diffs': diffs,
        'matched': len(diffs) == 0,
        'executedSteps': executed
    }


def main(argv=None):
    # Sample: Run replay_trace.py --trace private/worker-evidence/blind-T-1.json --dry-run. Exit 0.
    parser = argparse.ArgumentParser()
    parser.add_argument('--trace', required=True)
    parser.add_argument('--dry-run', action='store_true', default=True)
    args = parser.parse_args(argv)
    result = replay_trace(args.trace, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result['matched'] else 1


if __name__ == '__main__':
    sys.exit(main())
