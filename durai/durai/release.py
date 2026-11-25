"""Sample: Verify preconditions for release target R-1 and activate with digest-bound review."""
from pathlib import Path
from psycopg.types.json import Jsonb
from .common import Conflict, audit, digest, uid, event
from .db import transaction


def precondition(target):
    return digest({key: target[key] for key in ('application_id', 'id', 'kind', 'active_version', 'generation')})


class ReleaseController:
    def __init__(self, registry, runner):
        self.registry, self.runner, self.database = registry, runner, registry.database

    def initialize_target(self, target_id='trading-local', kind='LOCAL_DEMO'):
        with transaction('durai_release', self.database) as db:
            db.execute("INSERT INTO durai_governance.target(application_id,id,kind,active_version,state) VALUES(durai_core.application_id(),%s,%s,'broken-v1','ACTIVE') ON CONFLICT DO NOTHING", (target_id, kind))
            target = db.execute('SELECT * FROM durai_governance.target WHERE id=%s', (target_id,)).fetchone()
            if target['kind'] != kind:
                raise Conflict('Target kind is immutable')
            return target

    def approve(self, version_id, expected_digest, target_id, reviewer, authority='HUMAN'):
        version = self.registry.get(version_id)
        if expected_digest != version['digest']:
            raise Conflict('Reviewed digest differs from the registered version')
        with transaction('durai_reviewer', self.database) as db:
            evaluation = db.execute('SELECT * FROM durai_governance.evaluation WHERE version_id=%s', (version_id,)).fetchone()
            # The reviewer cannot mutate targets. Activation checks this snapshot
            # again under the controller's target lock before any state change.
            target = db.execute('SELECT * FROM durai_governance.target WHERE id=%s', (target_id,)).fetchone()
            if not target or target['state'] != 'ACTIVE':
                raise Conflict('Target is unavailable or changing')
            if not evaluation or evaluation['verdict'] != 'PASS' or evaluation['digest'] != expected_digest:
                raise Conflict('Protected evaluation must pass for this exact digest')
            suite = Path(__file__).resolve().parents[1] / 'tests/protected/f101.json'
            if evaluation['suite_digest'] != digest(suite.read_bytes()):
                raise Conflict('The protected suite changed; obtain a new evaluation')
            if authority == 'TEST_ONLY' and target['kind'] != 'TEST':
                raise Conflict('Test authority cannot approve a local demonstration target')
            if not reviewer.strip():
                raise ValueError('A reviewer identity is required')
            approval_id = uid()
            db.execute('INSERT INTO durai_governance.approval(application_id,id,version_id,digest,target,precondition_digest,policy_version,authority,reviewer) VALUES(durai_core.application_id(),%s,%s,%s,%s,%s,\'local-v1\',%s,%s)',
                       (approval_id, version_id, expected_digest, target_id, precondition(target), authority, reviewer))
            audit(db, 'APPROVE_RELEASE', {'approvalId': approval_id, 'digest': expected_digest, 'authority': authority, 'target': target_id})
            return approval_id

    def activate(self, approval_id, finding_id, run_id):
        # Model/worker code cannot obtain this connection through its environment.
        with transaction('durai_release', self.database) as db:
            approval = db.execute('SELECT * FROM durai_governance.approval WHERE id=%s', (approval_id,)).fetchone()
            if not approval:
                raise Conflict('Release approval is required')
            target = db.execute('SELECT * FROM durai_governance.target WHERE id=%s FOR UPDATE', (approval['target'],)).fetchone()
            previous = db.execute('SELECT * FROM durai_governance.activation WHERE id=%s', (approval_id,)).fetchone()
            if previous:
                if (previous['evidence'].get('findingId'), previous['evidence'].get('runId')) != (finding_id, run_id):
                    raise Conflict('Activation replay must retain its original finding and run')
                if previous['version_id'] != target['active_version'] or previous['generation'] != target['generation'] or previous['state'] == 'ROLLED_BACK':
                    raise Conflict('Activation is no longer current; obtain a new review')
                if previous['state'] == 'VERIFIED':
                    return previous
            else:
                if target['state'] != 'ACTIVE' or approval['precondition_digest'] != precondition(target):
                    raise Conflict('Target changed after approval')
                if target['kind'] != 'TEST' and approval['authority'] != 'HUMAN':
                    raise Conflict('Human approval is required')
                evaluation = db.execute('SELECT * FROM durai_governance.evaluation WHERE version_id=%s', (approval['version_id'],)).fetchone()
                if not evaluation or evaluation['verdict'] != 'PASS' or evaluation['digest'] != approval['digest']:
                    raise Conflict('Evaluated digest does not match approval')
                finding = db.execute('SELECT * FROM durai_improvement.finding WHERE id=%s AND run_id=%s', (finding_id, run_id)).fetchone()
                if not finding or finding['state'] != 'OPEN':
                    raise Conflict('Finding does not belong to this open run')
                row = db.execute("SELECT * FROM app_trading.trade WHERE id='T-101' FOR SHARE").fetchone()
                if digest(row) != finding['evidence']['snapshotDigest']:
                    raise Conflict('Application snapshot changed after discovery')
                target = db.execute("UPDATE durai_governance.target SET previous_version=active_version,active_version=%s,state='VERIFYING',generation=generation+1 WHERE id=%s RETURNING *", (approval['version_id'], target['id'])).fetchone()
                db.execute('INSERT INTO durai_agents.release(application_id,id,version_id,digest,suite_digest,approval_id) VALUES(durai_core.application_id(),%s,%s,%s,%s,%s)',
                           (approval_id, approval['version_id'], approval['digest'], evaluation['suite_digest'], approval_id))
                db.execute("INSERT INTO durai_governance.activation(application_id,id,target,version_id,approval_id,generation,state,evidence,release_id) VALUES(durai_core.application_id(),%s,%s,%s,%s,%s,'VERIFYING',%s,%s)",
                           (approval_id, target['id'], approval['version_id'], approval_id, target['generation'], Jsonb({'findingId': finding_id, 'runId': run_id}), approval_id))
                audit(db, 'ACTIVATE_PENDING_VERIFICATION', {'approvalId': approval_id, 'generation': target['generation']})
                event(db, run_id, 'ACTIVATION_STARTED', {'approvalId': approval_id, 'generation': target['generation']})
        # Do not hold a database transaction during package execution.
        version = self.registry.get(target['active_version'])
        if version['digest'] != approval['digest']:
            raise Conflict('Activated bytes differ')
        cases = [({'lots': 2, 'lot_size': 100}, 200),
                 ({'lots': 2, 'lot_size': 100, 'correction_lots': 1}, 100),
                 ({'lots': -2, 'lot_size': 100}, -200)]
        observations = [self.runner.execute(version['source_bytes'], version['digest'], value)['shares'] for value, _ in cases]
        if observations != [expected for _, expected in cases]:
            self.rollback(target['id'], reason='TARGET_VERIFICATION_FAILED')
            raise Conflict('Target verification failed; previous version restored')
        with transaction('durai_release', self.database) as db:
            current = db.execute('SELECT * FROM durai_governance.target WHERE id=%s FOR UPDATE', (target['id'],)).fetchone()
            if current['generation'] != target['generation'] or current['active_version'] != version['id']:
                raise Conflict('Target owner changed before verification commit')
            evidence = {'findingId': finding_id, 'runId': run_id, 'shares': observations[0],
                        'correctionShares': observations[1], 'shortShares': observations[2],
                        'digest': version['digest'], 'generation': target['generation']}
            db.execute("UPDATE durai_governance.activation SET state='VERIFIED',evidence=%s WHERE id=%s", (Jsonb(evidence), approval_id))
            db.execute("UPDATE durai_governance.target SET state='ACTIVE' WHERE id=%s", (target['id'],))
            db.execute("UPDATE app_trading.trade SET stored_shares=%s,source_version=%s WHERE id='T-101'", (observations[0], version['id']))
            db.execute("UPDATE durai_improvement.finding SET state='RESOLVED' WHERE id=%s", (finding_id,))
            db.execute("UPDATE durai_agents.run SET state='SUCCEEDED' WHERE id=%s", (run_id,))
            audit(db, 'VERIFY_TARGET', evidence)
            event(db, run_id, 'TARGET_VERIFIED', evidence)
            return evidence

    def rollback(self, target_id, reason='OPERATOR_REQUEST'):
        with transaction('durai_release', self.database) as db:
            target = db.execute('SELECT * FROM durai_governance.target WHERE id=%s', (target_id,)).fetchone()
            if not target or not target['previous_version']:
                raise Conflict('No previous release is available')
        version_id = target['previous_version']
        version = self.registry.get(version_id)
        shares = self.runner.execute(version['source_bytes'], version['digest'], {'lots': 2, 'lot_size': 100})['shares']
        with transaction('durai_release', self.database) as db:
            current = db.execute('SELECT * FROM durai_governance.target WHERE id=%s FOR UPDATE', (target_id,)).fetchone()
            if current['generation'] != target['generation'] or current['active_version'] != target['active_version']:
                raise Conflict('Target changed during rollback verification')
            db.execute("UPDATE durai_governance.target SET active_version=previous_version,previous_version=active_version,generation=generation+1,state='ACTIVE' WHERE id=%s", (target_id,))
            activations = db.execute("UPDATE durai_governance.activation SET state='ROLLED_BACK' WHERE target=%s AND generation=%s RETURNING evidence", (target_id, target['generation'])).fetchall()
            for activation in activations:
                finding_id = activation['evidence'].get('findingId')
                db.execute("UPDATE durai_improvement.finding SET state='OPEN' WHERE id=%s", (finding_id,))
                db.execute("UPDATE durai_agents.run SET state='WAITING_FOR_REVIEW' WHERE id=%s", (activation['evidence'].get('runId'),))
                event(db, activation['evidence']['runId'], 'RELEASE_ROLLED_BACK', {'target': target_id, 'restoredVersion': version_id})
            db.execute("UPDATE app_trading.trade SET stored_shares=%s,source_version=%s WHERE id='T-101'", (shares, version_id))
            audit(db, 'ROLLBACK', {'target': target_id, 'restoredVersion': version_id, 'reason': reason})
            return {'target': target_id, 'versionId': version_id, 'shares': shares}
