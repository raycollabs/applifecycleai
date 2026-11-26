"""Sample: Run DetectorTests. Check that compare requests fire and build requests do not."""
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai import compare_intent  # noqa: E402
from durai.compare_intent import CompareTemplateError, compose, detect, enhance, load_template  # noqa: E402

HOOK = Path(__file__).resolve().parents[1] / 'tools' / 'enhance_hook.py'
COMPARE = 'Openshell from nvidia is opensourced. Compare with Durai and tell me what we lack. Also compare github.com/acme/agentbox.'
BUILD = 'Let a trader book a trade. If it is sent twice, count it once.'


class DetectorTests(unittest.TestCase):
    def test_compare_github_repo_fires(self):
        # Sample: "compare Durai with github.com/acme/agentbox" returns a detection with the URL as a target.
        found = detect('compare Durai with github.com/acme/agentbox')
        self.assertEqual('comparative-research', found['kind'])
        self.assertIn('github.com/acme/agentbox', found['targets'])

    def test_compare_framework_fires(self):
        # Sample: "Compare with the LangGraph framework, what do we lack" returns target LangGraph.
        self.assertIn('LangGraph', detect('Compare with the LangGraph framework, what do we lack')['targets'])

    def test_replace_with_tool_fires(self):
        # Sample: "Can we replace our runner with the OpenShell tool?" fires.
        self.assertIsNotNone(detect('Can we replace our runner with the OpenShell tool?'))

    def test_plain_build_request_is_ignored(self):
        # Sample: The booking request returns None.
        self.assertIsNone(detect(BUILD))

    def test_one_tool_question_is_ignored(self):
        # Sample: "How do I install the OpenShell tool?" returns None.
        self.assertIsNone(detect('How do I install the OpenShell tool?'))

    def test_compare_without_a_target_noun_is_ignored(self):
        # Sample: "Compare the two dates on the invoice" returns None.
        self.assertIsNone(detect('Compare the two dates on the invoice'))

    def test_opt_out_env_disables(self):
        # Sample: DURAI_ENHANCE=0 turns a compare request into None.
        self.assertIsNone(detect(COMPARE, environ={'DURAI_ENHANCE': '0'}))
        self.assertIsNotNone(detect(COMPARE, environ={}))


class ComposeTests(unittest.TestCase):
    def setUp(self):
        # Sample: Detect the compare request. Compose its text.
        self.template = load_template()
        self.text = compose(COMPARE, detect(COMPARE), self.template)

    def test_request_stays_first(self):
        # Sample: The text starts with the exact request.
        self.assertTrue(self.text.startswith(COMPARE))

    def test_all_ten_sections_named(self):
        # Sample: Each of the ten section titles appears with its number.
        self.assertEqual(10, len(self.template['sections']))
        for number, section in enumerate(self.template['sections'], 1):
            self.assertIn(str(number) + '. ' + section['title'] + ':', self.text)

    def test_verdict_words_and_cost_scale_present(self):
        # Sample: ADOPT, ADAPT, KEEP, SKIP, REPLACE and S = under 1 day appear.
        for word in ('ADOPT', 'ADAPT', 'KEEP', 'SKIP', 'REPLACE', 'S = under 1 day', 'M = 2 to 5 days', 'L = more than 1 week'):
            self.assertIn(word, self.text)

    def test_targets_are_named(self):
        # Sample: The line Targets named lists the repository URL without its final period, then Openshell. The word Also is not a target.
        self.assertIn('Targets named: github.com/acme/agentbox, Openshell\n', self.text)

    def test_same_input_same_digest(self):
        # Sample: Two runs give one digest. The digest equals SHA-256 of the text.
        first, second = enhance(COMPARE), enhance(COMPARE)
        self.assertEqual(first['enhancedDigest'], second['enhancedDigest'])
        self.assertEqual(hashlib.sha256(first['text'].encode('utf-8')).hexdigest(), first['enhancedDigest'])

    def test_bad_template_digest_is_refused(self):
        # Sample: Change the title of section 1 in a copy. Load the copy. Raise CompareTemplateError.
        changed = json.loads(json.dumps(self.template))
        changed['sections'][0]['title'] = 'Changed'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'template.json'
            path.write_text(json.dumps(changed), encoding='utf-8')
            with self.assertRaises(CompareTemplateError):
                load_template(path)

    def test_build_request_returns_none(self):
        # Sample: enhance of the booking request returns None.
        self.assertIsNone(enhance(BUILD))


