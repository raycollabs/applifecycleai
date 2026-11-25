"""Sample: Prepare durai_test with prompt_0001. Check revision immutability, builder views and immutable enhancement records."""
import unittest

import psycopg

from durai.common import uid
from durai.db import connect, prepare
from durai.prompt_store import prepare_prompt

from prompt_fixtures import DATABASE, add_concept, add_kind, add_template, evaluator

DIGEST = 'a' * 64


def as_builder(query, params=()):
    # Sample: Switch to durai_build_reader. Run the query. Return the rows.
    with connect('postgres', DATABASE) as db:
        db.execute('SET ROLE durai_build_reader')
        return db.execute(query, params).fetchall()


class PromptStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test and apply prompt_0001. Apply again. Check one receipt.
        prepare(DATABASE)
        prepare_prompt(DATABASE)

    def test_template_revision_is_immutable(self):
        # Sample: Insert template T-IDEMP-DESIGN revision 1. Update its body. Check refusal. Insert revision 2. Check accepted.
        kind, key = 'idem-' + uid()[:8], 'T-IDEMP-DESIGN-' + uid()[:6]
        with evaluator() as db:
            add_kind(db, kind)
            add_template(db, key, kind, 'DESIGN')
        with self.assertRaises(psycopg.errors.IntegrityConstraintViolation):
            with connect('postgres', DATABASE) as admin:
                admin.execute('UPDATE durai_prompt.template SET body=%s WHERE template_key=%s AND revision=1',
                              ('## Purpose\nChanged.\n## Inputs\nX\n## Checks\nY\n', key))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with evaluator() as db:
                db.execute("UPDATE durai_prompt.template SET body='x' WHERE template_key=%s", (key,))
        with evaluator() as db:
            add_template(db, key, kind, 'DESIGN', text='## Purpose\nNew.\n## Inputs\nNew.\n## Checks\nNew.\n', revision=2)
            rows = db.execute('SELECT revision FROM durai_prompt.template WHERE template_key=%s ORDER BY revision', (key,)).fetchall()
        self.assertEqual([1, 2], [row['revision'] for row in rows])
        with self.assertRaises(psycopg.errors.IntegrityConstraintViolation):
            with evaluator() as db:
                add_template(db, key + '-X', kind, 'PLAN', text='## Purpose\nOnly one section.\n')

    def test_builder_reads_published_only(self):
        # Sample: Insert concept C-1 DRAFT and C-2 REVIEWED. Read as the builder role. Check C-2 only. Select enhancement. Check permission denied.
        token = uid()[:8]
        first, second = 'c-draft-' + token, 'c-reviewed-' + token
        with evaluator() as db:
            add_concept(db, first, 'draft concept', 'A draft definition.', ['k-' + token], status='DRAFT')
            add_concept(db, second, 'reviewed concept', 'A reviewed definition.', ['k-' + token])
        rows = as_builder('SELECT concept_key FROM durai_prompt.published_concept WHERE %s = ANY(kind_keys)', ('k-' + token,))
        self.assertEqual([second], [row['concept_key'] for row in rows])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            as_builder('SELECT * FROM durai_prompt.enhancement')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            as_builder('SELECT * FROM durai_prompt.concept')

    def test_enhancement_is_immutable(self):
        # Sample: Insert enhancement E-1. Update enhanced_digest. Check refusal.
        ident = 'E-1-' + uid()[:8]
        with evaluator() as db:
            db.execute("INSERT INTO durai_prompt.enhancement(enhancement_id,request_digest,kind_key,enhanced_digest,mode) "
                       "VALUES(%s,%s,'idempotent-command',%s,'RULES')", (ident, DIGEST, DIGEST))
        with self.assertRaises(psycopg.errors.IntegrityConstraintViolation):
            with connect('postgres', DATABASE) as admin:
                admin.execute('UPDATE durai_prompt.enhancement SET enhanced_digest=%s WHERE enhancement_id=%s', ('b' * 64, ident))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with evaluator() as db:
                db.execute('UPDATE durai_prompt.enhancement SET enhanced_digest=%s WHERE enhancement_id=%s', ('b' * 64, ident))
        with evaluator() as db:
            row = db.execute('SELECT enhanced_digest FROM durai_prompt.enhancement WHERE enhancement_id=%s', (ident,)).fetchone()
        self.assertEqual(DIGEST, row['enhanced_digest'])


if __name__ == '__main__':
    unittest.main()
