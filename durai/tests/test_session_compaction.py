"""Sample: Publish 4 segment nodes for turns 1 to 48. Merge them. Check one parent with all references."""
import json
import os
import unittest
from unittest.mock import patch

from durai.common import uid
from durai.db import connect, server
from durai.session_compaction import head_tokens, top_nodes
from durai.session_context import SessionContext, prepare_session_context
from durai.session_summarizer import DEEPSEEK_FLASH, SessionSummaryWorker, SummaryCompletion

DATABASE = 'durai_test'
REFS = ('93a7a7b', 'durai/session_summarizer.py', 'session-context-phase-a-20261120.json')


def estimate(text):
    # Sample: Count 400 UTF-8 bytes as 100 estimated tokens.
    return max(1, -(-len(text.encode('utf-8')) // 4))


class PairStore(SessionContext):
    def claim(self):
        # Sample: Claim at most 12 turns per call.
        return super().claim(max_turns=12, max_tokens=2000)


class Scripted:
    def __init__(self, goal='Segment.'):
        # Sample: Reply with goal text of the given size. Keep each request.
        self.goal, self.contents = goal, []

    def complete(self, system, content, profile):
        # Sample: Read turns 13 to 24. Return a summary with those turns and the required references.
        self.contents.append(content)
        request = json.loads(content)
        turns = sorted({row['turn'] for row in request.get('turns', [])}
                       | {turn for child in request.get('children', []) for turn in child['sourceTurns']})
        return SummaryCompletion(json.dumps({'goal': self.goal, 'decisions': [], 'corrections': [], 'openQuestions': [],
                                             'nextAction': 'Go.', 'sourceTurns': turns,
                                             'refs': request['requiredRefs']}), 'stop', 100, 50)


class CompactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Require the disposable connection descriptor. Apply both migrations.
        target = server()
        if (os.getenv('DURAI_SESSION_ISOLATED') != '1' or target['host'] != '127.0.0.1' or target['port'] == 54410
                or 'durai-evaldb-' not in str(target['settings'])):
            raise unittest.SkipTest('Run with the disposable session context verifier')
        prepare_session_context(DATABASE)

    def setUp(self):
        # Sample: Start with an empty queue, flag on, and default caps.
        with connect('postgres', DATABASE) as db:
            db.execute('TRUNCATE durai_context.session_node,durai_context.session_attempt,durai_context.session_pending,'
                       'durai_context.session_summary,durai_context.session_turn,durai_context.session_conversation,'
                       'durai_context.session_spend,durai_context.session_cap_event')
        patcher = patch('durai.session_settings.read_record', return_value={'enabled': True})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conversation = 'c-' + uid()
        self.context = SessionContext(DATABASE)
        self.store = PairStore(DATABASE, 'durai_evaluator')

    def append(self, count, text=lambda number: f'Turn {number}.'):
        # Sample: Append 24 flushed turns with text for each number.
        for number in range(1, count + 1):
            self.context.append_turn(self.conversation, f'm-{number}', 'user', text(number), 5, flush=True)

    def worker(self, provider):
        # Sample: Build a worker whose references are the known values found in the claimed turns.
        return SessionSummaryWorker(self.store, provider, DEEPSEEK_FLASH, estimate, lambda claim: [
            ref for ref in REFS if any(ref in (turn['text'] or '') for turn in claim['turns'])])

    def test_segment_summary_has_no_prior_head(self):
        # Sample: Publish segment 1 to 12. Claim 13 to 24. Check the prompt has turns 13 to 24 and no first summary text.
        self.append(24)
        provider = Scripted('FIRST-SEGMENT-TEXT')
        worker = self.worker(provider)
        worker.run_once()
        worker.run_once()
        second = json.loads(provider.contents[1])
        self.assertEqual(list(range(13, 25)), [row['turn'] for row in second['turns']])
        self.assertNotIn('FIRST-SEGMENT-TEXT', provider.contents[1])
        self.assertNotIn('priorSummary', second)

    def test_four_segments_merge_to_parent(self):
        # Sample: Publish 4 level-0 nodes for turns 1 to 48 with 3 references. Check one level-1 node with all 3.
        refs = {5: REFS[0], 21: REFS[1], 37: REFS[2]}
        self.append(48, lambda number: f'Turn {number}. {refs.get(number, "")}')
        provider = Scripted()
        worker = self.worker(provider)
        for _ in range(4):
            worker.run_once()
        self.assertEqual(5, len(provider.contents))
        nodes = self.store.read_nodes(self.conversation)['nodes']
        parents = [node for node in nodes if node['level'] == 1]
        self.assertEqual([(1, 48)], [(node['first'], node['last']) for node in parents])
        self.assertEqual(sorted(REFS), parents[0]['body']['refs'])
        self.assertEqual([1], [node['level'] for node in top_nodes(nodes)])
        self.assertEqual(sorted(REFS), self.context.get_context(self.conversation)['summary']['refs'])

    def test_head_fits_visible_limit(self):
        # Sample: Publish 6 level-0 nodes of 240 tokens. Check the head is at most 800 tokens and covers all turns.
        self.append(72)
        worker = self.worker(Scripted('x' * 600))
        for _ in range(6):
            worker.run_once()
        head = self.context.get_context(self.conversation)
        self.assertEqual(72, head['coveredTurn'])
        self.assertEqual(list(range(1, 73)), head['summary']['sourceTurns'])
        self.assertLessEqual(estimate(json.dumps(head['summary'], sort_keys=True)), 800)
        nodes = self.store.read_nodes(self.conversation)['nodes']
        self.assertLessEqual(head_tokens(top_nodes(nodes), estimate), 800)

    def test_spend_counts_provider_calls_only(self):
        # Sample: Claim a range, then release it with no provider call. Check session_spend calls 0.
        self.append(1)
        claim = self.store.claim()
        self.assertEqual('failed', self.store.finish(claim, 'failed')['state'])
        self.assertEqual(0, self.store.read_spend(self.conversation)['dayCalls'])
        with connect('postgres', DATABASE) as db:
            self.assertEqual(0, db.execute('SELECT coalesce(sum(calls),0) AS n FROM durai_context.session_spend').fetchone()['n'])


if __name__ == '__main__':
    unittest.main()
