"""Verify retained local evidence and print a concise five-phase handoff."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.common import canonical, digest
from durai.db import transaction
from durai.engineering import Engineering, check_walkthroughs
from durai.files import import_package
from durai.generation import verify_bundle
from durai.local_factory import preserve_target
from durai.packs import PackStore


def load(name):
    # Sample: Load tests.json. Read the recorded test verdict without exposing settings.
    return json.loads((ROOT / 'receipts' / name).read_text(encoding='utf-8'))


def main():
    # Sample: Recheck all five phases, retained model evidence, UI proof, and unchanged target.
    proof, model, tests, browser = [load(name) for name in
                                  ('five-phase.json', 'greenfield.json', 'tests.json', 'generated-ui.json')]
    assert proof['state'] == tests['state'] == browser['state'] == 'PASS'
    assert digest(preserve_target()) == proof['originalStateDigest']
    store = PackStore()
    imported = proof['ontology']['tradingImport']
    document = store.get(imported['reference'])
    graph = store.project(imported['reference'])
    assert graph['fullIdentityAndDigestComparison']
    for result in [proof['genericOnlyGreenfield'], proof['applicationGreenfield'], model]:
        folder = Path(result['outputDirectory'])
        manifest = verify_bundle(folder)
        files, _ = import_package(folder.parent / (result['runId'] + '.sqlite'), result['packageDigest'])
        assert manifest['files'] == {name: digest(data) for name, data in files.items()}
        assert Engineering().get(result['runId'])['state'] == 'WAITING_FOR_REVIEW'
    assert model['generationMethod'] == 'deepagents-openrouter' and model['evaluation']['verdict'] == 'PASS'
    assert Engineering().get(proof['replica']['runId'])['state'] == 'WAITING_FOR_REVIEW'
    verify_bundle(proof['replica']['outputDirectory'])
    source_files = ['durai/packs.py', 'durai/engineering.py', 'durai/archives.py',
                    'durai/trading_import.py', 'durai/generation.py', 'durai/replica.py',
                    'durai/local_factory.py', 'durai/protected/position_suite.py',
                    'durai/model_routes.py', 'durai/model_fallbacks.py', 'durai/models.py', 'durai/coding_worker.py',
                    'tests/test_ontology.py', 'tests/test_generation.py',
                    'tests/test_model_fallbacks.py',
                    'tests/test_publication_scan.py',
                    'templates/position-app/program.py', 'templates/position-app/server.py',
                    'templates/position-app/test_business.py', 'tools/qualify_generated_ui.py',
                    'tools/five_phase_report.py']
    coverage = {name: check_walkthroughs((ROOT / name).read_bytes())['functions'] for name in source_files}
    with transaction() as db:
        failures = db.execute("SELECT r.id,e.document->>'errorType' AS error_type FROM durai_engineering.run r JOIN durai_engineering.evidence e ON e.run_id=r.id AND e.application_id=r.application_id WHERE e.stage='FAILED' ORDER BY r.created_at DESC LIMIT 5").fetchall()
    result = {'state': 'PASS', 'phases': {str(n): 'LOCAL_PROOF_VERIFIED' for n in range(1, 6)},
              'scope': 'Synthetic position application and single-process historical producer replica',
              'testsPassed': tests['testsRun'], 'genericRecords': proof['ontology']['genericProjection']['records'],
              'applicationRecords': len(document['records']), 'applicationGraphEdges': graph['edges'],
              'sourceRowsPreserved': sum(imported['provenance']['reconciliation']['sourceCounts'].values()),
              'sourceCatalog': {'artifacts': 18, 'rules': 30, 'solutions': 3},
              'checkedPythonFunctionWalkthroughs': sum(coverage.values()), 'walkthroughCoverage': coverage,
              'modelRun': {'id': model['runId'], 'model': model.get('model'), 'candidateDigest': model['candidateDigest'],
                           'checksPassed': len(model['evaluation']['checks']), 'modelAttempts': model.get('modelAttempts', []),
                           'fallbackUsed': model.get('fallbackUsed', False),
                           'scope': 'Retained independently evaluated model candidate; failed attempts are separate'},
              'recentFailedRuns': failures, 'browser': browser['checks'], 'existingApprovedTargetUnchanged': True,
              'newHumanApprovals': 0, 'allNewCandidatesAwaitReview': True,
              'limits': ['Qualified position-domain adapter only', 'No general planner/critic model qualification',
                         'No new workflow release UI', 'No multi-service production clone or billion-token benchmark',
                         'Existing image and browser dependencies still required']}
    (ROOT / 'receipts/five-phase-summary.json').write_bytes(canonical(result))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
