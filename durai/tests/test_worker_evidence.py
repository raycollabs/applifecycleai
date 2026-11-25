"""Sample: Refuse an unchanged worker, retain redacted tool evidence, and pass a corrected second attempt."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage, ToolMessage
from durai.common import Conflict, digest
from durai.db import prepare
from durai.harness import prepare_checkpoints
from durai.runtime_deepagents import DeepAgentsWorker
from durai.worker_evidence import retain_worker_trace
from test_dev_loop import FIX, LoopHarness, loop_spec
from test_lessons import RecordingModel
from test_workspace_worker import FILES, SequenceModel, task


class WorkerEvidenceTests(unittest.TestCase):
    def setUp(self):
        # Sample: Create separate work and evidence folders for test task T-1.
        self.temporary = tempfile.TemporaryDirectory(prefix='durai-worker-evidence-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.task = task()

    def test_completed_no_change_is_rejected_with_durable_trace(self):
        # Sample: Return final text with no edit for A-1. Check refusal and the retained final text after worker cleanup.
        worker = DeepAgentsWorker(SequenceModel(steps=[], replies=[]), self.root / 'work', 'A-1',
                                  evidence_root=self.root / 'evidence')
        with self.assertRaisesRegex(Conflict, 'no declared file change'):
            worker.execute(self.task, dict(FILES))
        path = Path(worker.worker_evidence['path'])
        record = json.loads(path.read_bytes())
        self.assertEqual(record['changedPaths'], [])
        self.assertEqual(record['messages'][-1]['content'], 'Scripted edit complete.')
        worker.close()
        self.assertTrue(path.is_file())
        self.assertEqual(list((self.root / 'work').rglob('*')), [])

    def test_trace_redacts_provider_keys_and_excludes_thinking(self):
        # Sample: Return synthetic key K-1 in visible text and a thinking block. Check redaction and no saved thinking text.
        key = 'nvapi-synthetic-evidence-key-123456'
        messages = [AIMessage(content=[{'type': 'thinking', 'thinking': 'PRIVATE-THINKING-TEXT'},
                                       {'type': 'text', 'text': 'The configured key is ' + key}]),
                    ToolMessage(content='Read result with ' + key, tool_call_id='read-1', name='read_file')]
        with patch('durai.model_routes.providers', return_value=[{'apiKey': key}]):
            receipt = retain_worker_trace(self.task, 'A-2', messages, [], self.root / 'evidence')
        content = Path(receipt['path']).read_text()
        self.assertNotIn(key, content)
        self.assertNotIn('PRIVATE-THINKING-TEXT', content)
        self.assertIn('[REDACTED]', content)

    def test_successful_edit_keeps_tool_result_and_digest(self):
        # Sample: Edit a.py from 199 to 200 in A-3. Check its saved call, tool result, changed path, and exact trace digest.
        steps = [('edit_file', {'file_path': '/workspace/a.py', 'old_string': 'return 199', 'new_string': 'return 200'})]
        worker = DeepAgentsWorker(SequenceModel(steps=steps, replies=[]), self.root / 'work', 'A-3',
                                  evidence_root=self.root / 'evidence')
        worker.execute(self.task, dict(FILES))
        receipt = worker.export_evidence(self.task)['workerEvidence']
        record = json.loads(Path(receipt['path']).read_bytes())
        checksum = record.pop('digest')
        self.assertEqual(digest(record), checksum)
        self.assertEqual(checksum, receipt['digest'])
        self.assertEqual(record['changedPaths'], ['/workspace/a.py'])
        call = next(entry['tool_calls'][0] for entry in record['messages'] if entry.get('tool_calls'))
        self.assertEqual(call['args']['new_string'], 'return 200')
        tool = next(entry for entry in record['messages'] if entry['role'] == 'tool')
        self.assertEqual((tool['name'], tool['status']), ('edit_file', 'success'))
        self.assertTrue(tool['content'])
        worker.close()
        self.assertTrue(Path(receipt['path']).is_file())


    def test_trace_retains_safe_completion_metadata(self):
        # Sample: Save max_tokens with 16000 output and reasoning tokens. Check the counts and no raw provider or thinking data.
        message = AIMessage(content=[{'type': 'thinking', 'thinking': 'PRIVATE-THINKING-TEXT'}],
            response_metadata={'stop_reason': 'max_tokens', 'untrusted_payload': 'DO-NOT-RETAIN'},
            usage_metadata={'input_tokens': 100, 'output_tokens': 16000, 'total_tokens': 16100,
                            'output_token_details': {'reasoning': 16000}})
        receipt = retain_worker_trace(self.task, 'A-4', [message], [], self.root / 'evidence')
        content = Path(receipt['path']).read_text()
        record = json.loads(content)
        self.assertEqual(record['messages'][0]['completion'],
                         {'stopReason': 'max_tokens', 'inputCount': 100, 'outputCount': 16000, 'reasoningCount': 16000, 'unit': 'tokens'})
        self.assertNotIn('PRIVATE-THINKING-TEXT', content)
        self.assertNotIn('DO-NOT-RETAIN', content)

    def test_truncated_completion_is_rejected_with_specific_evidence(self):
        # Sample: End A-5 at its output token limit before any edit. Check a token-limit error and the retained completion state.
        worker = DeepAgentsWorker(SequenceModel(steps=[], replies=[]), self.root / 'work', 'A-5',
                                  evidence_root=self.root / 'evidence')
        message = AIMessage(content=[], response_metadata={'stop_reason': 'max_tokens'},
                            usage_metadata={'input_tokens': 100, 'output_tokens': 16000, 'total_tokens': 16100})
        with patch.object(worker, 'graph') as graph:
            graph.return_value.invoke.return_value = {'messages': [message]}
            with self.assertRaisesRegex(Conflict, 'output token limit'):
                worker.execute(self.task, dict(FILES))
        record = json.loads(Path(worker.worker_evidence['path']).read_bytes())
        self.assertEqual(record['messages'][-1]['completion']['stopReason'], 'max_tokens')
        self.assertEqual(record['changedPaths'], [])


class WorkerEvidenceLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test and checkpoint tables for a two-attempt scripted loop.
        prepare('durai_test')
        prepare_checkpoints('durai_test')

    def test_no_change_feedback_reaches_next_attempt(self):
        # Sample: Finish attempt 1 without edits. Apply FIX in attempt 2. Check one evaluation, concrete retry feedback, and review.
        harness = LoopHarness({1: [], 2: [FIX]})
        models = {}

        def model(attempt):
            # Sample: Return an unchanged model for attempt 1 and a share-conversion edit for attempt 2.
            models[attempt] = RecordingModel(steps=harness.scripts[attempt], replies=[], prompts=[])
            return models[attempt]

        harness.model = model
        with patch.object(harness.evaluator, 'evaluate', wraps=harness.evaluator.evaluate) as evaluate:
            result = harness.run(loop_spec())
        self.assertEqual(result['outcome'], 'PASSED')
        self.assertEqual([row['verdict'] for row in result['history']], ['FAIL', 'PASS'])
        self.assertIn('no declared file change', result['history'][0]['error'])
        self.assertIn('no declared file change', models[2].prompts[0])
        self.assertEqual(evaluate.call_count, 1)
        self.assertNotIn('package', result['history'][0])


if __name__ == '__main__':
    unittest.main()
