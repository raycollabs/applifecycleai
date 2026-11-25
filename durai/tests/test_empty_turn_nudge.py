"""Sample: Script an empty first reply, nudge the worker once, and accept the edit on the second reply."""
import tempfile
import unittest
from pathlib import Path
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from durai.common import Conflict
from durai.runtime_adapter import invoke
from durai.runtime_deepagents import DeepAgentsWorker
from test_workspace_worker import FILES, task

EDIT = {'name': 'edit_file', 'id': 'edit-1', 'args': {'file_path': '/workspace/a.py', 'old_string': 'return 199', 'new_string': 'return 200'}}


class ScriptedModel(BaseChatModel):
    script: list = []
    seen: list = []

    @property
    def _llm_type(self):
        # Sample: Select the scripted nudge model. Make no external request.
        return 'durai-nudge-probe'

    def bind_tools(self, tools, **kwargs):
        # Sample: Bind the file tools. Return the same model.
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Return reply 1 (empty). Record the human texts. Return reply 2 (edit_file). Return an empty reply after the script ends.
        self.seen.append([str(message.content) for message in messages if message.type == 'human'])
        step = self.script[len(self.seen) - 1] if len(self.seen) <= len(self.script) else None
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[step] if step else []))])


class TestEmptyTurnNudge(unittest.TestCase):
    def setUp(self):
        # Sample: Create an empty scratch folder and task T-1 that permits a.py and b.py.
        self.scratch = Path(tempfile.mkdtemp(prefix='durai-nudge-'))
        self.task = task()

    def worker(self, script):
        # Sample: Script replies [empty, edit]. Return the worker, the model, and the worker nudge list.
        model = ScriptedModel(script=script, seen=[])
        return DeepAgentsWorker(model, self.scratch), model

    def test_empty_reply_gets_nudge_then_edit(self):
        # Sample: Reply 1 is empty with stop. Reply 2 edits a.py to 200. Check 1 nudge, 3 model calls (empty, edit, finish), a.py changed, and the candidate state.
        worker, model = self.worker([None, EDIT])
        result = invoke(worker, 'execute', self.task, dict(FILES))
        self.assertEqual(result['state'], 'CANDIDATE')
        self.assertIn(b'return 200', result['files']['/workspace/a.py'])
        self.assertEqual(len(model.seen), 3)
        self.assertEqual(len(worker.nudges), 1)
        self.assertIn('Your last reply was empty', model.seen[1][-1])
        self.assertIn('/workspace/a.py', model.seen[1][-1])
        self.assertIn('edit_file', model.seen[1][-1])

    def test_nudge_limit_two(self):
        # Sample: Script 3 empty replies. Check 2 nudges, 3 model calls, and "no declared file change".
        worker, model = self.worker([None, None, None])
        with self.assertRaisesRegex(Conflict, 'no declared file change'):
            invoke(worker, 'execute', self.task, dict(FILES))
        self.assertEqual(len(worker.nudges), 2)
        self.assertEqual(len(model.seen), 3)

    def test_nudge_recorded(self):
        # Sample: Script 1 empty reply then an edit. Check one WORKER_NUDGED payload with nudge 1 and empty true.
        worker, model = self.worker([None, EDIT])
        invoke(worker, 'execute', self.task, dict(FILES))
        self.assertEqual(worker.nudges, [{'nudge': 1, 'empty': True}])


if __name__ == '__main__':
    unittest.main()
