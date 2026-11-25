"""Qualify or restore one synthetic replica. Keep release approval separate."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.replica_phase1 import qualify, restore


def main():
    # Sample: Run K-1 once. Use the same key to read its terminal result.
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    run = commands.add_parser('qualify')
    run.add_argument('--binding', required=True)
    run.add_argument('--key', required=True)
    run.add_argument('--database', choices=['durai_local', 'durai_test'], default='durai_local')
    recovery = commands.add_parser('restore')
    recovery.add_argument('--package', required=True)
    recovery.add_argument('--output', required=True)
    recovery.add_argument('--digest', required=True)
    recovery.add_argument('--scope', required=True)
    args = parser.parse_args()
    if args.action == 'qualify':
        result = qualify(args.binding, args.key, args.database)
    else:
        result = restore(args.package, args.output, args.digest, args.scope)
    print(json.dumps(result, indent=2))
    return 0 if result['state'] in ('WAITING_FOR_REVIEW', 'RESTORED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
