"""Sample: Reject undeclared files and comments. Preserve pinned policy versions."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from durai.common import digest, uid, Conflict
from durai.comment_policy import validate_comments
from durai.coding_worker import repair_workspace
from durai.engineering import Engineering
from durai.generation import DEFAULT_SPEC
from durai.packs import PackStore, generic_pack
from durai.worker_contract import resolve_contract, verify_contract

SAMPLE = b'def book():\n    # Sample: Book 2 lots of 100. Check 200 shares.\n    return 200\n'


class ProbeModel(BaseChatModel):
    action: str = ''
    arguments: dict = {}
    names: list[str] = []
    replies: list[str] = []

    @property
    def _llm_type(self):
        # Sample: Select a scripted model. Make no external request.
        return 'foundation-probe'

    def bind_tools(self, tools, **kwargs):
        # Sample: Record three file tools. Check that task is absent.
        self.names.extend(tool.name for tool in tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Request one file edit. Read its tool result before finishing.
        results = [message for message in messages if message.type == 'tool']
        self.replies.extend(str(message.content) for message in results)
        if self.action and not results:
            message = AIMessage(content='', tool_calls=[{'name': self.action, 'id': 'probe', 'args': self.arguments}])
        else:
            message = AIMessage(content='Sample edit complete.')
        return ChatResult(generations=[ChatGeneration(message=message)])


class CommentPolicyTests(unittest.TestCase):
    def test_sample_is_accepted(self):
        # Sample: Check the two-lot walkthrough. Accept its expected 200 shares.
        self.assertEqual(validate_comments(SAMPLE)['policy'], 'sample-only-asd-ste100/v2')

    def test_rationale_is_rejected(self):
        # Sample: Add a forbidden sentence. Reject it beside a valid walkthrough.
        for value in (b'# This code is easy.\n', b'# Sample: Do this because it is faster.\n'):
            with self.assertRaises(Conflict):
                validate_comments(SAMPLE + value)

    def test_docstring_rule_is_enforced(self):
        # Sample: Refuse a explanatory docstring. Accept a concrete sample docstring.
        with self.assertRaises(Conflict):
            validate_comments(b'"""Explain the producer."""\n' + SAMPLE)
        validate_comments(b'"""Sample: Book 2 lots of 100. Check 200 shares."""\n' + SAMPLE)

    def test_data_strings_are_not_comments(self):
        # Sample: Store #tag as data. Preserve the complete string.
        validate_comments(SAMPLE + b'label = "#tag"\n')

    def test_standalone_string_comment_is_rejected(self):
        # Sample: Add an unused explanation string. Check refusal after a valid function.
        with self.assertRaises(Conflict):
            validate_comments(SAMPLE + b'"This code is easy."\n')

    def test_legacy_version_is_preserved(self):
        # Sample: Load G-1 and G-2. Keep G-1 rules and separate G-2 identity.
        old, new = generic_pack('1'), generic_pack('2')
        self.assertEqual(old['version'], '1')
        self.assertEqual(new['version'], '2')
        self.assertIn('comment-purpose', {p['id'] for p in old['records'][0]['body']['policies']})
        self.assertIn('sample-comments-only', {p['id'] for p in new['records'][0]['body']['policies']})
        self.assertNotEqual(digest(old), digest(new))


class ContractTests(unittest.TestCase):
    def setUp(self):
        # Sample: Bind G-2 to a unique synthetic app in durai_test.
        self.store = PackStore('durai_test')
        self.generic = self.store.install(generic_pack())
        self.binding = 'contract-' + uid()
        self.store.bind(self.binding, [self.generic], 'SYNTHETIC_LOCAL')

    def test_fork_reaches_worker_contract(self):
        # Sample: Add an exact rounding rule to G-3. Resolve it for the worker.
        body = copy.deepcopy(generic_pack()['records'][0]['body'])
        body['policies'].append({'id': 'custom-rounding', 'rule': 'Reject fractional lot input. Do not round it.'})
        fork = self.store.fork(self.generic, 'fork.' + uid(), '3', 'design.knowledge', body)
        self.store.bind(self.binding, [fork], 'SYNTHETIC_LOCAL')
        contract = resolve_contract(self.binding, database='durai_test')
        self.assertIn('custom-rounding', {p['id'] for p in contract['policies']})
        self.assertEqual(contract['packs'], [fork])
        self.assertTrue(verify_contract(contract, 'durai_test'))

    def test_conflicting_domain_blocks_dispatch(self):
        # Sample: Declare ADD_LOTS in app A. Refuse the requested REMOVE_LOTS behavior.
        document = copy.deepcopy(generic_pack())
        document.update(id='domain.' + uid(), kind='APPLICATION')
        document['records'][0]['body']['correctionDirection'] = 'ADD_LOTS'
        document['records'][0]['digest'] = digest(document['records'][0]['body'])
        domain = self.store.install(document)
        self.store.bind(self.binding, [self.generic, domain], 'SYNTHETIC_LOCAL')
        with self.assertRaises(Conflict):
            resolve_contract(self.binding, database='durai_test')

    def test_stale_contract_is_rejected(self):
        # Sample: Change one contract byte. Then change its current binding. Refuse both.
        contract = resolve_contract(self.binding, database='durai_test')
        changed = copy.deepcopy(contract)
        changed['allowedFiles'].append('/extra.py')
        with self.assertRaises(Conflict):
            verify_contract(changed, 'durai_test')
        body = copy.deepcopy(generic_pack()['records'][0]['body'])
        body['policies'].append({'id': 'new-rule', 'rule': 'Use integer quantities.'})
        fork = self.store.fork(self.generic, 'fork.' + uid(), '3', 'design.knowledge', body)
        self.store.bind(self.binding, [fork], 'SYNTHETIC_LOCAL')
        with self.assertRaises(Conflict):
            verify_contract(contract, 'durai_test')

    def test_policy_change_blocks_readiness(self):
        # Sample: Create a run with G-2. Change its binding before design readiness.
        engine = Engineering('durai_test')
        run = engine.create(self.binding, DEFAULT_SPEC, {'source': 'S-1'})
        old = self.store.install(generic_pack('1'))
        self.store.bind(self.binding, [old], 'SYNTHETIC_LOCAL')
        with self.assertRaises(Conflict):
            engine.record(run, 'DESIGN_READY', engine.evidence(run, briefDigest='B-1'))


class WorkerBoundaryTests(unittest.TestCase):
    def setUp(self):
        # Sample: Materialize program.py and a protected test file in a new folder.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'workspace').mkdir()
        (self.root / 'workspace/program.py').write_bytes(SAMPLE)
        (self.root / 'tests.py').write_bytes(b'protected')

    def test_task_tool_is_absent(self):
        # Sample: Build a worker. Check the three permitted tools and no task tool.
        model = ProbeModel()
        repair_workspace(self.root, model, 'Keep the sample source.')
        self.assertEqual(set(model.names), {'read_file', 'edit_file', 'write_file'})

    def test_extra_file_write_is_denied(self):
        # Sample: Request extra.py. Check a permission error and no created file.
        model = ProbeModel(action='write_file', arguments={'file_path': '/extra.py', 'content': 'extra'})
        repair_workspace(self.root, model, 'Create extra.py.')
        self.assertFalse((self.root / 'extra.py').exists())
        self.assertTrue(any('permission denied' in reply.lower() for reply in model.replies))

    def test_protected_file_change_is_rejected(self):
        # Sample: Change tests.py after graph dispatch. Reject the returned candidate.
        class Graph:
            def invoke(inner, *args, **kwargs):
                # Sample: Replace one protected byte. Return no model message.
                (self.root / 'tests.py').write_bytes(b'changed')
                return {'messages': []}
        with patch('durai.coding_worker.create_deep_agent', return_value=Graph()):
            with self.assertRaises(ValueError):
                repair_workspace(self.root, ProbeModel(), 'Keep the source.')

    def test_candidate_comments_are_checked(self):
        # Sample: Add a forbidden comment through write_file. Refuse the candidate.
        content = (SAMPLE + b'# This is easy because it is short.\n').decode()
        model = ProbeModel(action='write_file', arguments={'file_path': '/workspace/program.py', 'content': content})
        with self.assertRaises(Conflict):
            repair_workspace(self.root, model, 'Replace the program.')

    def test_undeclared_tool_call_is_rejected(self):
        # Sample: Request task without a registered task tool. Refuse dispatch.
        model = ProbeModel(action='task', arguments={'description': 'Edit source', 'subagent_type': 'general-purpose'})
        with self.assertRaises(Conflict):
            repair_workspace(self.root, model, 'Delegate the edit.')


class GenerationContractTests(unittest.TestCase):
    def test_generated_model_uses_effective_contract(self):
        # Sample: Fix the lot conversion through one real scripted edit. Pass eight goals.
        from durai.generation import generate, ROOT
        from durai.files import NativeFixtureRunner
        store = PackStore('durai_test')
        ref = store.install(generic_pack())
        binding = 'model-contract-' + uid()
        store.bind(binding, [ref], 'SYNTHETIC_LOCAL')
        old = 'return lots  # Sample: Capture 2 lots. Keep 2 before conversion.'
        model = ProbeModel(action='edit_file', arguments={'file_path': '/workspace/program.py', 'old_string': old, 'new_string': 'return lots * lot_size'})
        route = {'id': 'scripted', 'model': 'scripted'}
        with patch('durai.models.require_configuration', return_value={}), patch('durai.model_routes.candidates', return_value=[route]), patch('durai.coding_worker.configured_model', return_value=model):
            result = generate(binding, DEFAULT_SPEC, 'durai_test', use_model=True, runner=NativeFixtureRunner(ROOT / 'private/foundation-test-workspaces'))
        self.assertEqual(result['state'], 'WAITING_FOR_REVIEW')
        self.assertEqual(result['workerContractDigest'], resolve_contract(binding, database='durai_test')['digest'])
        self.assertEqual(len(result['evaluation']['checks']), 8)


class RepairReadinessTests(unittest.TestCase):
    def test_retry_uses_remaining_correction_defect(self):
        # Sample: Start with opening 200 and correction 199. Keep correction as the red goal.
        from durai.trading import seed
        from durai.files import NativeFixtureRunner
        from durai.generation import ROOT
        from durai.worker_contract import repair_contract
        from durai.repair_readiness import prepare_repair
        registry = seed('durai_test')
        baseline = registry.get('quantity-only-v2')['source_bytes']
        contract = repair_contract('durai_test')
        runner = NativeFixtureRunner(ROOT / 'private/foundation-test-workspaces')
        engine, run, _, _ = prepare_repair(baseline, contract, 'durai_test', runner)
        red = engine.get(run)['evidence'][2]['document']
        self.assertEqual(red['expected'], 100)
        self.assertEqual(red['observed'], 199)
        self.assertEqual(engine.get(run)['state'], 'IMPLEMENTATION_READY')


class FactoryBindingTests(unittest.TestCase):
    def test_factory_delivers_selected_binding_to_worker(self):
        # Sample: Start a factory with binding B-2. Check B-2 at proposal dispatch.
        from durai.factory import factory_graph
        from durai.trading import seed
        from durai.harness import prepare_checkpoints
        from durai.files import NativeFixtureRunner
        from durai.generation import ROOT
        from durai.evaluation_consumer import LocalProtectedEvaluator
        from durai.release import ReleaseController
        registry = seed('durai_test')
        prepare_checkpoints('durai_test')
        runner = NativeFixtureRunner(ROOT / 'private/foundation-test-workspaces')
        controller = ReleaseController(registry, runner)
        target = 'binding-target-' + uid()
        controller.initialize_target(target, 'TEST')
        run = uid()
        captured = []
        def proposer(state):
            # Sample: Receive B-2 once. Return the known repaired fixture for evaluation.
            captured.append(state.get('binding_id'))
            return 'repaired-v3'
        with factory_graph(registry, LocalProtectedEvaluator(registry, runner), controller, proposer) as graph:
            result = graph.invoke({'run_id': run, 'target_id': target, 'binding_id': 'B-2'}, {'configurable': {'thread_id': 'foundation-binding:' + run}})
        self.assertEqual(captured, ['B-2'])
        self.assertEqual(result['version_id'], 'repaired-v3')
