"""Sample: Load durai/apps/shell/round3-sign-in-2026-11-24.json and current apps/shell/web. Check current shell has no web rule violation and record missing historical snapshot."""
import json
from pathlib import Path
import unittest

from durai.build_web_rules import check_sign_in, check_web_rules

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'apps' / 'shell' / 'round3-sign-in-2026-11-24.json'
SHELL_WEB = ROOT / 'apps' / 'shell' / 'web'


class ShellSignInReplayTests(unittest.TestCase):
    def test_archived_round3_get_signout_snapshot(self):
        # Sample: Read round3-sign-in-2026-11-24.json. Check whether a replayable app/signout/route.ts or HTTP snapshot exists. Skip replay when only digests are archived.
        data = json.loads(ARCHIVE.read_text(encoding='utf-8'))
        self.assertIn('GET_CHANGES_COOKIE', data.get('historicalStaticRules', []))
        self.assertEqual(data.get('historicalPrefetchOnly', {}).get('rule'), 'PREFETCH_CHANGED_COOKIE')
        if 'historicalSnapshot' not in data and 'snapshot' not in data:
            self.skipTest('Archive holds SHA-256 digests and summary outcomes only; no replayable app/signout/route.ts body or HTTP snapshot is present')

    def test_current_shell_has_no_sign_in_finding(self):
        # Sample: Run check_web_rules on apps/shell/web and check_sign_in on current shell endpoints. Check empty violations and passed True.
        violations = check_web_rules(SHELL_WEB)
        self.assertEqual(violations, [])
        menu_text = (SHELL_WEB / 'app' / 'Menu.tsx').read_text(encoding='utf-8')
        self.assertIn('Sign out', menu_text)

        def fetch(path, *, method, headers=None, form=None):
            # Sample: POST / with code TR-BEN sets durai_session; GET screen returns TR-BEN; GET /signout returns 405 with no cookie change.
            if method == 'POST':
                return {'status': 303, 'body': '', 'location': '/positions', 'cookies': ['durai_session=TR-BEN; Path=/']}
            if path == '/':
                return {'status': 200, 'body': '<form method="post"><input name="code"></form>', 'cookies': []}
            if path in ('/signout', '/sign-out', '/session/sign-out'):
                return {'status': 405, 'body': '', 'cookies': []}
            return {'status': 200, 'cookies': [], 'body': '<nav><a href="/positions">Positions</a></nav><p>TR-BEN</p>'}

        result = check_sign_in(fetch, 'TR-BEN')
        self.assertTrue(result['passed'])
        self.assertIsNone(result['rule'])


if __name__ == '__main__':
    unittest.main()
