"""Sample: Import a plan into durai_test. Export its records to a new Markdown file."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.context_store import ContextStore
from durai.context_transfer import import_plan


def main():
    # Sample: Run import P-1.md with --plan P-1. Store one immutable plan revision in durai_test.
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', default='durai_test', choices=['durai_test', 'durai_local'])
    parser.add_argument('--plan', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('import').add_argument('path')
    sub.add_parser('export').add_argument('path')
    sub.add_parser('read').add_argument('--key')
    sub.add_parser('search').add_argument('query')
    sub.add_parser('summary')
    args = parser.parse_args()
    store = ContextStore(args.database)
    if args.command == 'import':
        result = import_plan(store, args.path, args.plan)
    elif args.command == 'export':
        path = Path(args.path)
        with path.open('x', encoding='utf-8') as output:
            output.write(store.export(args.plan))
        result = {'path': str(path), 'planId': args.plan}
    elif args.command == 'search':
        result = store.search(args.plan, args.query)
    elif args.command == 'summary':
        result = store.summary(args.plan)
    else:
        result = store.read(args.plan, args.key)
    print(json.dumps(result, default=str, indent=2))


if __name__ == '__main__':
    main()
