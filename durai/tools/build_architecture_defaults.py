"""Sample: Build the G-10 successor as candidate.json. Leave installed packs and bindings unchanged."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.architecture_defaults import architecture_defaults_pack
from durai.common import canonical, Conflict
from durai.packs import generic_pack, validate_pack


def main():
    # Sample: Write candidate C-1 twice. Keep equal bytes. Refuse different bytes at the same output path.
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-pack', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    base = json.loads(args.base_pack.read_text(encoding='utf-8-sig')) if args.base_pack else generic_pack('10')
    candidate = architecture_defaults_pack(base)
    content = canonical(candidate) + b'\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open('xb') as stream:
            stream.write(content)
    except FileExistsError:
        if args.output.is_symlink() or args.output.read_bytes() != content:
            raise Conflict('Existing candidate output differs') from None
    print(json.dumps({'id': candidate['id'], 'version': candidate['version'], 'digest': validate_pack(candidate),
        'output': str(args.output.resolve()), 'installed': False, 'bindingChanged': False}))


if __name__ == '__main__':
    main()
