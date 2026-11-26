"""Sample: Run ChatContract on the Slack adapter and a fake adapter. Check signatures, threads, drafts and reviewer-only approval with no network call."""
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.adapters.chat.slack import SlackChat, sign  # noqa: E402
from durai.chat_bridge import ChatBridge, redact  # noqa: E402
from durai.ports.errors import InvalidSignature  # noqa: E402

TOKEN = 'xo' + 'xb-sample-test-token'
SECRET = 'sample-signing-secret'
NOW = 1700000000


class FakeHttp:
    def __init__(self):
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, json.loads(body)))
        return {'ok': True, 'ts': '100.' + str(len(self.calls))}


def signed(body, stamp=NOW, secret=SECRET):
    # Sample: Return headers with the timestamp and the v0 signature of the body.
    return {'X-Slack-Request-Timestamp': str(stamp), 'X-Slack-Signature': sign(secret, stamp, body)}


def command(text, user='U1', channel='C1'):
    # Sample: Return the form body of /durai build a dashboard from U1 in C1.
    return urlencode({'command': '/durai', 'text': text, 'user_id': user, 'channel_id': channel}).encode()


def click(user, value, channel='C1'):
    # Sample: Return the interaction body of a click on durai_approve with the value.
    payload = {'type': 'block_actions', 'user': {'id': user}, 'channel': {'id': channel}, 'message': {'ts': '100.1'},
               'actions': [{'action_id': 'durai_approve', 'value': value}]}
    return urlencode({'payload': json.dumps(payload)}).encode()


class ChatContract:
    """Sample: Mix into a test case. Set self.chat in make_chat. Run the goals."""

    def make_chat(self):
        raise NotImplementedError

    def setUp(self):
        self.chat = self.make_chat()

    def test_post_returns_id(self):
        # Sample: Post "Gate open" to C1. Return a non-empty message id.
        self.assertTrue(self.chat.post('C1', 'Gate open'))

    def test_thread_keeps_workflow(self):
        # Sample: Two replies in thread 100.1 of C1 give one thread key and one workflow id.
        one = self.chat.thread_key('C1', '100.1')
        two = self.chat.thread_key('C1', '100.1')
        bridge = ChatBridge(self.chat, lambda text: {'text': text}, gates=object())
        self.assertEqual((one, bridge.workflow_for(one)), (two, bridge.workflow_for(two)))
        self.assertNotEqual(bridge.workflow_for(one), bridge.workflow_for(self.chat.thread_key('C1', '200.1')))


class SlackContractTests(ChatContract, unittest.TestCase):
    def make_chat(self):
        return SlackChat(TOKEN, SECRET, FakeHttp(), now=lambda: NOW)


class MemoryChat:
    def __init__(self):
        self.sent = []

    def post(self, channel, text, thread=None, blocks=None):
        self.sent.append((channel, text, thread))
        return str(len(self.sent))

    def thread_key(self, channel, thread):
        return channel + '/' + (thread or '')


class MemoryContractTests(ChatContract, unittest.TestCase):
    def make_chat(self):
        return MemoryChat()


class SlackVerifyTests(unittest.TestCase):
    def setUp(self):
        self.chat = SlackChat(TOKEN, SECRET, FakeHttp(), now=lambda: NOW)

    def test_good_signature(self):
        # Sample: A body signed with the right secret at the current time passes.
        body = command('build a dashboard')
        self.assertIsNone(self.chat.verify(signed(body), body))

    def test_bad_signature_refused(self):
        # Sample: A body changed after signing raises InvalidSignature.
        body = command('build a dashboard')
        with self.assertRaises(InvalidSignature):
            self.chat.verify(signed(body), body + b'x')

    def test_old_timestamp_refused(self):
        # Sample: A 6 minute old timestamp with a valid signature raises InvalidSignature.
        body = command('build a dashboard')
        with self.assertRaises(InvalidSignature):
            self.chat.verify(signed(body, NOW - 360), body)

    def test_missing_header_refused(self):
        # Sample: Headers with no signature raise InvalidSignature.
        with self.assertRaises(InvalidSignature):
            self.chat.verify({}, b'x')

    def test_repr_has_no_secret(self):
        # Sample: The text of the adapter holds neither the token nor the signing secret.
        self.assertNotIn(TOKEN, repr(self.chat))
        self.assertNotIn(SECRET, repr(self.chat))

    def test_post_sends_token_in_header_only(self):
        # Sample: Post in thread C1:100.1. The body has thread_ts 100.1 and no token. The header has the bearer token.
        self.chat.post('C1', 'Gate open', 'C1:100.1')
        url, headers, body = self.chat.http.calls[0]
        self.assertEqual((body['thread_ts'], TOKEN in json.dumps(body), headers['Authorization']), ('100.1', False, 'Bearer ' + TOKEN))

    def test_post_refusal_raises(self):
        # Sample: Slack answers ok false with channel_not_found. Raise RuntimeError with that code and no token.
        chat = SlackChat(TOKEN, SECRET, lambda url, headers, body: {'ok': False, 'error': 'channel_not_found'}, now=lambda: NOW)
        with self.assertRaises(RuntimeError) as caught:
            chat.post('C9', 'x')
        self.assertIn('channel_not_found', str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))


