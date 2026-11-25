"""Sample: Publish reviewed Git commits to raycollabs/durai without leaking credentials."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OWNER, NAME = 'raycollabs', 'durai'
GIT = ['git', '-c', 'safe.directory=' + ROOT.as_posix()]


def git(*args):
    result = subprocess.run([*GIT, *args], cwd=ROOT, capture_output=True, check=True)
    return result.stdout


def scan_history():
    # Sample: Load both provider keys. Reject either key in a committed source blob.
    secrets = []
    def collect(value):
        # Sample: Read a private key value. Keep only its bytes for in-memory comparison.
        if isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, str) and len(value) >= 20:
            secrets.append(value.encode())
    for name in ('local.json', 'model.json'):
        path = ROOT / 'durai/private' / name
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            if name == 'model.json':
                data = {'apiKey': data.get('apiKey'),
                        'providerKeys': [profile.get('apiKey') for profile in data.get('fallbackProviders', [])],
                        'environmentKeys': [os.environ.get(key) for key in
                            ['OPENROUTER_API_KEY', 'DURAI_MODEL_API_KEY', 'MISTRAL_API_KEY'] +
                            [profile.get('apiKeyEnv', '') for profile in data.get('fallbackProviders', [])]]}
            collect(data)
    patterns = [rb'sk-or-v1-[A-Za-z0-9]{24,}', rb'gh[pousr]_[A-Za-z0-9]{30,}',
                rb'github_pat_[A-Za-z0-9_]{30,}']
    objects = git('rev-list', '--objects', '--all').decode().splitlines()
    blobs = 0
    for record in objects:
        fields = record.split(' ', 1)
        if len(fields) != 2:
            continue
        identity, name = fields
        if re.search(r'(^|/)(private|logs|__pycache__|\.env)(/|$)', name):
            raise ValueError('Private path found in history: ' + name)
        if git('cat-file', '-t', identity).strip() != b'blob':
            continue
        content = git('cat-file', 'blob', identity)
        if any(secret in content for secret in secrets) or any(re.search(pattern, content) for pattern in patterns):
            raise ValueError('Potential credential found in history: ' + name)
        blobs += 1
    return {'state': 'PASS', 'blobsScanned': blobs}


def credentials():
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
    result = subprocess.run([*GIT, 'credential', 'fill'], cwd=ROOT, env=environment,
        input=f'protocol=https\nhost=github.com\nusername={OWNER}\n\n',
        text=True, capture_output=True)
    if result.returncode:
        raise ValueError('Git Credential Manager has no usable GitHub login for raycollabs. Sign in locally before publishing.')
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if not values.get('password'):
        raise ValueError('GitHub authentication is unavailable')
    return values['password']


def api(token, path, method='GET', value=None):
    request = urllib.request.Request('https://api.github.com' + path, method=method,
        data=json.dumps(value).encode() if value is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'durai-local-publisher'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise ValueError('GitHub API request failed with HTTP ' + str(error.code)) from None


def verify_owner(token):
    user = api(token, '/user')
    if not user or user['login'].lower() != OWNER:
        raise ValueError('Authenticated GitHub identity is not the requested raycollabs account')
    return user['login']


def verify_remote_head(token, head):
    remote = api(token, '/repos/' + OWNER + '/' + NAME + '/commits/main')
    if not remote or remote['sha'] != head:
        raise ValueError('Remote commit verification failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'scan', 'publish'])
    args = parser.parse_args()
    scan = scan_history()
    if args.action == 'scan':
        print(json.dumps(scan))
        return
    token = credentials()
    owner = verify_owner(token)
    repo = api(token, '/repos/' + OWNER + '/' + NAME)
    if args.action == 'inspect':
        print(json.dumps({'authenticatedOwner': owner, 'target': OWNER + '/' + NAME,
                          'exists': repo is not None, 'private': repo.get('private') if repo else None,
                          'historyScan': scan}, indent=2))
        return
    if repo is not None:
        raise ValueError('The target repository already exists. Preserve it and review before pushing.')
    repo = api(token, '/user/repos', 'POST', {'name': NAME, 'private': True,
        'description': 'Durai local software factory: business evidence, protected evaluation, and verified trading repair.',
        'auto_init': False})
    url = 'https://github.com/' + OWNER + '/' + NAME + '.git'
    if git('remote').strip():
        raise ValueError('A Git remote already exists. Review it before changing publication settings.')
    git('remote', 'add', 'origin', url)
    # Standard credential helper owns Git authentication. Tokens never enter argv.
    git('push', '-u', 'origin', 'HEAD:refs/heads/main')
    head = git('rev-parse', 'HEAD').decode().strip()
    verify_remote_head(token, head)
    api(token, '/repos/' + OWNER + '/' + NAME, 'PATCH', {'default_branch': 'main'})
    result = {'state': 'PUBLISHED', 'url': repo['html_url'], 'private': repo['private'],
              'branch': 'main', 'commit': head, 'historyScan': scan}
    (ROOT / 'durai/private/github-publication.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError, urllib.error.URLError) as error:
        # Do not echo helper output, token-bearing headers, or provider responses.
        print(str(error) if isinstance(error, ValueError) else 'Publication command failed; credentials were not logged.')
        raise SystemExit(1)
