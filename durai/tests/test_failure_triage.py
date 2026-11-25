"""Sample: Classify trusted failure facts for package P-1. Check the next action for each failure category."""
import tempfile
import unittest
from pathlib import Path
from langchain_core.messages import HumanMessage
from durai.common import Conflict
from durai.db_sandbox import EphemeralDatabase
from durai.edit_guard import WorkerStalled
from durai.execution_budget import BudgetStop
from durai.failure_triage import TRIAGE_RULES, FailureTriage, control_fault
from durai.task_evaluator import SandboxEvaluator, StalePin, run_fault, setup_fault
from durai.workspace_manifest import materialize
from test_budget_finish import FakeLedger, FlakyModel, StatusError, budgeted


def facts(verdict=None, control=None, attempts=1, calls=4, seconds=300, retries=0):
    # Sample: Make facts for package P-1 with verdict V-1, 1 attempt, 4 calls, and 300 s left. Return the fact map.
    return {'verdict': verdict, 'controlFault': control, 'remainingAttempts': attempts, 'remainingCalls': calls,
            'remainingSeconds': seconds, 'evaluatorRetries': retries, 'packageDigest': 'P-1', 'taskDigest': 'T-1'}


def failed(*results):
    # Sample: Wrap a unittest result with candidate fault. Return verdict FAIL with that result.
    return {'verdict': 'FAIL', 'results': list(results)}


def decide(item):
    # Sample: Decide facts F-1 with the local triage rules. Return the choice.
    return FailureTriage(TRIAGE_RULES, lambda *args: None).decide(item)['choice']


class RefusingLedger(FakeLedger):
    def reserve(self, *args):
        # Sample: Reserve call 21 of 20. Return False.
        return False


