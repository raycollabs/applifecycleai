"""Sample: A Slack user sends /durai build a dashboard. The bridge verifies the request, builds an enhanced draft and posts it. A reviewer clicks Approve. Only an allow-listed Slack ID becomes a durai_reviewer approval."""
import re
import time

from .common import digest
from .workflow_gates import REVIEWER

SECRET_PATTERN = re.compile(r'(xox[a-z]-[A-Za-z0-9-]+|v0=[0-9a-f]{16,})')


def redact(text):
    # Sample: The text "token xoxb-1-2-abc" becomes "token [redacted]".
    return SECRET_PATTERN.sub('[redacted]', str(text))


class ChatBridge:
    def __init__(self, chat, enhance, gates=None, reviewers=(), database='durai_local', ttl=3600, clock=time.time):
        # Sample: Use chat adapter C, enhance(text) returning {"text": ...}, the gates module and reviewer IDs U9 and U10. Tests pass a fake gates object with approve.
        if gates is None:
            from . import workflow_gates as gates
        self.chat, self.enhance, self.gates, self.database = chat, enhance, gates, database
        self.reviewers, self.ttl, self.clock = frozenset(reviewers), ttl, clock
        self.log = []

    def workflow_for(self, thread):
        # Sample: Thread C1:100.1 returns chat-<12 hex digits>. The same thread returns the same id.
        return 'chat-' + digest(thread)[:12]

    def note(self, kind, **fields):
        # Sample: Record kind IGNORED_CLICK with user U2. The log line holds no token and no signature.
        self.log.append({'kind': kind, **{key: redact(value) for key, value in fields.items()}})

    def handle(self, headers, body):
        # Sample: Verify first. A bad signature raises InvalidSignature and nothing else runs. A challenge returns the challenge text. A command or message posts a draft. An action goes to the approval path.
        self.chat.verify(headers, body)
        event = self.chat.parse(headers, body)
        kind = event['kind']
        if kind == 'challenge':
            return {'challenge': event['challenge']}
        if kind in ('command', 'message'):
            return self.draft(event)
        if kind == 'action':
            return self.click(event)
        return {'state': 'IGNORED'}

    def draft(self, event):
        # Sample: Text "build a dashboard" returns the enhanced text. Open a gate that ends in one hour. Post it in the thread with an Approve button that carries the workflow id and subject digest.
        text = (event.get('text') or '').strip()
        if not text:
            return {'state': 'IGNORED'}
        workflow = self.workflow_for(event['thread'])
        enhanced = self.enhance(text)['text']
        subject = digest({'request': text, 'draft': enhanced})
        self.gates.propose_subject(workflow, 'chat', 'before', subject, self.database)
        self.gates.open_gate(workflow, 'chat', subject, 'Approve the Durai draft for: ' + text[:200], self.clock() + self.ttl, self.database)
        blocks = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': enhanced[:2900]}},
                  {'type': 'actions', 'elements': [{'type': 'button', 'action_id': 'durai_approve',
                                                    'text': {'type': 'plain_text', 'text': 'Approve'}, 'value': workflow + '|' + subject}]}]
        message = self.chat.post(event['channel'], 'Durai draft ready. A reviewer must approve it.', event['thread'], blocks)
        self.note('DRAFT_POSTED', workflow=workflow, user=event['user'])
        return {'state': 'DRAFT_POSTED', 'workflow': workflow, 'subjectDigest': subject, 'message': message}

    def click(self, event):
        # Sample: User U9 is on the list. Record a durai_reviewer approval for the workflow and subject. User U2 is not on the list. Ignore the click and log it.
        if event.get('actionId') != 'durai_approve' or '|' not in event.get('value', ''):
            return {'state': 'IGNORED'}
        if event['user'] not in self.reviewers:
            self.note('IGNORED_CLICK', user=event['user'], reason='not a reviewer')
            return {'state': 'IGNORED'}
        workflow, subject = event['value'].split('|', 1)
        self.gates.approve(workflow, subject, 'chat:' + event['user'], 'APPROVE', self.database, REVIEWER)
        self.note('APPROVED', user=event['user'], workflow=workflow)
        self.chat.post(event['channel'], 'Approved by reviewer <@' + event['user'] + '>.', event['thread'])
        return {'state': 'APPROVED', 'workflow': workflow, 'subjectDigest': subject}
