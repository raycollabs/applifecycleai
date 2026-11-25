"""Sample: Resolve conversation C-1 for its signed-in owner. Pass visible turn 7 to the worker."""
from dataclasses import dataclass
import re

from .common import Conflict, digest
from .session_settings import load_session_settings
from .session_summarizer import SessionSummaryWorker

PLAN_ID = re.compile(r'[A-Za-z0-9_.-]{1,160}')
RECORD_KEY = re.compile(r'(?:status|plan/rev/[1-9][0-9]*|(?:approval|decision|evidence)/[0-9]{3,})')
RECORD_DIGEST = re.compile(r'[0-9a-f]{64}')


@dataclass(frozen=True)
class SessionIdentity:
    application_id: str
    subject_id: str


@dataclass(frozen=True, order=True)
class PlanPin:
    plan_id: str
    record_key: str


@dataclass(frozen=True)
class ConversationGrant:
    owner: SessionIdentity
    conversation_id: str
    access_revision: int
    plan_ids: frozenset[str] = frozenset()
    pins: frozenset[PlanPin] = frozenset()

    def storage_id(self):
        # Sample: Derive a stable ID for owner P-1 and conversation C-1 at revision 2.
        if (not isinstance(self.owner, SessionIdentity)
                or any(not isinstance(value, str) or not value.strip() or len(value) > 200
                       for value in (self.owner.application_id, self.owner.subject_id, self.conversation_id))
                or type(self.access_revision) is not int or self.access_revision < 1):
            raise PermissionError('Conversation access is unavailable')
        if (not isinstance(self.plan_ids, frozenset) or not isinstance(self.pins, frozenset)
                or any(not isinstance(plan, str) or PLAN_ID.fullmatch(plan) is None for plan in self.plan_ids)
                or any(not isinstance(pin, PlanPin) or pin.plan_id not in self.plan_ids
                       or not isinstance(pin.record_key, str) or RECORD_KEY.fullmatch(pin.record_key) is None
                       for pin in self.pins)):
            raise PermissionError('Conversation access is unavailable')
        scope = [self.owner.application_id, self.owner.subject_id, self.conversation_id, self.access_revision,
                 sorted(self.plan_ids), [[pin.plan_id, pin.record_key] for pin in sorted(self.pins)]]
        return 'host-v1-' + digest(scope)


def count_text_bytes(visible_text):
    # Sample: Count two UTF-8 bytes for the visible text é.
    return len(visible_text.encode('utf-8'))


class SessionHost:
    def __init__(self, store, authenticate, read_grant, count_tokens=count_text_bytes):
        # Sample: Connect a trusted sign-in lookup and conversation registry to the store.
        self.store = store
        self.authenticate = authenticate
        self.read_grant = read_grant
        self.count_tokens = count_tokens

    def _owned_grant(self, identity, conversation_id, store):
        # Sample: Ask for C-1 as a different user. Refuse the request before reading its context.
        if not isinstance(identity, SessionIdentity) or not identity.application_id or not identity.subject_id:
            raise PermissionError('Conversation access is unavailable')
        grant = self.read_grant(identity, conversation_id)
        if (not isinstance(grant, ConversationGrant) or grant.owner != identity
                or grant.conversation_id != conversation_id or store.application_id() != identity.application_id):
            raise PermissionError('Conversation access is unavailable')
        grant.storage_id()
        return grant

    def append_turn(self, session, conversation_id, message_id, role, visible_text, plan_id=None, *, flush=False):
        # Sample: Save visible user text from P-1 in C-1. Refuse a turn from a tool.
        if not load_session_settings().enabled:
            return None
        grant = self._owned_grant(self.authenticate(session), conversation_id, self.store)
        if plan_id is not None and plan_id not in grant.plan_ids:
            raise PermissionError('Conversation plan access is unavailable')
        if role not in ('user', 'assistant') or not isinstance(visible_text, str) or not visible_text.strip():
            raise ValueError('A visible user or assistant turn is required')
        count = self.count_tokens(visible_text)
        if type(count) is not int or not 1 <= count <= 2000:
            raise ValueError('The visible turn exceeds its token budget or has no valid count')
        row = self.store.append_turn(grant.storage_id(), message_id, role, visible_text, count, plan_id, flush=flush)
        return dict(row, conversation_id=grant.conversation_id)

    def get_context(self, session, conversation_id, **limits):
        # Sample: Read C-1 twice as P-1. Check the same conversation ID and stored turn on both reads.
        if not load_session_settings().enabled:
            return None
        grant = self._owned_grant(self.authenticate(session), conversation_id, self.store)
        context = self.store.get_context(grant.storage_id(), **limits)
        if grant != self._owned_grant(self.authenticate(session), conversation_id, self.store):
            raise PermissionError('Conversation access is unavailable')
        return dict(context, conversationId=grant.conversation_id) if context is not None else None

    def read_turns(self, session, conversation_id, first, last):
        # Sample: Fetch omitted turns 1 to 3 for C-1 as its signed-in owner.
        if not load_session_settings().enabled:
            return []
        grant = self._owned_grant(self.authenticate(session), conversation_id, self.store)
        rows = self.store.read_turns(grant.storage_id(), first, last)
        if grant != self._owned_grant(self.authenticate(session), conversation_id, self.store):
            raise PermissionError('Conversation access is unavailable')
        return rows

    def search(self, session, conversation_id, text, limit=5):
        # Sample: Search "93a7a7b" in C-1 as its signed-in owner. Refuse Bob before any row is read.
        if not load_session_settings().enabled:
            return []
        grant = self._owned_grant(self.authenticate(session), conversation_id, self.store)
        rows = self.store.search(grant.storage_id(), text, limit)
        if grant != self._owned_grant(self.authenticate(session), conversation_id, self.store):
            raise PermissionError('Conversation access is unavailable')
        return rows

    def summary_worker(self, controller, plans, find_grant, provider, profile):
        # Sample: Use the controller and plan store to give a scripted worker pins from C-1.
        def check_claim(claim):
            # Sample: Reassign C-1. Refuse a claim with the previous owner's storage ID.
            storage_id = claim['conversationId']
            recorded = find_grant(storage_id)
            if not isinstance(recorded, ConversationGrant):
                raise PermissionError('Conversation access is unavailable')
            grant = self._owned_grant(recorded.owner, recorded.conversation_id, controller)
            if grant.storage_id() != storage_id or any(
                turn['text'] is not None and turn['planId'] is not None and turn['planId'] not in grant.plan_ids
                for turn in claim['turns']
            ):
                raise PermissionError('Conversation access is unavailable')
            return grant

        def resolve_refs(claim):
            # Sample: Pin status revision 3 and approval/001. Return their keys and exact digests.
            grant = check_claim(claim)
            references = []
            for pin in sorted(grant.pins):
                try:
                    record = plans.read(pin.plan_id, pin.record_key)
                except KeyError:
                    raise Conflict('A pinned plan record is unavailable') from None
                if (record['application_id'] != grant.owner.application_id or record['plan_id'] != pin.plan_id
                        or record['key'] != pin.record_key or RECORD_DIGEST.fullmatch(record['digest']) is None):
                    raise PermissionError('Pinned plan record access is unavailable')
                references.append(f'plan:{pin.plan_id}:{pin.record_key}@{record["digest"]}')
            return references

        return SessionSummaryWorker(controller, provider, profile, self.count_tokens, resolve_refs, check_claim)
