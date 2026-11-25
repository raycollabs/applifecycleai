"""Sample: Audit the 22 transaction prompts and write a receipt with no model calls."""
import argparse
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.blueprint_patterns import transaction_pack
from durai.common import Conflict, digest
from durai.context_pack import build_pack
from durai.context_tools import TOOL_NAMES
from durai.ontology_patterns import patterns
from durai.packs import generic_pack, validate_pack
from durai import prompt_quality
from durai.prompt_quality import BINDING, PROFILE, audit_prompt, blueprint_guidance, classify_receipt
from durai.qualification import TEMPLATES, fixture_files, fixture_spec, fixture_suite
from durai.runtime_deepagents import worker_prompt
from durai.task_contract import build_task
from durai.workspace_manifest import visible


def matching_revision():
    # Sample: Build the pack in memory. Return 3 when its version and digest equal the revision 3 pin. Raise Conflict when no pin matches.
    pack = transaction_pack(generic_pack('14'))
    found = [revision for revision, pin in prompt_quality.PINS.items()
             if (pin['packVersion'], pin['packDigest']) == (pack['version'], validate_pack(pack))]
    if not found:
        raise Conflict('No prompt quality pin matches the built pack ' + pack['version'])
    return found[0]


def candidate_framework():
    # Sample: Build the pending pack in memory. Return its coding-worker patterns and its digest. Select the pin revision that equals the built pack.
    pack = transaction_pack(generic_pack('14'))
    pack_digest = validate_pack(pack)
    revision = matching_revision()
    return {'bindingId': BINDING, 'bindingRevision': revision,
            'bindingDigest': digest({'candidate': pack_digest, 'revision': revision}),
            'profile': PROFILE, 'packReferences': [{'id': pack['id'], 'version': pack['version'], 'digest': pack_digest}],
            'patterns': [item for item in patterns(pack)
                         if 'coding-worker' in item['audience'] and
                         (PROFILE in item['applicability']['profiles'] or '*' in item['applicability']['profiles'])]}


def render_fixture(folder, framework):
    # Sample: Render the seats task with the pending pack. Return its task, the exact worker prompt, its context pack, and its protected suite.
    with patch('durai.qualification.contract_digest', return_value='a' * 64):
        spec = fixture_spec(folder.name, 'durai_test', 'synthetic', 'synthetic')
    spec['taskId'] = folder.name
    task = build_task(spec)['task']
    files = visible(task, fixture_files(folder))
    guidance = blueprint_guidance(task, files, framework)
    pack = build_pack(task, files, goals=[task['readiness']['failingAcceptanceGoal']], guidance=guidance)
    context = dict(pack, tools=list(TOOL_NAMES))
    return task, worker_prompt(task, files, context=context), pack, fixture_suite(folder)


def audit_fixture(folder, framework):
    # Sample: Render the seats task with the pending pack. Check the required facts using the exact worker renderer.
    task, text, pack, suite = render_fixture(folder, framework)
    audit = audit_prompt(task, text, suite)
    return {'fixture': folder.name, 'packDigest': pack['digest'], 'packCharacters': pack['characters'], **audit}


def main():
    # Sample: Build the pack from the catalog. Check all 22 prompts against the matching pin and classify one retained failure. Save the fact and failure IDs.
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipt', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    expected = sorted(TEMPLATES.glob('task-tx-*-v14*'))
    with patch('durai.prompt_quality.ACTIVE_REVISION', matching_revision()):
        framework = candidate_framework()
        rows = [audit_fixture(folder, framework) for folder in expected]
    failures = [classify_receipt(json.loads(path.read_text(encoding='utf-8-sig'))) for path in args.receipt]
    passed = len(rows) == 22 and all(row['state'] == 'PASS' and row['packCharacters'] <= 60000 for row in rows)
    ref = framework['packReferences'][0]
    result = {'state': 'PASS' if passed else 'FAIL', 'packVersion': ref['version'],
              'candidateDigest': ref['digest'], 'modelCalls': 0, 'audits': rows, 'failures': failures}
    result['digest'] = digest(result)
    with args.output.open('x', encoding='utf-8', newline='\n') as output:
        output.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'state': result['state'], 'taskCount': len(rows),
                      'failed': [{'fixture': row['fixture'], 'missing': row['missing'],
                                  'protectedLineMatches': row['protectedLineMatches']}
                                 for row in rows if row['state'] != 'PASS']}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
