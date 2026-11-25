"""Sample: Permit a.py and b.py. Edit both with a scripted model. Refuse c.py and the holdout read."""
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from durai.common import Conflict, uid
from durai.runtime_adapter import invoke
from durai.runtime_deepagents import DeepAgentsWorker
from durai.task_contract import build_task
from durai.workspace_manifest import capture, manifest, materialize, verify
from test_task_contract import feature_spec

FILES = {'/workspace/a.py': b'def a():\n    # Sample: Return 199 shares.\n    return 199\n',
         '/workspace/b.py': b'def b():\r\n    # Sample: Return 99 shares.\r\n    return 99\r\n',
         '/workspace/README.md': b'Position notes\r\n',
         '/workspace/asset.bin': b'\x00\xff\r\n\x1a',
         '/workspace/tests/holdout.py': b'EXPECTED_SHARES = 200\n'}


class SequenceModel(BaseChatModel):
    steps: list = []
    replies: list = []
    calls: int = 0

    @property
    def _llm_type(self):
        # Sample: Select the scripted sequence model. Make no external request.
        return 'durai-sequence-probe'

    def bind_tools(self, tools, **kwargs):
        # Sample: Bind read_file, edit_file, and write_file. Return the same model.
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Send step 1 as a tool call. Read its result. Send step 2. Finish after the last step.
        self.calls += 1
        results = [message for message in messages if message.type == 'tool']
        self.replies[:] = [str(message.content) for message in results]
        if len(results) < len(self.steps):
            name, arguments = self.steps[len(results)]
            message = AIMessage(content='', tool_calls=[{'name': name, 'id': 'step-' + str(len(results)), 'args': arguments}])
        else:
            message = AIMessage(content='Scripted edit complete.')
        return ChatResult(generations=[ChatGeneration(message=message)])


def task():
    # Sample: Seal FEATURE T-1 for deepagents at the installed version. Permit a.py and b.py.
    return build_task(feature_spec(taskId='T-' + uid()[:8],
                                   runtime={'name': 'deepagents', 'version': DeepAgentsWorker.version}))['task']


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        # Sample: Create an empty scratch folder for one test.
        self.scratch = Path(tempfile.mkdtemp(prefix='durai-ws-'))

    def test_binary_files_keep_exact_bytes(self):
        # Sample: Keep asset.bin with 00 FF 0D 0A 1A and CRLF b.py. Materialize, capture, and restore. Check equal bytes.
        first = materialize(self.scratch / 'one', FILES)
        captured = capture(self.scratch / 'one')
        self.assertEqual(captured, FILES)
        restored = materialize(self.scratch / 'two', captured)
        self.assertEqual(restored['digest'], first['digest'])
        self.assertEqual(verify(self.scratch / 'two', first)['digest'], first['digest'])

    def test_symlink_and_extra_files_are_refused(self):
        # Sample: Add extra.py after capture. Check a verification failure. Add a symlink. Check refusal.
        expected = materialize(self.scratch / 'one', FILES)
        (self.scratch / 'one/workspace/extra.py').write_bytes(b'x = 1\n')
        with self.assertRaisesRegex(Conflict, 'extra.py'):
            verify(self.scratch / 'one', expected)
        with self.assertRaises(Conflict):
            materialize(self.scratch / 'one', FILES)
        (self.scratch / 'one/workspace/link.py').write_bytes(b'')
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda path: path.name == 'link.py' or original(path)):
            with self.assertRaisesRegex(Conflict, 'symbolic link'):
                capture(self.scratch / 'one')

    def test_parallel_tasks_have_separate_roots(self):
        # Sample: Materialize T-1 and T-2. Write a.py with 1 in T-1 and 2 in T-2. Check separate contents.
        materialize(self.scratch / 'T-1', FILES)
        materialize(self.scratch / 'T-2', FILES)
        (self.scratch / 'T-1/workspace/a.py').write_bytes(b'1')
        (self.scratch / 'T-2/workspace/a.py').write_bytes(b'2')
        self.assertEqual(capture(self.scratch / 'T-1')['/workspace/a.py'], b'1')
        self.assertEqual(capture(self.scratch / 'T-2')['/workspace/a.py'], b'2')


