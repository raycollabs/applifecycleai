"""Sample: Detect "Compare Durai with github.com/acme/agentbox." Return the kind comparative-research. Compose the request first, then ten answer sections."""
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / 'design/prompt/compare/template.json'
KIND = 'comparative-research'
IGNORED_NAMES = frozenset(('Durai GitHub The This That What How Can Could Should Would Compare Comparing Replace Adapt Our We It I '
                           'Also And But Then Tell Show Give Let Please Write Make Why Which Who When Where Are Is Does Do').split())


class CompareTemplateError(ValueError):
    """Sample: Change one word in template.json. Load it. Raise this error that names the digest."""


def template_digest(template):
    # Sample: Hash the template without its digest field. Return 64 hex characters. The same data always gives the same value.
    body = {key: value for key, value in template.items() if key != 'digest'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def load_template(path=None):
    # Sample: Read template.json. Check its digest. Return the parsed template. A changed file raises CompareTemplateError.
    template = json.loads(Path(path or TEMPLATE_PATH).read_text(encoding='utf-8'))
    if template.get('digest') != template_digest(template):
        raise CompareTemplateError('Compare template digest differs: ' + str(template.get('digest')))
    return template


def enabled(environ=None):
    # Sample: DURAI_ENHANCE=0 returns False. An unset variable returns True.
    return (os.environ if environ is None else environ).get('DURAI_ENHANCE', '1') != '0'


def target_names(request):
    # Sample: "Compare Durai with github.com/acme/agentbox and LangGraph" returns [github.com/acme/agentbox, LangGraph].
    found = re.findall(r'github\.com/[\w.-]+/[\w.-]*\w', request, flags=re.I)
    for word in re.findall(r'\b[A-Z][A-Za-z0-9]{2,}\b', request):
        if word not in IGNORED_NAMES and word not in found:
            found.append(word)
    return found


def detect(request, template=None, environ=None):
    # Sample: "Compare Durai with the OpenShell framework" returns targets [OpenShell]. "Let a trader book a trade." returns None. DURAI_ENHANCE=0 returns None.
    if not enabled(environ) or not isinstance(request, str) or not request.strip():
        return None
    rules = (template or load_template())['rules']
    text = request.lower()
    if not any(re.search(pattern, text) for pattern in rules['verbs']):
        return None
    if not any(re.search(pattern, text) for pattern in rules['targets']):
        return None
    return {'kind': KIND, 'targets': target_names(request)}


def compose(request, detection, template=None):
    # Sample: Compose the request first. Then name the targets. Then list ten sections, the verdict words, the cost scale and the rules.
    template = template or load_template()
    rules = template['rules']
    lines = [request, '', '--- Durai compare format (kind: ' + KIND + ') ---']
    if detection['targets']:
        lines.append('Targets named: ' + ', '.join(detection['targets']))
    lines.append('Answer with these sections in this order:')
    for number, section in enumerate(template['sections'], 1):
        lines.append(str(number) + '. ' + section['title'] + ': ' + section['instruction'])
    lines.append('Verdict words: ' + ', '.join(rules['verdicts']) + '.')
    lines.append('Cost scale: ' + '; '.join(key + ' = ' + value for key, value in rules['cost_scale'].items()) + '.')
    lines.append('Table columns: ' + ', '.join(rules['columns']) + '.')
    lines += template['instructions']
    return '\n'.join(lines) + '\n'


def enhance(request, environ=None):
    # Sample: Return None for a build request. Return text, kind and the digest of the text for a compare request.
    template = load_template()
    detection = detect(request, template, environ)
    if detection is None:
        return None
    text = compose(request, detection, template)
    return {'text': text, 'kind': KIND, 'targets': detection['targets'], 'templateDigest': template['digest'],
            'enhancedDigest': hashlib.sha256(text.encode('utf-8')).hexdigest()}
