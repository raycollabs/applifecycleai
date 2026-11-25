"""Sample: Build free starter pack and paid transaction pack. Sign with Ed25519 test key."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.common import canonical, digest
from durai.packs import generic_pack, make_record, validate_pack
from durai.pack_signing import (
    TrustedKeyList,
    export_private_key_hex,
    export_public_key_hex,
    generate_key_pair,
    load_private_key_hex,
    sign_pack,
    verify_pack,
)

KEY_ID = 'durai-test-signer-2026'
PRIVATE_KEY_PATH = ROOT / 'private/test_pack_signing_key.hex'
PACKS_DIR = ROOT / 'packs'


def get_or_create_test_key():
    # Sample: Check private key file. Load existing key or generate new key pair.
    if PRIVATE_KEY_PATH.exists():
        priv_hex = PRIVATE_KEY_PATH.read_text(encoding='utf-8').strip()
        priv = load_private_key_hex(priv_hex)
        pub = priv.public_key()
        return priv, pub
    priv, pub = generate_key_pair()
    PRIVATE_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_KEY_PATH.write_text(export_private_key_hex(priv), encoding='utf-8')
    return priv, pub


def build_starter_pack(private_key, key_id=KEY_ID):
    # Sample: Combine philosophy v15 and architecture defaults. Sign starter pack.
    base = generic_pack('15')
    arch_path = ROOT / 'design/architecture-defaults-v1.json'
    arch_data = json.loads(arch_path.read_text(encoding='utf-8'))

    records = list(base['records'])
    for pattern in arch_data.get('patterns', []):
        body = dict(pattern)
        records.append(make_record(f"pattern.architecture.{pattern['id']}", 'DESIGN', 'KNOWLEDGE', body))

    records.append(make_record(
        'architecture.defaults.catalog',
        'DESIGN',
        'KNOWLEDGE',
        {
            'format': arch_data.get('format'),
            'id': arch_data.get('id'),
            'checkedAt': arch_data.get('checkedAt'),
            'profiles': arch_data.get('profiles'),
            'authority': arch_data.get('authority')
        }
    ))

    document = {
        'format': 'durai-ontology-pack/v2',
        'id': 'durai.starter',
        'version': '1.0.0',
        'kind': 'GENERIC',
        'publisher': 'durai.io',
        'tier': 'FREE',
        'license_ref': 'durai-pack-license-v1',
        'min_shell_version': '1.0.0',
        'valid_from': None,
        'valid_until': None,
        'records': records,
        'edges': list(base.get('edges', [])),
        'provenance': {
            'philosophy': 'design/engineering-philosophy-v15.json',
            'architecture': 'design/architecture-defaults-v1.json'
        }
    }
    signed = sign_pack(document, private_key, key_id=key_id)
    validate_pack(signed)
    return signed


def build_paid_transaction_pack(private_key, key_id=KEY_ID):
    # Sample: Read transaction blueprints catalog. Build and sign paid pack.
    bp_path = ROOT / 'design/transaction-blueprints-v1.json'
    bp_data = json.loads(bp_path.read_text(encoding='utf-8'))

    records = []
    records.append(make_record(
        'blueprint.transactions.catalog',
        'DESIGN',
        'KNOWLEDGE',
        {
            'format': bp_data.get('format'),
            'id': bp_data.get('id'),
            'checkedAt': bp_data.get('checkedAt'),
            'profiles': bp_data.get('profiles'),
            'authority': bp_data.get('authority'),
            'cases': [e['id'] for e in bp_data.get('entries', [])]
        }
    ))

    for entry in bp_data.get('entries', []):
        entry_id = entry['id']
        default_body = {
            'blueprint': entry_id,
            'kind': 'DEFAULT',
            'rule': entry['rule'],
            'preferred': entry['preferred'],
            'when': entry['when'],
            'alternatives': entry['alternatives'],
            'examples': entry['examples']
        }
        records.append(make_record(f"blueprint.{entry_id}.default", 'SERVICE', 'KNOWLEDGE', default_body))

        correctness_body = {
            'blueprint': entry_id,
            'kind': 'CORRECTNESS',
            'correctness': entry['correctness'],
            'invariants': entry['invariants'],
            'procedure': entry['procedure'],
            'checks': entry['checks'],
            'interface': entry['interface'],
            'examples': entry['examples']
        }
        records.append(make_record(f"blueprint.{entry_id}.correctness", 'SERVICE', 'PROCEDURE', correctness_body))

    document = {
        'format': 'durai-ontology-pack/v2',
        'id': 'durai.blueprints.transactions',
        'version': '1.0.0',
        'kind': 'APPLICATION',
        'publisher': 'durai.io',
        'tier': 'PAID',
        'license_ref': 'durai-pack-commercial-v1',
        'min_shell_version': '1.0.0',
        'valid_from': None,
        'valid_until': None,
        'records': records,
        'edges': [],
        'provenance': {
            'catalog': 'design/transaction-blueprints-v1.json',
            'cases': len(bp_data.get('entries', []))
        }
    }
    signed = sign_pack(document, private_key, key_id=key_id)
    validate_pack(signed)
    return signed


def main(argv=None):
    # Sample: Build packs to directory packs. Print status JSON with pack digests.
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=PACKS_DIR)
    args = parser.parse_args(argv)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    priv, pub = get_or_create_test_key()
    trusted = TrustedKeyList()
    trusted.add_key(KEY_ID, pub)

    pub_key_path = output_dir / 'test_public_key.hex'
    pub_key_path.write_text(export_public_key_hex(pub), encoding='utf-8')

    starter = build_starter_pack(priv, key_id=KEY_ID)
    v_starter = verify_pack(starter, trusted)
    if not v_starter.valid:
        raise ValueError(f"Starter pack verification failed: {v_starter.error}")
    starter_file = output_dir / 'durai.starter-v1.0.0.json'
    starter_file.write_text(json.dumps(starter, indent=2, ensure_ascii=False), encoding='utf-8')

    paid = build_paid_transaction_pack(priv, key_id=KEY_ID)
    v_paid = verify_pack(paid, trusted)
    if not v_paid.valid:
        raise ValueError(f"Paid pack verification failed: {v_paid.error}")
    paid_file = output_dir / 'durai.blueprints.transactions-v1.0.0.json'
    paid_file.write_text(json.dumps(paid, indent=2, ensure_ascii=False), encoding='utf-8')

    summary = {
        'status': 'SUCCESS',
        'starter_pack': {
            'id': starter['id'],
            'version': starter['version'],
            'tier': starter['tier'],
            'records': len(starter['records']),
            'digest': digest(starter),
            'path': str(starter_file)
        },
        'paid_pack': {
            'id': paid['id'],
            'version': paid['version'],
            'tier': paid['tier'],
            'records': len(paid['records']),
            'digest': digest(paid),
            'path': str(paid_file)
        }
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