class FakeGates:
    def __init__(self):
        self.approvals = []
        self.opened = []

    def propose_subject(self, workflow_id, step, when, subject, database):
        return None

    def open_gate(self, workflow_id, step, subject, question, deadline, database):
        self.opened.append((workflow_id, subject, deadline))

    def approve(self, workflow_id, subject, scope, decision, database, role):
        self.approvals.append((workflow_id, subject, scope, decision, role))


class SlackBridgeTests(unittest.TestCase):
    def setUp(self):
        self.http = FakeHttp()
        self.chat = SlackChat(TOKEN, SECRET, self.http, now=lambda: NOW)
        self.gates = FakeGates()
        self.bridge = ChatBridge(self.chat, lambda text: {'text': 'Enhanced: ' + text}, gates=self.gates, reviewers={'U9'})

    def send(self, body):
        return self.bridge.handle(signed(body), body)

    def test_slash_command_enhanced(self):
        # Sample: /durai build a dashboard posts a draft that starts with Enhanced and has an Approve button.
        result = self.send(command('build a dashboard'))
        _, _, posted = self.http.calls[0]
        self.assertEqual(result['state'], 'DRAFT_POSTED')
        self.assertEqual(len(self.gates.opened), 1)
        self.assertIn('Enhanced: build a dashboard', posted['blocks'][0]['text']['text'])
        self.assertEqual(posted['blocks'][1]['elements'][0]['action_id'], 'durai_approve')

    def test_bad_signature_runs_nothing(self):
        # Sample: A command with a wrong signature raises InvalidSignature. No post is made.
        body = command('build a dashboard')
        with self.assertRaises(InvalidSignature):
            self.bridge.handle(signed(body, secret='wrong'), body)
        self.assertEqual(self.http.calls, [])

    def test_unlisted_user_cannot_approve(self):
        # Sample: User U2 clicks Approve. Return IGNORED. Write no approval. Log IGNORED_CLICK.
        value = self.send(command('build a dashboard'))
        result = self.send(click('U2', value['workflow'] + '|' + value['subjectDigest']))
        self.assertEqual((result['state'], self.gates.approvals, self.bridge.log[-1]['kind']), ('IGNORED', [], 'IGNORED_CLICK'))

    def test_listed_reviewer_approves(self):
        # Sample: User U9 clicks Approve. Write one approval as durai_reviewer for the workflow and subject.
        draft = self.send(command('build a dashboard'))
        result = self.send(click('U9', draft['workflow'] + '|' + draft['subjectDigest']))
        self.assertEqual(result['state'], 'APPROVED')
        self.assertEqual(self.gates.approvals, [(draft['workflow'], draft['subjectDigest'], 'chat:U9', 'APPROVE', 'durai_reviewer')])

    def test_url_verification_returns_challenge(self):
        # Sample: Slack sends url_verification with challenge abc. Return abc.
        body = json.dumps({'type': 'url_verification', 'challenge': 'abc'}).encode()
        self.assertEqual(self.send(body), {'challenge': 'abc'})

    def test_bot_message_ignored(self):
        # Sample: A message event with bot_id B1 returns IGNORED. No draft is posted.
        body = json.dumps({'type': 'event_callback', 'event': {'type': 'message', 'bot_id': 'B1', 'text': 'hi', 'channel': 'C1', 'ts': '1.1'}}).encode()
        self.assertEqual(self.send(body), {'state': 'IGNORED'})
        self.assertEqual(self.http.calls, [])

    def test_no_secret_in_receipt(self):
        # Sample: After a draft and an approval the log text holds neither the token nor the signing secret. Redact turns xoxb-1-2-abc into [redacted].
        draft = self.send(command('build a dashboard'))
        self.send(click('U9', draft['workflow'] + '|' + draft['subjectDigest']))
        text = json.dumps(self.bridge.log)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(SECRET, text)
        self.assertEqual(redact('token xoxb-1-2-abc'), 'token [redacted]')


if __name__ == '__main__':
    unittest.main()
