"""Sample: Sign pack P-1 with private key K-1. Verify signature with public key K-1."""
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from .common import canonical, digest


@dataclass
class VerificationResult:
    # Sample: Hold valid True, state VERIFIED, tier FREE, label FREE and error None.
    valid: bool
    state: str
    tier: str
    label: str
    error: Optional[str] = None


class TrustedKeyList:
    def __init__(self):
        # Sample: Start with 0 trusted keys. Add key K-1. Check 1 key in store.
        self._keys = {}

    def add_key(self, key_id: str, public_key: Any, status: str = 'ACTIVE', valid_from: Optional[str] = None, valid_until: Optional[str] = None):
        # Sample: Add key K-1 with status ACTIVE. Check K-1 returns the public key.
        if isinstance(public_key, str):
            public_key = load_public_key_hex(public_key)
        self._keys[key_id] = {
            'key_id': key_id,
            'public_key': public_key,
            'status': status,
            'valid_from': valid_from,
            'valid_until': valid_until
        }

    def rotate_key(self, old_key_id: str, new_key_id: str, new_public_key: Any):
        # Sample: Rotate key K-1 to K-2. Check K-1 has status RETIRED and K-2 has status ACTIVE.
        if old_key_id in self._keys:
            self._keys[old_key_id]['status'] = 'RETIRED'
        self.add_key(new_key_id, new_public_key, status='ACTIVE')

    def revoke_key(self, key_id: str):
        # Sample: Revoke key K-1. Check get_key returns None.
        if key_id in self._keys:
            self._keys[key_id]['status'] = 'REVOKED'

    def get_key(self, key_id: str) -> Optional[Any]:
        # Sample: Query key K-1 with status ACTIVE. Return the public key.
        entry = self._keys.get(key_id)
        if not entry:
            return None
        if entry['status'] == 'REVOKED':
            return None
        return entry['public_key']

    def is_trusted(self, key_id: str) -> bool:
        # Sample: Check key K-1. Return True when K-1 is ACTIVE or RETIRED.
        return self.get_key(key_id) is not None

    def list_keys(self) -> Sequence[Mapping[str, Any]]:
        # Sample: List all keys. Return 2 key summaries.
        return list(self._keys.values())


def generate_key_pair() -> Tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey]:
    # Sample: Generate 1 Ed25519 key pair. Check private key generates 1 public key.
    priv = ed25519.Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def export_private_key_hex(private_key: ed25519.Ed25519PrivateKey) -> str:
    # Sample: Export private key K-1. Return 64 hex characters.
    raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )
    return raw.hex()


def export_public_key_hex(public_key: ed25519.Ed25519PublicKey) -> str:
    # Sample: Export public key K-1. Return 64 hex characters.
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    return raw.hex()


def load_private_key_hex(hex_string: str) -> ed25519.Ed25519PrivateKey:
    # Sample: Load 64 hex characters. Return 1 Ed25519PrivateKey object.
    return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_string))


def load_public_key_hex(hex_string: str) -> ed25519.Ed25519PublicKey:
    # Sample: Load 64 hex characters. Return 1 Ed25519PublicKey object.
    return ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_string))


def canonical_record_digests(records: Sequence[Mapping[str, Any]]) -> bytes:
    # Sample: Sort 3 records by id. Return canonical JSON bytes of digest list.
    sorted_recs = sorted(records, key=lambda r: r['id'])
    return canonical([r['digest'] for r in sorted_recs])


def sign_pack(document: Mapping[str, Any], private_key: ed25519.Ed25519PrivateKey, key_id: str = 'durai-release-2026') -> dict:
    # Sample: Sign pack P-1 with key K-1. Return document with signature object.
    doc = dict(document)
    records = doc.get('records', [])
    payload = canonical_record_digests(records)
    sig = private_key.sign(payload)
    doc['signature'] = {
        'key_id': key_id,
        'algorithm': 'ed25519',
        'value': sig.hex()
    }
    return doc


def verify_pack(document: Mapping[str, Any], trusted_keys: Optional[TrustedKeyList] = None) -> VerificationResult:
    # Sample: Verify pack P-1 with 3 records. Check digest match and signature validity.
    records = document.get('records', [])
    for rec in records:
        if rec.get('digest') != digest(rec.get('body', {})):
            return VerificationResult(
                valid=False,
                state='FAILED',
                tier='COMMUNITY',
                label='FAILED',
                error=f"Record digest mismatch on {rec.get('id')}"
            )

    sig_obj = document.get('signature')
    declared_tier = document.get('tier', 'FREE')

    if not sig_obj:
        if declared_tier in ('PAID', 'ENTERPRISE'):
            return VerificationResult(
                valid=True,
                state='VERIFIED_COMMUNITY',
                tier='COMMUNITY',
                label='COMMUNITY',
                error=None
            )
        return VerificationResult(
            valid=True,
            state='VERIFIED',
            tier=declared_tier,
            label=declared_tier,
            error=None
        )

    key_id = sig_obj.get('key_id') if isinstance(sig_obj, dict) else None
    sig_val = sig_obj.get('value') if isinstance(sig_obj, dict) else str(sig_obj)

    if not trusted_keys or not key_id:
        return VerificationResult(
            valid=False,
            state='FAILED',
            tier='COMMUNITY',
            label='FAILED',
            error='No trusted key store or missing key id'
        )

    pub_key = trusted_keys.get_key(key_id)
    if not pub_key:
        return VerificationResult(
            valid=False,
            state='FAILED',
            tier='COMMUNITY',
            label='FAILED',
            error=f'Untrusted or revoked signing key {key_id}'
        )

    payload = canonical_record_digests(records)
    try:
        sig_bytes = bytes.fromhex(sig_val)
        pub_key.verify(sig_bytes, payload)
    except Exception as exc:
        return VerificationResult(
            valid=False,
            state='FAILED',
            tier='COMMUNITY',
            label='FAILED',
            error=f'Cryptographic signature check failed: {str(exc)}'
        )

    return VerificationResult(
        valid=True,
        state='VERIFIED',
        tier=declared_tier,
        label=declared_tier,
        error=None
    )
