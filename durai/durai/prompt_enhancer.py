"""Sample: Enhance "Let a trader book a trade. If it is sent twice, count it once." Check kind idempotent-command, the request first, open questions, concepts, four stage templates and a record."""
import hashlib
import json
import math
import re
from pathlib import Path

from psycopg.types.json import Jsonb

from .db import ROOT, transaction
from .plans_dir import plans_dir

STAGES = ('DESIGN', 'PLAN', 'IMPLEMENTATION', 'TESTS')
REQUIRED_FACTS = ('fill every section of the template', 'name the concepts you used', "cite the user's words",
                  "keep the user's words", 'ask about missing details instead of guessing', 'do not claim tests you did not run',
                  'build only what the request asks for', 'offer unrequested details as next steps')
DEFAULT_MANIFESTS = (plans_dir() / 'app-benchmark-drafts/trade-pilot-manifest.json',
                     plans_dir() / 'app-benchmark-drafts/xeditai-manifest.json')
SEED_DIRECTORY = ROOT / 'design/prompt'
MIN_MATCHED = 3
SCORE_WORDS = 8
MIN_COVERAGE = 0.3
MAX_QUESTIONS = 8
MAX_CONCEPTS = 8
MIN_CONCEPTS = 4
HISTORY_TOKENS = 800
HISTORY_SEARCHES = 12
MODEL_CALLS = 2
MODEL_COST_CAP = 5.0
INPUT_RATE = 2.0 / 1_000_000
OUTPUT_RATE = 10.0 / 1_000_000
STOP_WORDS = frozenset('a an and are as at be by for from has have if in is it its of on or that the then there this to was were will with'.split())
UNCLASSIFIED = 'unclassified'


class BlindCheckError(ValueError):
    pass


class SkillError(ValueError):
    pass


def digest_text(text):
    # Sample: Hash the text "abc". Return ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad.
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def stem(word):
    # Sample: Return book for books, count for counted and count for counting.
    word = word.lower()
    for suffix in ('ing', 'ed', 'es', 's'):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def lexemes(text):
    # Sample: Return the stems book, trade and twice for "Book a trade twice." and drop the word a.
    return {stem(word) for word in re.findall(r'[A-Za-z0-9]+', text) if word.lower() not in STOP_WORDS and len(word) > 1}


def audit_skill(skill, facts=REQUIRED_FACTS):
    # Sample: Skill SK-DESIGN lacks "fill every section of the template". Refuse and name that fact. A skill with all 6 facts passes.
    lower = ' '.join(skill.get('prompt_text', '').lower().split())
    missing = [fact for fact in facts if fact not in lower]
    if missing:
        raise SkillError('Skill {} lacks required fact: {}'.format(skill.get('skill_key'), '; '.join(missing)))


def terms_for(path):
    # Sample: Read manifest M-1 with booking.go. Return the file, stem and table term sets of that manifest.
    from tools.app_benchmark import baseline_terms, read_manifest
    manifest = read_manifest(path)
    try:
        return baseline_terms(manifest)
    except OSError:
        slim = dict(manifest, files=[item for item in manifest['files'] if not item['path'].lower().endswith('.sql')])
        return baseline_terms(slim)


def blind_findings(text, term_sets):
    # Sample: Check "Extend internal/service/booking.go" against a manifest with booking.go. Return file and path findings. Check "book once" and return none.
    from tools.app_benchmark import blind_violations
    found = []
    for terms in term_sets:
        found += blind_violations(text, terms)
    return sorted(set(found))


def load_manifest_terms(paths=DEFAULT_MANIFESTS):
    # Sample: Give two manifest paths and one missing file. Return one term set and skip the missing file.
    return [terms_for(path) for path in paths if Path(path).is_file()]


