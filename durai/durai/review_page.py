"""Sample: Open /review. Post app deepseek-flash-r2, component trade-booking, aspect FUNCTION, decision APPROVED, score 3, comment "Retry counted once". Check one human_review row with reviewer user."""
import base64
import hashlib
from html import escape
from urllib.parse import parse_qs

from .db import transaction

BASELINE_KEY = 'trade-pilot'
BASELINE_DEFAULT_VERSION = 'E6'
BUILD_KEY = 'deepseek-flash-r2'
BASELINE_URL = 'http://127.0.0.1:54350'
BUILD_URL = 'http://127.0.0.1:54360'
ROWS = (('trade-booking', 'Trade booking'), ('desk-access', 'Desk access'), ('market-prices', 'Market prices'),
        ('position-pnl', 'Position and P&L'), ('cash-settlement', 'Cash settlement'), ('screens', 'Screens'),
        ('', 'Whole app'))
ASPECTS = ('FUNCTION', 'USABILITY', 'CORRECTNESS', 'DESIGN', 'OVERALL')
DECISIONS = ('APPROVED', 'CHANGES_REQUESTED', 'REJECTED', 'COMMENT_ONLY')
TARGETS = ('baseline', 'build')
REVIEWER = 'user'
LIMIT = 200
MAX_BODY = 16_000

STYLE = (
    ':root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#66665f;--line:#d8d8d2;--panel:#fff;--accent:#1f5fbf;--bad:#b42318}'
    '@media(prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ececE8;--muted:#9a9a92;--line:#33332f;--panel:#1f1f1d;--accent:#7aa7ff;--bad:#ff7b72}}'
    'body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif}'
    'header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}'
    'h1{font-size:16px;margin:0}h2{font-size:14px;margin:24px 0 8px}main{padding:16px;overflow-x:auto}'
    'a{color:var(--accent)}table{border-collapse:collapse;width:100%;background:var(--panel)}'
    'th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}'
    'th{color:var(--muted);font-weight:600}form{display:grid;gap:4px;min-width:220px}'
    'input,select,textarea,button{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:3px 6px}'
    'button{cursor:pointer;border-color:var(--accent);color:var(--accent)}.muted{color:var(--muted)}.error{color:var(--bad)}'
    'code{font-family:ui-monospace,Consolas,monospace;font-size:12px}')