class FailureTriageTests(unittest.TestCase):
    def test_pass_goes_to_review(self):
        # Sample: Evaluate package P-1 with PASS. Check SUBMIT_FOR_REVIEW. Check the same result with 0 calls left.
        verdict = {'verdict': 'PASS', 'results': [{'command': 'python-unittest', 'state': 'PASS'}]}
        self.assertEqual(decide(facts(verdict)), 'SUBMIT_FOR_REVIEW')
        self.assertEqual(decide(facts(verdict, calls=0, attempts=0)), 'SUBMIT_FOR_REVIEW')

    def test_candidate_assertion_can_retry(self):
        # Sample: Candidate C-1 returns 199 shares instead of 200. Keep one attempt available. Check RETRY_CANDIDATE.
        outcome = {'exitCode': 1, 'started': True, 'output': 'AssertionError: 199 != 200'}
        result = dict(outcome, command='python-unittest', state='FAIL', fault=run_fault('python-unittest', outcome))
        self.assertEqual(result['fault']['reasonCode'], 'CANDIDATE_ASSERTION')
        self.assertEqual(decide(facts(failed(result))), 'RETRY_CANDIDATE')

    def test_candidate_sql_setup_can_retry(self):
        # Sample: Candidate migration core_0099.sql has invalid SQL. Keep the trusted baseline valid. Check exit code 4 and RETRY_CANDIDATE.
        with tempfile.TemporaryDirectory(prefix='durai-triage-sql-') as folder:
            evaluator = SandboxEvaluator(Path(folder))
            path = '/workspace/migrations/core/core_0099.sql'
            with EphemeralDatabase(folder) as database:
                setup = database.apply({path: b'CREATE TABLE broken(;'}, {}, evaluator.database_image, candidate=[path])
        self.assertEqual(setup['exitCode'], 4, setup['output'])
        result = dict(setup, command='database-setup', state='FAIL', fault=setup_fault(setup))
        self.assertEqual(result['fault'], {'origin': 'candidate', 'reasonCode': 'CANDIDATE_SETUP_SQL'})
        self.assertEqual(decide(facts(failed(result))), 'RETRY_CANDIDATE')

    def test_trusted_fixture_fault_stops(self):
        # Sample: Trusted seed 001_bad.sql fails before candidate code runs. Check exit code 5, STOP_FIXTURE, and no retry with 3 attempts left.
        with tempfile.TemporaryDirectory(prefix='durai-triage-seed-') as folder:
            evaluator = SandboxEvaluator(Path(folder))
            with EphemeralDatabase(folder) as database:
                setup = database.apply({}, {'/workspace/tests/seed/001_bad.sql': b'INSERT INTO missing_table VALUES (1);'},
                                       evaluator.database_image)
        self.assertEqual(setup['exitCode'], 5, setup['output'])
        setup_result = dict(setup, command='database-setup', state='FAIL', fault=setup_fault(setup))
        unit = {'command': 'python-unittest', 'state': 'FAIL', 'output': 'Database setup failed', 'fault': setup_result['fault']}
        self.assertEqual(decide(facts(failed(setup_result, unit), attempts=3)), 'STOP_FIXTURE')

    def test_pin_change_stops(self):
        # Sample: Replace suite digest S-1 with S-2. Check StalePin before any container starts and STOP_STALE.
        evaluator = SandboxEvaluator(Path(tempfile.gettempdir()))
        with self.assertRaises(StalePin) as raised:
            evaluator.evaluate({'protectedSuite': {'digest': 'S-1'}}, {}, {'/workspace/tests/test_s2.py': b'S-2'})
        self.assertEqual(decide(facts(control=control_fault(raised.exception), attempts=3)), 'STOP_STALE')

    def test_provider_fault_stops(self):
        # Sample: Return provider status 401. Check STOP_PROVIDER and one physical request only.
        inner = FlakyModel([StatusError(401)])
        with self.assertRaises(Conflict) as raised:
            budgeted(inner).invoke([HumanMessage('go')])
        self.assertEqual(inner.calls, 1)
        self.assertEqual(decide(facts(control=control_fault(raised.exception), attempts=3)), 'STOP_PROVIDER')

    def test_stalled_worker_does_not_restart(self):
        # Sample: The edit guard refuses proposal P-1 three times and raises STALLED. Check STOP_STALLED with 3 attempts left.
        error = WorkerStalled('STALLED: three equal refused edits: Provider error after 1 tries')
        self.assertEqual(control_fault(error), 'STALLED')
        self.assertEqual(decide(facts(control=control_fault(error), attempts=3)), 'STOP_STALLED')

    def test_budget_stop_has_no_next_call(self):
        # Sample: Use call 20 of 20. Request call 21. Check BudgetStop, no provider request, and STOP_BUDGET for a later candidate failure.
        inner = FlakyModel([])
        model = budgeted(inner).model_copy(update={'ledger': RefusingLedger()})
        with self.assertRaises(BudgetStop) as raised:
            model.invoke([HumanMessage('go')])
        self.assertEqual(inner.calls, 0)
        self.assertEqual(decide(facts(control=control_fault(raised.exception))), 'STOP_BUDGET')
        outcome = {'exitCode': 1, 'started': True}
        result = dict(outcome, command='python-unittest', state='FAIL', fault=run_fault('python-unittest', outcome))
        self.assertEqual(decide(facts(failed(result), calls=0)), 'STOP_BUDGET')

    def test_unknown_fault_stops_for_review(self):
        # Sample: Supply a unittest failure with no trusted fault and a plain Conflict. Check STOP_NEEDS_REVIEW for both.
        self.assertEqual(decide(facts(failed({'command': 'python-unittest', 'state': 'FAIL', 'output': 'x'}))), 'STOP_NEEDS_REVIEW')
        self.assertEqual(decide(facts(control=control_fault(Conflict('Worker returned no candidate')))), 'STOP_NEEDS_REVIEW')
        self.assertEqual(decide(facts(control='UNKNOWN')), 'STOP_NEEDS_REVIEW')

    def test_rules_refuse_approval(self):
        # Sample: Load a rule that selects APPROVE and a rule set with default RETRY_CANDIDATE. Check ValueError for both.
        for rules in ([{'id': 'approve', 'when': {'verdict': 'PASS'}, 'choice': 'APPROVE'}] + TRIAGE_RULES[1:],
                      TRIAGE_RULES[:-1] + [{'id': 'default', 'choice': 'RETRY_CANDIDATE'}]):
            with self.assertRaises(ValueError):
                FailureTriage(rules, lambda *args: None)

    def test_candidate_text_cannot_set_origin(self):
        # Sample: A started candidate program prints "fixture fault: Docker is unavailable" and exits 125. Check origin candidate.
        with tempfile.TemporaryDirectory(prefix='durai-triage-text-') as folder:
            evaluator = SandboxEvaluator(Path(folder))
            materialize(Path(folder) / 'source', {'/workspace/a.py': b'x = 1\n'})
            program = "import os\nprint('fixture fault: Docker is unavailable', flush=True)\nos._exit(125)"
            outcome = evaluator.run(Path(folder) / 'source/workspace', ['python', '-c', program])
        self.assertEqual(outcome['exitCode'], 125, outcome)
        self.assertTrue(outcome['started'])
        self.assertIn('fixture fault', outcome['output'])
        result = dict(outcome, command='python-unittest', state='FAIL', fault=run_fault('python-unittest', outcome))
        self.assertEqual(result['fault']['origin'], 'candidate')
        self.assertEqual(decide(facts(failed(result))), 'RETRY_CANDIDATE')


