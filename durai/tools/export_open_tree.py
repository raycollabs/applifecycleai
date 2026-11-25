"""Sample: Run export_open_tree.py D:/out. Copy the OPEN and FREE_PACK files to D:/out. Start a new git history with one commit. Refuse when a file has no class or a secret rule matches. Push nothing."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boundary_scan  # noqa: E402
import secret_scan  # noqa: E402

FREE_PACK_FOLDER = 'packs/starter'


def plan_export(files, rules, hidden_patterns, include_free_pack=True):
    # Sample: Files a.py (OPEN) and p.json (PAID_PACK). Return a.py to copy and p.json skipped. A file with no class raises ValueError.
    report = boundary_scan.scan(files, rules, hidden_patterns)
    if report['unclassified']:
        raise ValueError('Unclassified files: ' + ', '.join(report['unclassified'][:5]))
    if report['hiddenGoalInPublic']:
        raise ValueError('Hidden goal file in the public set: ' + ', '.join(report['hiddenGoalInPublic'][:5]))
    wanted = {'OPEN'} | ({'FREE_PACK'} if include_free_pack else set())
    copy = [path for path in files if boundary_scan.classify(path, rules) in wanted]
    return {'copy': copy, 'skipped': len(files) - len(copy)}


def target_path(path, rules):
    # Sample: durai/durai/db.py stays durai/durai/db.py. A FREE_PACK file durai/design/engineering-philosophy-v15.json goes to packs/starter/design/engineering-philosophy-v15.json. Two files with the same name keep their own folders.
    if boundary_scan.classify(path, rules) == 'FREE_PACK':
        return FREE_PACK_FOLDER + '/' + (path[len('durai/'):] if path.startswith('durai/') else path)
    return path


def export(root, destination, include_free_pack=True):
    # Sample: Export the repository root to a new empty folder. Return the count of copied files and the commit id. Refuse a folder that is not empty.
    root, destination = Path(root), Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Destination is not empty')
    rules, hidden_patterns = boundary_scan.load_rules()
    files = boundary_scan.tracked_files(root)
    plan = plan_export(files, rules, hidden_patterns, include_free_pack)
    leaks = [item for item in secret_scan.scan_tree(root, only_public=True)]
    if leaks:
        raise ValueError('Secret rules match in the public set: ' + json.dumps(leaks[:5]))
    destination.mkdir(parents=True, exist_ok=True)
    for path in plan['copy']:
        out = destination / target_path(path, rules)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / path, out)
    run = lambda *args: subprocess.run(['git', '-C', str(destination), '-c', 'user.name=Durai export', '-c',
                                        'user.email=export@example.org', *args], check=True, capture_output=True, text=True)
    run('init', '-q')
    run('add', '-A')
    run('commit', '-q', '-m', 'Durai open shell: initial public export')
    commit = run('rev-parse', '--short', 'HEAD').stdout.strip()
    return {'copied': len(plan['copy']), 'skipped': plan['skipped'], 'commit': commit}


def main(argv=None):
    # Sample: Run with a destination. Print one JSON line. Exit 0 when the export is finished.
    parser = argparse.ArgumentParser()
    parser.add_argument('destination')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[2]))
    arguments = parser.parse_args(argv)
    try:
        result = export(arguments.root, arguments.destination)
    except ValueError as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
