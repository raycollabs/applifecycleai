"""Sample: Offer only three file tools. Run no subagent."""
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from deepagents import HarnessProfile, GeneralPurposeSubagentProfile, register_harness_profile
from .common import Conflict

ALLOWED_TOOLS = frozenset({'read_file', 'edit_file', 'write_file'})


class UndeclaredToolRequest(Conflict):
    def __init__(self, tool):
        # Sample: Request tool python. Keep the message and set requestedTool to python. Cut a 300-character name to 80 characters.
        super().__init__('Worker requested an undeclared tool')
        self.requestedTool = str(tool)[:80]


class IsolatedModel(BaseChatModel):
    model_name: str = 'durai:isolated-coder'
    inner: Any
    bound: Any = None
    extra: frozenset = frozenset()

    @property
    def _llm_type(self):
        # Sample: Build the isolated coder. Select its fixed harness identity.
        return 'durai-isolated-coder'

    def bind_tools(self, tools, **kwargs):
        # Sample: Offer read_file and edit_file. Reject task or execute before dispatch.
        names = {getattr(tool, 'name', None) for tool in tools}
        if not names <= ALLOWED_TOOLS | self.extra:
            raise Conflict('Worker exposes an undeclared tool')
        return self.model_copy(update={'bound': self.inner.bind_tools(tools, **kwargs)})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Forward one approved edit request. Preserve the returned tool call.
        if self.bound is None:
            raise Conflict('Worker tools must be bound before inference')
        message = self.bound.invoke(messages, stop=stop, **kwargs)
        declared, renamed, calls = ALLOWED_TOOLS | self.extra, [], []
        for call in message.tool_calls:
            name = call['name']
            for prefix in ('functions/', 'functions.'):
                if name.startswith(prefix) and name[len(prefix):] in declared:
                    renamed.append(name)
                    call = dict(call, name=name[len(prefix):])
            calls.append(call)
        if renamed:
            metadata = dict(message.response_metadata or {}, duraiToolNamesNormalized=renamed)
            message = message.model_copy(update={'tool_calls': calls, 'response_metadata': metadata})
        refused = next((call['name'] for call in message.tool_calls if call['name'] not in declared), None)
        if refused is not None:
            raise UndeclaredToolRequest(refused)
        return ChatResult(generations=[ChatGeneration(message=message)])


def isolated_model(model, extra=()):
    # Sample: Disable the default default agent. Keep only declared file tools. Add describe_table. Refuse run_sql.
    from .context_tools import TOOL_NAMES
    if not set(extra) <= set(TOOL_NAMES):
        raise Conflict('Worker exposes an undeclared tool')
    register_harness_profile('durai:isolated-coder', HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_tools=frozenset({'task', 'execute', 'ls', 'glob', 'grep', 'delete', 'write_todos'})))
    return IsolatedModel(inner=model, extra=frozenset(extra))
