"""Sample: Reserve two model calls for T-1. Use both on M-1. Refuse M-2 before its provider call."""
import time
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from .common import Conflict, uid


class ProviderFault(Conflict):
    state = 'PROVIDER'


class BudgetStop(Conflict):
    state = 'BUDGET'


class CandidateFault(Conflict):
    state = 'CANDIDATE'


def finish_notice(remaining):
    # Sample: Read 2 remaining model calls. Return a notice that names 2 calls and tells the worker to end the turn.
    return (str(remaining) + ' model calls remain after this one. Write any declared file that still needs its change, '
            'then end the turn. Do not read more files.')


TRANSIENT_STATUS = (408, 429, 500, 502, 503, 504)
TRANSIENT_NAMES = ('APIConnectionError', 'OpenAIConnectionError', 'ConnectError', 'ReadError', 'RemoteProtocolError',
                   'ConnectionResetError')
TIMEOUT_NAMES = ('APITimeoutError', 'ConnectTimeout', 'ReadTimeout', 'TimeoutError', 'WriteTimeout', 'PoolTimeout')
TRANSIENT_TEXT = ('UNAVAILABLE', 'RESOURCE_EXHAUSTED', 'overloaded', 'Connection error')


def transient_error(error):
    # Sample: Read a connection reset, a 503, a read timeout, and a 400. Return True, True, False, and False.
    seen = error
    while seen is not None:
        if type(seen).__name__ in TIMEOUT_NAMES:
            return False
        status = getattr(seen, 'status_code', None) or getattr(seen, 'code', None)
        if status in TRANSIENT_STATUS or type(seen).__name__ in TRANSIENT_NAMES:
            return True
        if any(text in str(seen) for text in TRANSIENT_TEXT):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


class BudgetedModel(BaseChatModel):
    inner: Any
    ledger: Any
    budget_id: str
    attempt_id: str
    owner: str
    generation: int
    label: str = 'model'
    lease_seconds: int = 600
    limit: Any = None
    retry_delays: tuple = (2, 4, 8)
    bound: Any = None

    @property
    def _llm_type(self):
        # Sample: Wrap route M-1 for attempt T-1 1. Return the budgeted model type.
        return 'durai-budgeted-model'

    def bind_tools(self, tools, **kwargs):
        # Sample: Bind read_file and edit_file to M-1. Keep the same budget and owner.
        return self.model_copy(update={'bound': self.inner.bind_tools(tools, **kwargs)})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Renew W-1 generation 1. Reserve call 19 of limit 20. Add the 1-call finish notice. Call M-1. Reconcile 1.
        self.ledger.renew(self.attempt_id, self.owner, self.generation, self.lease_seconds)
        reservation = self.attempt_id + ':' + self.label + ':' + uid()
        if not self.ledger.reserve(self.budget_id, reservation, 'modelCalls', 1):
            raise BudgetStop('Model-call budget is exhausted')
        if self.limit:
            remaining = self.limit - (self.ledger.usage(self.budget_id).get('modelCalls') or 0)
            if remaining <= 2:
                messages = list(messages) + [HumanMessage(content=finish_notice(remaining))]
        try:
            message = self.call_provider(messages, stop, **kwargs)
        finally:
            self.ledger.reconcile(self.budget_id, reservation, 1)
        calls = len(getattr(message, 'tool_calls', None) or [])
        if calls:
            tools = reservation + ':tools'
            if not self.ledger.reserve(self.budget_id, tools, 'toolCalls', calls):
                raise BudgetStop('Tool-call budget is exhausted')
            self.ledger.reconcile(self.budget_id, tools, calls)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def call_provider(self, messages, stop=None, **kwargs):
        # Sample: Get a connection reset twice, then a reply. Wait 2 s and 4 s. Return the reply. Get a 400. Raise Provider error.
        tries = 0
        while True:
            retry_reservation = None
            if tries:
                retry_reservation = self.attempt_id + ':' + self.label + ':retry:' + uid()
                if not self.ledger.reserve(self.budget_id, retry_reservation, 'modelCalls', 1):
                    raise BudgetStop('Model-call budget is exhausted before provider retry')
            try:
                return (self.bound or self.inner).invoke(messages, stop=stop, **kwargs)
            except Conflict:
                raise
            except Exception as error:
                if not transient_error(error) or tries >= len(self.retry_delays):
                    raise ProviderFault('Provider error after ' + str(tries + 1) + ' tries: ' + type(error).__name__ + ': ' +
                                   str(error)[:300]) from None
                time.sleep(self.retry_delays[tries])
                tries += 1
                self.ledger.renew(self.attempt_id, self.owner, self.generation, self.lease_seconds)
            finally:
                if retry_reservation is not None:
                    self.ledger.reconcile(self.budget_id, retry_reservation, 1)