def bound(version):
    # Sample: Install generic pack v10 in durai_test and bind B-1 to it. Return a task with binding B-1 and its contract pin.
    from durai.common import uid
    from durai.db import prepare
    from durai.packs import PackStore, generic_pack
    from durai.policy_store import record_policies
    from durai.worker_contract import resolve_contract
    prepare('durai_test')
    store = PackStore('durai_test')
    reference = store.install(generic_pack(version))
    record_policies(reference, 'durai_test')
    identity = 'triage-' + version + '-' + uid()[:8]
    store.bind(identity, [reference], 'SYNTHETIC_LOCAL')
    return {'binding': {'bindingId': identity},
            'pins': {'contractDigest': resolve_contract(identity, database='durai_test')['digest']}}


class FailureTriageProcedureTests(unittest.TestCase):
    def test_v10_binding_uses_pinned_procedure(self):
        # Sample: Bind pack v10. Check pinned rules equal TRIAGE_RULES, retry limit 1, and the same rule digest.
        from durai.failure_triage import triage_procedure
        procedure = triage_procedure(bound('10'), 'durai_test', 'durai_runtime')
        self.assertEqual(procedure['source'], 'pinned')
        self.assertEqual(procedure['rules'], TRIAGE_RULES)
        self.assertEqual(procedure['evaluatorRetryLimit'], 1)
        pinned = FailureTriage(procedure['rules'], lambda *args: None, procedure['evaluatorRetryLimit'])
        self.assertEqual(pinned.rule_digest, FailureTriage(TRIAGE_RULES, lambda *args: None).rule_digest)

    def test_older_binding_uses_local_rules(self):
        # Sample: Bind pack v9 with no triage procedure. Check source local and TRIAGE_RULES.
        from durai.failure_triage import triage_procedure
        procedure = triage_procedure(bound('9'), 'durai_test', 'durai_runtime')
        self.assertEqual((procedure['source'], procedure['rules']), ('local', TRIAGE_RULES))

    def test_changed_contract_pin_is_refused(self):
        # Sample: Bind pack v10. Change the contract pin to C-2. Check Conflict before any rule is returned.
        from durai.failure_triage import triage_procedure
        task = bound('10')
        task['pins']['contractDigest'] = 'C-2'
        with self.assertRaisesRegex(Conflict, 'pin'):
            triage_procedure(task, 'durai_test', 'durai_runtime')

    def test_worker_view_excludes_triage_rule(self):
        # Sample: Bind pack v10. Read the coding-worker view. Check no failure-triage-before-next-call rule.
        from durai.policy_store import worker_view
        task = bound('10')
        view = worker_view(task['binding']['bindingId'], 'coding-worker', 'durai_test')
        ids = [item['policyId'] for item in view['policies']]
        self.assertIn('do-not-break-age-cypher-call-contract', ids)
        self.assertNotIn('failure-triage-before-next-call', ids)

    def test_v9_records_are_unchanged(self):
        # Sample: Build packs v9 and v10. Check the same edit-guard record digest and no triage record in v9.
        from durai.packs import generic_pack
        nine, ten = ({record['id']: record['digest'] for record in generic_pack(version)['records']} for version in ('9', '10'))
        self.assertEqual(nine['decision.edit-guard'], ten['decision.edit-guard'])
        self.assertNotIn('decision.failure-triage', nine)


