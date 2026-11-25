"""Sample: Run Task B with a 4-call cap. Edit in call 2. Read until the cap. Check one notice, one evaluated candidate, and one edit row."""
from pathlib import Path
import tempfile
import unittest
from langchain_core.messages import AIMessage, HumanMessage
from durai.db import prepare
from durai.common import Conflict
from durai.execution_budget import BudgetedModel
from durai.execution_events import replay
from durai.harness import prepare_checkpoints
from durai.packs import generic_pack
from durai.qualification import CORRECT_BODY, MODULE, BASELINE_BODY, run_task_b
from durai.runtime_deepagents import instructions
from durai.tool_ledger import operations
from test_workspace_worker import SequenceModel, task

DATABASE = 'durai_test'
READ = ('read_file', {'file_path': MODULE})
NOTICES = []


class NoticeModel(SequenceModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Receive call 3 with notice "2 model calls remain". Record it. Send scripted step 3.
        last = messages[-1]
        if last.type == 'human' and 'model calls remain' in str(last.content):
            NOTICES.append(str(last.content))
        return super()._generate(messages, stop, run_manager, **kwargs)


def run(steps, calls):
    # Sample: Run Task B with steps S-1, a cap of 4 model calls, and one attempt. Return the loop result.
    return run_task_b(lambda attempt: NoticeModel(steps=steps, replies=[]),
                      Path(tempfile.mkdtemp(prefix='durai-budget-finish-')), DATABASE,
                      budget={'modelCalls': calls, 'attempts': 1})


class FakeLedger:
    def renew(self, *args):
        # Sample: Renew lease W-1. Return nothing.
        return None

    def reserve(self, *args):
        # Sample: Reserve 1 model call. Return True.
        return True

    def reconcile(self, *args):
        # Sample: Reconcile reservation R-1 with 1 call. Return nothing.
        return None

    def usage(self, budget_id):
        # Sample: Read budget B-1 with no limit set. Return an empty usage map.
        return {}


class FlakyModel:
    def __init__(self, errors):
        # Sample: Hold two connection resets. Count calls from 0.
        self.errors, self.calls = list(errors), 0

    def invoke(self, messages, stop=None, **kwargs):
        # Sample: Raise the first held error on call 1. Return reply "done" when no error is left.
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return AIMessage(content='done')


class StatusError(Exception):
    def __init__(self, status_code):
        # Sample: Make an error with status 503. Keep the status on the error.
        super().__init__('status ' + str(status_code))
        self.status_code = status_code


def budgeted(inner):
    # Sample: Wrap model M-1 with a fake ledger and no retry wait. Return the budgeted model.
    return BudgetedModel(inner=inner, ledger=FakeLedger(), budget_id='B-1', attempt_id='A-1', owner='W-1',
                         generation=1, retry_delays=(0, 0, 0))


class BudgetFinishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply migrations and checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_worker_prompt_has_finish_rule(self):
        # Sample: Build the worker prompt with pack version 5. Check rule do-not-explore-after-deliverables and "end the turn".
        text = instructions(task(), {'/workspace/a.py': b''}, generic_pack('5')['records'][0]['body']['policies'])
        self.assertIn('[do-not-explore-after-deliverables]', text)
        self.assertIn('then end the turn', text)

    def test_setup_failure_is_fed_back(self):
        # Sample: Read InvalidSchemaName for graph durai_app_1. Check SQLSTATE 3F000 and no graph name in feedback.
        from durai.development_loop import setup_feedback
        output = ('Traceback (most recent call last):\n  File "/runner/durai/db.py", line 88, in prepare\n'
                  'psycopg.errors.InvalidSchemaName: graph "durai_app_1" does not exist.\n'
                  "CONTEXT:  SQL statement \"SELECT ag_catalog.create_vlabel('durai_app_1','Policy')\"\n")
        text = setup_feedback(output)
        self.assertTrue(text.startswith('Database setup failed: '))
        self.assertIn('InvalidSchemaName', text)
        self.assertNotIn('durai_app_1', text)
        self.assertIn('3F000', text)

    def test_worker_prompt_has_age_graph_rule(self):
        # Sample: Build the worker prompt with pack version 6. Check rule do-not-create-age-graph-as-schema and create_graph.
        text = instructions(task(), {'/workspace/a.py': b''}, generic_pack('6')['records'][0]['body']['policies'])
        self.assertIn('[do-not-create-age-graph-as-schema]', text)
        self.assertIn('ag_catalog.create_graph(name)', text)

    def test_transient_error_is_retried(self):
        # Sample: Get two connection resets, then reply "done". Check reply "done" after 3 provider calls.
        inner = FlakyModel([ConnectionResetError('reset'), ConnectionResetError('reset')])
        self.assertEqual(budgeted(inner).invoke([HumanMessage('go')]).content, 'done')
        self.assertEqual(inner.calls, 3)

    def test_fourth_transient_failure_fails_attempt(self):
        # Sample: Get status 503 four times. Check Conflict "Provider error after 4 tries" after 4 provider calls.
        inner = FlakyModel([StatusError(503)] * 4)
        with self.assertRaisesRegex(Conflict, 'Provider error after 4 tries'):
            budgeted(inner).invoke([HumanMessage('go')])
        self.assertEqual(inner.calls, 4)

    def test_timeout_is_not_retried(self):
        # Sample: Get one read timeout after a stalled stream. Check Conflict "Provider error after 1 tries" after 1 provider call.
        inner = FlakyModel([TimeoutError('read timed out')])
        with self.assertRaisesRegex(Conflict, 'Provider error after 1 tries'):
            budgeted(inner).invoke([HumanMessage('go')])
        self.assertEqual(inner.calls, 1)

    def test_bad_request_is_not_retried(self):
        # Sample: Get status 400 once. Check Conflict "Provider error after 1 tries" after 1 provider call.
        inner = FlakyModel([StatusError(400)])
        with self.assertRaisesRegex(Conflict, 'Provider error after 1 tries'):
            budgeted(inner).invoke([HumanMessage('go')])
        self.assertEqual(inner.calls, 1)

    def test_candidate_database_error_is_fed_back(self):
        # Sample: Read a SyntaxError with a fixed parser diagnosis. Keep the diagnosis. Omit QUERY and CONTEXT lines.
        from durai.development_loop import database_error_feedback
        output = '\n'.join(['  File "/workspace/durai/a.py", line 13, in project',
                            'psycopg.errors.SyntaxError: a dollar-quoted string constant is expected',
                            "QUERY:  SELECT * FROM ag_catalog.cypher('g','MATCH (n) RETURN n')",
                            'CONTEXT:  PL/pgSQL function project(text) line 7 at EXECUTE'])
        text = database_error_feedback(output)
        self.assertIn('a dollar-quoted string constant is expected', text)
        self.assertNotIn('QUERY:', text)
        self.assertNotIn('CONTEXT:', text)
        self.assertIsNone(database_error_feedback('AssertionError: 1 != 2'))

    def test_worker_prompt_has_cypher_rule(self):
        # Sample: Build the worker prompt with pack version 7. Check rule do-not-quote-cypher-with-format and the $q$ example.
        text = instructions(task(), {'/workspace/a.py': b''}, generic_pack('7')['records'][0]['body']['policies'])
        self.assertIn('[do-not-quote-cypher-with-format]', text)
        self.assertIn('$q$ MATCH (n) RETURN n $q$', text)

    def test_two_calls_left_warning_is_sent(self):
        # Sample: Cap 4. Read in calls 1 to 4. Check notices with 2, 1, and 0 calls remaining on calls 2, 3, and 4.
        NOTICES.clear()
        run([READ] * 6, 4)
        self.assertEqual([item.split(' ')[0] for item in NOTICES], ['2', '1', '0'])

    def test_cap_with_changed_files_is_evaluated(self):
        # Sample: Cap 4. Edit the module correctly in call 2. Read until the cap. Check PASS and event ATTEMPT_BUDGET_STOPPED.
        steps = [READ, ('edit_file', {'file_path': MODULE, 'old_string': BASELINE_BODY, 'new_string': CORRECT_BODY})] + [READ] * 6
        result = run(steps, 4)
        self.assertEqual(result['outcome'], 'PASSED', result.get('verdict'))
        kinds = [event['kind'] for event in replay(result['runId'], database=DATABASE)]
        self.assertIn('ATTEMPT_BUDGET_STOPPED', kinds)

    def test_cap_without_changes_still_fails(self):
        # Sample: Cap 3. Read in every call. Check FAILED with "Model-call budget is exhausted".
        result = run([READ] * 6, 3)
        self.assertEqual(result['outcome'], 'FAILED')
        self.assertNotIn('ATTEMPT_BUDGET_STOPPED', [event['kind'] for event in replay(result['runId'], database=DATABASE)])

    def test_loop_records_edit_operations(self):
        # Sample: Edit the module once in a scripted loop. Check one edit_file row in the tool-call ledger for the attempt.
        steps = [READ, ('edit_file', {'file_path': MODULE, 'old_string': BASELINE_BODY, 'new_string': CORRECT_BODY})]
        result = run(steps, 6)
        rows = operations(result['attempts'][0]['attemptId'], DATABASE)
        self.assertEqual(len(rows), 1)


if __name__ == '__main__':
    unittest.main()
