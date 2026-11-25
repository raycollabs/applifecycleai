"""Sample: Delete the module docstring outside scope. Check refusal. Reject a..b.py with a substring check. Check FAIL."""
from pathlib import Path
import tempfile
import unittest
from durai.comment_delta import comment_findings, sql_comment_findings
from durai.common import Conflict
from durai.db import prepare
from durai.harness import prepare_checkpoints
from durai.packs import generic_pack
from durai.qualification import BASELINE_BODY, CORRECT_BODY, FIXTURE, FixtureSources, MODULE, load, run_task_b, task_b_spec
from durai.runtime_deepagents import instructions
from durai.scope_guard import check_scope, unrequested_edits
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator
from test_task_contract import feature_spec
from test_workspace_worker import SequenceModel

DATABASE = 'durai_test'
BASE = load(FIXTURE / 'workspace')
SOURCE = BASE[MODULE].decode()
STRICT_BODY = CORRECT_BODY.replace("'..' in path.split('/')", "'..' in path")


def candidate(body, keep_docstring=True):
    # Sample: Replace return True with the complete validation body. Remove the docstring when asked.
    text = SOURCE.replace(BASELINE_BODY, body)
    if not keep_docstring:
        text = text.split('\n', 1)[1]
    return {MODULE: text.encode()}


class RecordingModel(SequenceModel):
    prompts: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Record the first human message for attempt 2. Continue the scripted steps.
        self.prompts.extend(str(message.content) for message in messages if message.type == 'human')
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class OntologyRuleTests(unittest.TestCase):
    def test_generic_pack_has_do_not_rules_for_workers(self):
        # Sample: Read generic pack G-3. Check two DO NOT rules for the coding worker. Keep G-2 unchanged.
        policies = {item['id']: item for item in generic_pack('3')['records'][0]['body']['policies']}
        for name in ('do-not-edit-unrequested', 'do-not-over-constrain'):
            self.assertTrue(policies[name]['rule'].startswith('DO NOT'))
            self.assertIn('coding-worker', policies[name]['audience'])
        self.assertNotIn('do-not-edit-unrequested', {item['id'] for item in generic_pack('2')['records'][0]['body']['policies']})

    def test_worker_receives_only_worker_rules(self):
        # Sample: Build instructions with G-3 policies. Check both DO NOT rules and no design-before-code rule.
        policies = generic_pack('3')['records'][0]['body']['policies']
        task = build_task(feature_spec())['task']
        text = instructions(task, {'/workspace/a.py': b''}, policies)
        self.assertIn('[do-not-edit-unrequested] DO NOT change', text)
        self.assertIn('[do-not-over-constrain] DO NOT reject', text)
        self.assertNotIn('[design-before-code]', text)
        self.assertIn('keep every existing comment and docstring unchanged', text)


class CommentDeltaTests(unittest.TestCase):
    def test_explanatory_comment_is_refused(self):
        # Sample: Add a comment that the check was too strict before. Check one added-comment finding.
        text = SOURCE.replace(BASELINE_BODY, CORRECT_BODY.replace("    return True", "    # Fixed because the old check was too strict.\n    return True"))
        findings = comment_findings(BASE[MODULE], text.encode())
        self.assertEqual(len(findings), 1)
        self.assertIn('added comment is not a sample walkthrough', findings[0])

    def test_legacy_comment_is_kept_without_finding(self):
        # Sample: Keep a legacy note above file_digest. Change validate_change with its sample. Check no finding.
        legacy = SOURCE.replace("def file_digest", "# Legacy note kept from an earlier owner.\ndef file_digest").encode()
        changed = legacy.decode().replace(BASELINE_BODY, CORRECT_BODY).encode()
        self.assertEqual(comment_findings(legacy, changed), [])

    def test_changed_function_needs_sample(self):
        # Sample: Remove the sample line from validate_change and change its body. Check one missing-sample finding.
        text = SOURCE.replace(BASELINE_BODY, CORRECT_BODY)
        text = '\n'.join(line for line in text.split('\n') if 'Check /workspace/a.py with b"200", allowed' not in line)
        findings = comment_findings(BASE[MODULE], text.encode())
        self.assertEqual(1, len(findings))
        self.assertTrue(findings[0].startswith('added or changed function has no sample walkthrough: validate_change (def at line '))
        self.assertIn('first line inside its body', findings[0])

    def test_nested_function_hint_names_inner_body(self):
        # Sample: Add def handler(conn) inside create_operation with a sample only on create_operation. Check one finding for create_operation.handler that says the outer sample does not cover it.
        after = (b'def create_operation(conn):\n'
                 b'    # Sample: Create operation OP-1 for account A-1. Return OP-1.\n'
                 b'    def handler(cursor):\n'
                 b'        return cursor.execute("SELECT 1")\n'
                 b'    return handler(conn)\n')
        findings = comment_findings(None, after)
        self.assertEqual(1, len(findings))
        self.assertIn('create_operation.handler (def at line 3)', findings[0])
        self.assertIn('the sample of create_operation does not cover this inner function', findings[0])

    def test_sql_comments_allow_only_samples(self):
        # Sample: Add a sample comment, an explanation comment, and a literal with two dashes to core_0010.sql. Check one finding.
        sql = (b"-- Sample: Create table policy with one row per rule.\n"
               b"-- Store rules here for the view.\n"
               b"INSERT INTO t VALUES('a--b');\n")
        self.assertEqual(sql_comment_findings(None, sql),
                         ['added SQL comment is not a sample walkthrough: -- Store rules here for the view.'])

    def test_worker_receives_comment_rule_from_ontology(self):
        # Sample: Build instructions with G-4 policies. Check the DO NOT rule for explanatory comments.
        task = build_task(feature_spec())['task']
        text = instructions(task, {'/workspace/a.py': b''}, generic_pack('4')['records'][0]['body']['policies'])
        self.assertIn('[do-not-write-explanatory-comments] DO NOT write comments', text)


