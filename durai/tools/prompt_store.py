"""Sample: Load design/prompt into durai_test as DRAFT. Show the counts of each kind. Check every seed text against both baseline manifests."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from psycopg.types.json import Jsonb  # noqa: E402
from durai.common import Conflict  # noqa: E402
from durai.db import transaction  # noqa: E402
from durai.prompt_enhancer import (DEFAULT_MANIFESTS, REQUIRED_FACTS, SEED_DIRECTORY, STAGES, Enhancer, SeedKnowledge, SkillError,  # noqa: E402
                                   audit_skill, blind_findings, load_manifest_terms)

WRITER = 'durai_evaluator'
FILES = (('kinds.json', 'kinds'), ('concepts.json', 'concepts'), ('templates.json', 'templates'), ('samples.json', 'samples'),
         ('checklists.json', 'items'), ('skills.json', 'skills'))
LIMITS = {'concepts': (6, 12), 'templates': (4, 4), 'samples': (3, 4), 'items': (6, 12)}


def read_seed(directory=SEED_DIRECTORY):
    # Sample: Read the six JSON files of design/prompt. Return a dict with kinds, concepts, templates, samples, items and skills.
    seed = {}
    for name, key in FILES:
        data = json.loads((Path(directory) / name).read_text(encoding='utf-8'))
        seed[key] = data[key]
        seed[key + '_revision'] = data['revision']
        seed[key + '_author'] = data['author']
    return seed


def seed_texts(directory=SEED_DIRECTORY):
    # Sample: Return every text of the seed with a label such as concept:idempotency-key. Return more than 200 texts.
    seed = read_seed(directory)
    texts = []
    for row in seed['kinds']:
        texts += [('kind:' + row['kind_key'], row['name'] + '\n' + row['description'])]
    for row in seed['concepts']:
        texts += [('concept:' + row['concept_key'], row['name'] + '\n' + row['definition'])]
    for row in seed['templates']:
        texts += [('template:' + row['template_key'], row['body'])]
    for row in seed['samples']:
        texts += [('sample:' + row['sample_key'], row['request_text'] + '\n' + row['enhanced_example'])]
    for row in seed['items']:
        texts += [('item:' + row['item_key'], row['item_text'] + '\n' + row['question_text'])]
    for row in seed['skills']:
        texts += [('skill:' + row['skill_key'], row['prompt_text'])]
    return texts


def check_seed(directory=SEED_DIRECTORY, manifests=DEFAULT_MANIFESTS):
    # Sample: Check the seed. Return no problems. Change one concept key in a link. Return the unknown concept. Add a baseline file name to a text. Return the finding.
    seed = read_seed(directory)
    problems = []
    kinds = {row['kind_key'] for row in seed['kinds']}
    concepts = {row['concept_key']: row for row in seed['concepts']}
    for row in seed['concepts']:
        for kind in row['kind_keys']:
            if kind not in kinds:
                problems.append('Concept {} names unknown kind {}'.format(row['concept_key'], kind))
        for link in row.get('links', []):
            if link['to'] not in concepts:
                problems.append('Concept {} links to unknown concept {}'.format(row['concept_key'], link['to']))
            if link['relation'] not in ('REQUIRES', 'CONFLICTS', 'REFINES', 'TESTED_BY'):
                problems.append('Concept {} has unknown relation {}'.format(row['concept_key'], link['relation']))
    for kind in sorted(kinds):
        counts = {'concepts': sum(kind in row['kind_keys'] for row in seed['concepts']),
                  'templates': sum(row['kind_key'] == kind for row in seed['templates']),
                  'samples': sum(row['kind_key'] == kind for row in seed['samples']),
                  'items': sum(row['kind_key'] == kind for row in seed['items'])}
        for name, (low, high) in LIMITS.items():
            if not low <= counts[name] <= high:
                problems.append('Kind {} has {} {}; allowed {} to {}'.format(kind, counts[name], name, low, high))
        stages = sorted(row['stage'] for row in seed['skills'] if row['kind_key'] == kind)
        if stages != sorted(STAGES):
            problems.append('Kind {} skills cover stages {}'.format(kind, stages))
    for row in seed['templates']:
        for section in row['required_sections']:
            if ('\n## ' + section + '\n') not in ('\n' + row['body'] + '\n'):
                problems.append('Template {} lacks section {}'.format(row['template_key'], section))
    for row in seed['templates']:
        if row['stage'] in ('DESIGN', 'PLAN') and 'Next steps offered' not in row['required_sections']:
            problems.append('Template {} lacks the section Next steps offered'.format(row['template_key']))
    for kind in sorted(kinds):
        if not any(row['kind_key'] == kind and row.get('item_kind') == 'OFFER' for row in seed['items']):
            problems.append('Kind {} has no OFFER item'.format(kind))
    for row in seed['samples']:
        if not row['enhanced_example'].startswith(row['request_text']):
            problems.append('Sample {} example does not start with the request'.format(row['sample_key']))
    for row in seed['skills']:
        try:
            audit_skill(row)
        except SkillError as error:
            problems.append(str(error))
    terms = load_manifest_terms(manifests)
    for label, text in seed_texts(directory):
        found = blind_findings(text, terms)
        if found:
            problems.append('Blind check {}: {}'.format(label, ', '.join(found)))
    enhancer = Enhancer(SeedKnowledge(directory), None, manifests=())
    for row in seed['samples']:
        kind = enhancer.classify(row['request_text'])['kind']
        if kind != row['kind_key']:
            problems.append('Sample {} classifies as {}'.format(row['sample_key'], kind))
    return problems


def insert_or_confirm(db, table, key_columns, key, columns, values, digest, status, reviewer):
    # Sample: Insert concept C-1 revision 1 as DRAFT. Insert it again. Return unchanged. Insert it as REVIEWED with a reviewer. Return updated. Insert changed text at revision 1. Refuse.
    where = ' AND '.join(column + '=%s' for column in key_columns)
    row = db.execute('SELECT digest, status FROM durai_prompt.' + table + ' WHERE ' + where, key).fetchone()
    if row is None:
        names = ','.join(key_columns + columns + ['status', 'author', 'reviewer'])
        marks = ','.join(['%s'] * (len(key_columns) + len(columns) + 3))
        db.execute('INSERT INTO durai_prompt.' + table + '(' + names + ') VALUES(' + marks + ')', key + values + [status, 'durai-seed', reviewer])
        return 'loaded'
    if row['digest'] != digest:
        raise Conflict('Stored {} {} differs from the file at the same revision'.format(table, key))
    if status != row['status'] and row['status'] == 'DRAFT':
        db.execute('UPDATE durai_prompt.' + table + ' SET status=%s, reviewer=%s WHERE ' + where, [status, reviewer] + key)
        return 'updated'
    return 'unchanged'


def load_seed(directory=SEED_DIRECTORY, database='durai_local', status='DRAFT', reviewer=None):
    # Sample: Load the seed as DRAFT. Return loaded counts. Load again. Return unchanged. Ask for REVIEWED without a reviewer. Refuse.
    from durai.prompt_enhancer import digest_text
    if status not in ('DRAFT', 'REVIEWED'):
        raise ValueError('Status must be DRAFT or REVIEWED')
    if status == 'REVIEWED' and not (reviewer and reviewer.strip()):
        raise ValueError('REVIEWED needs a reviewer name')
    problems = check_seed(directory, manifests=())
    if problems:
        raise ValueError('Seed is refused: ' + '; '.join(problems[:5]))
    seed = read_seed(directory)
    reviewer = reviewer if status == 'REVIEWED' else None
    counts = {}

    def count(kind, result):
        counts[kind + ':' + result] = counts.get(kind + ':' + result, 0) + 1
    with transaction(WRITER, database) as db:
        for row in seed['kinds']:
            count('kind', insert_or_confirm(db, 'component_kind', ['kind_key'], [row['kind_key']], ['name', 'description'],
                                            [row['name'], row['description']], digest_text(row['name'] + '\n' + row['description']), status, reviewer))
        revision = seed['concepts_revision']
        for row in seed['concepts']:
            count('concept', insert_or_confirm(db, 'concept', ['concept_key', 'revision'], [row['concept_key'], revision], ['name', 'definition', 'kind_keys'],
                                               [row['name'], row['definition'], row['kind_keys']], digest_text(row['name'] + '\n' + row['definition']), status, reviewer))
        for row in seed['concepts']:
            for link in row.get('links', []):
                found = db.execute('SELECT status FROM durai_prompt.concept_link WHERE from_concept=%s AND to_concept=%s AND relation=%s',
                                   (row['concept_key'], link['to'], link['relation'])).fetchone()
                if found is None:
                    db.execute('INSERT INTO durai_prompt.concept_link(from_concept,to_concept,relation,status,author,reviewer) VALUES(%s,%s,%s,%s,%s,%s)',
                               (row['concept_key'], link['to'], link['relation'], status, 'durai-seed', reviewer))
                    count('link', 'loaded')
                elif found['status'] == 'DRAFT' and status != 'DRAFT':
                    db.execute('UPDATE durai_prompt.concept_link SET status=%s, reviewer=%s WHERE from_concept=%s AND to_concept=%s AND relation=%s',
                               (status, reviewer, row['concept_key'], link['to'], link['relation']))
                    count('link', 'updated')
                else:
                    count('link', 'unchanged')
        revision = seed['templates_revision']
        for row in seed['templates']:
            digest = digest_text(row['body'] + '\n' + '\n'.join(row['required_sections']))
            count('template', insert_or_confirm(db, 'template', ['template_key', 'revision'], [row['template_key'], revision], ['kind_key', 'stage', 'body', 'required_sections'],
                                                [row['kind_key'], row['stage'], row['body'], row['required_sections']], digest, status, reviewer))
        revision = seed['samples_revision']
        for row in seed['samples']:
            count('sample', insert_or_confirm(db, 'sample_prompt', ['sample_key', 'revision'], [row['sample_key'], revision], ['kind_key', 'request_text', 'enhanced_example'],
                                              [row['kind_key'], row['request_text'], row['enhanced_example']], digest_text(row['request_text'] + '\n' + row['enhanced_example']), status, reviewer))
        revision = seed['items_revision']
        for row in seed['items']:
            count('item', insert_or_confirm(db, 'detail_checklist', ['item_key', 'revision'], [row['item_key'], revision],
                                            ['kind_key', 'item_text', 'question_text', 'answer_terms', 'weight', 'item_kind'],
                                            [row['kind_key'], row['item_text'], row['question_text'], row['answer_terms'], row['weight'], row.get('item_kind', 'ASK')],
                                            digest_text(row['item_text'] + '\n' + row['question_text']), status, reviewer))
        revision = seed['skills_revision']
        for row in seed['skills']:
            count('skill', insert_or_confirm(db, 'response_skill', ['skill_key', 'revision'], [row['skill_key'], revision], ['kind_key', 'stage', 'prompt_text', 'required_facts'],
                                             [row['kind_key'], row['stage'], row['prompt_text'], Jsonb(list(REQUIRED_FACTS))], digest_text(row['prompt_text']), status, reviewer))
    return counts


def show(database='durai_local'):
    # Sample: Count rows of each kind by table and status in durai_test. Return one line for each kind.
    lines = []
    with transaction(WRITER, database) as db:
        for kind in db.execute('SELECT kind_key, status FROM durai_prompt.component_kind ORDER BY kind_key').fetchall():
            key = kind['kind_key']
            counts = [db.execute('SELECT count(*) AS n FROM durai_prompt.concept WHERE %s = ANY(kind_keys)', (key,)).fetchone()['n']]
            for table in ('template', 'sample_prompt', 'detail_checklist', 'response_skill'):
                counts.append(db.execute('SELECT count(*) AS n FROM durai_prompt.' + table + ' WHERE kind_key=%s', (key,)).fetchone()['n'])
            lines.append('{}\t{}\tconcepts {} templates {} samples {} items {} skills {}'.format(key, kind['status'], *counts))
    return lines


def main(argv=None):
    # Sample: Run "load --database durai_test". Print the counts. Run "check". Print the problems and exit 1 when there are any.
    parser = argparse.ArgumentParser(description='Prompt enhancer seed loader')
    commands = parser.add_subparsers(dest='command', required=True)
    first = commands.add_parser('load')
    first.add_argument('--database', required=True)
    first.add_argument('--directory', default=str(SEED_DIRECTORY))
    first.add_argument('--status', default='DRAFT', choices=('DRAFT', 'REVIEWED'))
    first.add_argument('--reviewer')
    second = commands.add_parser('show')
    second.add_argument('--database', required=True)
    third = commands.add_parser('check')
    third.add_argument('--directory', default=str(SEED_DIRECTORY))
    args = parser.parse_args(argv)
    try:
        if args.command == 'load':
            print(json.dumps(load_seed(args.directory, args.database, args.status, args.reviewer), sort_keys=True))
        elif args.command == 'show':
            print('\n'.join(show(args.database)))
        else:
            problems = check_seed(args.directory)
            print('\n'.join(problems) if problems else 'Seed passes')
            return 1 if problems else 0
    except (ValueError, OSError, Conflict) as error:
        print('Refused: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