def stage_order(selected, links):
    # Sample: Concepts exactly-once and idempotency-key with exactly-once REQUIRES idempotency-key. Return idempotency-key first.
    keys = sorted(selected)
    needs = {key: {to for source, to, relation in links if source == key and relation == 'REQUIRES' and to in selected} for key in keys}
    ordered, placed = [], set()
    while len(ordered) < len(keys):
        ready = [key for key in keys if key not in placed and needs[key] <= placed]
        if not ready:
            ready = [key for key in keys if key not in placed][:1]
        ordered.append(ready[0])
        placed.add(ready[0])
    return ordered


def session_enabled():
    # Sample: Settings record {"enabled": false}. Return False. Settings that cannot be read. Return False.
    try:
        from .session_settings import load_session_settings
        return bool(load_session_settings().enabled)
    except Exception:
        return False


class SeedKnowledge:
    def __init__(self, directory=SEED_DIRECTORY):
        # Sample: Read the six seed files of design/prompt. Hold 9 kinds, their concepts, checklists, templates and skills in memory.
        directory = Path(directory)

        def read(name, key):
            return json.loads((directory / name).read_text(encoding='utf-8'))[key]
        self.kind_rows = {item['kind_key']: item for item in read('kinds.json', 'kinds')}
        self.concept_rows = {item['concept_key']: dict(item, digest=digest_text(item['name'] + '\n' + item['definition']))
                             for item in read('concepts.json', 'concepts')}
        self.item_rows = [dict(item, digest=digest_text(item['item_text'] + '\n' + item['question_text']), weight=item.get('weight', 1), item_kind=item.get('item_kind', 'ASK'))
                          for item in read('checklists.json', 'items')]
        self.template_rows = [dict(item, digest=digest_text(item['body'] + '\n' + '\n'.join(item['required_sections'])))
                              for item in read('templates.json', 'templates')]
        self.skill_rows = [dict(item, digest=digest_text(item['prompt_text'])) for item in read('skills.json', 'skills')]

    def kinds(self, only=None):
        # Sample: List 9 kind keys. With only=[idempotent-command] list 1.
        return [key for key in sorted(self.kind_rows) if only is None or key in only]

    def rank(self, request, only=None):
        # Sample: Rank "book a trade once even if resent". Return idempotent-command first. A stem in every kind weighs 0. A stem in one kind weighs most.
        wanted = lexemes(request)
        vocabulary = {}
        for kind in self.kinds():
            words = set()
            for concept in self.concept_rows.values():
                if kind in concept['kind_keys']:
                    words |= lexemes(concept['name'] + ' ' + concept['definition'])
            for item in self.item_rows:
                if item['kind_key'] == kind and item['item_kind'] == 'ASK':
                    words |= lexemes(item['item_text'])
            vocabulary[kind] = words
        scores = []
        for kind in self.kinds(only):
            hit = vocabulary[kind] & wanted
            if hit:
                weight = sum(math.log(len(vocabulary) / sum(word in words for words in vocabulary.values())) for word in hit)
                scores.append({'kind': kind, 'matched': len(hit), 'total': len(wanted), 'weight': round(weight, 6)})
        return sorted(scores, key=lambda row: (-row['weight'], -row['matched'], row['kind']))

    def concepts(self, kind, request):
        # Sample: Read the concepts of idempotent-command. Return each with the count of request stems it shares.
        wanted = lexemes(request)
        return [{'concept_key': row['concept_key'], 'name': row['name'], 'definition': row['definition'], 'digest': row['digest'],
                 'hits': len(lexemes(row['name'] + ' ' + row['definition']) & wanted)}
                for row in self.concept_rows.values() if kind in row['kind_keys']]

    def links(self, keys):
        # Sample: Read the links among two concepts. Return (exactly-once, idempotency-key, REQUIRES).
        found = []
        for key in keys:
            for link in self.concept_rows[key].get('links', []):
                if link['to'] in keys:
                    found.append((key, link['to'], link['relation']))
        return found

    def checklist(self, kind):
        # Sample: Read the 8 checklist items of idempotent-command. Return them with their weights.
        return [row for row in self.item_rows if row['kind_key'] == kind]

    def templates(self, kind):
        # Sample: Read the templates of idempotent-command. Return four, one for each stage.
        return {row['stage']: row for row in self.template_rows if row['kind_key'] == kind}

    def skills(self, kind):
        # Sample: Read the skills of idempotent-command. Return four. For kind None return the general skill.
        return {row['stage']: row for row in self.skill_rows if row.get('kind_key') == kind}


