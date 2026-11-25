"""Sample: Load rubric revision 1 and 4 skills. Record 3 goal checks and 2 reviewer scores for an attempt. Print the report with NEEDS_ANCHOR items."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402
from durai.build_tools import BuildTools, ToolError  # noqa: E402
from durai.common import Conflict  # noqa: E402
from durai.db import transaction  # noqa: E402

WRITER = 'durai_evaluator'
REQUIRED_FACTS = ('cite evidence', 'score from code and test results', 'score each hidden goal separately',
                  'do not reward volume', 'same rubric', 'never show hidden goals or baseline files to a builder')
UI_FACTS = ('check the front end stack', 'next.js with typescript', 'at most 1', 'passing ui test')
REVIEW_STAGES = ('DESIGN', 'PLAN', 'IMPLEMENTATION', 'TESTS', 'UI_UX')


def facts_for(stage):
    # Sample: Stage UI_UX needs 10 facts. Stage DESIGN needs the 6 facts of REQUIRED_FACTS only.
    return REQUIRED_FACTS + UI_FACTS if stage == 'UI_UX' else REQUIRED_FACTS
REVIEWER_B = 'deepseek-flash-reviewer'
VERDICTS = ('PASS', 'FAIL', 'UNTESTABLE')
METHODS = ('EXECUTED', 'BASELINE_RECEIPT')
MAX_CALLS = 20
SHA = re.compile(r'^[a-f0-9]{64}$')


def digest_text(text):
    # Sample: Hash the text "abc". Return ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad.
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def digest_levels(stage, levels):
    # Sample: Hash stage DESIGN and levels 0 to 4 as sorted JSON. Return 64 hex characters.
    return digest_text(json.dumps({'stage': stage, 'levels': levels}, sort_keys=True, separators=(',', ':')))


def audit_skill(skill, facts=REQUIRED_FACTS):
    # Sample: Skill S-REVIEW-DESIGN lacks "cite evidence". Refuse and name cite evidence. Skill with all 6 facts passes.
    lower = ' '.join(skill.get('prompt_text', '').lower().split())
    missing = [fact for fact in facts if fact not in lower]
    if missing:
        raise ValueError('Skill {} lacks required fact: {}'.format(skill.get('skill_id'), '; '.join(missing)))


def read_json(path):
    # Sample: Read design/review/rubric-v1.json. Return the parsed object.
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_rubric(path, database='durai_local'):
    # Sample: Load 4 rubrics of revision 1. Return loaded 4. Load the same file again. Return unchanged 4. Load changed levels under revision 1. Refuse.
    data = read_json(path)
    revision, loaded, unchanged = data['revision'], 0, 0
    with transaction(WRITER, database) as db:
        for item in data['rubrics']:
            levels, stage = item['levels'], item['stage']
            if stage not in REVIEW_STAGES:
                raise ValueError('Unknown stage: ' + str(stage))
            digest = digest_levels(stage, levels)
            stored = db.execute('SELECT digest FROM durai_bench.review_rubric WHERE rubric_id=%s AND revision=%s',
                                (item['rubric_id'], revision)).fetchone()
            if stored:
                if stored['digest'] != digest:
                    raise Conflict('Rubric revision is immutable: {} revision {}'.format(item['rubric_id'], revision))
                unchanged += 1
                continue
            db.execute('INSERT INTO durai_bench.review_rubric(rubric_id,revision,stage,levels,digest) VALUES(%s,%s,%s,%s,%s)',
                       (item['rubric_id'], revision, stage, Jsonb(levels), digest))
            loaded += 1
    return {'loaded': loaded, 'unchanged': unchanged, 'revision': revision}


def load_skills(path, database='durai_local'):
    # Sample: Load 4 skills of revision 1 after the fact audit. Return loaded 4. A skill without "cite evidence" refuses all 4.
    data = read_json(path)
    revision, loaded, unchanged = data['revision'], 0, 0
    for item in data['skills']:
        audit_skill(item, facts_for(item['stage']))
    with transaction(WRITER, database) as db:
        for item in data['skills']:
            digest = digest_text(item['prompt_text'])
            stored = db.execute('SELECT digest FROM durai_bench.review_skill WHERE skill_id=%s AND revision=%s',
                                (item['skill_id'], revision)).fetchone()
            if stored:
                if stored['digest'] != digest:
                    raise Conflict('Skill revision is immutable: {} revision {}'.format(item['skill_id'], revision))
                unchanged += 1
                continue
            db.execute('INSERT INTO durai_bench.review_skill(skill_id,revision,stage,prompt_text,required_facts,digest) VALUES(%s,%s,%s,%s,%s,%s)',
                       (item['skill_id'], revision, item['stage'], item['prompt_text'], Jsonb(list(facts_for(item['stage']))), digest))
            loaded += 1
    return {'loaded': loaded, 'unchanged': unchanged, 'revision': revision}


def entries(path, key):
    # Sample: Read a JSON list, or an object with key "checks". Return the list.
    data = read_json(path)
    items = data[key] if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError('File must hold a list of ' + key)
    return items


def verify_file_digest(item):
    # Sample: Check file c.py holds sha256 X and the entry says X. Accept. The entry says Y. Refuse. The file is missing. Accept.
    path, claimed = item.get('check_path'), item.get('check_sha256')
    if claimed is not None and not SHA.match(str(claimed)):
        raise ValueError('check_sha256 is not 64 hex characters')
    if path and claimed and Path(path).is_file():
        actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if actual != claimed:
            raise ValueError('check_sha256 differs from the file {}: {}'.format(path, actual))


def record_checks(path, database='durai_local'):
    # Sample: Record 3 checks for attempt A-1 goals 0 to 2. Return recorded 3. A wrong check_sha256 refuses all 3. A goal index 7 refuses.
    items = entries(path, 'checks')
    for item in items:
        if item.get('verdict') not in VERDICTS:
            raise ValueError('Verdict must be one of ' + ', '.join(VERDICTS))
        if item.get('method') not in METHODS:
            raise ValueError('Method must be one of ' + ', '.join(METHODS))
        verify_file_digest(item)
    with transaction(WRITER, database) as db:
        for item in items:
            if item.get('attempt_id'):
                row = db.execute('SELECT app_key, app_version, component_key FROM durai_bench.build_attempt WHERE attempt_id=%s',
                                 (item['attempt_id'],)).fetchone()
                if not row:
                    raise ValueError('Unknown attempt: ' + item['attempt_id'])
            else:
                row = {name: item.get(name) for name in ('app_key', 'app_version', 'component_key')}
                if not all(row.values()):
                    raise ValueError('Baseline check needs app_key, app_version and component_key')
            try:
                db.execute('INSERT INTO durai_bench.goal_check(attempt_id,app_key,app_version,component_key,goal_index,verdict,method,'
                           'check_path,check_sha256,output_sha256,guidance_overlap,reviewer) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (item.get('attempt_id'), row['app_key'], row['app_version'], row['component_key'], item['goal_index'],
                            item['verdict'], item['method'], item.get('check_path'), item.get('check_sha256'), item.get('output_sha256'),
                            bool(item.get('guidance_overlap', False)), item['reviewer']))
            except psycopg.errors.IntegrityError as error:
                raise ValueError('Check refused: ' + str(error).splitlines()[0]) from error
    return {'recorded': len(items)}


def skill_digests(db, stage):
    # Sample: Stage DESIGN has skill S-REVIEW-DESIGN with digest D. Return the set holding D.
    return {row['digest'] for row in db.execute('SELECT digest FROM durai_bench.review_skill WHERE stage=%s', (stage,)).fetchall()}


def insert_scores(db, items):
    # Sample: Insert 4 stage scores of attempt A-1 from reviewer opus-reviewer. Refuse a score of 3 or 4 if the attempt has zero executed PASS checks. Return 4. A prompt digest of no stored skill refuses.
    for item in items:
        stage = item.get('stage')
        if stage not in REVIEW_STAGES:
            raise ValueError('Stage must be one of ' + ', '.join(REVIEW_STAGES))
        if not isinstance(item.get('score'), int) or isinstance(item.get('score'), bool) or not 0 <= item['score'] <= 4:
            raise ValueError('Score must be a whole number from 0 to 4')
        if not str(item.get('evidence', '')).strip():
            raise ValueError('Evidence is missing for ' + stage)
        if item.get('prompt_digest') not in skill_digests(db, stage):
            raise ValueError('Prompt digest matches no stored review skill for ' + stage)
        row = db.execute('SELECT app_key, app_version, component_key FROM durai_bench.build_attempt WHERE attempt_id=%s',
                         (item['attempt_id'],)).fetchone()
        if not row or not row['component_key']:
            raise ValueError('Unknown component attempt: ' + str(item['attempt_id']))
        checks = db.execute('SELECT verdict, method FROM durai_bench.goal_check WHERE attempt_id=%s',
                            (item['attempt_id'],)).fetchall()
        executed = [c for c in checks if c['method'] == 'EXECUTED']
        if item['score'] > 0 and not executed:
            raise ValueError('Score {} for stage {} is refused: attempt {} has zero executed goal checks'.format(
                item['score'], stage, item['attempt_id']))
        if item['score'] >= 3 and not any(c['verdict'] == 'PASS' for c in executed):
            raise ValueError('Score {} for stage {} is refused: attempt {} has no executed PASS goal checks'.format(
                item['score'], stage, item['attempt_id']))
        revision = str(item['rubric_revision'])
        revision = revision if revision.startswith('r') else 'r' + revision
        try:
            db.execute('INSERT INTO durai_bench.build_score(attempt_id,stage,app_key,app_version,component_key,reviewer_profile,score,baseline_score,'
                       'evidence,rubric_revision,prompt_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                       (item['attempt_id'], stage, row['app_key'], row['app_version'], row['component_key'], item['reviewer_profile'],
                        item['score'], item.get('baseline_score'), item['evidence'], revision, item['prompt_digest']))
        except psycopg.errors.IntegrityError as error:
            raise ValueError('Score refused: ' + str(error).splitlines()[0]) from error
    return len(items)


def record_scores(path, database='durai_local'):
    # Sample: Record 4 stage scores for attempt A-1. Return recorded 4. One refused score records none of the 4.
    items = entries(path, 'scores')
    with transaction(WRITER, database) as db:
        return {'recorded': insert_scores(db, items)}


class ReviewFiles:
    def __init__(self, workspace, archive):
        # Sample: Open workspace W-1 and archive W-1-artifacts. Names start with workspace/ or archive/.
        self.roots = {}
        for name, folder in (('workspace', workspace), ('archive', archive)):
            if Path(folder).is_dir():
                self.roots[name] = BuildTools(folder, archive=Path(folder) / '.unused')
        self.table = {
            'list_files': (self.list_files, 'List files of the build. Give workspace or archive, or a path below them.', {'path': {'type': 'string'}}, []),
            'read_file': (self.read_file, 'Read one text file. Path starts with workspace/ or archive/.', {'path': {'type': 'string'}}, ['path'])}

    def tool_list(self):
        # Sample: Return 2 tool records, list_files and read_file.
        return [{'name': name, 'description': description, 'inputSchema': {'type': 'object', 'properties': properties, 'required': required}}
                for name, (_, description, properties, required) in self.table.items()]

    def split(self, path):
        # Sample: Split workspace/src/a.py into workspace and src/a.py. Split ../x into a refusal.
        head, _, rest = str(path or '').replace('\\', '/').strip('/').partition('/')
        if head not in self.roots:
            raise ToolError('Path must start with workspace/ or archive/')
        return self.roots[head], rest

    def list_files(self, path=None):
        # Sample: List workspace. Return workspace/docs/design.md. List with no path. Return both roots.
        if path in (None, '', '.', '/'):
            return '\n'.join(sorted(self.roots)) or 'No folders'
        tools, rest = self.split(path)
        head = str(path).replace('\\', '/').strip('/').split('/')[0]
        listing = tools.list_files(rest or None)
        return listing if listing == 'No files' else '\n'.join(head + '/' + line for line in listing.splitlines())

    def read_file(self, path):
        # Sample: Read workspace/docs/design.md. Return its text. Read workspace/../x. Return an error.
        tools, rest = self.split(path)
        if not rest:
            raise ToolError('Give a file path')
        return tools.read_file(rest)

    def call(self, name, arguments):
        # Sample: Call read_file with a missing file. Return isError true. Call write_file. Return isError true.
        try:
            if name not in self.table:
                raise ToolError('Unknown tool ' + str(name))
            function, _, properties, required = self.table[name]
            arguments = arguments or {}
            if any(field not in arguments for field in required) or set(arguments) - set(properties):
                raise ToolError('Arguments do not match the tool')
            return {'text': function(**arguments), 'isError': False}
        except ToolError as error:
            return {'text': 'ERROR: ' + str(error), 'isError': True}
        except Exception as error:
            return {'text': 'ERROR: ' + type(error).__name__ + ': ' + str(error)[:300], 'isError': True}


def attempt_context(db, attempt_id):
    # Sample: Read attempt A-1. Return the sample text, 2 hidden goals, 4 stage records, 3 check verdicts, and the active rubric and skills.
    attempt = db.execute('SELECT * FROM durai_bench.build_attempt WHERE attempt_id=%s', (attempt_id,)).fetchone()
    if not attempt or not attempt['component_key']:
        raise ValueError('Unknown component attempt: ' + attempt_id)
    goal = db.execute('SELECT g.hidden_goals, s.request_text FROM durai_bench.component_goal g JOIN durai_samples.sample_request s'
                      ' ON s.sample_id=g.sample_id AND s.revision=g.sample_revision WHERE g.app_key=%s AND g.app_version=%s AND g.component_key=%s',
                      (attempt['app_key'], attempt['app_version'], attempt['component_key'])).fetchone()
    stages = db.execute('SELECT stage, state, artifacts FROM durai_bench.build_stage WHERE attempt_id=%s', (attempt_id,)).fetchall()
    checks = db.execute('SELECT goal_index, verdict, method, guidance_overlap, reviewer FROM durai_bench.goal_check WHERE attempt_id=%s'
                        ' ORDER BY goal_index, reviewer', (attempt_id,)).fetchall()
    rubric = {row['stage']: row for row in db.execute('SELECT DISTINCT ON (stage) stage, revision, levels FROM durai_bench.review_rubric'
                                                      " WHERE status='ACTIVE' ORDER BY stage, revision DESC").fetchall()}
    skills = {row['stage']: row for row in db.execute('SELECT DISTINCT ON (stage) stage, prompt_text, digest FROM durai_bench.review_skill'
                                                      " WHERE status='ACTIVE' ORDER BY stage, revision DESC").fetchall()}
    return {'attempt': attempt, 'request': goal['request_text'], 'goals': goal['hidden_goals'], 'stages': stages,
            'checks': checks, 'rubric': rubric, 'skills': skills}


def review_prompt(context):
    # Sample: Build the prompt with the request, 2 numbered hidden goals, check verdicts, stage artifacts with SHA-256, rubric levels and skills.
    stages = [item['stage'] for item in context['stages']]
    lines = ['Review attempt ' + context['attempt']['attempt_id'] + ' for stages: ' + ', '.join(stages), '',
             'Request of the user:', context['request'], '', 'Hidden goals:']
    lines += ['{}. {}'.format(index, goal) for index, goal in enumerate(context['goals'])]
    lines += ['', 'Goal check verdicts already recorded (goal index, verdict, method, reviewer):']
    lines += ['{} {} {} {}'.format(c['goal_index'], c['verdict'], c['method'], c['reviewer']) for c in context['checks']] or ['none']
    lines += ['', 'Stage artifacts (path, SHA-256; the archive holds each file under its SHA-256):']
    for item in context['stages']:
        lines.append(item['stage'] + ' ' + item['state'] + ': ' + ', '.join('{} {}'.format(a['path'], a['sha256']) for a in item['artifacts']))
    for stage in stages:
        rubric, skill = context['rubric'].get(stage), context['skills'].get(stage)
        if not rubric or not skill:
            raise ValueError('No active rubric or skill for ' + stage)
        lines += ['', 'Stage ' + stage + ' rubric revision ' + str(rubric['revision']) + ':']
        lines += ['{}: {}'.format(level, rubric['levels'][str(level)]) for level in range(4, -1, -1)]
        lines += ['Skill: ' + skill['prompt_text']]
    lines += ['', 'Use list_files and read_file to read the build. Then answer with one JSON object only: '
              '{"scores": [{"stage": "DESIGN", "score": 3, "evidence": "docs/design.md:12 ..."}]} with one entry for each stage above.']
    return '\n'.join(lines)


def parse_scores(text, stages):
    # Sample: Reply holds a JSON object with 4 stage scores inside a code fence. Return the 4 entries. Reply with a missing stage. Refuse.
    match = re.search(r'\{.*\}', text or '', re.DOTALL)
    if not match:
        raise ValueError('Reply holds no JSON object')
    try:
        scores = json.loads(match.group(0))['scores']
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError('Reply JSON has no scores list') from error
    if not isinstance(scores, list) or sorted(str(item.get('stage')) for item in scores if isinstance(item, dict)) != sorted(stages):
        raise ValueError('Scores must hold exactly one entry for each stage: ' + ', '.join(stages))
    return scores


def reviewer_profile(provider):
    # Sample: Provider deepseek gives deepseek-flash-reviewer. Provider deepinfra-deepseek gives deepinfra-deepseek-reviewer.
    return REVIEWER_B if provider == 'deepseek' else provider + '-reviewer'


def review_deepseek(attempt_id, workspace, archive, model=None, database='durai_local', max_calls=MAX_CALLS, receipts=None, sleep=None,
                    provider='deepseek'):
    # Sample: Review attempt A-1 with a fake model. It reads 1 file, then answers 4 scores. Insert 4 scores for deepseek-flash-reviewer. Write the receipt with 2 calls.
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from durai.build_driver import estimate_cost, live_model, tool_specs, utc
    receipts = Path(receipts or ROOT / 'receipts')
    reviewer = reviewer_profile(provider)
    name = 'fake' if model else None
    if model is None:
        model, name = live_model(provider, 'verification')
    with transaction(WRITER, database) as db:
        context = attempt_context(db, attempt_id)
    stages = [item['stage'] for item in context['stages'] if item['stage'] in REVIEW_STAGES]
    files = ReviewFiles(workspace, archive)
    bound = model.bind_tools(tool_specs(files))
    messages = [SystemMessage(content='You are an independent reviewer of a software build. You act only through the tools you are given.'),
                HumanMessage(content=review_prompt(context))]
    started, calls, tokens_in, tokens_out, stop, detail, scores = utc(), 0, 0, 0, None, None, None
    while stop is None:
        if calls >= max_calls:
            stop = 'STOP_BUDGET'
            break
        if calls == max_calls - 1:
            messages.append(HumanMessage(content='This is your last call. Answer now with the JSON object.'))
        try:
            reply = bound.invoke(messages)
        except Exception as error:
            stop, detail = 'PROVIDER_ERROR', type(error).__name__ + ': ' + str(error)[:300]
            break
        usage = getattr(reply, 'usage_metadata', None) or {}
        calls += 1
        tokens_in += int(usage.get('input_tokens') or 0)
        tokens_out += int(usage.get('output_tokens') or 0)
        messages.append(reply)
        if not reply.tool_calls:
            try:
                scores = parse_scores(reply.content if isinstance(reply.content, str) else json.dumps(reply.content), stages)
                stop = 'FINISHED'
            except ValueError as error:
                stop, detail = 'BAD_REPLY', str(error)
            break
        for call in reply.tool_calls:
            outcome = files.call(call['name'], call.get('args') or {})
            messages.append(ToolMessage(content=outcome['text'], tool_call_id=call['id']))
    inserted = 0
    if scores is not None:
        items = [{'attempt_id': attempt_id, 'stage': item['stage'], 'reviewer_profile': reviewer, 'score': item.get('score'),
                  'evidence': item.get('evidence', ''), 'rubric_revision': context['rubric'][item['stage']]['revision'],
                  'prompt_digest': context['skills'][item['stage']]['digest']} for item in scores]
        try:
            with transaction(WRITER, database) as db:
                inserted = insert_scores(db, items)
        except ValueError as error:
            stop, detail = 'REFUSED', str(error)
    receipt = {'attempt_id': attempt_id, 'reviewer': reviewer, 'model': name, 'startedAt': started, 'finishedAt': utc(),
               'calls': calls, 'maxCalls': max_calls, 'inputTokens': tokens_in, 'outputTokens': tokens_out,
               'estimatedEnterpriseCost': estimate_cost(tokens_in, tokens_out), 'stopReason': stop, 'stopDetail': detail,
               'scoresInserted': inserted, 'promptDigests': {stage: context['skills'][stage]['digest'] for stage in stages}}
    receipts.mkdir(parents=True, exist_ok=True)
    (receipts / ('build-review-' + attempt_id + '-deepseek.json')).write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


def rate(rows):
    # Sample: 3 checks, 3 PASS. Return 3/3. No checks. Return 0/0.
    return '{}/{}'.format(sum(1 for row in rows if row['verdict'] == 'PASS'), len(rows))


def report(label=None, database='durai_local'):
    # Sample: Attempt A-1 has IMPLEMENTATION scores 4 and 2 and 3 PASS checks, one with overlap. Return pass_all 3/3, pass_without_overlap 2/2, flag NEEDS_ANCHOR.
    like = '%' + label + '%' if label else '%'
    with transaction(WRITER, database) as db:
        scores = db.execute('SELECT a.attempt_id, a.stage, a.score_a, a.score_b, a.baseline_score, a.anchor_score, a.difference, a.flag'
                            ' FROM durai_bench.reviewer_agreement a WHERE a.attempt_id LIKE %s ORDER BY a.attempt_id, a.stage', (like,)).fetchall()
        checks = db.execute("SELECT attempt_id, verdict, guidance_overlap FROM durai_bench.goal_check WHERE attempt_id LIKE %s", (like,)).fetchall()
    result = []
    for row in scores:
        mine = [item for item in checks if item['attempt_id'] == row['attempt_id']]
        result.append({**row, 'pass_all': rate(mine), 'pass_without_overlap': rate([item for item in mine if not item['guidance_overlap']])})
    return result


def main(argv=None):
    # Sample: Run "load-rubric design/review/rubric-v1.json". Print loaded 4.
    parser = argparse.ArgumentParser(description='Reviewer rubric, skills, goal checks, scores and report')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('load-rubric', 'load-skills', 'record-checks', 'record-scores'):
        sub = commands.add_parser(name)
        sub.add_argument('file')
        sub.add_argument('--database', default='durai_local')
    sub = commands.add_parser('review-deepseek')
    sub.add_argument('--attempt', required=True)
    sub.add_argument('--workspace', required=True)
    sub.add_argument('--archive', required=True)
    sub.add_argument('--max-calls', type=int, default=MAX_CALLS)
    sub.add_argument('--database', default='durai_local')
    sub.add_argument('--provider', default='deepseek')
    sub = commands.add_parser('report')
    sub.add_argument('--label')
    sub.add_argument('--database', default='durai_local')
    args = parser.parse_args(argv)
    try:
        if args.command == 'review-deepseek':
            print(json.dumps(review_deepseek(args.attempt, args.workspace, args.archive, database=args.database, max_calls=args.max_calls,
                                             provider=args.provider)))
        elif args.command == 'report':
            for row in report(args.label, args.database):
                print('\t'.join(str(row[key]) for key in ('attempt_id', 'stage', 'score_a', 'score_b', 'baseline_score', 'pass_all',
                                                           'pass_without_overlap', 'difference', 'anchor_score', 'flag')))
        else:
            function = {'load-rubric': load_rubric, 'load-skills': load_skills, 'record-checks': record_checks, 'record-scores': record_scores}[args.command]
            print(json.dumps(function(args.file, args.database)))
    except (ValueError, OSError, Conflict) as error:
        print('Refused: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
