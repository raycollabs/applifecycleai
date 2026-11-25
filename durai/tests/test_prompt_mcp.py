"""Sample: Open a build workspace with the enhancer on and off. Call get_enhanced_request. Check the text, one enhancement row, and no tool when off."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from durai.build_samples import JsonSampleSource
from durai.build_tools import BuildTools
from durai.db import connect, prepare
from durai.prompt_enhancer import Enhancer
from durai.prompt_store import prepare_prompt

from prompt_fixtures import DATABASE
from test_prompt_enhancer import make_world


def count_rows(label):
    # Sample: Count the enhancement rows of build label L-1. Return 1 after one call and 0 when the tool is off.
    with connect('postgres', DATABASE) as db:
        return db.execute('SELECT count(*) AS n FROM durai_prompt.enhancement WHERE build_label=%s', (label,)).fetchone()['n']


class McpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test with prompt_0001. Insert one unique test kind with concepts, items, templates and skills.
        prepare(DATABASE)
        prepare_prompt(DATABASE)
        cls.world = make_world()

    def setUp(self):
        # Sample: Make an empty workspace folder.
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / 'ws'

    def test_get_enhanced_request_records_enhancement(self):
        # Sample: Call the tool for S-TRADE-BOOK. Check text and one enhancement row with its digest.
        label = 'L-ON-' + self.world['kind']
        enhancer = Enhancer.for_database(DATABASE, manifests=(), kinds=[self.world['kind'], self.world['decoy']])
        tools = BuildTools(self.root, enhance=True, enhancer=enhancer, build_label=label)
        tools.call('get_sample', {'sampleId': 'S-TRADE-BOOK'})
        reply = tools.call('get_enhanced_request', {})
        request = JsonSampleSource().get('S-TRADE-BOOK')['requestText']
        self.assertFalse(reply['isError'], reply['text'])
        self.assertTrue(reply['text'].startswith(request + '\n'))
        self.assertIn('Durai recorded enhancement', reply['text'])
        self.assertEqual(1, count_rows(label))
        with connect('postgres', DATABASE) as db:
            row = db.execute('SELECT * FROM durai_prompt.enhancement WHERE build_label=%s', (label,)).fetchone()
        text = reply['text'].rsplit('\n[', 1)[0]
        self.assertEqual((hashlib.sha256(text.encode('utf-8')).hexdigest(), 'S-TRADE-BOOK', self.world['kind']),
                         (row['enhanced_digest'], row['sample_id'], row['kind_key']))
        self.assertIn('enhanced', tools.call('get_guidance', {'topic': 'enhanced'})['text'].lower())

    def test_enhance_off_returns_plain_request(self):
        # Sample: Call with enhance off. Check text equals the sample text and zero rows.
        label = 'L-OFF-' + self.world['kind']
        tools = BuildTools(self.root, enhance=False, build_label=label)
        request = JsonSampleSource().get('S-TRADE-BOOK')['requestText']
        self.assertEqual(['get_sample'], [item['name'] for item in tools.tool_list() if item['name'] == 'get_sample'])
        self.assertNotIn('get_enhanced_request', [item['name'] for item in tools.tool_list()])
        self.assertEqual(11, len(tools.tool_list()))
        text = tools.call('get_sample', {'sampleId': 'S-TRADE-BOOK'})['text']
        self.assertTrue(text.endswith('Request: ' + request))
        self.assertTrue(tools.call('get_enhanced_request', {})['isError'])
        self.assertTrue(tools.call('get_guidance', {'topic': 'enhanced'})['isError'])
        self.assertNotIn('get_enhanced_request', tools.call('get_guidance', {})['text'])
        self.assertEqual(0, count_rows(label))


if __name__ == '__main__':
    unittest.main()