STYLE_HASH = "'sha256-" + base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode() + "'"
POLICY = ("default-src 'none'; style-src " + STYLE_HASH + "; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


def parse_form(body):
    # Sample: Body "target=build&component=trade-booking&aspect=FUNCTION&decision=APPROVED&score=3&comment=Retry+counted+once&attempt_id=deepseek-flash-r2". Return those fields. Body with decision MAYBE. Raise ValueError.
    raw = parse_qs(body.decode('utf-8'), keep_blank_values=True, max_num_fields=20)
    field = lambda name: (raw.get(name) or [''])[0].strip()  # noqa: E731
    target, component = field('target'), field('component')
    if target not in TARGETS:
        raise ValueError('Unknown target')
    if component not in [key for key, _ in ROWS]:
        raise ValueError('Unknown component')
    if field('aspect') not in ASPECTS or field('decision') not in DECISIONS:
        raise ValueError('Unknown aspect or decision')
    score = field('score')
    if score not in ('', '0', '1', '2', '3', '4'):
        raise ValueError('Score must be 0 to 4')
    comment = (raw.get('comment') or [''])[0].strip()
    if len(comment) > 4000:
        raise ValueError('Comment is longer than 4000 characters')
    version = field('app_version') if target == 'baseline' else None
    attempt = field('attempt_id') if target == 'build' else None
    if not (version or attempt):
        raise ValueError('App version or attempt is missing')
    return {'target': target, 'component': component or None, 'aspect': field('aspect'), 'decision': field('decision'),
            'score': int(score) if score else None, 'comment': comment, 'app_version': version or None,
            'attempt_id': attempt or None}


def insert_review(fields, database):
    # Sample: Insert FUNCTION APPROVED for trade-booking of deepseek-flash-r2. Insert it again as REJECTED. Check the second row supersedes the first. Insert COMMENT_ONLY "Looks slow". Check it supersedes nothing and REJECTED stays latest. Return the new review id.
    key = BASELINE_KEY if fields['target'] == 'baseline' else BUILD_KEY
    with transaction('durai_evaluator', database) as db:
        previous = None if fields['decision'] == 'COMMENT_ONLY' else db.execute(
            'SELECT review_id FROM durai_bench.human_review_latest WHERE app_key=%s AND app_version IS NOT DISTINCT FROM %s '
            'AND attempt_id IS NOT DISTINCT FROM %s AND component_key IS NOT DISTINCT FROM %s AND aspect=%s',
            (key, fields['app_version'], fields['attempt_id'], fields['component'], fields['aspect'])).fetchone()
        row = db.execute(
            'INSERT INTO durai_bench.human_review(app_key,app_version,attempt_id,component_key,aspect,decision,score,comment,reviewer,'
            'supersedes_review_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING review_id',
            (key, fields['app_version'], fields['attempt_id'], fields['component'], fields['aspect'], fields['decision'],
             fields['score'], fields['comment'], REVIEWER, previous['review_id'] if previous else None)).fetchone()
    return row['review_id']


def load(database):
    # Sample: Read the baseline version E6 and the 200 newest reviews with a flag for each superseded one. Return both.
    with transaction('durai_reviewer', database) as db:
        db.execute('SET TRANSACTION READ ONLY')
        found = db.execute('SELECT app_version FROM durai_bench.app_benchmark WHERE app_key=%s ORDER BY recorded_at DESC LIMIT 1',
                           (BASELINE_KEY,)).fetchone()
        entries = db.execute(
            'SELECT h.review_id, h.app_key, h.app_version, h.attempt_id, h.component_key, h.aspect, h.decision, h.score, h.comment, '
            'h.reviewer, h.created_at, EXISTS(SELECT 1 FROM durai_bench.human_review n WHERE n.supersedes_review_id = h.review_id) AS superseded '
            'FROM durai_bench.human_review h ORDER BY h.created_at DESC, h.review_id DESC LIMIT %s', (LIMIT,)).fetchall()
    return (found['app_version'] if found else BASELINE_DEFAULT_VERSION), entries


def option_list(values, empty=False):
    # Sample: Values FUNCTION and DESIGN. Return two option tags. With empty True add a blank option first.
    return ('<option value=""></option>' if empty else '') + ''.join(
        '<option>' + escape(str(value)) + '</option>' for value in values)


def cell(target, component, label, default, token):
    # Sample: Target build, component trade-booking. Return a form that posts target build with the attempt deepseek-flash-r2 and the csrf token.
    field = 'app_version' if target == 'baseline' else 'attempt_id'
    caption = 'App version' if target == 'baseline' else 'Build attempt'
    return ('<form method="post" action="/review"><input type="hidden" name="csrf" value="' + escape(token) + '">'
            '<input type="hidden" name="target" value="' + target + '"><input type="hidden" name="component" value="' + escape(component) + '">'
            '<label class="muted">' + caption + ' <input name="' + field + '" value="' + escape(default) + '" required maxlength="64"></label>'
            '<select name="aspect" aria-label="Aspect">' + option_list(ASPECTS) + '</select>'
            '<select name="decision" aria-label="Decision">' + option_list(DECISIONS) + '</select>'
            '<select name="score" aria-label="Score 0 to 4">' + option_list(range(5), True) + '</select>'
            '<textarea name="comment" rows="2" maxlength="4000" placeholder="Comment for ' + escape(label) + '"></textarea>'
            '<button type="submit">Record</button></form>')


def render(database, token, scrub=lambda text: text, error=None):
    # Sample: Render with 1 earlier entry. Return a page with two columns, seven rows, links to ports 54350 and 54360, and the entry.
    version, entries = load(database)
    rows = ''
    for key, label in ROWS:
        rows += ('<tr><th>' + escape(label) + '</th><td>' + cell('baseline', key, label, version, token) + '</td><td>'
                 + cell('build', key, label, BUILD_KEY, token) + '</td></tr>')
    listed = ''
    for item in entries:
        target = item['app_version'] or item['attempt_id']
        listed += ('<tr' + (' class="muted"' if item['superseded'] else '') + '><td>' + escape(str(item['created_at'])[:19]) + '</td><td>'
                   + escape(item['app_key']) + ' <code>' + escape(target) + '</code></td><td>' + escape(item['component_key'] or 'whole app')
                   + '</td><td>' + escape(item['aspect']) + '</td><td>' + escape(item['decision']) + ('' if item['score'] is None else ' ' + str(item['score']))
                   + '</td><td>' + escape(scrub(item['comment'])) + '</td><td>' + escape(item['reviewer'])
                   + ('</td><td>superseded' if item['superseded'] else '</td><td>latest') + '</td></tr>')
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Durai review</title><style>' + STYLE + '</style></head><body><header><h1>Durai side-by-side review</h1>'
            '<a href="/">Dashboard</a></header><main>'
            + ('<p class="error">' + escape(error) + '</p>' if error else '') +
            '<table><thead><tr><th>Component</th><th><a href="' + BASELINE_URL + '" target="_blank" rel="noopener">Baseline trade-pilot</a></th>'
            '<th><a href="' + BUILD_URL + '" target="_blank" rel="noopener">DeepSeek build</a></th></tr></thead><tbody>' + rows + '</tbody></table>'
            '<h2>Earlier entries, newest first</h2>'
            + ('<table><thead><tr><th>When</th><th>App</th><th>Component</th><th>Aspect</th><th>Decision</th><th>Comment</th><th>Reviewer</th><th>State</th></tr></thead><tbody>'
               + listed + '</tbody></table>' if listed else '<p class="muted">No entries yet.</p>')
            + '</main></body></html>')
    return page.encode('utf-8')