class ScopeGuardTests(unittest.TestCase):
    def setUp(self):
        # Sample: Seal Task B with editScope validate_change.
        prepare(DATABASE)
        self.task = build_task(task_b_spec(DATABASE, 'sha256:I-1'))['task']

    def test_declared_change_passes(self):
        # Sample: Change only validate_change and add no import. Check PASS.
        self.assertEqual(check_scope(self.task, BASE, candidate(CORRECT_BODY))['scope'], 'PASS')

    def test_deleted_docstring_is_refused(self):
        # Sample: Delete the module docstring and change validate_change. Check one finding for the docstring.
        self.assertEqual(unrequested_edits(self.task, BASE, candidate(CORRECT_BODY, keep_docstring=False)),
                         [MODULE + ': module docstring changed'])

    def test_undeclared_function_change_is_refused(self):
        # Sample: Change file_digest to sha512. Check refusal that names file_digest.
        changed = {MODULE: candidate(CORRECT_BODY)[MODULE].replace(b'sha256(content)', b'sha512(content)')}
        with self.assertRaisesRegex(Conflict, 'file_digest changed without scope'):
            check_scope(self.task, BASE, changed)

    def test_out_of_scope_change_lists_allowed_names(self):
        # Sample: Scope change_manifest.py to validate_change. Change file_digest to sha512. Check the finding names file_digest, "allowed: validate_change", and "restore it exactly".
        changed = {MODULE: candidate(CORRECT_BODY)[MODULE].replace(b'sha256(content)', b'sha512(content)')}
        findings = unrequested_edits(self.task, BASE, changed)
        self.assertEqual(1, len(findings))
        self.assertIn('file_digest changed without scope; allowed: validate_change', findings[0])
        self.assertIn('restore it exactly as in the sealed source', findings[0])

    def test_scope_must_name_write_paths(self):
        # Sample: Declare editScope for /workspace/c.py, which is not writable. Check refusal.
        with self.assertRaisesRegex(Conflict, 'editScope'):
            build_task(feature_spec(editScope={'/workspace/c.py': ['run']}))


class OverConstraintTests(unittest.TestCase):
    def test_substring_check_fails_protected_suite(self):
        # Sample: Reject every path that contains two dots. Run suite H-B. Check FAIL for /workspace/a..b.py.
        evaluator = SandboxEvaluator(Path(tempfile.mkdtemp(prefix='durai-lesson-')))
        task = build_task(task_b_spec(DATABASE, evaluator.image))['task']
        suite = load(FIXTURE / 'protected')
        strict = evaluator.evaluate(task, candidate(STRICT_BODY), suite)
        exact = evaluator.evaluate(task, candidate(CORRECT_BODY), suite)
        self.assertEqual(strict['verdict'], 'FAIL')
        self.assertIn('a..b.py', strict['results'][1]['output'])
        self.assertEqual(exact['verdict'], 'PASS', exact['results'])


class LessonLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core_0009 and checkpoint tables to durai_test.
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_unrequested_edit_fails_then_feedback_fixes_it(self):
        # Sample: Apply a valid body, then request docstring removal. Check a tool refusal and PASS in the same attempt.
        delete_docstring = ('edit_file', {'file_path': MODULE, 'old_string': SOURCE.split('\n', 1)[0] + '\n', 'new_string': ''})
        fix = ('edit_file', {'file_path': MODULE, 'old_string': BASELINE_BODY, 'new_string': CORRECT_BODY})
        models = {1: RecordingModel(steps=[fix, delete_docstring], replies=[], prompts=[]),
                  2: RecordingModel(steps=[fix], replies=[], prompts=[])}
        result = run_task_b(lambda attempt: models[attempt], Path(tempfile.mkdtemp(prefix='durai-lesson-loop-')), DATABASE)
        self.assertEqual([(item['verdict'], item['state']) for item in result['attempts']], [('PASS', 'WAITING_FOR_REVIEW')])
        self.assertEqual(result['outcome'], 'PASSED')
        self.assertTrue(any('module docstring changed' in reply for reply in models[1].replies))
        self.assertEqual(models[2].prompts, [])
        self.assertNotIn('module docstring changed', models[1].prompts[0])
        self.assertTrue(result['module'].startswith('"""Sample:'))
        delivered = FixtureSources(DATABASE).policies(build_task(task_b_spec(DATABASE, 'sha256:I-1'))['task'])
        self.assertTrue({'do-not-edit-unrequested', 'do-not-over-constrain'} <= {item['policyId'] for item in delivered['policies']})


if __name__ == '__main__':
    unittest.main()
