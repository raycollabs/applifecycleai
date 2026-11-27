"""Sample: Check a Next.js workspace whose app/signout/route.ts has a GET handler that deletes the cookie. Return one violation named GET_CHANGES_COOKIE."""
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit
import re

SCAN = ('app',)
HANDLER = re.compile(r'export\s+(?:async\s+)?function\s+GET\b|export\s+const\s+GET\b')
COOKIE_WRITE = re.compile(r'cookies\s*\.\s*(?:set|delete)\s*\(|cookies\(\)\s*\.\s*(?:set|delete)\s*\(|set-cookie', re.IGNORECASE)
ABSOLUTE_REDIRECT = re.compile(r'new\s+URL\s*\(.*\b(?:request|req)\.url|nextUrl\.clone\(\)')

MESSAGES = {
    'GET_CHANGES_COOKIE': 'A GET route handler sets or deletes a cookie. Next.js prefetches links in the background, so a link to it changes the session by itself. Make the change a server action or a POST form.',
    'ABSOLUTE_REDIRECT': 'A route handler builds a redirect URL from the request URL. Inside the container that URL has the host 0.0.0.0. Redirect with redirect() from next/navigation in a server action.',
}


def route_files(workspace):
    # Sample: Workspace holds app/signout/route.ts and app/page.tsx. Return only app/signout/route.ts.
    root = Path(workspace)
    found = []
    for name in SCAN:
        base = root / name
        if base.is_dir():
            found += sorted(path for path in base.rglob('route.*') if path.suffix in ('.ts', '.tsx', '.js'))
    return found


def check_web_rules(workspace):
    # Sample: Handler with GET and cookies.set returns GET_CHANGES_COOKIE. Handler with POST only returns an empty list. Workspace without app/ returns an empty list.
    root = Path(workspace)
    violations = []
    for path in route_files(workspace):
        text = path.read_text(encoding='utf-8', errors='replace')
        where = path.relative_to(root).as_posix()
        if HANDLER.search(text) and COOKIE_WRITE.search(text):
            violations.append({'rule': 'GET_CHANGES_COOKIE', 'path': where, 'message': MESSAGES['GET_CHANGES_COOKIE']})
        if ABSOLUTE_REDIRECT.search(text):
            violations.append({'rule': 'ABSOLUTE_REDIRECT', 'path': where, 'message': MESSAGES['ABSOLUTE_REDIRECT']})
    return violations


def describe(violations):
    # Sample: One violation. Return the lines "web rule GET_CHANGES_COOKIE in app/signout/route.ts: ...". No violation. Return the text "web rules: ok".
    if not violations:
        return 'web rules: ok'
    return '\n'.join('web rule ' + item['rule'] + ' in ' + item['path'] + ': ' + item['message'] for item in violations)


class Page(HTMLParser):
    def __init__(self, body):
        # Sample: Read a page with one form and one menu link. Save both.
        super().__init__(convert_charrefs=True)
        self.forms, self.links, self.text = [], [], []
        self.form, self.nav, self.hidden = None, 0, 0
        self.feed(body)

    def handle_starttag(self, tag, pairs):
        # Sample: Read a POST form with one hidden input. Save its fields.
        attrs = dict(pairs)
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag == 'nav':
            self.nav += 1
        if tag == 'a' and self.nav and attrs.get('href'):
            self.links.append(attrs['href'])
        if tag == 'form':
            self.form = {'action': attrs.get('action') or '/', 'method': attrs.get('method', 'get').lower(), 'fields': {}}
            self.forms.append(self.form)
        if self.form is not None and tag == 'input' and attrs.get('name'):
            self.form['fields'][attrs['name']] = attrs.get('value') or ''

    def handle_endtag(self, tag):
        # Sample: Close the nav. Leave links after it out of the menu.
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if tag == 'nav':
            self.nav = max(0, self.nav - 1)
        if tag == 'form':
            self.form = None

    def handle_data(self, value):
        # Sample: Read the visible label Signed out. Keep it in the page text.
        if not self.hidden:
            self.text.append(value)


