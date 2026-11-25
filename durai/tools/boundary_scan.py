"""Sample: Run boundary_scan.py. Read the tracked files. Print the count of files for each class. Exit 1 when a file has no class or a hidden goal file is OPEN."""
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

RULES_FILE = Path(__file__).resolve().parents[1] / 'design' / 'open-core-boundary-v1.json'
PUBLIC = ('OPEN', 'FREE_PACK')


def glob_to_regex(glob):
    # Sample: Glob durai/tools/** matches durai/tools/a.py and durai/tools/x/b.py. Glob durai/*.md matches durai/a.md and not durai/x/a.md.
    parts, index = [], 0
    while index < len(glob):
        if glob.startswith('**/', index):
            parts.append('(?:.*/)?')
            index += 3
        elif glob.startswith('**', index):
            parts.append('.*')
            index += 2
        elif glob[index] == '*':
            parts.append('[^/]*')
            index += 1
        elif glob[index] == '?':
            parts.append('[^/]')
            index += 1
        else:
            parts.append(re.escape(glob[index]))
            index += 1
    return re.compile('^' + ''.join(parts) + '$')


def load_rules(path=RULES_FILE):
    # Sample: Read the rules file. Return a list of (compiled pattern, class) and the hidden goal name patterns.
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    rules = [(glob_to_regex(item['glob']), item['class']) for item in data['rules']]
    for _, name in rules:
        if name not in data['classes']:
            raise ValueError('Unknown class ' + name)
    return rules, list(data.get('hidden_goal_name_patterns', []))


def classify(path, rules):
    # Sample: Path durai/set-gemini-key.cmd returns NEVER. Path durai/durai/db.py returns OPEN. Path new/file.txt returns None.
    for pattern, name in rules:
        if pattern.match(path):
            return name
    return None


def tracked_files(root):
    # Sample: Run git ls-files in the repository. Return the list of paths with forward slashes.
    done = subprocess.run(['git', '-c', 'safe.directory=' + str(Path(root).resolve().as_posix()), '-C', str(root), 'ls-files'],
                          capture_output=True, text=True, check=True)
    return [line for line in done.stdout.splitlines() if line]


def scan(files, rules, hidden_patterns):
    # Sample: Files a.py (OPEN), b.json (no rule), goals-hidden.json (OPEN). Return counts, unclassified b.json, hidden goal goals-hidden.json.
    counts, unclassified, hidden = {}, [], []
    for path in files:
        name = classify(path, rules)
        if name is None:
            unclassified.append(path)
            continue
        counts[name] = counts.get(name, 0) + 1
        base = path.rsplit('/', 1)[-1].lower()
        if name in PUBLIC and any(fnmatch.fnmatch(base, pattern) for pattern in hidden_patterns):
            hidden.append(path)
    return {'counts': counts, 'unclassified': unclassified, 'hiddenGoalInPublic': hidden}


def main(argv=None):
    # Sample: Run in a repository root. Print one JSON report. Exit 0 when clean.
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[2]
    rules, hidden_patterns = load_rules()
    report = scan(tracked_files(root), rules, hidden_patterns)
    print(json.dumps({'counts': report['counts'], 'unclassifiedCount': len(report['unclassified']),
                      'unclassified': report['unclassified'][:50], 'hiddenGoalInPublic': report['hiddenGoalInPublic']}, indent=2))
    return 1 if report['unclassified'] or report['hiddenGoalInPublic'] else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
