"""Sample: Bind the owner defaults. Read the worker view and retain a customer constraint as a proposal."""
from copy import deepcopy
import json
import unittest
from durai.architecture_defaults import architecture_defaults_pack, catalog_patterns
from durai.pack_data import read_design
from durai.architecture_choices import record_architecture_proposal
from durai.common import Conflict, digest, uid
from durai.context_store import ContextStore
from durai.db import prepare
from durai.ontology_delivery import agent_view, support_view, worker_view
from durai.ontology_patterns import patterns
from durai.packs import PackStore, generic_pack, validate_pack
from durai.policy_store import record_policies
from durai.runtime_deepagents import worker_rules
from durai.worker_contract import resolve_contract
from test_context_store import plan, status
from test_ontology_phase1 import domain

DATABASE = 'durai_test'
STORAGE = 'architecture.storage.default'


class DefaultsCatalogTests(unittest.TestCase):
    def test_seven_preferences_have_separate_correctness_rules(self):
        # Sample: Read seven preferences. Check each has a separate required correctness rule.
        selected = {p['id']: p for p in catalog_patterns()}
        defaults = [p for p in selected.values() if p['strength'] == 'DEFAULT']
        self.assertEqual(len(defaults), 7)
        self.assertEqual(len(selected), 14)
        for p in defaults:
            required = selected[p['relatedPolicyId']]
            self.assertEqual(required['strength'], 'REQUIRED')
            self.assertTrue(required['invariants'])
            self.assertNotIn('preference', required)
            self.assertNotEqual(p['rule'], required['rule'])

    def test_each_pattern_has_operating_guidance(self):
        # Sample: Read an outbox preference. Check its conditions, alternatives, evidence, and synthetic walkthrough.
        for p in catalog_patterns():
            self.assertTrue(p['procedure'] and p['checks'] and p['examples'] and p['evidence'])
            self.assertTrue(all(x.startswith('Sample:') for x in p['examples']))
            if p['strength'] == 'DEFAULT':
                for field in ('when', 'notApplicable', 'alternatives', 'exceptionEvidence'):
                    self.assertTrue(p['preference'][field])
            self.assertTrue(all(e['url'].startswith('https://') and e['checkedAt'] == '2026-11-18' for e in p['evidence']))

    def test_generic_base_is_preserved(self):
        # Sample: Build from G-10. Check base records, edges, and the comment rule are unchanged.
        base = generic_pack('10'); before = deepcopy(base)
        candidate = architecture_defaults_pack(base)
        self.assertEqual(base, before)
        self.assertEqual(candidate['records'][:len(base['records'])], base['records'])
        self.assertEqual(candidate['edges'], base['edges'])
        self.assertEqual(candidate['provenance']['derivedFrom']['digest'], validate_pack(base))
        self.assertEqual(candidate['kind'], 'GENERIC')
        self.assertEqual(len(patterns(candidate)), 14)

    def test_candidate_identity_tracks_content(self):
        # Sample: Build twice with equal input. Change one rule. Check only changed content gets a new identity.
        base = generic_pack('10')
        one = architecture_defaults_pack(base)
        self.assertEqual(one, architecture_defaults_pack(base))
        catalog = read_design('architecture-defaults-v1.json')
        catalog['patterns'][0]['rule'] += ' Check the declared recovery target.'
        changed = architecture_defaults_pack(base, catalog)
        self.assertNotEqual(one['version'], changed['version'])
        with self.assertRaises(Conflict):
            architecture_defaults_pack(one)

    def test_malformed_preference_is_refused(self):
        # Sample: Remove exception evidence, repeat an alternative, or add approval authority. Check refusal.
        candidate = architecture_defaults_pack(generic_pack('10'))
        mutations = [lambda p: p['preference'].pop('exceptionEvidence'),
            lambda p: p['preference']['alternatives'].append(deepcopy(p['preference']['alternatives'][0])),
            lambda p: p['preference'].update(approved=True)]
        for change in mutations:
            bad = deepcopy(candidate)
            row = next(r for r in bad['records'] if r['id'] == 'pattern.' + STORAGE)
            change(row['body']['policies'][0]); row['digest'] = digest(row['body'])
            with self.subTest(change=change), self.assertRaises(Conflict):
                patterns(bad)

    def test_correctness_examples_cover_failure_modes(self):
        # Sample: Read retry and delivery guidance. Check mismatch, uncertain result, duplicate, and overload contracts.
        selected = {p['id']: p for p in catalog_patterns()}
        self.assertIn('different request', ' '.join(selected['architecture.idempotency.default']['procedure']))
        self.assertIn('uncertain', selected['architecture.idempotency.correctness']['rule'])
        self.assertIn('atomically', selected['architecture.consumption.correctness']['rule'])
        self.assertIn('stale', selected['architecture.concurrency.correctness']['rule'])
        self.assertIn('Accepted work is durable', ' '.join(selected['architecture.jobs.correctness']['invariants']))
        self.assertIn('not a database rollback', ' '.join(selected['architecture.workflows.default']['procedure']))


class DefaultsDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Install the new candidate and G-10 on the disposable server. Bind each separately.
        prepare(DATABASE)
        cls.store = PackStore(DATABASE)
        cls.base = cls.store.install(generic_pack('10')); record_policies(cls.base, DATABASE)
        cls.old = 'defaults-old-' + uid()[:8]
        cls.store.bind(cls.old, [cls.base], 'SYNTHETIC_LOCAL')
        cls.before = worker_view(cls.old, database=DATABASE)
        cls.candidate = architecture_defaults_pack(generic_pack('10'))
        cls.reference = cls.store.install(cls.candidate); record_policies(cls.reference, DATABASE)
        cls.binding = 'defaults-new-' + uid()[:8]
        cls.store.bind(cls.binding, [cls.reference], 'SYNTHETIC_LOCAL')

    def test_coding_worker_receives_defaults_and_invariants(self):
        # Sample: Resolve reservations with the new binding. Check PostgreSQL, outbox steps, and required rules in the prompt.
        contract = resolve_contract(self.binding, domain(), DATABASE)
        typed = [p for p in contract['policies'] if 'pattern' in p]
        self.assertEqual(len(typed), 14)
        prompt = worker_rules(contract['policies'])
        for text in ('PostgreSQL', 'transactional-outbox-polling', 'exceptionEvidence', 'REQUIRED', 'same transaction'):
            self.assertIn(text, prompt)
        view = worker_view(self.binding, database=DATABASE, specification=domain())
        self.assertEqual({r['policyId'] for r in view['policies'] if 'pattern' in r}, {p['id'] for p in typed})
        self.assertFalse(contract['releaseAuthority'])

    def test_review_and_support_receive_defaults(self):
        # Sample: Read the binding as review and support. Check all 14 rules and no release or edit tool.
        for view in (agent_view(self.binding, 'code-reviewer', DATABASE, domain()), support_view(self.binding, DATABASE, domain())):
            self.assertEqual(len([p for p in view['policies'] if 'pattern' in p]), 14)
            self.assertFalse(view['releaseAuthority'])
            self.assertNotIn('write_file', view['allowedTools'])
            self.assertNotIn('refund', view['allowedTools'])

    def test_old_binding_is_unchanged(self):
        # Sample: Read the old binding after the new install. Check the same view and pack digest.
        self.assertEqual(worker_view(self.old, database=DATABASE), self.before)
        self.assertEqual(self.store.get(self.base), generic_pack('10'))

    def test_new_pack_is_immutable(self):
        # Sample: Change a candidate record under its installed version. Check immutable-pack refusal.
        changed = deepcopy(self.candidate)
        changed['records'][-1]['body']['policies'][0]['rule'] = 'Changed rule.'
        changed['records'][-1]['digest'] = digest(changed['records'][-1]['body'])
        with self.assertRaisesRegex(Conflict, 'immutable'):
            self.store.install(changed)


class ArchitectureChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare only the disposable database. Build one fixed candidate for proposal tests.
        prepare(DATABASE)
        cls.candidate = architecture_defaults_pack(generic_pack('10'))

    def setUp(self):
        # Sample: Open a new plan. Store a synthetic customer MySQL constraint as immutable progress.
        self.store, self.plan, self.revision = plan()
        record = self.store.append(self.plan, 'progress', {'step':'Read customer requirement C-1',
            'result':'C-1 requires the existing MySQL transactional service.', 'evidence':[]}, 'customer-constraint')
        self.evidence = {'kind':'record','id':record['key'],'digest':record['digest']}
        self.constraint = {STORAGE:{'selected':'MySQL','reason':'Retain customer requirement C-1.','evidence':[self.evidence]}}

    def propose(self, constraint=None, key='architecture-choice'):
        # Sample: Record a reservation architecture proposal with one fixed client request key.
        return record_architecture_proposal(self.store, self.plan, self.candidate, 'reservation',
            self.constraint if constraint is None else constraint, key)

    def test_default_and_exception_are_recorded(self):
        # Sample: Record PostgreSQL. Then propose MySQL with C-1. Check both immutable decisions.
        default = self.propose({}, 'default')
        storage = next(x for x in default['value']['alternatives'] if x['policyId'] == STORAGE)
        self.assertEqual(storage['selected'], 'PostgreSQL')
        self.assertEqual(storage['state'], 'DEFAULT_PROPOSED')
        exception = self.propose()
        choice = next(x for x in exception['value']['alternatives'] if x['policyId'] == STORAGE)
        self.assertEqual(choice['selected'], 'MySQL')
        self.assertEqual(choice['state'], 'EXCEPTION_PROPOSED')
        self.assertEqual(exception['value']['evidence'], [self.evidence])
        self.assertEqual(exception['value']['sourcePackDigest'], validate_pack(self.candidate))

    def test_exception_requires_resolvable_evidence(self):
        # Sample: Supply missing, stale, foreign, or unregistered evidence. Check no decision is written.
        foreign, foreign_plan, foreign_revision = plan(ContextStore(DATABASE, 'durai_foreign'))
        foreign_record = foreign.append(foreign_plan, 'progress', {'step':'Read foreign requirement',
            'result':'Foreign-only database requirement F-1.', 'evidence':[]}, 'foreign-customer')
        mutable = self.store.set_status(self.plan, status(self.revision), 0, 'running')
        cases = [[], [dict(self.evidence, digest='0'*64)], [dict(self.evidence, id='progress/999')],
            [{'kind':'record','id':foreign_record['key'],'digest':foreign_record['digest']}],
            [{'kind':'record','id':'status','digest':mutable['digest']}],
            [{'kind':'receipt','id':'missing.json','digest':'1'*64}]]
        for index, references in enumerate(cases):
            constraint = deepcopy(self.constraint); constraint[STORAGE]['evidence'] = references
            with self.subTest(index=index), self.assertRaises((Conflict, KeyError)):
                self.propose(constraint, 'bad-' + str(index))
        self.assertFalse(any(r['kind'] == 'decision' for r in self.store.read(self.plan)))

    def test_proposal_requires_paired_correctness(self):
        # Sample: Remove storage correctness from the candidate. Refuse its preference proposal.
        bad = deepcopy(self.candidate)
        bad['records'] = [r for r in bad['records'] if r['id'] != 'pattern.architecture.storage.correctness']
        with self.assertRaisesRegex(Conflict, 'correctness policy'):
            record_architecture_proposal(self.store, self.plan, bad, 'reservation', {}, 'missing-correctness')
        self.assertFalse(any(r['kind'] == 'decision' for r in self.store.read(self.plan)))

    def test_unknown_choice_is_refused(self):
        # Sample: Select an unlisted database or unknown pattern. Check refusal and no decision.
        bad = deepcopy(self.constraint); bad[STORAGE]['selected'] = 'unlisted-store'
        for constraint in (bad, {'architecture.unknown.default':self.constraint[STORAGE]}):
            with self.assertRaises(Conflict):
                self.propose(constraint)
        self.assertFalse(any(r['kind'] == 'decision' for r in self.store.read(self.plan)))

    def test_proposal_cannot_approve_or_mutate(self):
        # Sample: Add an approval flag. Refuse it. Store a normal proposal and refuse a changed replay.
        bad = deepcopy(self.constraint); bad[STORAGE]['approved'] = True
        with self.assertRaises(Conflict):
            self.propose(bad)
        record = self.propose()
        self.assertEqual(record['value']['authority'], 'PROPOSED')
        self.assertEqual(record['value']['executionAuthority'], 'NONE')
        changed = deepcopy(self.constraint); changed[STORAGE]['reason'] = 'Changed reason.'
        with self.assertRaises(Conflict):
            self.propose(changed)
        self.assertEqual(self.store.read(self.plan, record['key'])['digest'], record['digest'])
        self.assertFalse(any(r['kind'] == 'approval' for r in self.store.read(self.plan)))

    def test_replayed_request_is_idempotent(self):
        # Sample: Send R-1 twice. Check one key, one digest, and one decision record.
        one = self.propose(); two = self.propose()
        self.assertEqual(one['key'], two['key'])
        self.assertEqual(one['digest'], two['digest'])
        self.assertEqual(len([r for r in self.store.read(self.plan) if r['kind'] == 'decision']), 1)


if __name__ == '__main__':
    unittest.main()