class DeepAgentsWorkerTests(unittest.TestCase):
    def setUp(self):
        # Sample: Create an empty scratch folder and seal T-1.
        self.scratch = Path(tempfile.mkdtemp(prefix='durai-da-'))
        self.task = task()

    def run_steps(self, steps, unchanged=False):
        # Sample: Run two scripted steps through the Deep Agents adapter. Return the result and the model.
        model = SequenceModel(steps=steps, replies=[])
        worker = DeepAgentsWorker(model, self.scratch)
        if unchanged:
            with self.assertRaisesRegex(Conflict, 'no declared file change'):
                invoke(worker, 'execute', self.task, dict(FILES))
            return None, model
        return invoke(worker, 'execute', self.task, dict(FILES)), model

    def test_declared_file_set_is_enforced(self):
        # Sample: Permit a.py and b.py. Write c.py and /workspace/../x.py. Check two errors and no created file.
        result, model = self.run_steps([('write_file', {'file_path': '/workspace/c.py', 'content': 'x = 1\n'}),
                                        ('write_file', {'file_path': '/workspace/../x.py', 'content': 'x = 1\n'})], unchanged=True)
        self.assertIsNone(result)
        self.assertIn('permission denied', model.replies[0])
        self.assertIn('Error', model.replies[1])
        self.assertEqual(list(self.scratch.rglob('*')), [])

    def test_protected_evaluator_is_unreadable(self):
        # Sample: Deny /workspace/tests/**. Read holdout.py. Check an error and no value 200 in the reply.
        result, model = self.run_steps([('read_file', {'file_path': '/workspace/tests/holdout.py'})], unchanged=True)
        self.assertIn('permission denied', model.replies[0])
        self.assertNotIn('200', model.replies[0])
        self.assertIsNone(result)

    def test_two_declared_files_change_in_one_task(self):
        # Sample: Edit a.py from 199 to 200 and b.py from 99 to 100. Check both paths in one candidate.
        result, model = self.run_steps([
            ('edit_file', {'file_path': '/workspace/a.py', 'old_string': 'return 199', 'new_string': 'return 200'}),
            ('edit_file', {'file_path': '/workspace/b.py', 'old_string': 'return 99', 'new_string': 'return 100'})])
        self.assertEqual(result['candidate']['changed'], ['/workspace/a.py', '/workspace/b.py'])
        self.assertIn(b'return 200', result['files']['/workspace/a.py'])
        self.assertEqual(result['files']['/workspace/b.py'], b'def b():\r\n    # Sample: Return 99 shares.\r\n    return 100\r\n')
        self.assertEqual(result['files']['/workspace/asset.bin'], FILES['/workspace/asset.bin'])
        self.assertEqual(result['toolCalls'], 2)
        self.assertEqual(result['candidate']['manifestDigest'], manifest(result['files'])['digest'])

    def test_comments_allow_only_sample_walkthroughs(self):
        # Sample: Add a comment about difficulty to a.py. Check candidate refusal.
        with self.assertRaisesRegex(Conflict, 'sample walkthrough'):
            self.run_steps([('edit_file', {'file_path': '/workspace/a.py', 'old_string': 'return 199',
                                           'new_string': 'return 200  # This fix is easy.'})])

    def test_instructions_request_code_changes(self):
        # Sample: Build instructions for T-1. Check that code changes are expected and the comment rule names comments only.
        from durai.runtime_deepagents import instructions
        text = instructions(self.task, FILES)
        self.assertIn('Code changes are expected', text)
        self.assertIn('applies to comments and docstrings only', text)
        self.assertIn('define a helper inside the function that uses it', text)
        self.assertNotIn('Only sample walkthrough comments and docstrings are allowed', text)

    def test_unsupported_recovery_is_declared(self):
        # Sample: Call resume on the adapter. Check UNSUPPORTED and zero model calls.
        model = SequenceModel(steps=[], replies=[])
        result = invoke(DeepAgentsWorker(model, self.scratch), 'resume', self.task, dict(FILES))
        self.assertEqual((result['state'], result['runtime']), ('UNSUPPORTED', 'deepagents'))
        self.assertEqual(model.calls, 0)


if __name__ == '__main__':
    unittest.main()
