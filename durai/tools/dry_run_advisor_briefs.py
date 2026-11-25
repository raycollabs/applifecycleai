"""Sample: Build PLAN and RETRY briefs for 22 transaction tasks from binding foundation-transaction-v14. Write one receipt with sizes and pattern IDs."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.advisor_brief import LIMIT, build_brief
from durai.advisor_select import framework_patterns, select_patterns
from durai.common import digest
from durai.qualification import TEMPLATES, fixture_config, fixture_files
from durai.workspace_manifest import visible

RETRY = [{'attemptId': 'dry-run', 'verdict': 'FAIL', 'feedback': 'A protected acceptance goal failed.', 'goals': []}]


def task_view(config):
    # Sample: Read the journal config. Return its write paths, edit scope, objective, goal, and read rules.
    return {'writePaths': config['writePaths'], 'editScope': config['editScope'], 'objective': config['objective'],
            'failingAcceptanceGoal': config['failingAcceptanceGoal'], 'readPaths': ['/workspace/**'], 'deniedPaths': ['/workspace/tests/**']}


def check(folder, framework):
    # Sample: Read task-tx-events-v14. Return PASS with publication first and both briefs under 4000 characters.
    config = fixture_config(folder)
    task = task_view(config)
    files = visible(task, fixture_files(folder))
    selection = select_patterns(task, files, framework)
    plan = build_brief(task, files, selection, 'PLAN')
    retry = build_brief(task, files, selection, 'RETRY', RETRY)
    checks = {'size': plan['characters'] <= LIMIT and retry['characters'] <= LIMIT,
              'sections': all(name in plan['text'] for name in ('## Problem', '## Required rules', '## Question')),
              'retryResult': '## Last result' in retry['text'] and '## Last result' not in plan['text'],
              'noProtectedPath': '/workspace/tests/' not in plan['text'] + retry['text'],
              'companions': all(item['relatedPolicyId'] in selection['patternIds'] for item in selection['patterns'] if item['strength'] != 'REQUIRED'),
              'interfaces': '## Interfaces' in plan['text'] or not any(item.get('interface') for item in selection['patterns'])}
    return {'template': folder.name, 'state': 'PASS' if all(checks.values()) else 'FAIL', 'checks': checks,
            'patternIds': selection['patternIds'], 'scores': {item['id']: item['score'] for item in selection['patterns']},
            'planCharacters': plan['characters'], 'retryCharacters': retry['characters'], 'planDigest': plan['digest']}


def main(argv=None):
    # Sample: Run the dry run for binding foundation-transaction-v14. Write receipts/advisor-briefs-dry-run-<id>.json. Return 0 when all pass.
    parser = argparse.ArgumentParser()
    parser.add_argument('--binding', default='foundation-transaction-v14')
    parser.add_argument('--database', default='durai_local')
    args = parser.parse_args(argv)
    framework = framework_patterns(args.binding, 'transaction', args.database)
    results = [check(folder, framework) for folder in sorted(TEMPLATES.glob('task-tx-*-v14*'))]
    for item in results:
        print(json.dumps({key: item[key] for key in ('template', 'state', 'patternIds', 'planCharacters')}), flush=True)
    body = {'format': 'durai-advisor-briefs-dry-run/v1', 'binding': args.binding, 'bindingDigest': framework['bindingDigest'],
            'framework': [item['id'] for item in framework['patterns']], 'modelCalls': 0, 'results': results,
            'state': 'PASS' if all(item['state'] == 'PASS' for item in results) else 'FAIL'}
    body['digest'] = digest(body)
    target = ROOT / 'receipts' / ('advisor-briefs-dry-run-' + body['digest'][:8] + '.json')
    target.write_text(json.dumps(body, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'receipt': target.relative_to(ROOT).as_posix(), 'state': body['state']}))
    return 0 if body['state'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