def local_path(value):
    # Sample: Accept /positions. Refuse a path that starts with a second slash or has a host.
    parsed = urlsplit(value)
    return (value.startswith('/') and not value.startswith('//') and not parsed.netloc and not parsed.scheme
            and chr(92) not in value and not any(ord(char) < 32 for char in value))


def cookie_values(response, previous):
    # Sample: Read a cookie with Max-Age 0. Remove its old value.
    result = previous.copy()
    for header in response.get('cookies', ()):
        values = SimpleCookie()
        values.load(header)
        for name, morsel in values.items():
            if not morsel.value or morsel['max-age'] == '0':
                result.pop(name, None)
            else:
                result[name] = morsel.value
    return result


def writes_session(response, name):
    # Sample: Read a Set-Cookie header for the current session. Return true even when its value is the same.
    return any(header.split('=', 1)[0].strip() == name for header in response.get('cookies', ()))

def check_sign_in(fetch, trader, secret='', paths=('/positions', '/book', '/prices', '/cash')):
    # Sample: Sign in as TR-BEN. Read four screens and prefetch the sign-out path. Keep the cookie. With paths None start from the post-sign-in redirect path.
    landing = fetch('/', method='GET')
    login = next((form for form in Page(landing['body']).forms
                  if {'code', 'trader'} & form['fields'].keys()), None)
    if not login or login['method'] != 'post' or not local_path(login['action']):
        return {'passed': False, 'rule': 'NO_SIGN_IN_FORM', 'screens': []}
    fields = {key: value for key, value in login['fields'].items() if key.startswith('$ACTION_')}
    fields.update({'code': trader} if 'code' in login['fields'] else {'trader': trader, 'secret': secret})
    reply = fetch(login['action'], method='POST', form=fields, headers={})
    if not 200 <= reply['status'] < 400:
        return {'passed': False, 'rule': 'SIGN_IN_REFUSED', 'screens': []}
    location = reply.get('location')
    if location and not local_path(location):
        return {'passed': False, 'rule': 'NONLOCAL_REDIRECT', 'screens': []}
    cookies = cookie_values(reply, {})
    session = next((name for name in ('durai_session', 'trader_code') if name in cookies), None)
    if session is None:
        return {'passed': False, 'rule': 'NO_SESSION_COOKIE', 'screens': []}
    snapshot = cookies[session]
    headers = {'Cookie': f'{session}={snapshot}'}
    visited = []
    pending = list(paths) if paths is not None else [location if location and local_path(location) else '/']
    prefetch = {'/signout', '/sign-out', '/session/sign-out'}
    while pending and len(visited) < 32:
        path = pending.pop(0)
        if path in visited or not local_path(path):
            continue
        response = fetch(path, method='GET', headers=headers)
        if (paths is None and response['status'] in (301, 302, 303, 307, 308)
                and not writes_session(response, session)):
            next_path = response.get('location') or ''
            if local_path(next_path) and next_path not in visited and next_path not in pending:
                pending.insert(0, next_path)
                continue
        page = Page(response['body'])
        text = ' '.join(page.text)
        if (response['status'] != 200 or re.search(r'\bsigned\s+out\b', text, re.IGNORECASE)
                or trader not in text or writes_session(response, session)):
            return {'passed': False, 'rule': 'SCREEN_LOST_SESSION', 'path': path, 'screens': visited}
        visited.append(path)
        for link in page.links:
            if not local_path(link):
                continue
            prefetch.add(link)
            if link != '/' and 'signout' not in link.lower() and 'sign-out' not in link.lower() and link not in pending:
                pending.append(link)
    if any(path not in visited for path in pending):
        return {'passed': False, 'rule': 'SCREEN_LIMIT', 'screens': visited}
    for path in sorted(prefetch):
        response = fetch(path, method='GET', headers={**headers, 'Purpose': 'prefetch', 'Next-Router-Prefetch': '1'})
        if writes_session(response, session):
            return {'passed': False, 'rule': 'PREFETCH_CHANGED_COOKIE', 'path': path, 'screens': visited}
    return {'passed': True, 'rule': None, 'screens': visited}
