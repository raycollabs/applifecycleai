"""Sample: Detect "Build a trade blotter screen." Return the default theme transaction-heavy. Compose the request first, then four theme options and CSS tokens."""
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / 'design/prompt/themes/catalog.json'
KIND = 'ui-theme'


class ThemeCatalogError(ValueError):
    """Sample: Change one word in catalog.json. Load it. Raise this error that names the digest."""


def catalog_digest(catalog):
    # Sample: Hash the catalog without its digest field. Return 64 hex characters. The same data always gives the same value.
    body = {key: value for key, value in catalog.items() if key != 'digest'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def load_catalog(path=None):
    # Sample: Read catalog.json. Check its digest. Return the parsed catalog. A changed file raises ThemeCatalogError.
    catalog = json.loads(Path(path or CATALOG_PATH).read_text(encoding='utf-8'))
    if catalog.get('digest') != catalog_digest(catalog):
        raise ThemeCatalogError('Theme catalog digest differs: ' + str(catalog.get('digest')))
    return catalog


def enabled(environ=None):
    # Sample: DURAI_ENHANCE=0 returns False. An unset variable returns True.
    return (os.environ if environ is None else environ).get('DURAI_ENHANCE', '1') != '0'


def words(text):
    # Sample: "Build a Trade-blotter screen" returns the lower case words build, a, trade, blotter, screen.
    return re.findall(r'[a-z0-9]+', text.lower())


def has_term(tokens, terms):
    # Sample: Terms [front end] match the words of "a front end for orders". Terms [app] do not match the word apple.
    spaced = ' ' + ' '.join(tokens) + ' '
    return any(' ' + ' '.join(words(term)) + ' ' in spaced for term in terms)


def choose(request, catalog):
    # Sample: "Build a trade blotter" returns transaction-heavy. "theme: report-heavy" returns report-heavy. No match returns the catalog default.
    ids = [theme['id'] for theme in catalog['themes']]
    match = re.search(catalog['rules']['choicePattern'], request.lower())
    if match and match.group(1) in ids:
        return match.group(1), True
    tokens = words(request)
    scores = {theme['id']: sum(1 for term in theme['whenWords'] if has_term(tokens, [term])) for theme in catalog['themes']}
    best = max(scores.values())
    if best == 0:
        return catalog['default'], False
    return next(theme_id for theme_id in ids if scores[theme_id] == best), False


def detect(request, catalog=None, environ=None):
    # Sample: "Build a trade blotter screen" returns default transaction-heavy. "Why does this loop fail" returns None. DURAI_ENHANCE=0 returns None.
    if not enabled(environ) or not isinstance(request, str) or not request.strip():
        return None
    catalog = catalog or load_catalog()
    tokens = words(request)
    rules = catalog['rules']
    if not has_term(tokens, rules['buildVerbs']) or not has_term(tokens, rules['appNouns']):
        return None
    theme_id, chosen = choose(request, catalog)
    return {'kind': KIND, 'default': theme_id, 'chosenByUser': chosen}


def css_block(theme):
    # Sample: Tokens color-bg and radius return ":root {" then "  --color-bg: #f5f6f8;" then "  --radius: 6px;" then "}".
    return ':root {\n' + '\n'.join('  --' + key + ': ' + value + ';' for key, value in theme['tokens'].items()) + '\n}'


def compose(request, detection, catalog=None):
    # Sample: Compose the request first. Then list four themes with the default marked. Then give layout, components, checks and CSS tokens of the default.
    catalog = catalog or load_catalog()
    theme = next(item for item in catalog['themes'] if item['id'] == detection['default'])
    lines = [request, '', '--- Durai UI theme options (kind: ' + KIND + ') ---']
    for item in catalog['themes']:
        mark = ' (default)' if item['id'] == theme['id'] else ''
        lines.append('- ' + item['id'] + mark + ': ' + item['name'] + '. Use for: ' + ', '.join(item['whenWords'][:5]) + '.')
    lines.append('Theme used: ' + theme['id'] + ('. The user chose it.' if detection['chosenByUser'] else '. Chosen from the request words.'))
    lines.append('Layout: ' + '; '.join(theme['layout']) + '.')
    lines.append('Components: ' + ', '.join(theme['components']) + '.')
    lines.append('Acceptance checks: ' + '; '.join(theme['acceptance']) + '.')
    lines.append(css_block(theme))
    lines += catalog['instructions']
    return '\n'.join(lines) + '\n'


def enhance(request, environ=None):
    # Sample: Return None for a code question. Return text, kind, theme id and the digest of the text for a build request.
    if not enabled(environ):
        return None
    catalog = load_catalog()
    detection = detect(request, catalog, environ)
    if detection is None:
        return None
    text = compose(request, detection, catalog)
    return {'text': text, 'kind': KIND, 'themeId': detection['default'], 'chosenByUser': detection['chosenByUser'],
            'catalogDigest': catalog['digest'], 'enhancedDigest': hashlib.sha256(text.encode('utf-8')).hexdigest()}