class HookTests(unittest.TestCase):
    def call(self, stdin):
        # Sample: Run the hook script with stdin text. Return the exit code and stdout.
        done = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, timeout=30)
        return done.returncode, done.stdout

    def test_hook_prints_additional_context(self):
        # Sample: A compare prompt gives JSON with additionalContext and a systemMessage. The context does not repeat the request.
        code, out = self.call(json.dumps({'prompt': COMPARE}))
        data = json.loads(out)
        self.assertEqual(0, code)
        self.assertEqual('Durai compare format applied', data['systemMessage'])
        context = data['hookSpecificOutput']['additionalContext']
        self.assertIn('Answer with these sections', context)
        self.assertNotIn(COMPARE, context)

    def test_hook_accepts_a_byte_order_mark(self):
        # Sample: Windows PowerShell adds a BOM before the JSON. The compare prompt still gives additionalContext.
        done = subprocess.run([sys.executable, str(HOOK)], input=('﻿' + json.dumps({'prompt': COMPARE})).encode('utf-8'),
                              capture_output=True, timeout=30)
        self.assertIn('additionalContext', done.stdout.decode('utf-8'))

    def test_hook_silent_for_other_requests(self):
        # Sample: The booking request gives no output and exit 0.
        self.assertEqual((0, ''), self.call(json.dumps({'prompt': BUILD})))

    def test_hook_fails_open(self):
        # Sample: Broken stdin gives no output and exit 0.
        self.assertEqual((0, ''), self.call('{not json'))

    def test_hook_makes_no_network_call(self):
        # Sample: Block socket creation. Run enhance. Check no connection is tried.
        original = socket.socket
        socket.socket = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network used'))
        try:
            self.assertIsNotNone(enhance(COMPARE))
        finally:
            socket.socket = original


class BoundaryTests(unittest.TestCase):
    def test_compare_data_is_free_other_prompt_data_is_paid(self):
        # Sample: Classify the compare template as OPEN and kinds.json as PAID_PACK. The first rule that matches wins.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
        import boundary_scan
        rules, _ = boundary_scan.load_rules()
        self.assertEqual('OPEN', boundary_scan.classify('durai/design/prompt/compare/template.json', rules))
        self.assertEqual('PAID_PACK', boundary_scan.classify('durai/design/prompt/kinds.json', rules))


class EnhancerWiringTests(unittest.TestCase):
    def enhancer(self):
        # Sample: Build an offline enhancer from the seed files with no baseline terms. It records nothing.
        from durai.prompt_enhancer import Enhancer
        return Enhancer.offline(term_sets=[])

    def test_compare_request_returns_compare_kind(self):
        # Sample: The compare request returns kind comparative-research, mode RULES, zero model calls and the compare text.
        result = self.enhancer().enhance(COMPARE)
        self.assertEqual('comparative-research', result['record']['kind'])
        self.assertEqual((0, 'RULES'), (result['record']['modelCalls'], result['record']['mode']))
        self.assertTrue(result['text'].startswith(COMPARE))

    def test_build_request_path_unchanged(self):
        # Sample: The booking request still classifies as idempotent-command and its text starts with the request.
        result = self.enhancer().enhance(BUILD)
        self.assertEqual('idempotent-command', result['record']['kind'])
        self.assertTrue(result['text'].startswith(BUILD))

    def test_record_without_recorder_is_refused(self):
        # Sample: Record on with an offline enhancer raises ValueError.
        with self.assertRaises(ValueError):
            self.enhancer().enhance(COMPARE, record=True)


if __name__ == '__main__':
    unittest.main()
