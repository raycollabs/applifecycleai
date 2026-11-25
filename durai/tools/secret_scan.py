"""Sample: Run secret_scan.py with --history. Print for each finding the rule, the file, the line and the commit. Print no secret value. Exit 1 when a finding exists."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RULES = {
    'GOOGLE_API_KEY': re.compile(r'AIza[0-9A-Za-z_\-]{35}'),
    'SK_KEY': re.compile(r'\bsk-[A-Za-z0-9_\-]{20,}'),
    'GITHUB_TOKEN': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})'),
    'AWS_ACCESS_KEY': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    'SLACK_TOKEN': re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}'),
    'PRIVATE_KEY_BLOCK': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----'),
    'BEARER_TOKEN': re.compile(r'[Bb]earer\s+[A-Za-z0-9._\-]{24,}'),
    'API_KEY_FIELD': re.compile(r'''["']?(?:api[_-]?key|apiKey|secret|token|password)["']?\s*[:=]\s*["'][A-Za-z0-9_\-./+=]{16,}["']'''),
}
WARN_RULES = {
    'PRIVATE_PATH': re.compile(r'[A-Za-z]:\\+Users\\+[A-Za-z0-9_.-]+', re.IGNORECASE),
    'EMAIL_ADDRESS': re.compile(r'\b[A-Za-z0-9._%+-]+@(?:gmail|outlook|hotmail|yahoo)\.com\b'),
}
PLACEHOLDER = re.compile(r'(?:example|placeholder|your[_-]?key|xxxx|<[^>]+>|\.\.\.|test[_-]?key|sk-test|synthetic|dummy|fake|changeme)', re.IGNORECASE)
SKIP_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.zip', '.gz', '.pdf', '.woff', '.woff2', '.lock'}


def scan_text(text, rules=None):
    # Sample: Text with a 39 character key that starts AIza returns one GOOGLE_API_KEY finding on line 1. A line with the word example returns none.
    found = []
    active = dict(RULES if rules is None else rules)
    for number, line in enumerate(text.splitlines(), 1):
        for name, pattern in active.items():
            match = pattern.search(line)
            if match and not PLACEHOLDER.search(match.group(0)):
                found.append({'rule': name, 'line': number})
    return found


def git(root, *args):
    # Sample: Run git ls-files in the root. Return the text output.
    done = subprocess.run(['git', '-c', 'safe.directory=' + Path(root).resolve().as_posix(), '-C', str(root), *args],
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    return done.stdout


def scan_tree(root, include_warnings=False, only_public=False):
    # Sample: Scan every tracked text file of the working tree. Return findings with rule, path and line. With only_public, skip files whose class is not OPEN or FREE_PACK.
    rules = dict(RULES)
    if include_warnings:
        rules.update(WARN_RULES)
    findings = []
    classes = None
    if only_public:
        import boundary_scan
        classes = boundary_scan.load_rules()[0]
    for path in git(root, 'ls-files').splitlines():
        if classes is not None and boundary_scan.classify(path, classes) not in boundary_scan.PUBLIC:
            continue
        target = Path(root) / path
        if target.suffix.lower() in SKIP_SUFFIXES or not target.is_file():
            continue
        try:
            text = target.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        for item in scan_text(text, rules):
            findings.append({'rule': item['rule'], 'path': path, 'line': item['line']})
    return findings


def scan_history(root):
    # Sample: Walk every commit that adds a line matching a secret rule. Return findings with rule, commit and path. Keep no value.
    findings, seen = [], set()
    for name, pattern in RULES.items():
        log = git(root, 'log', '--all', '--pretty=format:@@%h', '--name-only', '-G', pattern.pattern)
        commit = None
        for line in log.splitlines():
            if line.startswith('@@'):
                commit = line[2:]
            elif line.strip() and commit:
                key = (name, commit, line.strip())
                if key in seen:
                    continue
                seen.add(key)
                diff = git(root, 'show', '--pretty=format:', '-U0', commit, '--', line.strip())
                added = [row[1:] for row in diff.splitlines() if row.startswith('+') and not row.startswith('+++')]
                if any(pattern.search(row) and not PLACEHOLDER.search(pattern.search(row).group(0)) for row in added):
                    findings.append({'rule': name, 'commit': commit, 'path': line.strip()})
    return findings


def main(argv=None):
    # Sample: Run with the repository root. Print a JSON report. Exit 0 when no finding exists.
    parser = argparse.ArgumentParser()
    parser.add_argument('root', nargs='?', default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--warnings', action='store_true')
    parser.add_argument('--public-only', action='store_true')
    arguments = parser.parse_args(argv)
    report = {'tree': scan_tree(arguments.root, arguments.warnings, arguments.public_only)}
    if arguments.history:
        report['history'] = scan_history(arguments.root)
    print(json.dumps(report, indent=1))
    return 1 if report['tree'] or report.get('history') else 0


if __name__ == '__main__':
    sys.exit(main())
