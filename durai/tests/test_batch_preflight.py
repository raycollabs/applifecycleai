"""Sample: Hide database caller values, keep fixed AGE diagnoses, and cap all provider attempts including retries."""
import unittest
from langchain_core.messages import HumanMessage
from durai.common import Conflict
from durai.database_feedback import safe_database_feedback
from durai.execution_budget import BudgetedModel
from durai.packs import generic_pack
from durai.runtime_deepagents import instructions
from test_budget_finish import FakeLedger, FlakyModel, StatusError
from test_workspace_worker import task


class FeedbackSafetyTests(unittest.TestCase):
    def test_protected_values_do_not_reach_worker(self):
        # Sample: Read hidden quoted, escaped, numeric, and multiline inputs. Return only class, SQLSTATE, and a fixed diagnosis.
        output = ('psycopg.errors.InvalidParameterValue: third argument of cypher function must be a parameter '
                  "'HIDDEN-A' O''HIDDEN-B 912345\nQUERY: SELECT secret_HIDDEN_C\nDETAIL: HIDDEN-D\nCONTEXT: HIDDEN-E")
        text = safe_database_feedback(output, 'evaluation')
        self.assertIn('22023', text)
        self.assertIn('third argument of cypher function must be a parameter', text)
        for value in ('HIDDEN-A', 'HIDDEN-B', '912345', 'HIDDEN_C', 'HIDDEN-D', 'HIDDEN-E', 'QUERY:', 'CONTEXT:'):
            self.assertNotIn(value, text)

    def test_unknown_errors_do_not_echo_payload(self):
        # Sample: Read an unknown database error containing PRIVATE-VALUE. Return a generic operator-receipt instruction.
        text = safe_database_feedback('psycopg.errors.UnknownThing: PRIVATE-VALUE', 'setup')
        self.assertNotIn('PRIVATE-VALUE', text)
        self.assertIn('operator receipt', text)


class CappedLedger(FakeLedger):
    def __init__(self, limit):
        # Sample: Start a one-attempt budget at zero reservations with limit 1.
        self.limit, self.reserved, self.actual = limit, 0, 0

    def reserve(self, budget, reservation, resource, amount):
        # Sample: Reserve the first model attempt. Refuse the second when the limit is 1.
        if self.reserved + amount > self.limit:
            return False
        self.reserved += amount
        return True

    def reconcile(self, budget, reservation, actual):
        # Sample: Reconcile one provider attempt. Add 1 to the actual count.
        self.actual += actual


class ProviderBudgetTests(unittest.TestCase):
    def test_retries_share_physical_call_cap(self):
        # Sample: Get a 503 with one provider attempt allowed. Refuse the retry and record exactly one actual attempt.
        ledger, inner = CappedLedger(1), FlakyModel([StatusError(503), StatusError(503)])
        model = BudgetedModel(inner=inner, ledger=ledger, budget_id='B', attempt_id='A', owner='W',
                              generation=1, retry_delays=(0, 0, 0))
        with self.assertRaisesRegex(Conflict, 'budget is exhausted before provider retry'):
            model.invoke([HumanMessage('go')])
        self.assertEqual((inner.calls, ledger.actual), (1, 1))

    def test_successful_retry_reserves_each_attempt(self):
        # Sample: Get two resets and then a reply with limit 3. Check three provider calls and three reconciled reservations.
        ledger, inner = CappedLedger(3), FlakyModel([ConnectionResetError('reset'), ConnectionResetError('reset')])
        model = BudgetedModel(inner=inner, ledger=ledger, budget_id='B', attempt_id='A', owner='W',
                              generation=1, retry_delays=(0, 0, 0))
        self.assertEqual(model.invoke([HumanMessage('go')]).content, 'done')
        self.assertEqual((inner.calls, ledger.actual), (3, 3))


class AgePolicyTests(unittest.TestCase):
    def test_v8_delivers_complete_contract(self):
        # Sample: Build a worker prompt from G-8. Check both language forms, graph constants, quoted queries, and bound maps.
        text = instructions(task(), {'/workspace/a.py': b''}, generic_pack('8')['records'][0]['body']['policies'])
        for fragment in ('do-not-break-age-cypher-call-contract', 'sql.Literal', '$q$', '%s::ag_catalog.agtype',
                         '$1::ag_catalog.agtype', 'USING parameters', 'search_path'):
            self.assertIn(fragment, text)
        self.assertNotIn('[do-not-quote-cypher-with-format]', text)
