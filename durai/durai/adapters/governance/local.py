"""Sample: Make LocalGovernance with the starter egress list. Check a call to api.deepinfra.com and get allow. Check a call to example.com and get deny. Record the result and get a receipt with a digest."""
import json
import re
import time

from ...common import digest
from ...egress_ceiling import CeilingExceeded
from ...egress_policy import EgressPolicyError, parse_allow
from ...egress_presets import allow_list, starter_body, validate_body
from ... import ocsf_mapper
from ...ports.errors import Unsupported
from ...ports.governance import ALLOW, DENY, GATE

URL_PATTERN = re.compile(r'(https?)://([A-Za-z0-9*._-]+|\[[0-9a-fA-F:.]+\])(?::(\d{1,5}))?', re.I)
HOST_KEYS = ('host', 'hostname')
MAX_DEPTH = 6
MAX_TARGETS = 20
NOT_OPEN = 'NOT_OPEN'
DETAIL_LIMIT = 300


def egress_targets(args, depth=0):
    # Sample: Args {"url": "https://api.deepinfra.com/v1/x"} return ["api.deepinfra.com:443"]. Args {"url": "http://example.com:8080/a"} return ["example.com:8080"]. Args {"path": "notes.txt"} return [].
    found = []
    if depth > MAX_DEPTH:
        return found
    if isinstance(args, str):
        for scheme, host, port in URL_PATTERN.findall(args):
            found.append(host.strip('[]') + ':' + (port or ('80' if scheme.lower() == 'http' else '443')))
    elif isinstance(args, dict):
        for key, value in args.items():
            if key in HOST_KEYS and isinstance(value, str) and value.strip() and '/' not in value:
                found.append(value.strip() + ':' + str(args.get('port') or 443))
            else:
                found += egress_targets(value, depth + 1)
    elif isinstance(args, (list, tuple)):
        for value in args:
            found += egress_targets(value, depth + 1)
    unique = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique[:MAX_TARGETS]


def host_matches(pattern, host):
    # Sample: Pattern api.deepinfra.com matches api.deepinfra.com only. Pattern *.example.org matches api.example.org and not example.org.
    if pattern == host:
        return True
    return pattern.startswith('*.') and host.endswith(pattern[1:]) and host != pattern[2:]


def plain(value):
    # Sample: A dict with a set becomes JSON safe text form, so it can be hashed. A string stays a string.
    return json.loads(json.dumps(value, default=str, sort_keys=True))


