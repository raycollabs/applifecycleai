from pathlib import Path
import tempfile
import unittest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from durai.common import uid, digest
from durai.db import transaction
from durai.harness import prepare_checkpoints, trading_harness
from durai.ontology import project_trading_rule, RelationalOntology
from durai.coding_worker import repair_workspace
from durai.trading import ROOT, seed, FIXTURES
from durai.providers import Providers
from durai.common import Conflict


class ScriptedToolModel(BaseChatModel):
    @property
    def _llm_type(self):
        # Sample: Select the scripted adapter. Make no provider request.
        return 'scripted-contract-test'

    def bind_tools(self, tools, **kwargs):
        # Sample: Bind the permitted edit tool. Keep the scripted model.
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Convert 2 lots of 100 and remove 1 lot. Check both file edits.
        calls = sum(1 for message in messages if message.type == 'tool')
        edits = [('return lots\n', 'return lots * lot_size\n'),
                 ('return shares - lots\n', 'return shares - lots * lot_size\n')]
        if calls < len(edits):
            old, new = edits[calls]
            message = AIMessage(content='', tool_calls=[{'name': 'edit_file', 'id': 'edit-' + str(calls),
                                'args': {'file_path': '/workspace/program.py', 'old_string': old, 'new_string': new}}])
        else:
            message = AIMessage(content='The scripted test edits are complete.')
        return ChatResult(generations=[ChatGeneration(message=message)])


