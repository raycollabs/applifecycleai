"""Sample: Make SlackChat with a token, a signing secret and an http function. Verify a request. Parse a slash command. Post a reply. No network call is made without the http function."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs

from ...ports.errors import InvalidSignature

API = 'https://slack.com/api/chat.postMessage'
MAX_AGE = 300


def sign(secret, timestamp, body):
    # Sample: Secret S, timestamp 1700000000 and body b"a=1" return v0= followed by the HMAC-SHA256 hex of v0:1700000000:a=1.
    base = b'v0:' + str(timestamp).encode() + b':' + body
    return 'v0=' + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def lower(headers):
    # Sample: Header X-Slack-Signature becomes x-slack-signature.
    return {str(key).lower(): value for key, value in headers.items()}


class SlackChat:
    def __init__(self, token, signing_secret, http, now=time.time):
        # Sample: Keep the token and secret private. http(url, headers, body) returns a dict. No repr shows them.
        self._token, self._secret, self.http, self.now = token, signing_secret, http, now

    def __repr__(self):
        # Sample: Show SlackChat with no token and no secret.
        return 'SlackChat()'

    def verify(self, headers, body):
        # Sample: A valid signature at the current time passes. A changed body, a missing header or a timestamp older than 300 seconds raises InvalidSignature.
        head = lower(headers)
        stamp, given = head.get('x-slack-request-timestamp'), head.get('x-slack-signature')
        if not stamp or not given:
            raise InvalidSignature('Signature header is missing')
        try:
            age = abs(self.now() - int(stamp))
        except ValueError:
            raise InvalidSignature('Timestamp is not a number') from None
        if age > MAX_AGE:
            raise InvalidSignature('Timestamp is too old')
        if not hmac.compare_digest(sign(self._secret, stamp, body), given):
            raise InvalidSignature('Signature does not match')

    def thread_key(self, channel, thread):
        # Sample: Channel C1 and thread 100.1 return C1:100.1. No thread returns C1:.
        return channel + ':' + (thread or '')

    def parse(self, headers, body):
        # Sample: A form body with command=/durai and text=build a dashboard returns kind command. A payload with block_actions returns kind action and the button value. A message event returns kind message.
        text = body.decode('utf-8', 'replace')
        form = {key: values[0] for key, values in parse_qs(text, keep_blank_values=True).items()}
        if 'command' in form:
            channel = form.get('channel_id', '')
            return {'kind': 'command', 'user': form.get('user_id', ''), 'channel': channel,
                    'thread': self.thread_key(channel, form.get('thread_ts')), 'text': form.get('text', '')}
        if 'payload' in form:
            payload = json.loads(form['payload'])
            action = (payload.get('actions') or [{}])[0]
            channel = (payload.get('channel') or {}).get('id', '')
            message = payload.get('message') or {}
            return {'kind': 'action', 'user': (payload.get('user') or {}).get('id', ''), 'channel': channel,
                    'thread': self.thread_key(channel, message.get('thread_ts') or message.get('ts')),
                    'actionId': action.get('action_id', ''), 'value': action.get('value', '')}
        data = json.loads(text) if text.strip().startswith('{') else {}
        if data.get('type') == 'url_verification':
            return {'kind': 'challenge', 'challenge': data.get('challenge', '')}
        event = data.get('event') or {}
        if event.get('type') == 'message' and not event.get('bot_id'):
            channel = event.get('channel', '')
            return {'kind': 'message', 'user': event.get('user', ''), 'channel': channel,
                    'thread': self.thread_key(channel, event.get('thread_ts') or event.get('ts')), 'text': event.get('text', '')}
        return {'kind': 'ignored'}

    def post(self, channel, text, thread=None, blocks=None):
        # Sample: Post "Gate open" to C1 in thread C1:100.1. Send the bot token in the header only. Return the message ts. A reply with ok false raises RuntimeError with the Slack error code.
        body = {'channel': channel, 'text': text}
        if thread and ':' in thread and thread.split(':', 1)[1]:
            body['thread_ts'] = thread.split(':', 1)[1]
        if blocks:
            body['blocks'] = blocks
        reply = self.http(API, {'Authorization': 'Bearer ' + self._token, 'Content-Type': 'application/json'}, json.dumps(body).encode())
        if not reply.get('ok'):
            raise RuntimeError('Slack refused the post: ' + str(reply.get('error', 'unknown')))
        return reply.get('ts', '')