class LocalGovernance:
    def __init__(self, body=None, tier='balanced', gate=None, ceiling=None, ttl=3600, clock=time.time, sink=None):
        # Sample: Use the starter body and tier balanced. The list holds api.deepinfra.com:443. Pass an EgressGate for gates and an EgressCeiling for the operator boundary. Both are optional.
        self.body = validate_body(body if body is not None else starter_body())
        self.allowed = allow_list(self.body, tier)
        self.gate = gate
        self.ceiling = ceiling if ceiling is not None else getattr(gate, 'ceiling', None)
        self.ttl, self.clock, self.sink = ttl, clock, sink
        self.receipts = []
        self.ceiling_cache = {}

    def listed(self, entry):
        # Sample: Entry api.deepinfra.com:443 with a list that holds the same host and port returns True. A different port returns False.
        return any(item['port'] == entry['port'] and host_matches(item['host'], entry['host']) for item in self.allowed)

    def answer(self, decision, reason, **extra):
        # Sample: Decision deny and reason egress-not-listed return a dict with both and the time of the check.
        return {'decision': decision, 'reason': reason, 'checkedAt': self.clock(), **extra}

    def check_tool_call(self, tool_name, args, workflow_id=None, session_id=None, tool_call_id=None):
        # Sample: Fetch a listed host and get allow. Fetch an unlisted host and get deny, or gate when a boundary and a gate exist. Any error gets deny with reason governance-error.
        try:
            return self.evaluate(args, workflow_id, session_id)
        except Exception:
            return self.answer(DENY, 'governance-error')

    def evaluate(self, args, workflow_id, session_id):
        # Sample: Two targets, one listed and one unlisted. Return the answer of the unlisted target. With no target return allow with reason no-egress.
        targets = egress_targets(args)
        if not targets:
            return self.answer(ALLOW, 'no-egress', targets=[])
        gated = False
        for target in targets:
            try:
                entry = parse_allow(target)
            except EgressPolicyError:
                return self.answer(DENY, 'egress-host-invalid', targets=targets)
            if self.listed(entry):
                continue
            verdict = self.unlisted(entry, workflow_id, session_id)
            if verdict['decision'] != ALLOW:
                verdict['targets'] = targets
                return verdict
            gated = True
        return self.answer(ALLOW, 'gate-approved' if gated else 'egress-listed', targets=targets)

    def boundary(self, entry):
        # Sample: Ask the prover about example.com:443 once. Ask again. Use the saved answer. Return within_boundary or exceeds_boundary.
        key = digest(entry)
        if key not in self.ceiling_cache:
            self.ceiling_cache[key] = self.ceiling.check(entry)['state']
        return self.ceiling_cache[key]

    def unlisted(self, entry, workflow_id, session_id):
        # Sample: Unlisted host with no boundary and no gate returns deny egress-not-listed. With a boundary that exceeds it returns deny egress-ceiling-exceeded. Within the boundary the gate opens and the answer is gate with the subject digest.
        if self.ceiling is None and self.gate is None:
            return self.answer(DENY, 'egress-not-listed')
        if self.ceiling is not None:
            try:
                state = self.boundary(entry)
            except CeilingExceeded:
                return self.answer(DENY, 'egress-ceiling-error')
            if state != 'within_boundary':
                return self.answer(DENY, 'egress-ceiling-exceeded')
        if self.gate is None:
            return self.answer(DENY, 'egress-not-listed')
        workflow = workflow_id or (('session-' + digest(session_id)[:12]) if session_id else None)
        if workflow is None:
            return self.answer(DENY, 'workflow-required')
        subject = digest(entry)
        try:
            state = self.gate.state(workflow, {'subjectDigest': subject})
        except KeyError:
            self.gate.propose(workflow, entry, self.clock() + self.ttl)
            state = 'WAITING'
        if state == 'APPROVED':
            return self.answer(ALLOW, 'gate-approved', workflow=workflow, subjectDigest=subject)
        if state == 'WAITING':
            return self.answer(GATE, 'gate-waiting', workflow=workflow, subjectDigest=subject)
        return self.answer(DENY, 'gate-cancelled', workflow=workflow, subjectDigest=subject)

    def record_result(self, tool_name, args, result, decision=None, model=None, session_id=None, tool_call_id=None):
        # Sample: Record fetch with model Beam-1.0-501B-A23B. Return a receipt with the digest of the content, the digest of the arguments and of the result, and no raw argument or result. Keep it in receipts and pass it to the sink.
        content = {'tool': tool_name, 'argsDigest': digest(plain(args)), 'resultDigest': digest(plain(result)), 'decision': decision,
                   'model': model, 'sessionDigest': digest(session_id) if session_id else None,
                   'callDigest': digest(tool_call_id) if tool_call_id else None}
        receipt = {'receiptId': 'rcpt-' + digest(content)[:16], 'digest': digest(content), **content, 'recordedAt': self.clock()}
        self.receipts.append(receipt)
        if self.sink is not None:
            self.sink(receipt)
        return receipt

    def to_ocsf(self, receipt):
        # Sample: Map a receipt to an OCSF API Activity event with class_uid 6003, activity tool.fetch and the receipt fields under unmapped. No raw argument appears.
        return ocsf_mapper.event(receipt['receiptId'], 'tool.' + str(receipt['tool']), 'durai_runtime', receipt['recordedAt'], dict(receipt))

    def open_gate(self, workflow_id, entry, deadline=None):
        # Sample: Open a gate for example.com:443. A host outside the boundary raises CeilingExceeded and opens nothing. No gate store raises Unsupported.
        if self.gate is None:
            raise Unsupported('Gates are not configured')
        parsed = parse_allow(entry)
        if self.ceiling is not None and self.boundary(parsed) != 'within_boundary':
            raise CeilingExceeded('Entry exceeds the operator boundary')
        proposal = self.gate.propose(workflow_id, parsed, deadline if deadline is not None else self.clock() + self.ttl)
        return {'workflow': workflow_id, 'subjectDigest': proposal['subjectDigest'], 'question': proposal['question'], 'entry': proposal['entry']}

    def gate_state(self, workflow_id, subject_digest):
        # Sample: Read the gate for subject D-1. Return WAITING, APPROVED or CANCELLED. A gate that is not open returns NOT_OPEN. No gate store returns NOT_OPEN.
        if self.gate is None:
            return NOT_OPEN
        try:
            return self.gate.state(workflow_id, {'subjectDigest': subject_digest})
        except KeyError:
            return NOT_OPEN

    def egress_check(self, entry):
        # Sample: Entry api.deepinfra.com:443 returns listed true. Entry example.com:443 with a boundary returns listed false and withinBoundary true or false. Without a boundary withinBoundary is None.
        parsed = parse_allow(entry)
        within = None if self.ceiling is None else self.boundary(parsed) == 'within_boundary'
        return {'entry': parsed, 'listed': self.listed(parsed), 'withinBoundary': within}
