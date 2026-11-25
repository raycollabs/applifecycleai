"""Sample: Return tool call "functions/edit_file" from a fake model. Check the worker runs edit_file."""
from typing import Any
import unittest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from durai.worker_harness import IsolatedModel, UndeclaredToolRequest

ARGS = {'file_path': '/workspace/durai/payment_funds.py', 'old_string': 'a', 'new_string': 'b'}


class FakeModel(BaseChatModel):
    name_sent: str = 'edit_file'
    tools: Any = None

    @property
    def _llm_type(self):
        # Sample: Build the fake model. Return the type name fake.
        return 'fake'

    def bind_tools(self, tools, **kwargs):
        # Sample: Bind edit_file. Return the same fake model.
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Send one call with name functions/edit_file and id C-1.
        message = AIMessage(content='', tool_calls=[{'name': self.name_sent, 'args': ARGS, 'id': 'C-1'}])
        return ChatResult(generations=[ChatGeneration(message=message)])


def wrapped(name):
    # Sample: Wrap a fake model that sends name functions/edit_file. Return the bound isolated model.
    fake = FakeModel(name_sent=name)
    return IsolatedModel(inner=fake, bound=fake)


class WorkerHarnessTests(unittest.TestCase):
    def test_namespace_prefix_is_normalized(self):
        # Sample: Fake model returns call "functions/edit_file" for payment_funds.py. Check call name edit_file, same args, and metadata ["functions/edit_file"].
        for sent in ('functions/edit_file', 'functions.edit_file'):
            message = wrapped(sent).invoke([HumanMessage(content='Fix funds.')])
            self.assertEqual('edit_file', message.tool_calls[0]['name'])
            self.assertEqual(ARGS, message.tool_calls[0]['args'])
            self.assertEqual([sent], message.response_metadata['duraiToolNamesNormalized'])

    def test_prefixed_undeclared_tool_is_refused(self):
        # Sample: Fake model returns call "functions/execute". Check UndeclaredToolRequest with requestedTool "functions/execute".
        with self.assertRaises(UndeclaredToolRequest) as caught:
            wrapped('functions/execute').invoke([HumanMessage(content='Fix funds.')])
        self.assertEqual('functions/execute', caught.exception.requestedTool)


if __name__ == '__main__':
    unittest.main()
