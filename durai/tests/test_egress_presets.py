"""Sample: Run EgressPresetTests. Check the starter record, tier lists, revisions, the signed pack route and the gate."""
import copy
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai import egress_presets as presets  # noqa: E402
from durai.effective_view import EffectiveView  # noqa: E402
from durai.egress_gate import APPROVED, EgressGate  # noqa: E402
from durai.egress_policy import EgressPolicyError, build_policy  # noqa: E402
from durai.pack_registry import INSTALLED, PackRegistry  # noqa: E402
from durai.pack_signing import TrustedKeyList, sign_pack  # noqa: E402
from durai import workflow_gates  # noqa: E402


class ValidateTests(unittest.TestCase):
    def test_starter_body_is_valid_and_strict(self):
        # Sample: The starter body passes. Tier restricted is empty. Tier balanced allows api.deepinfra.com:443 only.
        body = presets.validate_body(presets.starter_body())
        self.assertEqual([], presets.allow_list(body, 'restricted'))
        self.assertEqual([('api.deepinfra.com', 443)], [(item['host'], item['port']) for item in presets.allow_list(body, 'balanced')])

    def test_bad_bodies_are_refused(self):
        # Sample: Wrong content kind, revision 0, an unknown tier, a tier with a missing preset, a bad preset name and an unsafe host each raise EgressPolicyError.
        def changed(edit):
            body = copy.deepcopy(presets.starter_body())
            edit(body)
            return body
        cases = [changed(lambda b: b.update(contentKind='OTHER')), changed(lambda b: b.update(revision=0)),
                 changed(lambda b: b['tiers'].update(wide=[])), changed(lambda b: b['tiers'].update(open=['nothing'])),
                 changed(lambda b: b['presets'].update({'Bad Name': {'host': 'example.com', 'port': 443}})),
                 changed(lambda b: b['presets'].update({'local': {'host': 'localhost', 'port': 80}})), 'text']
        for body in cases:
            with self.assertRaises(EgressPolicyError):
                presets.validate_body(body)

    def test_unknown_tier_in_lookup_is_refused(self):
        # Sample: Ask for tier wide. Raise EgressPolicyError.
        with self.assertRaises(EgressPolicyError):
            presets.allow_list(presets.starter_body(), 'wide')

    def test_record_wraps_body_with_digest(self):
        # Sample: The record has id sandbox.egress, layer DESIGN, type KNOWLEDGE and a digest of its body.
        record = presets.make_record(presets.starter_body())
        self.assertEqual(('sandbox.egress', 'DESIGN', 'KNOWLEDGE'), (record['id'], record['layer'], record['type']))
        self.assertEqual(record['digest'], presets.digest(record['body']))

    def test_policy_from_starter_has_one_network_rule(self):
        # Sample: Build the OpenShell policy from tier balanced. One rule exists for api.deepinfra.com.
        policy = build_policy(presets.allow_list(presets.starter_body(), 'balanced'))
        self.assertEqual(['egress_api_deepinfra_com_443'], list(policy['network_policies']))


class RevisionTests(unittest.TestCase):
    def test_with_rules_makes_revision_plus_one_and_keeps_the_old_body(self):
        # Sample: Add github-read to tier balanced. The new body has revision 2 and two balanced entries. The old body is unchanged.
        old = presets.starter_body()
        new = presets.with_rules(old, [('github-read', {'host': 'api.github.com', 'port': 443, 'access': 'read-only'})])
        self.assertEqual((1, 2), (old['revision'], new['revision']))
        self.assertEqual(['model-host', 'github-read'], new['tiers']['balanced'])
        self.assertEqual(['model-host'], old['tiers']['balanced'])

    def test_gate_question_names_host_port_binaries_and_access(self):
        # Sample: The question for api.github.com:443 read-only names the host, the port, the access, the protocol and /usr/bin/curl.
        text = presets.gate_question({'host': 'api.github.com', 'port': 443, 'access': 'read-only'})
        for part in ('api.github.com:443', 'read-only', 'rest', '/usr/bin/curl'):
            self.assertIn(part, text)


def signed_pack(body, key):
    # Sample: Make a FREE application pack with the egress record. Sign it with key id egress-test-signer.
    record = presets.make_record(body)
    document = {'format': 'durai-ontology-pack/v2', 'id': 'durai.egress-test', 'version': '1.0.0', 'kind': 'APPLICATION',
                'publisher': 'test', 'tier': 'FREE', 'license_ref': 'apache-2.0', 'min_shell_version': '1.0.0',
                'valid_from': None, 'valid_until': None, 'records': [record], 'edges': [], 'signature': None}
    return sign_pack(document, key, 'egress-test-signer')


