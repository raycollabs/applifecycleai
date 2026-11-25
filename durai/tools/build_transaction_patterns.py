"""Sample: Build the transaction pack candidate. Install it and bind foundation-transaction-v14 only on request."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.blueprint_patterns import transaction_pack
from durai.common import canonical, Conflict
from durai.packs import PackStore, generic_pack, validate_pack
from durai.policy_store import record_policies

BINDING = 'foundation-transaction-v14'


def write_candidate(candidate, output):
    # Sample: Write candidate C-1 twice. Keep equal bytes. Refuse different bytes at the same output path.
    content = canonical(candidate) + b'\n'
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open('xb') as stream:
            stream.write(content)
    except FileExistsError:
        if output.is_symlink() or output.read_bytes() != content:
            raise Conflict('Existing candidate output differs') from None


def install(candidate, database, rebind=False, binding=BINDING):
    # Sample: Install P-2 and bind B-1. Refuse a different pin. With rebind True write revision 2 for P-2 and keep revision 1.
    store = PackStore(database)
    reference = store.install(candidate)
    policies = record_policies(reference, database)
    try:
        existing = store.resolve(binding)
    except KeyError:
        existing = None
    pinned = existing is not None and not (existing['document']['packs'] != [reference] or existing['document']['mode'] != 'SYNTHETIC_LOCAL')
    if existing is not None and not pinned and not rebind:
        raise Conflict('Binding ' + binding + ' already pins a different pack')
    current = existing if pinned else store.bind(binding, [reference], 'SYNTHETIC_LOCAL')
    return {'reference': reference, 'policies': len(policies), 'binding': binding, 'bindingRevision': current['revision'],
            'bindingDigest': current['digest'], 'bindingCreated': not pinned}


def main(argv=None):
    # Sample: Run with --output C-1.json. Print the pack version and digest. Add --install --database durai_test to install it.
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--database', default='durai_test')
    parser.add_argument('--rebind', action='store_true')
    args = parser.parse_args(argv)
    candidate = transaction_pack(generic_pack('14'))
    if args.output:
        write_candidate(candidate, args.output)
    summary = {'id': candidate['id'], 'version': candidate['version'], 'digest': validate_pack(candidate),
               'output': str(args.output.resolve()) if args.output else None, 'installed': False, 'database': args.database}
    if args.install:
        summary.update(installed=True, **install(candidate, args.database, args.rebind))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
