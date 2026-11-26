"""Sample: Run python tools/sign_egress_pack.py --key-file C:/keys/durai/durai-publisher-private.hex --out packs/durai.egress-starter.json. Write a signed pack with one sandbox.egress record. Print the digest and the public key. Never print the private key."""
import argparse
import getpass
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.egress_presets import starter_body, validate_body  # noqa: E402
from durai.pack_signing import TrustedKeyList, export_public_key_hex, load_private_key_hex, sign_pack, verify_pack  # noqa: E402
from durai.packs import make_record  # noqa: E402

KEY_ID = 'durai-publisher-2026'


def build(private_key, key_id=KEY_ID, body=None):
    # Sample: Wrap the starter egress body as one record. Sign it. Verify it with the matching public key. A failed check raises ValueError.
    body = validate_body(body or starter_body())
    document = {'format': 'durai-ontology-pack/v2', 'id': 'durai.egress-starter', 'version': '1.0.0', 'kind': 'GENERIC', 'publisher': 'durai.io',
                'tier': 'FREE', 'license_ref': 'durai-pack-license-v1', 'min_shell_version': '1.0.0', 'valid_from': None, 'valid_until': None,
                'records': [make_record('sandbox.egress', 'DESIGN', 'KNOWLEDGE', body)], 'edges': []}
    signed = sign_pack(document, private_key, key_id=key_id)
    trusted = TrustedKeyList()
    trusted.add_key(key_id, private_key.public_key())
    if not verify_pack(signed, trusted).valid:
        raise ValueError('Signed pack did not verify')
    return signed


def main(argv=None):
    # Sample: Read the key from --key-file or a hidden prompt. Write the signed pack to --out. Print the record digest and the public key hex.
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-file')
    parser.add_argument('--key-id', default=KEY_ID)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    text = Path(args.key_file).read_text(encoding='utf-8').strip() if args.key_file else getpass.getpass('Private key hex (hidden): ').strip()
    private_key = load_private_key_hex(text)
    signed = build(private_key, args.key_id)
    Path(args.out).write_text(json.dumps(signed, indent=2) + '\n', encoding='utf-8')
    print('Signed record digest: ' + signed['records'][0]['digest'])
    print('Public key: ' + export_public_key_hex(private_key.public_key()))
    print('Wrote ' + args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