class DbKnowledge:
    def __init__(self, database='durai_local', role='durai_runtime'):
        # Sample: Open a reader on durai_test as durai_runtime. Read the published views only.
        self.database, self.role = database, role

    def _rows(self, query, args=()):
        with transaction(self.role, self.database) as db:
            return db.execute(query, args).fetchall()

    def kinds(self, only=None):
        # Sample: List the published kind keys. With only=[idempotent-command] list 1.
        rows = self._rows('SELECT kind_key FROM durai_prompt.published_kind ORDER BY kind_key')
        return [row['kind_key'] for row in rows if only is None or row['kind_key'] in only]

    def rank(self, request, only=None):
        # Sample: Rank "book a trade once even if resent" with PostgreSQL full-text stems. Return idempotent-command first. A stem in every kind weighs 0.
        rows = self._rows(
            "WITH q AS (SELECT tsvector_to_array(to_tsvector('english', %s)) AS lex),"
            " docs AS (SELECT unnest(kind_keys) AS kind_key, search FROM durai_prompt.published_concept"
            "          UNION ALL SELECT kind_key, search FROM durai_prompt.published_checklist WHERE item_kind = 'ASK'),"
            " words AS (SELECT DISTINCT d.kind_key, w AS word FROM docs d JOIN durai_prompt.published_kind k USING(kind_key),"
            "           unnest(tsvector_to_array(d.search)) w),"
            " idf AS (SELECT word, ln((SELECT count(*) FROM durai_prompt.published_kind)::float8 / count(*)) AS weight FROM words GROUP BY word),"
            " hits AS (SELECT w.kind_key, w.word, i.weight FROM words w JOIN idf i USING(word) CROSS JOIN q WHERE w.word = ANY(q.lex))"
            " SELECT kind_key, count(*) AS matched, round(sum(weight)::numeric, 6)::float8 AS weight, (SELECT cardinality(lex) FROM q) AS total"
            " FROM hits GROUP BY kind_key ORDER BY weight DESC, matched DESC, kind_key", (request,))
        return [{'kind': row['kind_key'], 'matched': row['matched'], 'total': row['total'], 'weight': row['weight']}
                for row in rows if only is None or row['kind_key'] in only]

    def concepts(self, kind, request):
        # Sample: Read the published concepts of idempotent-command with the count of request stems each shares.
        rows = self._rows(
            "SELECT concept_key, name, definition, digest, cardinality(ARRAY(SELECT unnest(tsvector_to_array(search))"
            " INTERSECT SELECT unnest(tsvector_to_array(to_tsvector('english', %s))))) AS hits"
            " FROM durai_prompt.published_concept WHERE %s = ANY(kind_keys) ORDER BY concept_key", (request, kind))
        return [dict(row) for row in rows]

    def links(self, keys):
        # Sample: Read the published links among two concepts. Return (exactly-once, idempotency-key, REQUIRES).
        rows = self._rows('SELECT from_concept, to_concept, relation FROM durai_prompt.published_link '
                          'WHERE from_concept = ANY(%s) AND to_concept = ANY(%s) ORDER BY link_id', (list(keys), list(keys)))
        return [(row['from_concept'], row['to_concept'], row['relation']) for row in rows]

    def checklist(self, kind):
        # Sample: Read the published checklist items of idempotent-command.
        return [dict(row) for row in self._rows(
            'SELECT item_key, item_text, question_text, answer_terms, weight, item_kind, digest FROM durai_prompt.published_checklist '
            'WHERE kind_key=%s ORDER BY item_key', (kind,))]

    def templates(self, kind):
        # Sample: Read the published templates of idempotent-command. Return one for each stage found.
        return {row['stage']: dict(row) for row in self._rows(
            'SELECT template_key, stage, body, required_sections, digest FROM durai_prompt.published_template WHERE kind_key=%s', (kind,))}

    def skills(self, kind):
        # Sample: Read the published skills of idempotent-command. For kind None read the general skill.
        rows = self._rows('SELECT skill_key, stage, prompt_text, required_facts, digest FROM durai_prompt.published_skill '
                          'WHERE kind_key IS NOT DISTINCT FROM %s', (kind,))
        return {row['stage']: dict(row) for row in rows}


