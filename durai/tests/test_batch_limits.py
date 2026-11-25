"""Sample: Share a physical attempt counter across models, enforce a deadline, and hide a failed provider body."""
from pathlib import Path
import tempfile
import time
import unittest
from langchain_core.messages import HumanMessage
from durai.batch_provider import BatchCounters, BatchProviderModel
from durai.common import Conflict
from test_budget_finish import FlakyModel, StatusError


class BatchLimitTests(unittest.TestCase):
    def test_models_share_physical_limit(self):
        # Sample: Allow two requests total and one per job. Call A and B once. Refuse a second A without an HTTP call.
        with tempfile.TemporaryDirectory() as folder:
            counter = BatchCounters(Path(folder) / 'counts.sqlite', time.time() + 600, 2, 1)
            inner = FlakyModel([])
            for job in ('A', 'B'):
                BatchProviderModel(inner=inner, counters=counter, job=job).invoke([HumanMessage('go')])
            with self.assertRaisesRegex(Conflict, 'physical provider cap'):
                BatchProviderModel(inner=inner, counters=counter, job='A').invoke([HumanMessage('go')])
            self.assertEqual((inner.calls, counter.status()['total']), (2, 2))

    def test_deadline_refuses_request_before_http(self):
        # Sample: Leave 30 seconds before the deadline with a 180-second request timeout. Check zero HTTP calls.
        with tempfile.TemporaryDirectory() as folder:
            counter = BatchCounters(Path(folder) / 'counts.sqlite', time.time() + 30)
            inner = FlakyModel([])
            with self.assertRaisesRegex(Conflict, 'batch deadline'):
                BatchProviderModel(inner=inner, counters=counter, job='A').invoke([HumanMessage('go')])
            self.assertEqual((inner.calls, counter.status()['total']), (0, 0))

    def test_provider_failure_blocks_batch_without_body(self):
        # Sample: Get HTTP 401 with a private response body. Check one request, a blocked batch, and no body in feedback.
        with tempfile.TemporaryDirectory() as folder:
            counter = BatchCounters(Path(folder) / 'counts.sqlite', time.time() + 600)
            error = StatusError(401)
            error.args = ('PRIVATE-PROVIDER-BODY',)
            inner = FlakyModel([error])
            model = BatchProviderModel(inner=inner, counters=counter, job='A')
            with self.assertRaises(Conflict) as raised:
                model.invoke([HumanMessage('go')])
            self.assertNotIn('PRIVATE-PROVIDER-BODY', str(raised.exception))
            with self.assertRaisesRegex(Conflict, 'Batch stopped'):
                model.invoke([HumanMessage('go')])
            self.assertEqual((inner.calls, counter.status()['total']), (1, 1))
