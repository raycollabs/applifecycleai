"""Sample: Select patterns for payment_funds.py from binding foundation-exchange-v2. Return idempotency and publication defaults with their correctness rules."""
import math
import re
from .common import Conflict, digest

WORD = re.compile(r'[a-z][a-z0-9]{2,}')
STOP = frozenset(('the', 'and', 'for', 'with', 'use', 'when', 'one', 'each', 'that', 'this', 'from', 'into', 'its', 'not',
                  'are', 'has', 'have', 'keep', 'check', 'prefer', 'all', 'any', 'can', 'only', 'before', 'after', 'than',
                  'return', 'returns', 'raise', 'raises', 'none', 'true', 'false', 'self', 'def', 'import', 'workspace'))
LIMIT = 3
MINIMUM = 1.0
RELATIVE = 0.5
CACHE = {}


def words(text):
    # Sample: Read "Store the outcome of request K-1". Return {"store", "outcome", "request"}.
    return {word for word in WORD.findall((text or '').lower()) if word not in STOP}


def pattern_text(pattern):
    # Sample: Read pattern architecture.idempotency.default. Return its id, rule, when, procedure, invariants, and checks as one text.
    preference = pattern.get('preference') or {}
    parts = [pattern['id'].replace('.', ' '), pattern['rule'], preference.get('preferred', '')]
    for key in ('procedure', 'invariants', 'checks'):
        parts += [item if isinstance(item, str) else str(item) for item in pattern.get(key, [])]
    parts += list((pattern.get('applicability') or {}).get('when', []))
    return '\n'.join(parts)


def cached_patterns(binding_digest, profile, loader):
    # Sample: Load binding digest B-1 for profile transaction twice. Check the loader runs once.
    key = (binding_digest, profile)
    if key not in CACHE:
        CACHE[key] = loader()
    return CACHE[key]


def framework_patterns(binding_id, profile='transaction', database='durai_local', role='durai_runtime',
                       *, audience='code-reviewer', revision=None):
    # Sample: Read binding foundation-exchange-v2 for profile transaction. Return the 14 architecture patterns and no trading-position pattern.
    from .db import transaction
    from .ontology_patterns import patterns
    from .packs import validate_pack
    with transaction(role, database) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        binding = db.execute('SELECT revision,document,digest FROM durai_ontology.binding WHERE id=%s '
                             'AND (%s::bigint IS NULL OR revision=%s) ORDER BY revision DESC LIMIT 1',
                             (binding_id, revision, revision)).fetchone()
        if not binding:
            raise KeyError('Binding unavailable in this scope')
        if digest(binding['document']) != binding['digest']:
            raise Conflict('Binding digest differs')

        def load():
            # Sample: Read 2 pinned packs with equal digests. Return their typed patterns for the profile.
            found = {}
            for reference in binding['document']['packs']:
                stored = db.execute('SELECT document,digest FROM durai_ontology.pack WHERE scope=%s AND id=%s AND version=%s',
                                    (reference['scope'], reference['id'], reference['version'])).fetchone()
                if not stored or stored['digest'] != reference['digest'] or validate_pack(stored['document']) != reference['digest']:
                    raise Conflict('Pinned pack is unavailable or changed')
                for pattern in patterns(stored['document']):
                    profiles = pattern['applicability']['profiles']
                    if (profile in profiles or '*' in profiles) and audience in pattern['audience']:
                        found.setdefault(pattern['id'], pattern)
            return {'bindingId': binding_id, 'bindingRevision': binding['revision'], 'bindingDigest': binding['digest'],
                    'profile': profile, 'packReferences': binding['document']['packs'],
                    'patterns': [found[key] for key in sorted(found)]}

        return cached_patterns(binding['digest'], (binding_id, profile, audience), load)


def task_text(task, files):
    # Sample: Read the funds task with payment_funds.py. Return the objective, the goal name, and the write-path code.
    parts = [task.get('objective', ''), (task.get('readiness') or {}).get('failingAcceptanceGoal') or task.get('failingAcceptanceGoal') or '']
    for path in task.get('writePaths', []):
        content = files.get(path)
        parts.append(content.decode('utf-8', 'replace') if isinstance(content, bytes) else content or '')
    return '\n'.join(parts)


def select_patterns(task, files, framework, limit=LIMIT):
    # Sample: Rank 36 patterns for the funds task. Return at most 3 ranked defaults with score 1.0 or more and half the top score or more, each followed by its correctness companion, its required defaults, and their correctness companions.
    catalog = {pattern['id']: pattern for pattern in framework['patterns']}
    texts = {key: words(pattern_text(pattern)) for key, pattern in catalog.items()}
    frequency = {}
    for terms in texts.values():
        for term in terms:
            frequency[term] = frequency.get(term, 0) + 1
    total = max(len(texts), 1)
    given = words(task_text(task, files))
    scores = {}
    for key, terms in texts.items():
        matched = terms & given
        if matched:
            scores[key] = round(sum(math.log(1 + total / frequency[term]) for term in matched) / math.sqrt(len(terms)), 4)
    written = set(task.get('writePaths', []))
    ranked = sorted((key for key in scores if catalog[key]['strength'] != 'REQUIRED' and
                     (not catalog[key].get('modules') or any('/workspace/durai/' + m + '.py' in written for m in catalog[key]['modules']))),
                    key=lambda key: (-scores[key], key))
    top = scores[ranked[0]] if ranked else 0.0
    ranked = [key for key in ranked if scores[key] >= MINIMUM and scores[key] >= RELATIVE * top]
    chosen = []

    def add(name):
        # Sample: Add architecture.idempotency.default twice. Check one entry in the chosen list.
        if name in catalog and name not in chosen:
            chosen.append(name)
    for picked, key in enumerate(ranked):
        if picked >= limit:
            break
        add(key)
        add(catalog[key].get('relatedPolicyId'))
        for name in catalog[key].get('requires', []):
            add(name)
            add((catalog.get(name) or {}).get('relatedPolicyId'))
    selected = [dict(catalog[key], score=scores.get(key, 0.0)) for key in chosen]
    return {'bindingId': framework['bindingId'], 'bindingDigest': framework['bindingDigest'], 'profile': framework['profile'],
            'patterns': selected, 'patternIds': chosen, 'digest': digest({'binding': framework['bindingDigest'], 'ids': chosen})}
