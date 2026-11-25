"""Sample: Copy fixture task-decision-edit-guard to task-decision-edit-guard-v10. Append its error contract. Bind pack v10."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.common import digest  # noqa: E402
from durai.error_contract import unnamed_errors  # noqa: E402
from durai.qualification import TEMPLATES, load  # noqa: E402
from durai.workspace_manifest import manifest  # noqa: E402

GRAPH = (' Error contract: project_binding raises psycopg InvalidParameterValue (SQLSTATE 22023) from the database for a binding'
         ' that is unknown or outside the login scope, has a changed pack pin, misses a rule row, references a foreign pack, or'
         ' contains SQL punctuation. application_policy_view raises KeyError for a binding outside the login scope, and Conflict'
         ' or KeyError for a changed pack pin, a missing rule row, or a foreign pack reference. A runtime that reads a foreign'
         ' graph table or runs cypher on a foreign graph gets psycopg InsufficientPrivilege (SQLSTATE 42501).')
CONTRACTS = {
    'task-7-1b-policy-store': (' Error contract: raise ValueError for a wrong pack digest, for policies that conflict between'
                               ' bound packs, and for a pack whose policies are not recorded. Raise KeyError when the reading'
                               ' application cannot see a binding, its policy rows, or a pack. The database refuses a policy row'
                               ' whose rule is not in its pack with psycopg CheckViolation (SQLSTATE 23514), and refuses any'
                               ' change to a recorded rule with a psycopg.Error.'),
    'task-7-1c-policy-graph': (' Error contract: raise ValueError for an application scope. A role other than the graph owner that'
                               ' reads Policy nodes gets a psycopg.Error from the database.'),
    'task-decision-edit-guard': (' Error contract: DecisionPoint raises ValueError for empty rules, a missing final default rule,'
                                 ' or a default rule before the end.'),
    'task-graph-tenancy': GRAPH,
    'task-graph-tenancy-migration-v9': GRAPH,
    'task-graph-tenancy-module-v9': GRAPH,
}
TARGETS = {'task-graph-tenancy-migration-v9': 'task-graph-tenancy-migration-v10',
           'task-graph-tenancy-module-v9': 'task-graph-tenancy-module-v10'}


def backfill():
    # Sample: Copy 6 source fixtures. Append 6 error contracts. Check 0 unnamed errors each. Return receipt rows with source and target digests.
    rows = []
    for source, sentence in CONTRACTS.items():
        target = TARGETS.get(source, source + '-v10')
        source_folder, target_folder = TEMPLATES / source, TEMPLATES / target
        if target_folder.exists():
            raise FileExistsError(target_folder)
        shutil.copytree(source_folder, target_folder, ignore=shutil.ignore_patterns('__pycache__'))
        config = json.loads((source_folder / 'task.json').read_text(encoding='utf-8'))
        config['objective'] = config['objective'].rstrip() + sentence
        config['binding'] = {'id': 'foundation-generic-v10', 'version': '10'}
        config['taskPrefix'] = config['taskPrefix'] + '-V10'
        (target_folder / 'task.json').write_text(json.dumps(config, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        suite = load(target_folder / 'protected')
        missing = unnamed_errors(config['objective'], suite)
        if missing:
            raise ValueError(target + ' still has unnamed errors: ' + ', '.join(missing))
        rows.append({'source': source, 'target': target, 'appendedContract': sentence.strip(),
                     'suiteDigest': manifest(suite)['digest'],
                     'workspaceDigest': manifest(load(target_folder / 'workspace'))['digest'],
                     'referenceDigest': manifest(load(target_folder / 'reference'))['digest'],
                     'sourceObjectiveDigest': digest(json.loads((source_folder / 'task.json').read_text(encoding='utf-8'))['objective']),
                     'targetObjectiveDigest': digest(config['objective']), 'binding': config['binding']})
    return rows


if __name__ == '__main__':
    receipt = {'date': '2026-11-18', 'plan': 'plan_durai_error_contract_readiness_2026-11-18.md', 'fixtures': backfill()}
    (ROOT / 'receipts/error-contract-fixture-backfill.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print([row['target'] for row in receipt['fixtures']])
