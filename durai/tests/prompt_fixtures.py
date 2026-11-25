"""Sample: Insert a REVIEWED kind, concepts, a link, templates, checklist items and a skill for a unique test kind into durai_test."""
from psycopg.types.json import Jsonb

from durai.common import uid
from durai.db import transaction

DATABASE = 'durai_test'
SECTIONS = ('Purpose', 'Inputs', 'Checks')


def body(sections=SECTIONS):
    # Sample: Return "## Purpose\nText.\n## Inputs\nText.\n## Checks\nText.\n" for three sections.
    return ''.join('## ' + name + '\nText.\n' for name in sections)


def reviewer(status):
    # Sample: Return reviewer-1 for REVIEWED. Return None for DRAFT.
    return None if status == 'DRAFT' else 'reviewer-1'


def add_kind(db, kind_key, status='REVIEWED'):
    # Sample: Insert kind idem-1a2b3c REVIEWED with reviewer reviewer-1.
    db.execute("INSERT INTO durai_prompt.component_kind(kind_key,name,description,status,author,reviewer) VALUES(%s,%s,%s,%s,'author-1',%s)",
               (kind_key, kind_key, 'A test kind for ' + kind_key, status, reviewer(status)))


def add_concept(db, key, name, definition, kinds, status='REVIEWED', revision=1):
    # Sample: Insert concept idempotency-key-1a2b "idempotency key" for kind idem-1a2b revision 1 REVIEWED.
    db.execute("INSERT INTO durai_prompt.concept(concept_key,revision,name,definition,kind_keys,status,author,reviewer) "
               "VALUES(%s,%s,%s,%s,%s,%s,'author-1',%s)", (key, revision, name, definition, list(kinds), status, reviewer(status)))


def add_link(db, source, target, relation='REQUIRES', status='REVIEWED'):
    # Sample: Insert link exactly-once REQUIRES idempotency-key REVIEWED.
    db.execute("INSERT INTO durai_prompt.concept_link(from_concept,to_concept,relation,status,author,reviewer) "
               "VALUES(%s,%s,%s,%s,'author-1','reviewer-1')", (source, target, relation, status))


def add_template(db, key, kind, stage, text=None, revision=1, status='REVIEWED', sections=SECTIONS):
    # Sample: Insert template T-IDEM-DESIGN-1a2b revision 1 for stage DESIGN with sections Purpose, Inputs and Checks.
    db.execute("INSERT INTO durai_prompt.template(template_key,revision,kind_key,stage,body,required_sections,status,author,reviewer) "
               "VALUES(%s,%s,%s,%s,%s,%s,%s,'author-1',%s)",
               (key, revision, kind, stage, text or body(sections), list(sections), status, reviewer(status)))


def add_item(db, key, kind, text, question, terms=(), weight=1, status='REVIEWED', item_kind='ASK'):
    # Sample: Insert checklist item CL-CURRENCY-1a2b with question "Which currency do amounts use?" and answer term usd.
    db.execute("INSERT INTO durai_prompt.detail_checklist(item_key,revision,kind_key,item_text,question_text,answer_terms,weight,item_kind,status,author,reviewer) "
               "VALUES(%s,1,%s,%s,%s,%s,%s,%s,%s,'author-1',%s)", (key, kind, text, question, list(terms), weight, item_kind, status, reviewer(status)))


def add_skill(db, key, kind, stage, text, facts, status='REVIEWED'):
    # Sample: Insert skill S-DESIGN-1a2b for kind idem-1a2b and stage DESIGN with the six required facts.
    db.execute("INSERT INTO durai_prompt.response_skill(skill_key,revision,kind_key,stage,prompt_text,required_facts,status,author,reviewer) "
               "VALUES(%s,1,%s,%s,%s,%s,%s,'author-1',%s)", (key, kind, stage, text, Jsonb(list(facts)), status, reviewer(status)))


def new_kind():
    # Sample: Return a fresh kind key such as idem-1a2b3c4d.
    return 'idem-' + uid()[:8]


def evaluator():
    # Sample: Open a transaction as durai_evaluator on durai_test.
    return transaction('durai_evaluator', DATABASE)
