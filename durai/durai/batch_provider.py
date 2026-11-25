"""Sample: Share 40 provider attempts across jobs A and B. Refuse attempt 21 for A and stop before the deadline."""
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from .common import Conflict, uid
from .execution_budget import BudgetStop


@contextmanager
def counter_connection(path):
    # Sample: Open C-1, commit one reservation, and close its Windows file handle before returning.
    with closing(sqlite3.connect(path, timeout=10)) as connection:
        with connection:
            yield connection


class BatchCounters:
    def __init__(self, path, deadline, total_limit=40, job_limit=20, request_seconds=180):
        # Sample: Open counter C-1 with a 40-attempt total and a 20-attempt job limit. Preserve existing reservations.
        self.path, self.deadline = Path(path), deadline
        self.total_limit, self.job_limit, self.request_seconds = total_limit, job_limit, request_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with counter_connection(self.path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS attempt(id text PRIMARY KEY,job text NOT NULL,started real NOT NULL,finished real,status text)')
            connection.execute('CREATE TABLE IF NOT EXISTS blocked(reason text NOT NULL)')

    def reserve(self, job):
        # Sample: Reserve A-1 before its HTTP call. Refuse it if A used 20 attempts or less than 180 seconds remain.
        if time.time() + self.request_seconds > self.deadline:
            raise BudgetStop('Model-call budget is exhausted: batch deadline')
        with counter_connection(self.path) as connection:
            connection.execute('BEGIN IMMEDIATE')
            blocked = connection.execute('SELECT reason FROM blocked LIMIT 1').fetchone()
            if blocked:
                raise Conflict('Batch stopped: ' + blocked[0])
            total = connection.execute('SELECT count(*) FROM attempt').fetchone()[0]
            count = connection.execute('SELECT count(*) FROM attempt WHERE job=?', (job,)).fetchone()[0]
            if total >= self.total_limit or count >= self.job_limit:
                raise BudgetStop('Model-call budget is exhausted: physical provider cap')
            identity = uid()
            connection.execute('INSERT INTO attempt VALUES(?,?,?,NULL,NULL)', (identity, job, time.time()))
            return identity

    def finish(self, identity, status, blocked=None):
        # Sample: Finish A-1 with status SUCCESS. Record a blocked reason on an authentication error.
        with counter_connection(self.path) as connection:
            connection.execute('UPDATE attempt SET finished=?,status=? WHERE id=?', (time.time(), status, identity))
            if blocked:
                connection.execute('INSERT INTO blocked VALUES(?)', (blocked,))

    def status(self):
        # Sample: Read three reserved module attempts and two migration attempts. Return total 5 and both job counts.
        with counter_connection(self.path) as connection:
            jobs = dict(connection.execute('SELECT job,count(*) FROM attempt GROUP BY job'))
            blocked = connection.execute('SELECT reason FROM blocked LIMIT 1').fetchone()
            attempts = [{'job': job, 'started': started, 'finished': finished, 'status': status,
                         'seconds': finished - started if finished else None}
                        for job, started, finished, status in connection.execute(
                            'SELECT job,started,finished,status FROM attempt ORDER BY started')]
        return {'total': sum(jobs.values()), 'jobs': jobs, 'blocked': blocked[0] if blocked else None,
                'attempts': attempts}


class BatchProviderModel(BaseChatModel):
    inner: Any
    counters: Any
    job: str
    bound: Any = None

    @property
    def _llm_type(self):
        # Sample: Wrap DeepSeek Flash for job A. Return the batch provider type.
        return 'durai-batch-provider'

    def bind_tools(self, tools, **kwargs):
        # Sample: Bind read_file and edit_file. Keep job A and its persistent counter.
        return self.model_copy(update={'bound': self.inner.bind_tools(tools, **kwargs)})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # Sample: Reserve one HTTP attempt for A. Return its reply. Stop the batch on a provider error without echoing its body.
        identity = self.counters.reserve(self.job)
        try:
            message = (self.bound or self.inner).invoke(messages, stop=stop, **kwargs)
        except Exception as error:
            status = getattr(error, 'status_code', None)
            kind = 'PROVIDER_ERROR_' + str(status) if isinstance(status, int) else 'PROVIDER_ERROR'
            self.counters.finish(identity, kind, kind)
            raise Conflict('Batch stopped: ' + kind) from None
        self.counters.finish(identity, 'SUCCESS')
        return ChatResult(generations=[ChatGeneration(message=message)])
