"""Sample: Stop a retry with 4 calls left, keep a database error after 6000 characters, and diagnose a missing agtype."""
import tempfile
import unittest
from pathlib import Path
from durai.failure_triage import TRIAGE_RULES, TRIAGE_RULES_V11, FailureTriage, triage_procedure
from durai.task_evaluator import SandboxEvaluator
from durai.workspace_manifest import materialize
from test_failure_triage import bound, facts, failed

AGTYPE = 'psycopg.errors.UndefinedObject: type "agtype" does not exist'


def candidate_failure(calls):
    # Sample: Make facts for a candidate assertion with 1 attempt and 4 calls left. Return the fact map.
    unit = {'command': 'python-unittest', 'state': 'FAIL', 'fault': {'origin': 'candidate', 'reasonCode': 'CANDIDATE_ASSERTION'}}
    return facts(failed(unit), calls=calls)


class DatabaseFeedbackTests(unittest.TestCase):
    def test_agtype_diagnosis(self):
        # Sample: Read the UndefinedObject agtype line. Check SQLSTATE 42704 and the function search_path diagnosis.
        from durai.database_feedback import safe_database_feedback
        text = safe_database_feedback(AGTYPE, 'evaluation')
        self.assertIn('SQLSTATE 42704', text)
        self.assertIn('ag_catalog in the search_path of each SQL or PL/pgSQL function', text)


class EvaluatorErrorFactsTests(unittest.TestCase):
    def test_error_lines_survive_truncation(self):
        # Sample: Print one UndefinedObject line, then 6000 characters. Check databaseErrorLines holds the UndefinedObject line.
        with tempfile.TemporaryDirectory(prefix='durai-error-lines-') as folder:
            evaluator = SandboxEvaluator(Path(folder))
            materialize(Path(folder) / 'source', {'/workspace/a.py': b'x = 1\n'})
            program = 'print(' + repr(AGTYPE) + ')\nprint("x" * 6000)\nraise SystemExit(1)'
            outcome = evaluator.run(Path(folder) / 'source/workspace', ['python', '-c', program])
        self.assertNotIn('agtype', outcome['output'])
        self.assertEqual(outcome['databaseErrorLines'], [AGTYPE])

    def test_feedback_uses_recorded_error_lines(self):
        # Sample: Give a unittest result with no /workspace/durai/ line and the agtype error line. Check feedback with SQLSTATE 42704.
        from durai.development_loop import database_error_feedback, recorded_error_feedback
        result = {'command': 'python-unittest', 'state': 'FAIL', 'output': 'FAILED (errors=4)', 'databaseErrorLines': [AGTYPE]}
        self.assertIsNone(database_error_feedback(result['output']))
        self.assertIn('SQLSTATE 42704', recorded_error_feedback(result))
        self.assertIsNone(recorded_error_feedback({'output': 'AssertionError'}))


class TestRetryFeedback(unittest.TestCase):
    def unit_result(self):
        # Sample: Return a failed unittest result with the IndeterminateDatatype line and a traceback through tests/test_events.py and durai/events.py.
        output = chr(10).join(['Traceback (most recent call last):',
                               '  File "/workspace/tests/test_events.py", line 9, in test_crash_lost_ack_and_redelivery',
                               '    batch = claim_batch(conn, PROTECTED-STREAM-77)',
                               '  File "/workspace/durai/events.py", line 42, in claim_batch',
                               'psycopg.errors.IndeterminateDatatype: could not determine data type of parameter $1', 'FAILED (errors=1)'])
        return {'command': 'python-unittest', 'state': 'FAIL', 'output': output,
                'databaseErrorLines': ['psycopg.errors.IndeterminateDatatype: could not determine data type of parameter $1']}

    def test_retry_feedback_has_database_error_line(self):
        # Sample: Fail the unit run with IndeterminateDatatype on parameter $1. Check the note names the class, parameter $1, and durai/events.py:42.
        from durai.development_loop import database_error_feedback, recorded_error_feedback
        result = self.unit_result()
        note = database_error_feedback(result['output']) or recorded_error_feedback(result)
        self.assertIn('IndeterminateDatatype', note)
        self.assertIn('parameter $1', note)
        self.assertIn('durai/events.py:42', note)

    def test_retry_feedback_has_no_protected_text(self):
        # Sample: Fail the unit run with a traceback line from tests/test_events.py. Check the note has no text from that line.
        from durai.development_loop import database_error_feedback, recorded_error_feedback
        result = self.unit_result()
        for note in (database_error_feedback(result['output']), recorded_error_feedback(result)):
            for text in ('test_events', 'PROTECTED-STREAM-77', 'claim_batch(conn', 'test_crash_lost_ack_and_redelivery'):
                self.assertNotIn(text, note)


class RetryMinimumTests(unittest.TestCase):
    def test_retry_needs_five_calls(self):
        # Sample: v11 rules. Candidate assertion with 4 calls left. Check STOP_BUDGET. With 5 calls left. Check RETRY_CANDIDATE.
        triage = FailureTriage(TRIAGE_RULES_V11, lambda *args: None, 1, 5)
        self.assertEqual(triage.decide(candidate_failure(4))['choice'], 'STOP_BUDGET')
        self.assertEqual(triage.decide(candidate_failure(5))['choice'], 'RETRY_CANDIDATE')

    def test_v10_rules_keep_old_behavior(self):
        # Sample: v10 rules, candidate assertion, 1 call left. Check RETRY_CANDIDATE.
        self.assertEqual(FailureTriage(TRIAGE_RULES, lambda *args: None).decide(candidate_failure(1))['choice'], 'RETRY_CANDIDATE')

    def test_v11_binding_uses_pinned_minimum(self):
        # Sample: Bind pack v11. Check source pinned, the v11 rules, and minimumRetryCalls 5.
        procedure = triage_procedure(bound('11'), 'durai_test', 'durai_runtime')
        self.assertEqual((procedure['source'], procedure['rules'], procedure['minimumRetryCalls']), ('pinned', TRIAGE_RULES_V11, 5))

    def test_v11_fixture_changes_only_binding(self):
        # Sample: Compare migration v10 with v11. Check equal suite, shell, reference, and objective, and binding foundation-generic-v11.
        import json
        from durai.qualification import TEMPLATES, load
        old, new = TEMPLATES / 'task-graph-tenancy-migration-v10', TEMPLATES / 'task-graph-tenancy-migration-v11'
        for part in ('protected', 'workspace', 'reference'):
            self.assertEqual(load(old / part), load(new / part), part)
        before, after = (json.loads((folder / 'task.json').read_text(encoding='utf-8')) for folder in (old, new))
        self.assertEqual(after['binding'], {'id': 'foundation-generic-v11', 'version': '11'})
        self.assertEqual({key: value for key, value in before.items() if key not in ('binding', 'taskPrefix')},
                         {key: value for key, value in after.items() if key not in ('binding', 'taskPrefix')})


if __name__ == '__main__':
    unittest.main()