class DbRecorder:
    def __init__(self, database='durai_local', role='durai_evaluator'):
        # Sample: Open a writer on durai_test as durai_evaluator.
        self.database, self.role = database, role

    def record(self, row):
        # Sample: Insert one enhancement row with its digests. Return its enhancement id.
        with transaction(self.role, self.database) as db:
            return db.execute(
                'INSERT INTO durai_prompt.enhancement(request_digest,session_ref,kind_key,template_digests,concept_keys,checklist_keys,history_refs,'
                'skill_digests,enhanced_digest,mode,model_calls,cost,sample_id,build_label) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                'RETURNING enhancement_id',
                (row['requestDigest'], row['sessionRef'], row['kind'], row['templateDigests'], row['conceptKeys'], row['checklistKeys'],
                 Jsonb(row['historyRefs']), row['skillDigests'], row['enhancedDigest'], row['mode'], row['modelCalls'], row['cost'],
                 row.get('sampleId'), row.get('buildLabel'))).fetchone()['enhancement_id']


class Enhancer:
    def __init__(self, knowledge, recorder=None, manifests=DEFAULT_MANIFESTS, term_sets=None, history_source=None, model=None, kinds=None):
        # Sample: Bind the published views of durai_test, a recorder, both manifests and no model. Hold no state between requests.
        self.knowledge, self.recorder, self.history_source, self.model, self.kinds = knowledge, recorder, history_source, model, kinds
        self.term_sets = load_manifest_terms(manifests) if term_sets is None else term_sets

    @classmethod
    def for_database(cls, database, **options):
        # Sample: Build an enhancer for durai_test that reads views as durai_runtime and records as durai_evaluator.
        return cls(DbKnowledge(database), DbRecorder(database), **options)

    @classmethod
    def offline(cls, directory=SEED_DIRECTORY, **options):
        # Sample: Build an enhancer from the seed files. It records nothing.
        return cls(SeedKnowledge(directory), None, **options)

    def classify(self, request, kinds=None):
        # Sample: Request "Let a trader book a trade. If it is sent twice, count it once." Return kind idempotent-command with a score above 0.3. Score is matched stems divided by the request stems, at most 8.
        ranked = self.knowledge.rank(request, kinds)
        for row in ranked:
            row['score'] = round(min(1.0, row['matched'] / min(row['total'], SCORE_WORDS)), 4) if row['total'] else 0.0
        if ranked and ranked[0]['matched'] >= MIN_MATCHED and ranked[0]['score'] >= MIN_COVERAGE:
            return {'kind': ranked[0]['kind'], 'score': ranked[0]['score'], 'ranked': ranked}
        return {'kind': UNCLASSIFIED, 'score': ranked[0]['score'] if ranked else 0.0, 'ranked': ranked}

    def history(self, session_ref, terms, max_tokens=HISTORY_TOKENS):
        # Sample: Session C-1 holds the turn "All amounts are in USD." Search the term usd. Return turn:3 with its digest and the excerpt.
        empty = {'items': [], 'text': ''}
        if not session_ref or self.history_source is None or not session_enabled():
            return empty
        conversation = session_ref['conversationId']
        found, seen = [], set()
        for term in list(terms)[:HISTORY_SEARCHES]:
            if 'session' in session_ref:
                rows = self.history_source.search(session_ref['session'], conversation, term, 3)
            else:
                rows = self.history_source.search(conversation, term, 3)
            for row in rows:
                if row['id'] not in seen:
                    seen.add(row['id'])
                    found.append(row)
        items, used = [], 0
        for row in found:
            cost = len(row['excerpt']) // 4 + 1
            if used + cost > max_tokens:
                break
            used += cost
            items.append({'id': row['id'], 'digest': digest_text(row['excerpt']), 'excerpt': row['excerpt']})
        return {'items': items, 'text': '\n'.join(item['excerpt'] for item in items)}

    def check_blind(self, text):
        # Sample: Check text that names booking.go against a manifest with booking.go. Raise BlindCheckError naming file booking.go.
        found = blind_findings(text, self.term_sets)
        if found:
            raise BlindCheckError('Enhanced text names baseline terms: ' + ', '.join(found))

    def gather(self, request, kind, history_text):
        # Sample: Gather 8 concepts, the unanswered checklist questions, 4 templates and 4 skills for idempotent-command.
        knowledge = self.knowledge
        concepts = {row['concept_key']: row for row in knowledge.concepts(kind, request)}
        chosen = [key for key, row in sorted(concepts.items(), key=lambda pair: (-pair[1]['hits'], pair[0])) if row['hits']][:MAX_CONCEPTS]
        for key in sorted(concepts):
            if len(chosen) >= MIN_CONCEPTS:
                break
            if key not in chosen:
                chosen.append(key)
        links = knowledge.links(list(concepts))
        for source, target, relation in links:
            if relation == 'REQUIRES' and source in chosen and target not in chosen and len(chosen) < MAX_CONCEPTS + 4:
                chosen.append(target)
        order = stage_order(set(chosen), links)
        known = lexemes(request + ' ' + history_text)
        open_items = [item for item in knowledge.checklist(kind)
                      if not (lexemes(' '.join(item.get('answer_terms') or [])) & known)]
        open_items.sort(key=lambda item: (-item['weight'], item['item_key']))
        questions = [item for item in open_items if item.get('item_kind', 'ASK') == 'ASK'][:MAX_QUESTIONS]
        offers = [item for item in open_items if item.get('item_kind') == 'OFFER'][:MAX_QUESTIONS]
        return {'concepts': [concepts[key] for key in order], 'links': links, 'questions': questions, 'offers': offers,
                'templates': knowledge.templates(kind), 'skills': knowledge.skills(kind)}

    def compose(self, request, kind, score, parts, history):
        # Sample: Compose the request first, then questions, concepts, and each stage with its template and skill.
        lines = [request, '', '--- Durai enhanced request (kind: ' + kind + ', match ' + str(score) + ') ---']
        if history['items']:
            lines += ['Known from earlier turns:'] + ['- ' + item['excerpt'] for item in history['items']]
        if parts['questions']:
            lines += ['Details the request does not give. Ask the user, or state your assumption and mark it:']
            lines += [str(number) + '. ' + item['question_text'] for number, item in enumerate(parts['questions'], 1)]
        if parts['offers']:
            lines += ['Next steps to offer. The request does not ask for them. Do not build them. List them for the user:']
            lines += ['- ' + item['question_text'] for item in parts['offers']]
        if parts['concepts']:
            names = {row['concept_key']: row['name'] for row in parts['concepts']}
            lines += ['Concepts to apply (required ones first):']
            for row in parts['concepts']:
                notes = ['{} {}'.format(relation.lower().replace('_', ' '), names[target]) for source, target, relation in parts['links']
                         if source == row['concept_key'] and target in names]
                lines.append('- ' + row['name'] + ': ' + row['definition'] + (' (' + '; '.join(notes) + ')' if notes else ''))
        for stage in STAGES:
            if stage in parts['templates']:
                lines += ['', 'Stage ' + stage + ' answer format:', parts['templates'][stage]['body'].rstrip()]
            if stage in parts['skills']:
                lines += ['How to answer in stage ' + stage + ': ' + parts['skills'][stage]['prompt_text'].strip()]
        return '\n'.join(lines) + '\n'

    def rewrite(self, text, request, max_calls=MODEL_CALLS, cost_cap=MODEL_COST_CAP):
        # Sample: A fake model returns the request first and a clearer body. Use it after 1 call. A reply without the request. Retry once, then keep the rules text.
        from langchain_core.messages import HumanMessage, SystemMessage
        calls = input_tokens = output_tokens = 0
        feedback = ''
        system = ('Rewrite the enhanced request more clearly. Keep the first lines exactly as given, keep every section and every question, '
                  'and add no file names, table names or module names.')
        while calls < max_calls and input_tokens * INPUT_RATE + output_tokens * OUTPUT_RATE < cost_cap:
            reply = self.model.invoke([SystemMessage(content=system), HumanMessage(content=text + feedback)])
            calls += 1
            usage = getattr(reply, 'usage_metadata', None) or {}
            input_tokens += int(usage.get('input_tokens') or 0)
            output_tokens += int(usage.get('output_tokens') or 0)
            answer = str(getattr(reply, 'content', '') or '')
            if answer.startswith(request) and not blind_findings(answer[len(request):], self.term_sets):
                text = answer if answer.endswith('\n') else answer + '\n'
                break
            feedback = '\n\nThe last rewrite was refused. Start with the request exactly and name no baseline file.'
        return text, calls, round(input_tokens * INPUT_RATE + output_tokens * OUTPUT_RATE, 6)

    def enhance(self, request, session_ref=None, kinds=None, use_model=False, record=False, sample_id=None, build_label=None):
        # Sample: Enhance the request with record on. Return the text, a record with digests and one enhancement id. With record off write no row.
        if not isinstance(request, str) or not request.strip():
            raise ValueError('Request must be a non-empty text')
        verdict = self.classify(request, kinds or self.kinds)
        kind = verdict['kind']
        history = {'items': [], 'text': ''}
        if kind == UNCLASSIFIED:
            skills = self.knowledge.skills(None)
            parts = {'questions': [], 'offers': [], 'concepts': [], 'links': [], 'templates': {}, 'skills': {}}
            general = skills.get('ANY')
            audited = [general] if general else []
        else:
            checklist = self.knowledge.checklist(kind)
            terms = sorted(lexemes(request))[:4] + [item['answer_terms'][0] for item in checklist if item.get('answer_terms')]
            history = self.history(session_ref, terms)
            parts = self.gather(request, kind, history['text'])
            audited = [parts['skills'][stage] for stage in STAGES if stage in parts['skills']]
        for skill in audited:
            audit_skill(skill)
        if kind == UNCLASSIFIED:
            text = request + '\n\n--- Durai enhanced request (kind: unclassified) ---\n' + (general['prompt_text'].strip() + '\n' if general else '')
        else:
            text = self.compose(request, kind, verdict['score'], parts, history)
        calls, cost = 0, 0.0
        if use_model and self.model is not None:
            text, calls, cost = self.rewrite(text, request)
        added = text[len(request):] if text.startswith(request) else text
        for item in history['items']:
            added = added.replace(item['excerpt'], '')
        self.check_blind(added)
        row = {'requestDigest': digest_text(request), 'sessionRef': session_ref['conversationId'] if session_ref else None, 'kind': kind,
               'templateDigests': [parts['templates'][stage]['digest'] for stage in STAGES if stage in parts['templates']],
               'conceptKeys': [item['concept_key'] for item in parts['concepts']],
               'checklistKeys': [item['item_key'] for item in parts['questions'] + parts['offers']],
               'historyRefs': [{'id': item['id'], 'digest': item['digest']} for item in history['items']],
               'skillDigests': [item['digest'] for item in audited], 'enhancedDigest': digest_text(text),
               'mode': 'RULES_PLUS_MODEL' if calls else 'RULES', 'modelCalls': calls, 'cost': cost, 'score': verdict['score'],
               'sampleId': sample_id, 'buildLabel': build_label}
        enhancement_id = None
        if record:
            if self.recorder is None:
                raise ValueError('No recorder is configured')
            enhancement_id = self.recorder.record(row)
        return {'text': text, 'record': row, 'enhancementId': enhancement_id}
