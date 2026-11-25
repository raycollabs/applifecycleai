"""Sample: Refuse task T-1 when its protected test expects KeyError and the objective does not name KeyError."""
import tempfile
import unittest
from pathlib import Path
from durai.error_contract import enforced, unnamed_errors
from test_failure_triage import bound


def suite(body):
    # Sample: Wrap one test body that expects ValueError. Return a protected suite file map.
    text = ('import unittest\nimport psycopg\n\n\nclass Goals(unittest.TestCase):\n    def test_goal(self):\n'
            '        # Sample: Book 0 lots. Check the expected error.\n' + body)
    return {'/workspace/tests/__init__.py': b'', '/workspace/tests/test_goal.py': text.encode()}


class ErrorContractTests(unittest.TestCase):
    def test_named_error_is_ready(self):
        # Sample: Objective "Raise ValueError for 0 lots". Test expects ValueError. Check no finding.
        found = unnamed_errors('Raise ValueError for 0 lots.', suite('        with self.assertRaises(ValueError):\n            int("x")\n'))
        self.assertEqual(found, [])

    def test_unnamed_error_is_reported(self):
        # Sample: Objective "Reject binding B-9". Test expects psycopg.errors.InvalidParameterValue. Check finding InvalidParameterValue.
        body = '        with self.assertRaises(psycopg.errors.InvalidParameterValue):\n            pass\n'
        self.assertEqual(unnamed_errors('Reject binding B-9.', suite(body)), ['InvalidParameterValue'])

    def test_sqlstate_code_names_its_class(self):
        # Sample: Objective "Raise SQLSTATE 22023" covers InvalidParameterValue. Test checks sqlstate '42501' unnamed. Check finding 42501.
        body = '        with self.assertRaises(psycopg.errors.InvalidParameterValue):\n            pass\n'
        self.assertEqual(unnamed_errors('Raise SQLSTATE 22023 for an unknown binding.', suite(body)), [])
        body = '        error = psycopg.Error()\n        self.assertEqual(error.sqlstate, "42501")\n'
        self.assertEqual(unnamed_errors('Refuse a foreign login.', suite(body)), ['42501'])
        self.assertEqual(unnamed_errors('Raise InsufficientPrivilege for a foreign login.', suite(body)), [])

    def test_either_error_group_needs_one_name(self):
        # Sample: Test expects Conflict or KeyError. Objective names KeyError. Check no finding. Objective names neither. Check "Conflict or KeyError".
        body = '        with self.assertRaises((Conflict, KeyError)):\n            pass\n'
        self.assertEqual(unnamed_errors('Raise KeyError for a hidden binding.', suite(body)), [])
        self.assertEqual(unnamed_errors('Refuse a hidden binding.', suite(body)), ['Conflict or KeyError'])

    def test_generic_exception_is_exempt(self):
        # Sample: Test expects Exception. Objective names no error. Check no finding.
        self.assertEqual(unnamed_errors('Book 2 lots.', suite('        with self.assertRaises(Exception):\n            pass\n')), [])

    def test_protected_values_are_not_reported(self):
        # Sample: Test expects ValueError with pattern H-991. Objective names no error. Check finding ValueError and no H-991.
        body = '        with self.assertRaisesRegex(ValueError, "H-991"):\n            pass\n'
        found = unnamed_errors('Book 2 lots.', suite(body))
        self.assertEqual(found, ['ValueError'])
        self.assertNotIn('H-991', ' '.join(found))

    def test_older_binding_is_not_enforced(self):
        # Sample: Bind pack v9. Check not enforced. Bind pack v10. Check enforced.
        self.assertFalse(enforced(bound('9'), 'durai_test', 'durai_runtime'))
        self.assertTrue(enforced(bound('10'), 'durai_test', 'durai_runtime'))


class ErrorContractLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply migrations and checkpoint tables to durai_test.
        from durai.db import prepare
        from durai.harness import prepare_checkpoints
        prepare('durai_test')
        prepare_checkpoints('durai_test')

    def test_unnamed_error_stops_before_model(self):
        # Sample: Bind v10. Suite expects KeyError. Objective says "Book 2 lots of 100". Check NEEDS_CLARIFICATION naming KeyError and zero model calls.
        from durai.common import uid
        from durai.development_loop import build_loop, development_graph
        from test_dev_loop import BASE, FIX, LoopHarness, loop_spec, pins_for
        protected = suite('        with self.assertRaises(KeyError):\n            {}["lots"]\n')

        class KeyErrorSources:
            def files(self, task):
                # Sample: Return the baseline shares.py.
                return dict(BASE)

            def suite(self, task):
                # Sample: Return the suite that expects KeyError.
                return dict(protected)
        task = bound('10')
        spec = loop_spec(suite=protected, objective='Book 2 lots of 100 as 200 shares.',
                         binding={'bindingId': task['binding']['bindingId'], 'contractDigest': task['pins']['contractDigest']})
        harness = LoopHarness([[FIX]])
        builder = build_loop(harness.ledger, harness.providers, harness.evaluator, harness.store, KeyErrorSources(),
                             harness.model, Path(tempfile.mkdtemp(prefix='durai-error-contract-')))
        with development_graph(builder, 'durai_test') as graph:
            state = graph.invoke({'run_id': 'run-' + uid(), 'spec': spec, 'current': pins_for(spec)},
                                 {'configurable': {'thread_id': 'error-contract-' + uid()}})
        self.assertEqual(state['outcome'], 'NEEDS_CLARIFICATION')
        self.assertIn('KeyError', state['detail'])
        self.assertEqual(harness.models, [])


if __name__ == '__main__':
    unittest.main()
