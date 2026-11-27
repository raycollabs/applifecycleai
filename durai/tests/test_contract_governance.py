"""Sample: Run the shared governance contract on LocalGovernance with fake gates and a fake prover. Check allow, deny, gate and receipt answers."""
import time
import unittest
from datetime import datetime, timezone

from durai import workflow_gates
from durai.adapters.governance.local import LocalGovernance
from durai.common import digest
from durai.egress_ceiling import EgressCeiling
from durai.egress_gate import EgressGate
from durai.ports.governance import ALLOW, DENY, GATE

LISTED = 'https://api.deepinfra.com/v1/chat/completions'
UNLISTED = 'https://example.com/data'


class FakeGates:
    def __init__(self):
        self.rows = {}

    def propose_subject(self, workflow_id, step_id, when, subject, database):
        return None

    def open_gate(self, workflow_id, step_id, subject, question, deadline, database, when='before'):
        rows = self.rows.setdefault(workflow_id, [])
        if workflow_gates.find_gate(rows, step_id, when, subject) is None:
            rows.append({'kind': 'progress', 'author': 'durai_runtime', 'created_at': datetime.now(timezone.utc),
                         'value': {'gate': workflow_gates.gate_name(step_id, when), 'subjectDigest': subject, 'question': question, 'deadline': '%.3f' % deadline}})
        return {'question': question}

    def gate_state(self, workflow_id, step_id, subject, database, when='before', now=None):
        rows = self.rows.get(workflow_id, [])
        gate = workflow_gates.find_gate(rows, step_id, when, subject)
        if gate is None:
            raise KeyError('Gate is not open')
        deadline = float(gate['value']['deadline'])
        decision = workflow_gates.reviewer_decision(rows, subject, deadline)
        if decision is not None:
            return workflow_gates.APPROVED if decision['value']['decision'] == 'APPROVE' else workflow_gates.CANCELLED
        return workflow_gates.CANCELLED if time.time() >= deadline else workflow_gates.WAITING

    def approve_as(self, role, workflow_id, subject):
        self.rows.setdefault(workflow_id, []).append({'kind': 'approval', 'author': role, 'created_at': datetime.now(timezone.utc),
                                                      'value': {'subjectDigest': subject, 'decision': 'APPROVE'}})


def prover(code, text=b''):
    def runner(args, stdin, seconds, cap, env=None):
        return code, text, None
    return runner


class GovernancePortContract:
    def make_port(self, boundary=None, prover_code=0):
        raise NotImplementedError

    def approve_as(self, role, workflow_id, subject):
        raise NotImplementedError

    def test_allowed_call_returns_allow(self):
        # Sample: Call fetch with url https://api.deepinfra.com/v1/chat/completions when the list holds api.deepinfra.com:443. Expect allow with reason egress-listed.
        answer = self.make_port().check_tool_call('fetch', {'url': LISTED}, workflow_id='W-1')
        self.assertEqual((answer['decision'], answer['reason']), (ALLOW, 'egress-listed'))

    def test_call_without_a_host_returns_allow(self):
        # Sample: Call read_file with path notes.txt. No host appears. Expect allow with reason no-egress.
        answer = self.make_port().check_tool_call('read_file', {'path': 'notes.txt'}, workflow_id='W-1')
        self.assertEqual((answer['decision'], answer['reason']), (ALLOW, 'no-egress'))

    def test_unlisted_host_denied(self):
        # Sample: Call fetch with url https://example.com/data when the list holds api.deepinfra.com:443 and no gate exists. Expect deny with reason egress-not-listed.
        answer = self.make_port().check_tool_call('fetch', {'url': UNLISTED}, workflow_id='W-1')
        self.assertEqual((answer['decision'], answer['reason']), (DENY, 'egress-not-listed'))

    def test_loopback_host_denied(self):
        # Sample: Call fetch with url http://127.0.0.1:8080/x. Expect deny with reason egress-host-invalid.
        answer = self.make_port().check_tool_call('fetch', {'url': 'http://127.0.0.1:8080/x'}, workflow_id='W-1')
        self.assertEqual((answer['decision'], answer['reason']), (DENY, 'egress-host-invalid'))

    def test_agent_cannot_approve(self):
        # Sample: Open a gate for example.com:443. A durai_runtime approval is written. The state stays WAITING and the call returns gate. A durai_reviewer approval makes it APPROVED and the call returns allow.
        port = self.make_port(boundary=['example.com:443'])
        first = port.check_tool_call('fetch', {'url': UNLISTED}, workflow_id='W-1')
        self.assertEqual((first['decision'], first['reason']), (GATE, 'gate-waiting'))
        self.approve_as('durai_runtime', 'W-1', first['subjectDigest'])
        self.assertEqual(port.gate_state('W-1', first['subjectDigest']), 'WAITING')
        self.assertEqual(port.check_tool_call('fetch', {'url': UNLISTED}, workflow_id='W-1')['decision'], GATE)
        self.approve_as('durai_reviewer', 'W-1', first['subjectDigest'])
        self.assertEqual(port.gate_state('W-1', first['subjectDigest']), 'APPROVED')
        self.assertEqual(port.check_tool_call('fetch', {'url': UNLISTED}, workflow_id='W-1')['reason'], 'gate-approved')

    def test_port_offers_no_approve_call(self):
        # Sample: The port object has no attribute named approve, grant or decide.
        port = self.make_port()
        self.assertFalse(any(hasattr(port, name) for name in ('approve', 'grant', 'decide', 'approve_gate')))

    def test_receipt_has_digest_and_model(self):
        # Sample: Record the result of fetch with model Beam-1.0-501B-A23B. The receipt has a 64 character digest, the model name and no raw argument.
        port = self.make_port()
        receipt = port.record_result('fetch', {'url': LISTED, 'note': 'sample-argument'}, {'status': 200}, decision=ALLOW, model='Beam-1.0-501B-A23B', session_id='S-1')
        self.assertEqual((len(receipt['digest']), receipt['model']), (64, 'Beam-1.0-501B-A23B'))
        self.assertNotIn('sample-argument', str(receipt))
        self.assertEqual(receipt['argsDigest'], digest({'url': LISTED, 'note': 'sample-argument'}))

    def test_egress_check_reports_listed(self):
        # Sample: Entry api.deepinfra.com:443 returns listed true. Entry example.com:443 returns listed false.
        port = self.make_port()
        self.assertTrue(port.egress_check('api.deepinfra.com:443')['listed'])
        self.assertFalse(port.egress_check('example.com:443')['listed'])