class AdapterTests(unittest.TestCase):
    def test_model_proposer_receives_declared_correction_rule(self):
        # Sample: Give the worker the correction rule. Keep protected expected quantities private.
        from unittest.mock import patch
        from durai.coding_worker import model_proposer
        registry = seed('durai_test')
        captured = {}
        source = registry.get('broken-v1')['source_bytes']
        def propose(folder, model, task, **kwargs):
            # Sample: Capture the brief and return the pinned source without a network call.
            captured['task'] = task
            from durai.replica import candidate_source
            candidate = candidate_source()
            return {'source': candidate, 'digest': digest(candidate)}
        from durai.release import ReleaseController
        from durai.files import NativeFixtureRunner
        controller = ReleaseController(registry, NativeFixtureRunner(ROOT / 'private/test-workspaces'))
        target = 'context-' + uid()
        controller.initialize_target(target, 'TEST')
        with patch('durai.coding_worker.configured_model', return_value=object()), \
             patch('durai.coding_worker.repair_workspace', side_effect=propose), \
             patch('durai.models.require_configuration', return_value={'model': 'scripted-test', 'apiKey': 'synthetic-test-key',
                 'provider': 'openrouter', 'baseUrl': 'https://openrouter.ai/api/v1'}):
            model_proposer(registry, {'target_id': target, 'run_id': uid(), 'attempt': 1})
        self.assertIn('positive correction_lots removes', captured['task'])
        self.assertIn('shares - correction_lots * lot_size', captured['task'])
        self.assertNotIn('200', captured['task'])
        self.assertNotIn('100', captured['task'])

    def test_factory_rejects_regression_waits_for_review_and_resumes(self):
        # Sample: Run the whole factory. Refuse the first repair. Resume only with test review.
        from durai.factory import factory_graph, fixture_proposer
        from durai.evaluation_consumer import LocalProtectedEvaluator
        from durai.files import NativeFixtureRunner
        from durai.release import ReleaseController
        from langgraph.types import Command
        registry = seed('durai_test')
        prepare_checkpoints('durai_test')
        runner = NativeFixtureRunner(ROOT / 'private/test-workspaces')
        evaluator = LocalProtectedEvaluator(registry, runner)
        controller = ReleaseController(registry, runner)
        target = 'factory-' + uid()
        controller.initialize_target(target, 'TEST')
        run_id = uid()
        config = {'configurable': {'thread_id': 'factory:' + run_id}}
        with factory_graph(registry, evaluator, controller, fixture_proposer) as graph:
            graph.invoke({'run_id': run_id, 'target_id': target}, config)
            state = graph.get_state(config)
            self.assertEqual(state.values['attempt'], 2)
            self.assertEqual(state.next, ('review',))
            self.assertEqual(state.values['verdict']['verdict'], 'PASS')
        with transaction(database='durai_test') as db:
            self.assertEqual(db.execute('SELECT state FROM durai_agents.run WHERE id=%s', (run_id,)).fetchone()['state'], 'WAITING_FOR_REVIEW')
        approval = controller.approve('repaired-v3', registry.get('repaired-v3')['digest'],
                                       target, 'factory-test', 'TEST_ONLY')
        with factory_graph(registry, evaluator, controller, fixture_proposer) as graph:
            result = graph.invoke(Command(resume={'approvalId': approval}), config)
        self.assertEqual((result['state'], result['result']['shares']), ('SUCCEEDED', 200))
        controller.rollback(target)

    def test_replacement_routes_new_work_and_drains_pinned_provider(self):
        # Sample: Pin V-1. Route new work to V-2. Remove V-1 only after its work ends.
        providers = Providers()
        providers.register('sandbox', 'v1', 'native')
        with providers.acquire('sandbox') as pinned:
            providers.register('sandbox', 'v2', 'docker')
            with providers.acquire('sandbox') as replacement:
                self.assertEqual((pinned, replacement), ('native', 'docker'))
            with self.assertRaises(Conflict):
                providers.deregister('sandbox', 'v1')
        providers.deregister('sandbox', 'v1')

    def test_age_projection_matches_canonical_rule(self):
        # Sample: Bind the SQL rule to a fixed AGE node. Refuse foreign canonical lookup.
        seed('durai_test')
        result = project_trading_rule('durai_test')
        rule = RelationalOntology('durai_test').get_rule('quantity-unit', '1')
        self.assertEqual(result['canonicalDigest'], digest(rule))
        with self.assertRaises(KeyError):
            RelationalOntology('durai_test', 'durai_foreign').get_rule('quantity-unit', '1')

    def test_langgraph_resumes_after_connection_restart(self):
        # Sample: Stop at the boundary. Open a new saver and graph. Book one 200-share row.
        seed('durai_test')
        prepare_checkpoints('durai_test')
        run_id = uid()
        config = {'configurable': {'thread_id': 'trading:' + run_id}}
        with trading_harness('durai_test') as graph:
            first = graph.invoke({'run_id': run_id}, config)
            self.assertEqual(first['stage'], 'PREPARED')
        with trading_harness('durai_test') as graph:
            resumed = graph.invoke(None, config)
            self.assertEqual((resumed['stage'], resumed['shares']), ('BOOKED', 200))
        with transaction(database='durai_test') as db:
            count = db.execute('SELECT count(*) AS n FROM app_trading.trade WHERE id=%s', ('H-' + run_id,)).fetchone()['n']
        self.assertEqual(count, 1)

    def test_deepagents_dispatches_edits_without_release_authority(self):
        # Sample: Use a scripted model to dispatch two real file edits. Do not call a provider.
        with tempfile.TemporaryDirectory(dir=ROOT / 'private') as folder:
            target = Path(folder, 'workspace/program.py')
            target.parent.mkdir()
            source = (FIXTURES / 'broken.py').read_bytes()
            source = source.replace(source.splitlines()[0] + b'\n', b'')
            source = source.replace(b'def book(lots, lot_size):\n', b'def book(lots, lot_size):\n    # Sample: Book 2 lots of 100. Check 200 shares.\n')
            source = source.replace(b'def correct(shares, lots, lot_size):\n', b'def correct(shares, lots, lot_size):\n    # Sample: Remove 1 lot of 100 from 200. Check 100 shares.\n')
            target.write_bytes(source)
            result = repair_workspace(folder, ScriptedToolModel(),
                                      'Convert opening lots and correction lots to canonical shares.')
            self.assertIn(b'return lots * lot_size', result['source'])
            self.assertIn(b'return shares - lots * lot_size', result['source'])
            self.assertEqual(result['authority'], 'candidate-proposal-only')
            self.assertNotEqual(result['digest'], digest(source))