class SignedPackTests(unittest.TestCase):
    def setUp(self):
        # Sample: Make a throwaway Ed25519 key. Trust it in a new registry.
        self.key = ed25519.Ed25519PrivateKey.generate()
        keys = TrustedKeyList()
        keys.add_key('egress-test-signer', self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
        self.registry = PackRegistry(trusted_keys=keys)

    def test_installed_pack_gives_its_body(self):
        # Sample: Install the pack with revision 2. from_view returns revision 2. The state is INSTALLED.
        body = presets.with_rules(presets.starter_body(), [('github-read', {'host': 'api.github.com', 'port': 443, 'access': 'read-only'})])
        entry = self.registry.install(signed_pack(body, self.key))
        self.assertEqual(INSTALLED, entry['state'])
        self.assertEqual(2, presets.from_view(EffectiveView(self.registry))['revision'])

    def test_view_without_the_record_gives_the_starter_body(self):
        # Sample: An empty registry gives the starter body, revision 1.
        self.assertEqual(presets.starter_body(), presets.from_view(EffectiveView(self.registry)))

    def test_unsigned_egress_record_is_not_trusted(self):
        # Sample: Install the pack signed by an unknown key. It does not reach the view. from_view returns the starter body.
        other = ed25519.Ed25519PrivateKey.generate()
        body = presets.with_rules(presets.starter_body(), [('evil', {'host': 'evil.example.com', 'port': 443})])
        self.registry.install(signed_pack(body, other))
        self.assertEqual(1, presets.from_view(EffectiveView(self.registry))['revision'])


ROW_TIME = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


class FakeGates:
    """Sample: Keep gate rows in memory. Read decisions with the real reviewer_decision function."""

    def __init__(self):
        self.rows, self.deadline = [], {}

    def propose_subject(self, workflow_id, step_id, when, subject, database):
        # Sample: Record the proposal once.
        return {'subject': subject}

    def open_gate(self, workflow_id, step_id, subject, question, deadline, database, when='before'):
        # Sample: Open the gate and keep the deadline. Return the question.
        self.deadline[subject] = deadline
        return {'question': question}

    def approve(self, subject, author, decision='APPROVE'):
        # Sample: Add an approval row by the given author.
        self.rows.append({'kind': 'approval', 'author': author, 'created_at': ROW_TIME,
                          'value': {'subjectDigest': subject, 'decision': decision, 'scope': 'egress'}})

    def gate_state(self, workflow_id, step_id, subject, database, when='before', now=None):
        # Sample: Return APPROVED or CANCELLED from a durai_reviewer row. Return WAITING when there is none and time remains.
        decision = workflow_gates.reviewer_decision(self.rows, subject, self.deadline[subject])
        if decision is not None:
            return workflow_gates.APPROVED if decision['value']['decision'] == 'APPROVE' else workflow_gates.CANCELLED
        return workflow_gates.WAITING


class EgressGateTests(unittest.TestCase):
    def setUp(self):
        # Sample: Make the gate bridge on the fake gates. Propose api.github.com:443 read-only with deadline 4102444800.
        self.fake = FakeGates()
        self.gate = EgressGate(self.fake, 'durai_test')
        self.proposal = self.gate.propose('W-1', {'host': 'api.github.com', 'port': 443, 'access': 'read-only'}, 4102444800)

    def test_proposal_has_digest_and_question(self):
        # Sample: The proposal holds a 64 character digest and a question that names api.github.com:443.
        self.assertEqual(64, len(self.proposal['subjectDigest']))
        self.assertIn('api.github.com:443', self.proposal['question'])

    def test_same_rule_gives_the_same_digest(self):
        # Sample: Propose the same rule twice. The digests are equal.
        again = self.gate.propose('W-1', 'api.github.com:443', 4102444800)
        self.assertNotEqual(again['subjectDigest'], self.proposal['subjectDigest'])
        same = self.gate.propose('W-1', {'host': 'api.github.com', 'port': 443, 'access': 'read-only'}, 4102444800)
        self.assertEqual(same['subjectDigest'], self.proposal['subjectDigest'])

    def test_runtime_cannot_approve_egress_rule(self):
        # Sample: durai_runtime approves. The state stays WAITING and the body does not change.
        self.fake.approve(self.proposal['subjectDigest'], 'durai_runtime')
        self.assertEqual('WAITING', self.gate.state('W-1', self.proposal))
        body = presets.starter_body()
        self.assertEqual(body, self.gate.apply('W-1', body, [self.proposal]))

    def test_reviewer_approval_makes_new_pack_revision(self):
        # Sample: durai_reviewer approves. The state is APPROVED. apply returns revision 2 with one new preset.
        self.fake.approve(self.proposal['subjectDigest'], 'durai_reviewer')
        self.assertEqual(APPROVED, self.gate.state('W-1', self.proposal))
        new = self.gate.apply('W-1', presets.starter_body(), [self.proposal])
        self.assertEqual(2, new['revision'])
        self.assertIn('api.github.com', [item['host'] for item in presets.allow_list(new, 'balanced')])

    def test_reviewer_rejection_means_no_change(self):
        # Sample: durai_reviewer rejects. The state is CANCELLED. The body is unchanged.
        self.fake.approve(self.proposal['subjectDigest'], 'durai_reviewer', 'REJECT')
        self.assertEqual('CANCELLED', self.gate.state('W-1', self.proposal))
        body = presets.starter_body()
        self.assertEqual(body, self.gate.apply('W-1', body, [self.proposal]))

    def test_unsafe_rule_is_refused_before_a_gate_opens(self):
        # Sample: Propose localhost:80. Raise EgressPolicyError. No gate state exists.
        with self.assertRaises(EgressPolicyError):
            self.gate.propose('W-1', 'localhost:80', 4102444800)


if __name__ == '__main__':
    unittest.main()
