"""Sample: Prepare durai_test with bench_0007. Insert review R-1, supersede it with R-2, post the review form, and read the rows."""
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

import psycopg

from durai.bench_store import prepare_bench
from durai.common import uid
from durai.dashboard_server import serve
from durai.db import connect, prepare, transaction

DATABASE = 'durai_test'
PORT = 54432
BASE = 'http://127.0.0.1:' + str(PORT)


def insert(review_id, decision, comment, supersedes=None, app_key=None, component='trade-booking'):
    # Sample: Insert review R-1 for trade-booking FUNCTION with decision APPROVED and comment "Booking works" as durai_evaluator.
    with transaction('durai_evaluator', DATABASE) as db:
        db.execute("INSERT INTO durai_bench.human_review(review_id,app_key,app_version,component_key,aspect,decision,score,comment,reviewer,"
                   "supersedes_review_id) VALUES(%s,%s,'E6',%s,'FUNCTION',%s,3,%s,'user',%s)",
                   (review_id, app_key, component, decision, comment, supersedes))


class HumanReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test with migrations up to bench_0007.
        prepare(DATABASE)
        prepare_bench(DATABASE)

    def test_review_is_immutable(self):
        # Sample: Insert review R-1 trade-booking FUNCTION APPROVED "Booking works". Update its comment. Check refusal. Insert R-2 supersedes R-1 CHANGES_REQUESTED. Check accepted.
        token, key = uid()[:8], 'trade-pilot-' + uid()[:8]
        first, second = 'R-1-' + token, 'R-2-' + token
        insert(first, 'APPROVED', 'Booking works', app_key=key)
        with self.assertRaises(psycopg.Error):
            with transaction('durai_evaluator', DATABASE) as db:
                db.execute("UPDATE durai_bench.human_review SET comment='Changed' WHERE review_id=%s", (first,))
        with self.assertRaises(psycopg.errors.RestrictViolation):
            with connect('postgres', DATABASE) as admin:
                admin.execute("UPDATE durai_bench.human_review SET comment='Changed' WHERE review_id=%s", (first,))
        with self.assertRaises(psycopg.errors.RestrictViolation):
            with connect('postgres', DATABASE) as admin:
                admin.execute('DELETE FROM durai_bench.human_review WHERE review_id=%s', (first,))
        insert(second, 'CHANGES_REQUESTED', 'Retry counted twice', supersedes=first, app_key=key)
        with transaction('durai_reviewer', DATABASE) as db:
            rows = db.execute('SELECT review_id, comment FROM durai_bench.human_review WHERE app_key=%s ORDER BY created_at', (key,)).fetchall()
        self.assertEqual([(first, 'Booking works'), (second, 'Retry counted twice')], [(row['review_id'], row['comment']) for row in rows])

    def test_latest_view_uses_supersede(self):
        # Sample: Insert R-1 APPROVED, then R-2 superseding R-1 REJECTED. Check the view shows REJECTED only.
        token, key = uid()[:8], 'trade-pilot-' + uid()[:8]
        insert('R-1-' + token, 'APPROVED', 'Booking works', app_key=key)
        insert('R-2-' + token, 'REJECTED', 'Booking loses a lot', supersedes='R-1-' + token, app_key=key)
        with transaction('durai_reviewer', DATABASE) as db:
            rows = db.execute('SELECT review_id, decision FROM durai_bench.human_review_latest WHERE app_key=%s', (key,)).fetchall()
        self.assertEqual([('R-2-' + token, 'REJECTED')], [(row['review_id'], row['decision']) for row in rows])

    def test_comment_does_not_replace_decision(self):
        # Sample: Insert R-1 APPROVED "Booking works", then R-2 COMMENT_ONLY "Looks slow". Check the view still shows R-1 APPROVED.
        token, key = uid()[:8], 'trade-pilot-' + uid()[:8]
        insert('R-1-' + token, 'APPROVED', 'Booking works', app_key=key)
        insert('R-2-' + token, 'COMMENT_ONLY', 'Looks slow', app_key=key)
        with transaction('durai_reviewer', DATABASE) as db:
            rows = db.execute('SELECT review_id, decision FROM durai_bench.human_review_latest WHERE app_key=%s', (key,)).fetchall()
        self.assertEqual([('R-1-' + token, 'APPROVED')], [(row['review_id'], row['decision']) for row in rows])

    def test_builder_role_reads_no_human_review(self):
        # Sample: Connect as durai_build_reader. Select human_review. Check permission denied.
        for table in ('human_review', 'human_review_latest', 'review_comment'):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with connect('postgres', DATABASE) as db:
                    db.execute('SET ROLE durai_build_reader')
                    db.execute('SELECT count(*) FROM durai_bench.' + table)


class ReviewPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test and start the dashboard on port 54432.
        prepare(DATABASE)
        prepare_bench(DATABASE)
        ready = threading.Event()

        def connected(server):
            cls.server = server
            ready.set()
        cls.thread = threading.Thread(target=serve, args=(PORT,), kwargs={'ready': connected, 'database': DATABASE}, daemon=True)
        cls.thread.start()
        if not ready.wait(5):
            raise RuntimeError('Dashboard test server did not start')
        cls.cookies = urllib.request.HTTPCookieProcessor()
        cls.opener = urllib.request.build_opener(cls.cookies)

    @classmethod
    def tearDownClass(cls):
        # Sample: Stop the dashboard.
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def post(self, fields, origin=BASE):
        # Sample: Post the fields with the Origin header and return the answer.
        request = urllib.request.Request(BASE + '/review', data=urllib.parse.urlencode(fields).encode(), method='POST',
                                         headers={'Origin': origin, 'Content-Type': 'application/x-www-form-urlencoded'})
        return self.opener.open(request)

    def test_form_inserts_review(self):
        # Sample: Post app deepseek-flash-r2, component trade-booking, aspect FUNCTION, decision APPROVED, score 3, comment "Retry counted once". Check one row with reviewer user. Post with a foreign Origin. Check 403 and no second row.
        attempt, comment = 'deepseek-flash-r2', 'Retry counted once ' + uid()[:8]
        page = self.opener.open(BASE + '/review').read().decode()
        self.assertIn('http://127.0.0.1:54350', page)
        self.assertIn('http://127.0.0.1:54360', page)
        token = next(cookie.value for cookie in self.cookies.cookiejar if cookie.name == 'durai_dashboard')
        fields = {'csrf': token, 'target': 'build', 'component': 'trade-booking', 'attempt_id': attempt, 'aspect': 'FUNCTION',
                  'decision': 'APPROVED', 'score': '3', 'comment': comment}
        answer = self.post(fields)
        self.assertEqual(200, answer.status)
        self.assertIn(comment, answer.read().decode())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(fields, origin='http://evil.example')
        self.assertEqual(403, caught.exception.code)
        with transaction('durai_reviewer', DATABASE) as db:
            rows = db.execute('SELECT app_key, attempt_id, component_key, aspect, decision, score, reviewer FROM durai_bench.human_review '
                              'WHERE comment=%s', (comment,)).fetchall()
        self.assertEqual([('deepseek-flash-r2', attempt, 'trade-booking', 'FUNCTION', 'APPROVED', 3, 'user')],
                         [tuple(row.values()) for row in rows])


if __name__ == '__main__':
    unittest.main()