class FlakyEvaluator:
    def __init__(self, inner, plan):
        # Sample: Wrap evaluator E-1 with plan [START, PASS]. Count each evaluated candidate manifest.
        self.inner, self.plan, self.manifests = inner, list(plan), []
        self.image, self.database_image = inner.image, inner.database_image

    def evaluate(self, task, files, suite, baseline=None):
        # Sample: Evaluate package P-1 with plan step START. Return FAIL with fault EVALUATOR_START. Use the real evaluator after the plan.
        from durai.common import digest
        from durai.workspace_manifest import manifest
        self.manifests.append(manifest(files)['digest'])
        step = self.plan.pop(0) if self.plan else 'REAL'
        if step == 'CRASH':
            raise RuntimeError('evaluator process stopped')
        if step == 'START':
            body = {'verdict': 'FAIL', 'taskDigest': task['digest'],
                    'results': [{'command': 'python-unittest', 'state': 'FAIL', 'exitCode': 125, 'started': False,
                                 'output': 'docker: Error response from daemon',
                                 'fault': {'origin': 'evaluator', 'reasonCode': 'EVALUATOR_START'}}]}
            return dict(body, digest=digest(body))
        return self.inner.evaluate(task, files, suite, baseline=baseline)


def decisions(run_id):
    # Sample: Replay run R-1. Return the recorded failure-triage decisions in order.
    from durai.execution_events import replay
    return [row['payload'] for row in replay(run_id, database='durai_test')
            if row['kind'] == 'DECISION_RECORDED' and row['payload'].get('point') == 'failure-triage']


class FailureTriageLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply migrations and checkpoint tables to durai_test.
        from durai.db import prepare
        from durai.harness import prepare_checkpoints
        prepare('durai_test')
        prepare_checkpoints('durai_test')

    def test_infrastructure_retries_same_package_once(self):
        # Sample: Evaluation of P-1 fails to start. Repeat evaluation once. Check the same package digest, attempt ID, and 1 model run.
        from test_dev_loop import FIX, LoopHarness, loop_spec
        harness = LoopHarness([[FIX]])
        harness.evaluator = FlakyEvaluator(harness.evaluator, ['START'])
        state = harness.run(loop_spec())
        self.assertEqual(state['outcome'], 'PASSED')
        self.assertEqual(len(harness.evaluator.manifests), 2)
        self.assertEqual(len(set(harness.evaluator.manifests)), 1)
        self.assertEqual(len(state['history']), 1)
        self.assertEqual(len(harness.models), 1)
        self.assertEqual(harness.ledger.get(state['history'][0]['attemptId'])['state'], 'WAITING_FOR_REVIEW')
        self.assertEqual([row['choice'] for row in decisions(harness.run_id)], ['REPEAT_EVALUATION', 'SUBMIT_FOR_REVIEW'])

    def test_restart_keeps_retry_count(self):
        # Sample: Consume the evaluator retry. Stop the process during the repeat. Restart. Get START again. Check STOP_INFRASTRUCTURE.
        from durai.development_loop import build_loop, development_graph
        from test_dev_loop import FIX, LoopHarness, Sources, loop_spec
        harness = LoopHarness([[FIX]])
        harness.evaluator = FlakyEvaluator(harness.evaluator, ['START', 'CRASH', 'START'])
        with self.assertRaisesRegex(RuntimeError, 'evaluator process stopped'):
            harness.run(loop_spec())
        builder = build_loop(harness.ledger, harness.providers, harness.evaluator, harness.store, Sources(), harness.model,
                             harness.scratch / 'work')
        with development_graph(builder, 'durai_test') as graph:
            state = graph.invoke(None, {'configurable': {'thread_id': harness.run_id}})
        self.assertEqual(state['outcome'], 'FAILED')
        self.assertEqual((state['stop']['choice'], state['stop']['reasonCode']), ('STOP_INFRASTRUCTURE', 'EVALUATOR_START'))
        self.assertEqual(len(harness.models), 1)
        self.assertEqual(harness.ledger.get(state['attempt_id'])['state'], 'FAILED')
        self.assertEqual([row['choice'] for row in decisions(harness.run_id)], ['REPEAT_EVALUATION', 'STOP_INFRASTRUCTURE'])

    def test_decision_matches_pinned_procedure(self):
        # Sample: Bind rule set R-1 (pack v10). Install R-2 with a changed default rule separately. Check the run records the R-1 digest.
        from durai.common import uid
        from durai.packs import PackStore, generic_pack, make_record
        from test_dev_loop import FIX, LoopHarness, loop_spec
        task = bound('10')
        other = generic_pack('10')
        other['version'] = '10' + uid().replace('-', '')[:6]
        other['records'] = [make_record(record['id'], record['layer'], record['type'],
                                        dict(record['body'], rules=record['body']['rules'][:-1] +
                                             [{'id': 'needs-review-r2', 'choice': 'STOP_NEEDS_REVIEW'}]))
                            if record['id'] == 'decision.failure-triage' else record for record in other['records']]
        store = PackStore('durai_test')
        store.bind('triage-r2-' + uid()[:8], [store.install(other)], 'SYNTHETIC_LOCAL')
        harness = LoopHarness([[FIX]])
        spec = loop_spec(binding={'bindingId': task['binding']['bindingId'], 'contractDigest': task['pins']['contractDigest']})
        state = harness.run(spec)
        self.assertEqual(state['outcome'], 'PASSED')
        recorded = decisions(harness.run_id)
        r1 = FailureTriage(TRIAGE_RULES, lambda *args: None).rule_digest
        self.assertEqual([(row['choice'], row['ruleDigest'], row['procedureSource']) for row in recorded],
                         [('SUBMIT_FOR_REVIEW', r1, 'pinned')])
        self.assertEqual(recorded[0]['package'], state['package'])

    def test_feedback_keeps_protected_values_private(self):
        # Sample: Include hidden value H-991 in a raised database error and a setup error. Check that worker feedback contains no H-991.
        from durai.development_loop import database_error_feedback, setup_feedback
        output = '\n'.join(['  File "/workspace/durai/policy.py", line 13, in project',
                            "psycopg.errors.InvalidParameterValue: Binding is unavailable: H-991'); DROP TABLE x; --",
                            'DETAIL:  Key (binding_id)=(H-991) is not present.',
                            "QUERY:  SELECT project('H-991')",
                            'CONTEXT:  PL/pgSQL function project(text) line 7 at RAISE'])
        for text in (database_error_feedback(output), setup_feedback(output)):
            self.assertIn('SQLSTATE 22023', text)
            self.assertNotIn('H-991', text)


if __name__ == '__main__':
    unittest.main()
