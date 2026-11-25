import argparse
import hashlib
import json
from pathlib import Path
from shutil import copyfile
import sys

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / 'apps/shell'
sys.path.insert(0, str(ROOT))


def seed(workspace):
    # Sample: Make an empty workspace. Copy the shell files and write their SHA-256 values.
    workspace = Path(workspace).resolve()
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError('Use a new or empty build workspace')
    workspace.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for directory, target in ((SHELL / 'api', workspace / 'shell_api'), (SHELL / 'web', workspace)):
        for source in sorted(directory.rglob('*')):
            relative = source.relative_to(directory)
            if (not source.is_file() or source.is_symlink() or any(part.startswith('.') for part in relative.parts)
                    or '__pycache__' in relative.parts or 'node_modules' in relative.parts):
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            copyfile(source, destination)
            manifest[destination.relative_to(workspace).as_posix()] = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest_path = workspace / 'shell-manifest.json'
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8', newline='\n')
    return {'files': len(manifest), 'manifest': hashlib.sha256(manifest_path.read_bytes()).hexdigest()}


def main(argv=None):
    # Sample: Run seed --workspace W-1. Print the file count and manifest digest.
    parser = argparse.ArgumentParser(description='Prepare or check the transaction app shell')
    parser.add_argument('action', choices=('seed', 'check'))
    parser.add_argument('--workspace', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.action == 'seed':
        print(json.dumps(seed(args.workspace)))
        return 0
    from durai.build_run_node import check_node_app
    outcome = check_node_app(args.workspace)
    print(json.dumps(outcome))
    return 0 if outcome['status'] == 200 else 1


if __name__ == '__main__':
    raise SystemExit(main())