class LocalGovernanceTests(GovernancePortContract, unittest.TestCase):
    def make_port(self, boundary=None, prover_code=0):
        self.gates = FakeGates()
        if boundary is None:
            return LocalGovernance()
        ceiling = EgressCeiling(boundary, cli=['prover'], runner=prover(prover_code, b'counterexample'))
        return LocalGovernance(gate=EgressGate(self.gates, step_id='egress'), ceiling=ceiling, ttl=3600)

    def test_gate_without_boundary_opens_gate(self):
        # Sample: A gate store exists and no boundary. Call fetch to example.com. Expect gate with reason gate-waiting and one gate row.
        self.gates = FakeGates()
        port = LocalGovernance(gate=EgressGate(self.gates, step_id='egress'))
        answer = port.check_tool_call('fetch', {'url': UNLISTED}, workflow_id='W-1')
        self.assertEqual((answer['decision'], answer['reason']), (GATE, 'gate-waiting'))
        self.assertEqual(len(self.gates.rows['W-1']), 1)

    def approve_as(self, role, workflow_id, subject):
        self.gates.approve_as(role, workflow_id, subject)

    def test_ceiling_exceeded_denied(self):
        # Sample: Boundary holds api.deepinfra.com:443 and the prover exits 1 for example.com:443. Call fetch to example.com. Expect deny with reason egress-ceiling-exceeded and no gate row.
        port = self.make_port(boundary=['api.deepinfra.com:443'], prover_code=1)
        answer = port.check_tool_call('fetch', {'url': UNLISTED}, workflow_id='W-1')
        self.assertEqual((answer['decision'], answer['reason']), (DENY, 'egress-ceiling-exceeded'))
        self.assertEqual(self.gates.rows, {})

    def test_prover_error_denied(self):
        # Sample: The prover exits 2. Call fetch to example.com. Expect deny with reason egress-ceiling-error.
        port = self.make_port(boundary=[], prover_code=2)
        answer = port.check_tool_call('fetch', {'url': UNLISTED}, workflow_id='W-1')
        self.assertEqual((answer['decision'], answer['reason']), (DENY, 'egress-ceiling-error'))

    def test_fails_closed_on_error(self):
        # Sample: The gate store raises RuntimeError while the call needs a gate. Expect deny with reason governance-error and no exception text in the answer.
        port = self.make_port(boundary=['example.com:443'])
        secret_text = 'sample' + '-secret'

        def broken(*args, **kwargs):
            raise RuntimeError('database password is ' + secret_text)
        self.gates.gate_state = broken
        answer = port.check_tool_call('fetch', {'url': UNLISTED}, workflow_id='W-1')
        self.assertEqual((answer['decision'], answer['reason']), (DENY, 'governance-error'))
        self.assertNotIn(secret_text, str(answer))

    def test_gate_needs_a_workflow(self):
        # Sample: Call fetch to example.com with no workflow id and no session id. Expect deny with reason workflow-required.
        port = self.make_port(boundary=['example.com:443'])
        answer = port.check_tool_call('fetch', {'url': UNLISTED})
        self.assertEqual((answer['decision'], answer['reason']), (DENY, 'workflow-required'))

    def test_wildcard_entry_matches_a_subdomain(self):
        # Sample: Body lists *.example.org:443. A call to https://api.example.org/x returns allow. A call to https://example.org/x returns deny.
        from durai.egress_presets import starter_body
        body = starter_body()
        body['presets']['wild'] = {'host': '*.example.org', 'port': 443}
        body['tiers']['balanced'].append('wild')
        port = LocalGovernance(body=body)
        self.assertEqual(port.check_tool_call('fetch', {'url': 'https://api.example.org/x'})['decision'], ALLOW)
        self.assertEqual(port.check_tool_call('fetch', {'url': 'https://example.org/x'})['decision'], DENY)

    def test_receipts_are_kept_and_map_to_ocsf(self):
        # Sample: Record two results. The adapter keeps two receipts. The OCSF event of the first has class_uid 6003 and holds no raw argument.
        port = self.make_port()
        port.record_result('fetch', {'url': LISTED}, 'ok', decision=ALLOW)
        port.record_result('fetch', {'url': LISTED}, 'ok2', decision=ALLOW)
        self.assertEqual(len(port.receipts), 2)
        event = port.to_ocsf(port.receipts[0])
        self.assertEqual(event['class_uid'], 6003)
        self.assertNotIn(LISTED, str(event))


if __name__ == '__main__':
    unittest.main()
