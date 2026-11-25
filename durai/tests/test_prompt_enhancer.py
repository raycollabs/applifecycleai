"""Sample: Enhance a trade request against a unique test kind in durai_test. Check kind, verbatim words, questions, concept order, blind check and skill audit."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from durai.common import uid
from durai.db import connect, prepare
from durai.prompt_enhancer import (REQUIRED_FACTS, SEED_DIRECTORY, UNCLASSIFIED, BlindCheckError, Enhancer, SkillError, DbKnowledge, DbRecorder, audit_skill)
from durai.prompt_store import prepare_prompt

from prompt_fixtures import (DATABASE, add_concept, add_item, add_kind, add_link, add_skill, add_template, body, evaluator)

REQUEST = 'Let a trader book a trade. If it is sent twice, count it once.'
SKILL_TEXT = ('Fill every section of the template. Name the concepts you used. Cite the user\'s words. Keep the user\'s words. '
              'Ask about missing details instead of guessing. Do not claim tests you did not run. '
              'Build only what the request asks for. Offer unrequested details as next steps.')


def general_skill():
    # Sample: Insert the general skill SK-GENERAL revision 1 once. Insert again. Keep one row.
    from psycopg.types.json import Jsonb
    seed = json.loads((SEED_DIRECTORY / 'skills.json').read_text(encoding='utf-8'))['skills']
    text = next(row['prompt_text'] for row in seed if row['skill_key'] == 'SK-GENERAL')
    with evaluator() as db:
        db.execute("INSERT INTO durai_prompt.response_skill(skill_key,revision,kind_key,stage,prompt_text,required_facts,status,author,reviewer) "
                   "VALUES('SK-GENERAL',1,NULL,'ANY',%s,%s,'REVIEWED','durai-seed','reviewer-1') ON CONFLICT DO NOTHING", (text, Jsonb(list(REQUIRED_FACTS))))


def make_world(skill_text=SKILL_TEXT):
    # Sample: Insert kind idem-1a2b with concepts exactly-once and idempotency-key, two checklist items, four templates and four skills. Return the keys.
    token = uid()[:8]
    kind, decoy = 'idem-' + token, 'ledg-' + token
    keys = {'kind': kind, 'decoy': decoy, 'first': 'exactly-once-' + token, 'second': 'idempotency-key-' + token}
    with evaluator() as db:
        add_kind(db, kind)
        add_kind(db, decoy)
        add_concept(db, keys['first'], 'exactly-once effect', 'A booking that is sent twice has the effect of one booking. Count it once.', [kind])
        add_concept(db, keys['second'], 'idempotency key', 'A caller key that names one request so a resend is recognised and answered again.', [kind])
        add_concept(db, 'audit-' + token, 'audit trail', 'Record who booked what and when for every trade.', [kind])
        add_concept(db, 'window-' + token, 'duplicate window', 'The time a key is remembered after a trade is booked.', [kind])
        add_concept(db, 'ledger-' + token, 'ledger total', 'A sum of postings that must balance to zero.', [decoy])
        add_link(db, keys['first'], keys['second'])
        add_item(db, 'cl-currency-' + token, kind, 'Currency of amounts in a trade', 'Which currency do the amounts use?', ['usd', 'eur'], weight=3)
        add_item(db, 'cl-window-' + token, kind, 'Time a duplicate trade is recognised', 'How long must a repeated send be recognised?', ['minutes', 'hours'], weight=2)
        add_item(db, 'cl-broker-' + token, kind, 'Counterparty or broker field of a trade', 'Add a counterparty field to each trade, to show who is on the other side.',
                 ['counterparty', 'broker'], item_kind='OFFER')
        add_item(db, 'cl-ledger-' + token, decoy, 'Ledger balance rule', 'Which accounts must balance?', ['accounts'])
        for stage in ('DESIGN', 'PLAN', 'IMPLEMENTATION', 'TESTS'):
            add_template(db, 'T-' + stage + '-' + token, kind, stage)
            add_skill(db, 'SK-' + stage + '-' + token, kind, stage, skill_text, REQUIRED_FACTS)
    general_skill()
    return keys


class EnhancerBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test with prompt_0001. Insert one unique test kind with concepts, items, templates and skills.
        prepare(DATABASE)
        prepare_prompt(DATABASE)
        cls.world = make_world()

    def enhancer(self, **options):
        # Sample: Build an enhancer on durai_test with no manifest file and no model.
        options.setdefault('manifests', ())
        return Enhancer.for_database(DATABASE, **options)

    def enhance(self, request=REQUEST, **options):
        # Sample: Enhance the request for the test kind and the decoy kind only.
        enhancer = options.pop('enhancer', None) or self.enhancer()
        return enhancer.enhance(request, kinds=options.pop('kinds', [self.world['kind'], self.world['decoy']]), **options)


class EnhancerTests(EnhancerBase):
    def test_classifies_request_to_kind(self):
        # Sample: Request "Let a trader book a trade. If it is sent twice, count it once." Check kind idempotent-command and score above the threshold.
        verdict = self.enhancer().classify(REQUEST, [self.world['kind'], self.world['decoy']])
        self.assertEqual(self.world['kind'], verdict['kind'])
        self.assertGreater(verdict['score'], 0.3)
        self.assertEqual(self.world['kind'], verdict['ranked'][0]['kind'])

    def test_unclassified_gets_general_skill_only(self):
        # Sample: Request "Plan my vegetable garden for spring." Check kind unclassified and zero templates.
        result = self.enhance('Plan my vegetable garden for spring.', kinds=[self.world['kind']])
        self.assertEqual(UNCLASSIFIED, result['record']['kind'])
        self.assertEqual([], result['record']['templateDigests'])
        self.assertEqual([], result['record']['conceptKeys'])
        self.assertEqual(1, len(result['record']['skillDigests']))
        self.assertTrue(result['text'].startswith('Plan my vegetable garden for spring.'))
        self.assertNotIn('Stage DESIGN', result['text'])

    def test_history_fills_known_details(self):
        # Sample: History turn "All amounts are in USD." Check the currency question is absent and the other questions remain.
        class Turns:
            def search(self, conversation, term, limit):
                return [{'id': 'turn:3', 'excerpt': 'All amounts are in USD.'}] if term == 'usd' else []
        before = self.enhance()
        with patch('durai.session_settings.read_record', return_value={'enabled': True}):
            after = self.enhance(enhancer=self.enhancer(history_source=Turns()), session_ref={'conversationId': 'c-1'})
        self.assertIn('Which currency do the amounts use?', before['text'])
        self.assertNotIn('Which currency do the amounts use?', after['text'])
        self.assertIn('How long must a repeated send be recognised?', after['text'])
        self.assertEqual(1, len(before['record']['checklistKeys']) - len(after['record']['checklistKeys']))
        self.assertEqual('turn:3', after['record']['historyRefs'][0]['id'])
        self.assertEqual(hashlib.sha256(b'All amounts are in USD.').hexdigest(), after['record']['historyRefs'][0]['digest'])

    def test_history_is_empty_when_flag_is_off(self):
        # Sample: Switch the session flag off. Enhance with a session reference. Check no history item and the currency question remains.
        class Turns:
            def search(self, conversation, term, limit):
                raise AssertionError('The session must not be read')
        with patch('durai.session_settings.read_record', return_value={'enabled': False}):
            result = self.enhance(enhancer=self.enhancer(history_source=Turns()), session_ref={'conversationId': 'c-1'})
        self.assertEqual([], result['record']['historyRefs'])
        self.assertIn('Which currency do the amounts use?', result['text'])

    def test_offer_items_are_listed_not_asked(self):
        # Sample: Kind has an OFFER item "counterparty field". Enhance the trade request. Check the item under Next steps to offer and not among the questions.
        result = self.enhance()
        text = result['text']
        offer = 'Add a counterparty field to each trade, to show who is on the other side.'
        self.assertIn('Next steps to offer', text)
        self.assertGreater(text.index(offer), text.index('Next steps to offer'))
        asked = text.split('Next steps to offer')[0]
        self.assertNotIn(offer, asked)
        self.assertIn('Which currency do the amounts use?', asked)
        self.assertTrue(any(key.startswith('cl-broker-') for key in result['record']['checklistKeys']))
        covered = self.enhance(REQUEST + ' Show the broker on each trade.')
        self.assertNotIn(offer, covered['text'])

    def test_user_words_stay_verbatim(self):
        # Sample: Request R. Check enhanced text starts with R.
        result = self.enhance()
        self.assertTrue(result['text'].startswith(REQUEST + '\n'))
        self.assertEqual(hashlib.sha256(REQUEST.encode('utf-8')).hexdigest(), result['record']['requestDigest'])

    def test_requires_concepts_come_first(self):
        # Sample: Concept "exactly-once effect" REQUIRES "idempotency key". Check the key concept lists first.
        result = self.enhance()
        keys = result['record']['conceptKeys']
        self.assertLess(keys.index(self.world['second']), keys.index(self.world['first']))
        self.assertLess(result['text'].index('- idempotency key:'), result['text'].index('- exactly-once effect:'))
        self.assertIn('requires idempotency key', result['text'])

    def test_output_passes_blind_check(self):
        # Sample: Load a concept text that names a stem of the trade-pilot manifest. Check Conflict that names it.
        with tempfile.TemporaryDirectory() as folder:
            files = [{'path': 'internal/service/booking.go', 'size': 10, 'sha256': 'a' * 64}]
            lines = ''.join('{}\t{}\t{}\n'.format(item['path'], item['size'], item['sha256']) for item in files)
            manifest = {'root': folder, 'files': files, 'digest': hashlib.sha256(lines.encode('utf-8')).hexdigest()}
            path = Path(folder) / 'm.json'
            path.write_text(json.dumps(manifest), encoding='utf-8')
            enhancer = self.enhancer(manifests=[path])
            self.assertEqual(self.world['kind'], self.enhance(enhancer=enhancer)['record']['kind'])
            token = uid()[:8]
            kind = 'blind-' + token
            with evaluator() as db:
                add_kind(db, kind)
                add_concept(db, 'leak-' + token, 'booking flow', 'A trade booking sent twice is stored by internal/service/booking.go once.', [kind])
            with self.assertRaises(BlindCheckError) as caught:
                enhancer.enhance(REQUEST, kinds=[kind])
            self.assertIn('booking.go', str(caught.exception))

    def test_model_step_is_off_by_default_and_capped(self):
        # Sample: Enhance with a fake model and the flag off. Check 0 calls. Switch it on with a bad reply twice. Check 2 calls and the rules text.
        class Model:
            def __init__(self, replies):
                self.replies, self.calls = replies, 0

            def invoke(self, messages):
                self.calls += 1
                return SimpleNamespace(content=self.replies[min(self.calls, len(self.replies)) - 1],
                                       usage_metadata={'input_tokens': 1000, 'output_tokens': 100})
        silent = Model(['x'])
        plain = self.enhance(enhancer=self.enhancer(model=silent))
        self.assertEqual((0, 'RULES'), (silent.calls, plain['record']['mode']))
        bad = Model(['not the request', 'still not the request'])
        capped = self.enhance(enhancer=self.enhancer(model=bad), use_model=True)
        self.assertEqual((2, plain['text']), (bad.calls, capped['text']))
        self.assertEqual(('RULES_PLUS_MODEL', 2), (capped['record']['mode'], capped['record']['modelCalls']))
        self.assertEqual(0.006, capped['record']['cost'])
        good = Model([REQUEST + '\nClearer text.\n'])
        better = self.enhance(enhancer=self.enhancer(model=good), use_model=True)
        self.assertEqual((1, REQUEST + '\nClearer text.\n'), (good.calls, better['text']))

    def test_record_writes_one_row_only_when_asked(self):
        # Sample: Enhance with record off. Check 0 rows. Enhance with record on. Check 1 row with the enhanced digest.
        self.enhance()
        with connect('postgres', DATABASE) as db:
            count = lambda: db.execute('SELECT count(*) AS n FROM durai_prompt.enhancement WHERE kind_key=%s', (self.world['kind'],)).fetchone()['n']
            self.assertEqual(0, count())
            result = self.enhance(record=True, sample_id='S-TRADE-BOOK', build_label='L-1')
            self.assertEqual(1, count())
            row = db.execute('SELECT * FROM durai_prompt.enhancement WHERE enhancement_id=%s', (result['enhancementId'],)).fetchone()
        self.assertEqual((result['record']['enhancedDigest'], 'S-TRADE-BOOK', 'L-1'), (row['enhanced_digest'], row['sample_id'], row['build_label']))
        self.assertEqual(4, len(row['template_digests']))


class SkillTests(unittest.TestCase):
    def test_skill_without_required_fact_is_refused(self):
        # Sample: Load skill S-DESIGN without the template-fill rule. Check the audit names the missing fact.
        weak = SKILL_TEXT.replace('Fill every section of the template. ', '')
        with self.assertRaises(SkillError) as caught:
            audit_skill({'skill_key': 'S-DESIGN', 'prompt_text': weak})
        self.assertIn('fill every section of the template', str(caught.exception))
        audit_skill({'skill_key': 'S-DESIGN', 'prompt_text': SKILL_TEXT})
        prepare(DATABASE)
        prepare_prompt(DATABASE)
        world = make_world(skill_text=weak)
        with self.assertRaises(SkillError):
            Enhancer.for_database(DATABASE, manifests=()).enhance(REQUEST, kinds=[world['kind']], record=True)
        with connect('postgres', DATABASE) as db:
            rows = db.execute('SELECT count(*) AS n FROM durai_prompt.enhancement WHERE kind_key=%s', (world['kind'],)).fetchone()['n']
        self.assertEqual(0, rows)


if __name__ == '__main__':
    unittest.main()
