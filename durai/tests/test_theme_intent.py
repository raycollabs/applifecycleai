"""Sample: Run the theme tests. A build request for a screen gets four theme options. A code question gets nothing."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai import theme_intent  # noqa: E402
from durai.theme_intent import ThemeCatalogError, compose, detect, enhance, load_catalog  # noqa: E402

HOOK = Path(__file__).resolve().parents[1] / 'tools' / 'enhance_hook.py'
BLOTTER = 'Build a trade blotter screen'
COMPARE = 'Compare Durai with github.com/acme/agentbox and tell me what we lack.'


class ThemeCatalogTests(unittest.TestCase):
    def test_digest_matches(self):
        # Sample: Load the catalog. The stamped digest equals the digest of its body. A changed word raises ThemeCatalogError.
        catalog = load_catalog()
        self.assertEqual(catalog['digest'], theme_intent.catalog_digest(catalog))
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / 'catalog.json'
            shutil.copy(theme_intent.CATALOG_PATH, copy)
            copy.write_text(copy.read_text(encoding='utf-8').replace('Enterprise admin', 'Enterprise adminx'), encoding='utf-8')
            with self.assertRaises(ThemeCatalogError):
                load_catalog(copy)

    def test_four_themes_have_tokens(self):
        # Sample: Four themes. Each has color, type, spacing and density tokens, layout, components and acceptance checks.
        themes = load_catalog()['themes']
        self.assertEqual(['enterprise-admin', 'transaction-heavy', 'report-heavy', 'app-lifecycle-manager'], [theme['id'] for theme in themes])
        for theme in themes:
            for key in ('color-bg', 'color-primary', 'font-family', 'font-size-base', 'space-unit', 'density'):
                self.assertIn(key, theme['tokens'])
            self.assertTrue(theme['layout'] and theme['components'] and theme['acceptance'])


class ThemeDetectTests(unittest.TestCase):
    def test_app_request_triggers(self):
        # Sample: Send "Build a trade blotter screen". Check the first theme is transaction-heavy.
        found = detect(BLOTTER)
        self.assertEqual(('ui-theme', 'transaction-heavy', False), (found['kind'], found['default'], found['chosenByUser']))

    def test_code_question_quiet(self):
        # Sample: Send "Why does this loop fail" and "Fix the bug in the dashboard". Check both return None.
        self.assertIsNone(detect('Why does this loop fail'))
        self.assertIsNone(detect('Fix the bug in the dashboard'))

    def test_user_choice_wins(self):
        # Sample: Send "Build a trade blotter screen theme: report-heavy". Check report-heavy is chosen by the user.
        found = detect(BLOTTER + ' theme: report-heavy')
        self.assertEqual(('report-heavy', True), (found['default'], found['chosenByUser']))

    def test_no_match_uses_catalog_default(self):
        # Sample: Send "Build an app for the team". Check the default enterprise-admin.
        self.assertEqual('enterprise-admin', detect('Build an app for the team')['default'])

    def test_other_themes_from_words(self):
        # Sample: "Create a reporting dashboard with charts" gives report-heavy. "Make a release pipeline app" gives app-lifecycle-manager.
        self.assertEqual('report-heavy', detect('Create a reporting dashboard with charts')['default'])
        self.assertEqual('app-lifecycle-manager', detect('Make a release pipeline app')['default'])


class ThemeComposeTests(unittest.TestCase):
    def test_user_words_first(self):
        # Sample: Send the blotter request. Check the text starts with the request and the request appears once.
        text = enhance(BLOTTER)['text']
        self.assertTrue(text.startswith(BLOTTER + '\n'))
        self.assertEqual(1, text.count(BLOTTER))

    def test_css_tokens_output(self):
        # Sample: Send the blotter request. Check the text holds ":root {" and "--color-primary: #0b5cab;" and lists all four theme ids.
        text = enhance(BLOTTER)['text']
        self.assertIn(':root {', text)
        self.assertIn('--color-primary: #0b5cab;', text)
        for theme_id in ('enterprise-admin', 'transaction-heavy', 'report-heavy', 'app-lifecycle-manager'):
            self.assertIn(theme_id, text)
        self.assertIn('transaction-heavy (default)', text)

    def test_off_switch(self):
        # Sample: Set DURAI_ENHANCE=0. Send the blotter request. Check enhance returns None.
        self.assertIsNone(enhance(BLOTTER, {'DURAI_ENHANCE': '0'}))

    def test_same_request_same_digest(self):
        # Sample: Enhance the blotter request twice. Check both enhanced digests are equal.
        self.assertEqual(enhance(BLOTTER)['enhancedDigest'], enhance(BLOTTER)['enhancedDigest'])

    def test_compose_marks_user_choice(self):
        # Sample: Compose with chosenByUser True for report-heavy. Check the text says "The user chose it."
        text = compose(BLOTTER, {'default': 'report-heavy', 'chosenByUser': True})
        self.assertIn('The user chose it.', text)


class ThemeHookTests(unittest.TestCase):
    def call(self, prompt):
        # Sample: Run the hook with a JSON prompt. Return the exit code and stdout.
        done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({'prompt': prompt}), capture_output=True, text=True, timeout=30)
        return done.returncode, done.stdout

    def test_hook_adds_themes_for_build_request(self):
        # Sample: Send the blotter request to the hook. Check the message is "Durai UI themes applied" and the context holds :root.
        code, out = self.call(BLOTTER)
        data = json.loads(out)
        self.assertEqual((0, 'Durai UI themes applied'), (code, data['systemMessage']))
        self.assertIn(':root {', data['hookSpecificOutput']['additionalContext'])

    def test_compare_request_keeps_compare_path(self):
        # Sample: Send a compare request. Check the message is the compare message.
        self.assertEqual('Durai compare format applied', json.loads(self.call(COMPARE)[1])['systemMessage'])

    def test_code_question_gives_no_output(self):
        # Sample: Send "Why does this loop fail". Check exit 0 and no output.
        self.assertEqual((0, ''), self.call('Why does this loop fail'))


class ThemeWiringTests(unittest.TestCase):
    def enhancer(self):
        # Sample: Build an offline enhancer with no baseline terms. It records nothing.
        from durai.prompt_enhancer import Enhancer
        return Enhancer.offline(term_sets=[])

    def test_enhancer_appends_theme_block(self):
        # Sample: Enhance the blotter request. Check the text starts with the request, holds the theme block and the record lists the catalog digest.
        result = self.enhancer().enhance(BLOTTER)
        self.assertTrue(result['text'].startswith(BLOTTER))
        self.assertIn('Durai UI theme options', result['text'])
        self.assertIn(load_catalog()['digest'], result['record']['templateDigests'])

    def test_compare_request_gets_no_theme_block(self):
        # Sample: Enhance a compare request. Check the text has no theme block.
        self.assertNotIn('Durai UI theme options', self.enhancer().enhance(COMPARE)['text'])


class ThemeBoundaryTests(unittest.TestCase):
    def test_catalog_is_open(self):
        # Sample: Classify the theme catalog as OPEN.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
        import boundary_scan
        rules, _ = boundary_scan.load_rules()
        self.assertEqual('OPEN', boundary_scan.classify('durai/design/prompt/themes/catalog.json', rules))


if __name__ == '__main__':
    unittest.main()
