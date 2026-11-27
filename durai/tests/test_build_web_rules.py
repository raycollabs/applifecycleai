"""Sample: Run WebRuleTests. Check the sign-out handler of round 3 is refused and a POST-only handler is accepted."""
import tempfile
import unittest
from pathlib import Path

from durai.build_web_rules import check_sign_in, check_web_rules, describe

SIGNOUT_GET = (
    'import { NextResponse } from "next/server";\n'
    'export function GET(request: Request): NextResponse {\n'
    '  const answer = NextResponse.redirect(new URL("/", request.url));\n'
    '  answer.cookies.set("trader_code", "", { path: "/", maxAge: 0 });\n'
    '  return answer;\n'
    '}\n')
NESTED = (
    'export function POST(request: Request) {\n'
    '  return NextResponse.redirect(new URL(signOutPath(), request.url), 303);\n'
    '}\n')
POST_ONLY = 'export async function POST(): Promise<Response> {\n  return Response.json({ ok: true });\n}\n'


class WebRuleTests(unittest.TestCase):
    def workspace(self, text):
        # Sample: Make a folder with app/signout/route.ts holding the given text. Return the folder.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        target = Path(folder.name, 'app', 'signout', 'route.ts')
        target.parent.mkdir(parents=True)
        target.write_text(text, encoding='utf-8')
        return folder.name

    def test_get_handler_that_clears_the_cookie_is_refused(self):
        # Sample: Check the round 3 sign-out handler. Check the rules GET_CHANGES_COOKIE and ABSOLUTE_REDIRECT.
        found = check_web_rules(self.workspace(SIGNOUT_GET))
        self.assertEqual(['ABSOLUTE_REDIRECT', 'GET_CHANGES_COOKIE'], sorted(item['rule'] for item in found))

    def test_post_only_handler_is_accepted(self):
        # Sample: Check a handler with POST only. Check no violation and the text web rules: ok.
        found = check_web_rules(self.workspace(POST_ONLY))
        self.assertEqual([], found)
        self.assertEqual('web rules: ok', describe(found))

    def test_redirect_with_a_nested_call_is_refused(self):
        # Sample: Check new URL(signOutPath(), request.url) in a POST handler. Check the rule ABSOLUTE_REDIRECT.
        found = check_web_rules(self.workspace(NESTED))
        self.assertEqual(['ABSOLUTE_REDIRECT'], [item['rule'] for item in found])

    def test_workspace_without_app_folder_is_accepted(self):
        # Sample: Check an empty folder. Check no violation.
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual([], check_web_rules(folder))

    def test_check_sign_in_discovers_non_trading_routes_when_paths_is_none(self):
        # Sample: Sign in redirects to /inventory with links to /checkout and /ledger. With paths None check both discovered screens pass without /positions.
        def fake_fetch(path, method='GET', headers=None, form=None):
            if path == '/' and method == 'GET':
                if (headers or {}).get('Cookie'):
                    return {'status': 307, 'body': '', 'cookies': [], 'location': '/inventory'}
                return {'status': 200, 'body': '<form method="post" action="/"><input name="trader"><input name="secret"></form>', 'cookies': []}
            if path == '/' and method == 'POST':
                return {'status': 303, 'body': '', 'cookies': ['durai_session=tok123; Path=/'], 'location': '/inventory'}
            if path in ('/inventory', '/checkout', '/ledger') and method == 'GET':
                if not (headers or {}).get('Cookie'):
                    return {'status': 200, 'body': 'Signed out', 'cookies': []}
                return {'status': 200, 'body': f'Trader {form or ""} TR-BEN <nav><a href="/inventory">Inventory</a><a href="/checkout">Checkout</a><a href="/ledger">Ledger</a></nav>', 'cookies': []}
            return {'status': 404, 'body': 'Not found', 'cookies': []}

        result = check_sign_in(fake_fetch, 'TR-BEN', 's', paths=None)
        self.assertTrue(result['passed'], result)
        self.assertEqual(['/inventory', '/checkout', '/ledger'], result['screens'])


if __name__ == '__main__':
    unittest.main()
