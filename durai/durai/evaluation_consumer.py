"""Trading consumer bridge. The full eval service remains owned by track X.

This deterministic local adapter runs protected fixture cases in the trusted
process. It is not a model judge, and it never produces a human approval.
"""
import json
from pathlib import Path
from psycopg.types.json import Jsonb
from .common import audit, digest, Conflict
from .db import transaction

ROOT = Path(__file__).resolve().parents[1]


class LocalProtectedEvaluator:
    def __init__(self, registry, runner):
        self.registry, self.runner = registry, runner
        self.suite_bytes = (ROOT / 'tests/protected/f101.json').read_bytes()
        self.suite = json.loads(self.suite_bytes)

    def evaluate(self, version_id):
        version = self.registry.get(version_id)
        results = []
        for case in self.suite['cases']:
            try:
                observed = self.runner.execute(version['source_bytes'], version['digest'], case['input'])['shares']
                results.append({'caseId': case['id'], 'expectedShares': case['expectedShares'],
                                'observedShares': observed,
                                'pass': type(observed) is int and observed == case['expectedShares']})
            except (ValueError, KeyError, TimeoutError) as error:
                results.append({'caseId': case['id'], 'expectedShares': case['expectedShares'],
                                'observedShares': None, 'pass': False,
                                'errorType': type(error).__name__})
        verdict = 'PASS' if all(row['pass'] for row in results) else 'FAIL'
        evidence = {'provider': 'local-protected-deterministic/v1', 'results': results,
                    'candidateEditable': False,
                    'modelGenerated': version['manifest'].get('generationMethod') == 'deepagents-model'}
        suite_digest = digest(self.suite_bytes)
        with transaction('durai_evaluator', self.registry.database) as db:
            before = db.execute('SELECT * FROM durai_governance.evaluation WHERE version_id=%s', (version_id,)).fetchone()
            if before:
                if (before['digest'], before['suite_digest'], before['verdict'], before['evidence']) != (version['digest'], suite_digest, verdict, evidence):
                    raise Conflict('Existing immutable evaluation differs')
            else:
                db.execute('INSERT INTO durai_governance.evaluation VALUES(durai_core.application_id(),%s,%s,%s,%s,%s)',
                           (version_id, version['digest'], suite_digest, verdict, Jsonb(evidence)))
                audit(db, 'EVALUATE', {'versionId': version_id, 'verdict': verdict, 'suiteDigest': suite_digest})
        return {'versionId': version_id, 'digest': version['digest'], 'suiteDigest': suite_digest,
                'verdict': verdict, 'evidence': evidence}
