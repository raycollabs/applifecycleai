import unittest

from durai.build_web_rules import check_sign_in


class SignInBehaviorTests(unittest.TestCase):
    def fetch(self, path, *, method, headers=None, form=None):
        # Sample: Read the sign-in form. Post TR-BEN and get a session cookie.
        if method == 'POST':
            self.assertEqual(form['code'], 'TR-BEN')
            self.assertEqual(form['$ACTION_ID_one'], '')
            return {'status': 303, 'body': '', 'location': '/positions', 'cookies': ['trader_code=fixture; Path=/']}
        if path == '/':
            return {'status': 200, 'body': '<form method="post"><input name="$ACTION_ID_one" type="hidden">'
                    '<input name="code"></form>', 'cookies': []}
        if path == '/signout':
            return {'status': 405, 'body': '', 'cookies': ['trader_code=; Max-Age=0'] if self.old else []}
        self.assertEqual(headers['Cookie'], 'trader_code=fixture')
        return {'status': 200, 'cookies': [],
                'body': '<nav><a href="/extra">Extra</a></nav><p>TR-BEN</p><script>"Signed out"</script>'}

    def test_current_session_survives_navigation(self):
        # Sample: Sign in as TR-BEN. Check four known screens and one new menu screen.
        self.old = False
        result = check_sign_in(self.fetch, 'TR-BEN')
        self.assertTrue(result['passed'])
        self.assertEqual(result['screens'], ['/positions', '/book', '/prices', '/cash', '/extra'])

    def test_prefetch_get_cannot_end_session(self):
        # Sample: Return a cookie deletion from GET /signout. Check a prefetch failure.
        self.old = True
        result = check_sign_in(self.fetch, 'TR-BEN')
        self.assertEqual((result['passed'], result['rule']), (False, 'PREFETCH_CHANGED_COOKIE'))
