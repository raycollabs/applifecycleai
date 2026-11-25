"""Sample: Refuse invalid edits before a write, record pinned decisions, and stop after three equal refusals."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from langchain_core.messages import ToolMessage
from durai.common import Conflict
from durai.decision_points import DecisionPoint, EDIT_RULES, edit_rules
from durai.edit_guard import EditGuardMiddleware, WorkerStalled
from durai.runtime_deepagents import DeepAgentsWorker
from durai.workspace_manifest import materialize
from durai.task_contract import build_task
from durai.qualification import BASELINE_BODY, CORRECT_BODY, FIXTURE, MODULE, load
from test_task_contract import feature_spec
from test_workspace_worker import SequenceModel


class DecisionPointTests(unittest.TestCase):
    def test_order_and_default(self):
        # Sample: Match 200 before the default. Refuse missing defaults, duplicate IDs, and a boolean for integer 1.
        rules = [{'id':'R-1','when':{'shares':200},'choice':'KEEP'}, {'id':'D','choice':'REVIEW'}]
        point = DecisionPoint('position', rules, lambda *args: None)
        self.assertEqual(point.decide({'shares':200})['choice'], 'KEEP')
        self.assertEqual(point.decide({'shares':100})['choice'], 'REVIEW')
        for invalid in ([], rules[:-1], [rules[-1], rules[0], rules[-1]]):
            with self.assertRaises(ValueError):
                DecisionPoint('position', invalid, lambda *args: None)
        point = DecisionPoint('position', [{'id':'R','when':{'shares':1},'choice':'KEEP'}, rules[-1]], lambda *args: None)
        self.assertEqual(point.decide({'shares':True})['choice'], 'REVIEW')

    def test_pins_and_event(self):
        # Sample: Reverse feature key order and change the caller rules. Check stable digests and unchanged saved events.
        rules, events = copy.deepcopy(EDIT_RULES), []
        point = DecisionPoint('edit', rules, lambda kind, payload: events.append((kind,payload)))
        features = {'hasFindings':False,'repeatLimitReached':False}
        first = point.decide(features)
        rules[-1]['choice'] = 'CHANGED'
        second = point.decide(dict(reversed(list(features.items()))))
        self.assertEqual(first, second)
        first['reasons'].append('CHANGED')
        self.assertEqual(events[0][0], 'DECISION_RECORDED')
        self.assertEqual(events[0][1]['reasons'], ['valid-proposal'])
        self.assertEqual(len(second['ruleDigest']),64)


class EditGuardTests(unittest.TestCase):
    def setUp(self):
        # Sample: Materialize the Task B shell and permit validate_change only.
        self.temporary = tempfile.TemporaryDirectory(prefix='durai-guard-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root, self.files = Path(self.temporary.name), load(FIXTURE/'workspace')
        materialize(self.root,self.files)
        self.task = build_task(feature_spec(writePaths=[MODULE],editScope={MODULE:['validate_change']}))['task']
        self.events, self.writes = [], []
        self.guard = EditGuardMiddleware(self.task,self.files,self.root,
                                        lambda kind,payload:self.events.append(payload))

    def call(self, old, new, identity='C-1', path=MODULE, replace_all=False):
        # Sample: Propose replacement C-1 in validate_change. Return one request with the full file tool arguments.
        return SimpleNamespace(tool_call={'id':identity,'name':'edit_file','args':{
            'file_path':path,'old_string':old,'new_string':new,'replace_all':replace_all}})

    def handler(self, request):
        # Sample: Apply one accepted replacement to the materialized file and record one write.
        call = request.tool_call
        args, target = call['args'], self.root / call['args']['file_path'].lstrip('/')
        target.write_text(target.read_text().replace(args['old_string'],args['new_string']),encoding='utf-8')
        self.writes.append(call['id'])
        return ToolMessage(content='applied',tool_call_id=call['id'])

    def test_scope_before_write(self):
        # Sample: Remove the module docstring. Check a tool error, a scope finding, and unchanged source bytes.
        original = self.files[MODULE].decode().splitlines()[0]+'\n'
        result = self.guard.wrap_tool_call(self.call(original,''),self.handler)
        self.assertEqual(result.status,'error')
        self.assertIn('module docstring changed',result.content)
        self.assertEqual(self.writes,[])
        self.assertEqual((self.root / MODULE.lstrip('/')).read_bytes(),self.files[MODULE])

    def test_comment_before_write(self):
        # Sample: Add an explanatory comment to a valid body. Check refusal and zero file writes.
        result = self.guard.wrap_tool_call(self.call(BASELINE_BODY,CORRECT_BODY+'    # This is easy.\n'),self.handler)
        self.assertEqual(result.status,'error')
        self.assertEqual(self.writes,[])

    def test_stalled_after_three(self):
        # Sample: Refuse the same invalid path twice. Restore the last decision and stop the third proposal.
        request = self.call('return True','return False',path='/workspace/../outside.py')
        for _ in range(2):
            self.assertEqual(self.guard.wrap_tool_call(request,self.handler).status,'error')
        restored = EditGuardMiddleware(self.task,self.files,self.root,lambda *args:None,previous=self.events[-1])
        with self.assertRaisesRegex(WorkerStalled,'STALLED'):
            restored.wrap_tool_call(request,self.handler)
        self.assertEqual(self.writes,[])

    def test_syntax_refusal_has_line_number(self):
        # Sample: Propose new_string "def f(:" after the module docstring. Check the refusal has the line number, offset, and "def f(:".
        new = 'def f(:' + chr(10) + BASELINE_BODY
        result = self.guard.wrap_tool_call(self.call(BASELINE_BODY, new), self.handler)
        line = (self.files[MODULE].decode().replace(BASELINE_BODY, new)).splitlines().index('def f(:') + 1
        self.assertEqual(result.status, 'error')
        self.assertIn('SyntaxError line ' + str(line) + ' offset ', result.content)
        self.assertIn('def f(:', result.content)
        self.assertEqual(self.writes, [])

    def test_valid_edit_and_replay(self):
        # Sample: Refuse a comment, then accept a valid body. Check one write and refusal count reset to zero.
        self.guard.wrap_tool_call(self.call(BASELINE_BODY,CORRECT_BODY+'    # This is easy.\n'),self.handler)
        self.assertEqual(self.guard.wrap_tool_call(self.call(BASELINE_BODY,CORRECT_BODY),self.handler).content,'applied')
        self.assertEqual(self.writes,['C-1'])
        self.assertEqual(self.guard.count,0)

    def test_stalled_worker_refuses_later_valid_write(self):
        # Sample: Refuse P-1 three times, then queue a valid edit. Check STALLED and zero writes for both proposals.
        bad = self.call('absent-A','x')
        for _ in range(2):
            self.guard.wrap_tool_call(bad,self.handler)
        with self.assertRaises(WorkerStalled):
            self.guard.wrap_tool_call(bad,self.handler)
        with self.assertRaises(WorkerStalled):
            self.guard.wrap_tool_call(self.call(BASELINE_BODY,CORRECT_BODY),self.handler)
        self.assertEqual(self.writes,[])

    def test_proposal_paths_and_replace(self):
        # Sample: Propose an absent match, an ambiguous match, and a path escape. Check no writes.
        for request in (self.call('not-present','x'),self.call('    ','  '),self.call('a','b',path='/workspace/../x.py')):
            self.assertEqual(self.guard.wrap_tool_call(request,self.handler).status,'error')
        self.assertEqual(self.writes,[])

    def test_replace_all_and_changed_refusal(self):
        # Sample: Refuse A twice, then B once. Check count 1. Replace two return values with replace_all and check one write.
        first = self.call('absent-A','x')
        for _ in range(2):
            self.guard.wrap_tool_call(first,self.handler)
        self.guard.wrap_tool_call(self.call('absent-B','x'),self.handler)
        self.assertEqual(self.guard.count,1)
        files = {'/workspace/a.py': b'def a():\n    # Sample: Return 1.\n    return 1\n\ndef b():\n    # Sample: Return 1.\n    return 1\n'}
        target = self.root/'other'
        materialize(target,files)
        task = build_task(feature_spec(writePaths=['/workspace/a.py']))['task']
        guard = EditGuardMiddleware(task,files,target,lambda *args:None)
        writes = []
        request = self.call('return 1','return 2',path='/workspace/a.py',replace_all=True)
        guard.wrap_tool_call(request,lambda value:writes.append(value))
        self.assertEqual(len(writes),1)

    def test_worker_stops_before_fourth_call(self):
        # Sample: Send three equal explanatory comments. Check STALLED at call 3 and no call 4.
        bad = ('edit_file',{'file_path':MODULE,'old_string':BASELINE_BODY,'new_string':CORRECT_BODY+'    # This is easy.\n'})
        model = SequenceModel(steps=[bad,bad,bad],replies=[])
        task = dict(self.task,runtime={'name':'deepagents','version':DeepAgentsWorker.version},
                    digest='test',pins={'contractDigest':'test'})
        with self.assertRaisesRegex(WorkerStalled,'STALLED'):
            DeepAgentsWorker(model,self.root/'worker').execute(task,self.files)
        self.assertEqual(model.calls,3)


class DecisionOntologyTests(unittest.TestCase):
    def test_resume_keeps_two_refusals(self):
        # Sample: Pause after two refused edits. Resume the checkpoint. Check STALLED on the next edit and three durable events.
        from langgraph.checkpoint.postgres import PostgresSaver
        from durai.db import connect,prepare
        from durai.harness import prepare_checkpoints
        from durai.qualification import task_b_spec,pins
        from durai.task_ledger import TaskLedger
        from durai.execution_events import replay
        from durai.common import uid
        prepare('durai_test')
        prepare_checkpoints('durai_test')
        task = build_task(task_b_spec('durai_test','sha256:test'))['task']
        ledger = TaskLedger('durai_test')
        run_id = 'guard-resume-'+uid()
        attempt = ledger.create_attempt(task,run_id,pins(task))
        ledger.claim(attempt['id'],'W-1',seconds=60)
        bad = ('edit_file',{'file_path':MODULE,'old_string':BASELINE_BODY,'new_string':CORRECT_BODY+'    # This is easy.\n'})

        class PauseAfterTwo(SequenceModel):
            def _generate(self,*args,**kwargs):
                # Sample: Return two tool calls. Stop before call 3 to preserve its checkpoint.
                if self.calls == 2:
                    raise RuntimeError('Pause after two refused edits')
                return super()._generate(*args,**kwargs)

        with tempfile.TemporaryDirectory(prefix='durai-guard-resume-') as scratch:
            with connect('durai_runtime','durai_test',autocommit=True) as connection:
                connection.execute('SET search_path=durai_checkpoints')
                saver = PostgresSaver(connection)
                first = DeepAgentsWorker(PauseAfterTwo(steps=[bad,bad,bad],replies=[]),scratch,attempt['id'],saver,'durai_test')
                with self.assertRaises(RuntimeError):
                    first.execute(task,load(FIXTURE/'workspace'))
                model = SequenceModel(steps=[bad,bad,bad],replies=[])
                second = DeepAgentsWorker(model,scratch,attempt['id'],saver,'durai_test')
                with self.assertRaisesRegex(WorkerStalled,'STALLED'):
                    second.resume(task,load(FIXTURE/'workspace'))
                self.assertFalse(second.root().exists())
        decisions = [row['payload'] for row in replay(run_id,database='durai_test') if row['kind']=='DECISION_RECORDED']
        self.assertEqual([row['choice'] for row in decisions],['REFUSED','REFUSED','STALLED'])

    def test_pinned_procedure_and_durable_event(self):
        # Sample: Bind G-9, accept one Task B edit, and read its exact rule digest in the durable decision event.
        from durai.db import prepare
        from durai.harness import prepare_checkpoints
        from durai.packs import PackStore,generic_pack
        from durai.policy_store import record_policies
        from durai.worker_contract import resolve_contract
        from durai.common import uid
        from durai.qualification import fixture_spec
        from tools.run_review_batch import run_job
        from durai.execution_events import replay
        prepare('durai_test')
        prepare_checkpoints('durai_test')
        store = PackStore('durai_test')
        ref = store.install(generic_pack('9'))
        record_policies(ref,'durai_test')
        identity = 'guard-9-'+uid()[:8]
        store.bind(identity,[ref],'SYNTHETIC_LOCAL')
        binding = {'bindingId':identity,'contractDigest':resolve_contract(identity,database='durai_test')['digest']}
        steps = [('edit_file',{'file_path':MODULE,'old_string':BASELINE_BODY,'new_string':CORRECT_BODY})]
        with tempfile.TemporaryDirectory(prefix='durai-guard-loop-') as scratch:
            result = run_job('task-b-change-manifest',lambda attempt:SequenceModel(steps=steps,replies=[]),Path(scratch),
                             binding=binding,candidate_root=Path(scratch)/'candidates')
        self.assertEqual(result['outcome'],'PASSED')
        decisions = [entry['payload'] for entry in replay(result['runId'],database='durai_test') if entry['kind']=='DECISION_RECORDED' and entry['payload']['point']=='edit-guard']
        self.assertEqual([row['choice'] for row in decisions],['APPLIED'])
        self.assertEqual(decisions[0]['ruleDigest'],DecisionPoint('edit-guard',EDIT_RULES,lambda *args:None).rule_digest)
        bad = {'binding':binding,'pins':{'contractDigest':'changed'}}
        with self.assertRaisesRegex(Conflict,'pin'):
            edit_rules(bad,'durai_test','durai_runtime')
