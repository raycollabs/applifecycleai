"""Sample: Create an in-memory registry. Install pack P-1. Check state INSTALLED."""
from datetime import datetime, timezone
from typing import Any, Callable, List, Mapping, Optional, Sequence
from .common import Conflict, canonical, digest, uid
from .pack_signing import (
    TrustedKeyList,
    VerificationResult,
    canonical_record_digests,
    sign_pack,
    verify_pack,
)

STAGED = 'STAGED'
VERIFIED = 'VERIFIED'
VERIFIED_COMMUNITY = 'VERIFIED_COMMUNITY'
INSTALLED = 'INSTALLED'
ACTIVE = 'ACTIVE'
SUPERSEDED = 'SUPERSEDED'
EXPIRED = 'EXPIRED'
REVOKED = 'REVOKED'
FAILED = 'FAILED'


def utc_iso():
    # Sample: Return current UTC time formatted as an ISO timestamp.
    return datetime.now(timezone.utc).isoformat()


def make_license_token(
    organization: str,
    pack_ids: Sequence[str],
    seats: int = 1,
    update_channel: str = 'stable',
    valid_until: Optional[str] = None,
    private_key: Optional[Any] = None,
    key_id: str = 'durai-license-root'
) -> dict:
    # Sample: Create license token for Acme with pack P-1. Sign with key K-1.
    token_body = {
        'organization': organization,
        'pack_ids': list(pack_ids),
        'seats': int(seats),
        'update_channel': update_channel,
        'valid_until': valid_until
    }
    token = dict(token_body)
    token['digest'] = digest(token_body)
    if private_key:
        sig = private_key.sign(canonical(token_body))
        token['signature'] = {
            'key_id': key_id,
            'algorithm': 'ed25519',
            'value': sig.hex()
        }
    return token


def verify_license_token(token: Mapping[str, Any], trusted_keys: Optional[TrustedKeyList] = None) -> Mapping[str, Any]:
    # Sample: Verify token T-1 for pack P-1. Check validity and expiration.
    body = {
        'organization': token.get('organization', ''),
        'pack_ids': list(token.get('pack_ids', [])),
        'seats': int(token.get('seats', 1)),
        'update_channel': token.get('update_channel', 'stable'),
        'valid_until': token.get('valid_until')
    }
    sig_obj = token.get('signature')
    if sig_obj and trusted_keys:
        key_id = sig_obj.get('key_id')
        pub = trusted_keys.get_key(key_id)
        if not pub:
            return {'valid': False, 'error': f'Untrusted license key {key_id}', 'expired': False}
        try:
            pub.verify(bytes.fromhex(sig_obj['value']), canonical(body))
        except Exception as exc:
            return {'valid': False, 'error': f'Invalid license signature: {str(exc)}', 'expired': False}

    valid_until = token.get('valid_until')
    expired = False
    if valid_until:
        try:
            expiry_dt = datetime.fromisoformat(valid_until.replace('Z', '+00:00'))
            now_dt = datetime.now(timezone.utc)
            if now_dt > expiry_dt:
                expired = True
        except Exception:
            expired = True

    return {
        'valid': not expired,
        'expired': expired,
        'organization': token.get('organization'),
        'pack_ids': token.get('pack_ids', []),
        'seats': token.get('seats', 1),
        'valid_until': valid_until,
        'error': 'License expired' if expired else None
    }


class RegistryStore:
    # Sample: Define store interface with put, get, update, and audit methods.
    def put_pack(self, entry: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def get_pack(self, pack_id: str, version: str) -> Optional[dict]:
        raise NotImplementedError

    def list_packs(self, state: Optional[str] = None) -> Sequence[dict]:
        raise NotImplementedError

    def update_pack_state(self, pack_id: str, version: str, new_state: str) -> None:
        raise NotImplementedError

    def add_audit(self, audit_row: Mapping[str, Any]) -> None:
        raise NotImplementedError

    def list_audit(self) -> Sequence[dict]:
        raise NotImplementedError


class InMemoryRegistryStore(RegistryStore):
    def __init__(self):
        # Sample: Initialize empty in-memory store for packs and audit rows.
        self._packs = {}
        self._audit = []

    def put_pack(self, entry: Mapping[str, Any]) -> None:
        # Sample: Store pack P-1 version 1. Check pack in dictionary.
        key = (entry['id'], entry['version'])
        self._packs[key] = dict(entry)

    def get_pack(self, pack_id: str, version: str) -> Optional[dict]:
        # Sample: Retrieve pack P-1 version 1. Return dictionary or None.
        return self._packs.get((pack_id, version))

    def list_packs(self, state: Optional[str] = None) -> Sequence[dict]:
        # Sample: List installed packs. Return matching entries.
        entries = list(self._packs.values())
        if state is not None:
            entries = [e for e in entries if e.get('state') == state]
        return entries

    def update_pack_state(self, pack_id: str, version: str, new_state: str) -> None:
        # Sample: Change state of pack P-1 version 1 to REVOKED.
        key = (pack_id, version)
        if key in self._packs:
            self._packs[key]['state'] = new_state

    def add_audit(self, audit_row: Mapping[str, Any]) -> None:
        # Sample: Append audit row A-1. Check audit log length increases by 1.
        self._audit.append(dict(audit_row))

    def list_audit(self) -> Sequence[dict]:
        # Sample: Read all audit rows. Return chronological list.
        return list(self._audit)


class PackRegistry:
    def __init__(self, store: Optional[RegistryStore] = None, trusted_keys: Optional[TrustedKeyList] = None):
        # Sample: Create registry with memory store and key store.
        self.store = store or InMemoryRegistryStore()
        self.trusted_keys = trusted_keys or TrustedKeyList()
        self._dirty_listeners: List[Callable[[], None]] = []
        self._licenses: dict = {}

    def register_dirty_listener(self, listener: Callable[[], None]) -> None:
        # Sample: Register callback function F-1. Call it on pack state change.
        self._dirty_listeners.append(listener)

    def _mark_dirty(self) -> None:
        # Sample: Notify all registered listeners when pack state changes.
        for listener in self._dirty_listeners:
            listener()

    def record_license(self, token: Mapping[str, Any]) -> Mapping[str, Any]:
        # Sample: Record license token for Acme. Return license verification status.
        result = verify_license_token(token, self.trusted_keys)
        for pack_id in token.get('pack_ids', []):
            self._licenses[pack_id] = token
        self._mark_dirty()
        return result

    def get_license(self, pack_id: str) -> Optional[dict]:
        # Sample: Fetch license token registered for pack P-1.
        return self._licenses.get(pack_id)

    def stage(self, document: Mapping[str, Any]) -> dict:
        # Sample: Stage pack P-1. Return staged entry with state STAGED.
        pack_id = document['id']
        version = document['version']
        doc_digest = digest(document)
        entry = {
            'id': pack_id,
            'version': version,
            'digest': doc_digest,
            'document': dict(document),
            'state': STAGED,
            'tier': document.get('tier', 'COMMUNITY'),
            'label': document.get('tier', 'COMMUNITY'),
            'records': document.get('records', []),
            'active': False,
            'created_at': utc_iso()
        }
        self.store.put_pack(entry)
        self.store.add_audit({
            'id': uid(),
            'action': 'PACK_STAGE',
            'pack_id': pack_id,
            'version': version,
            'digest': doc_digest,
            'state': STAGED,
            'timestamp': utc_iso()
        })
        self._mark_dirty()
        return entry

    def verify(self, document: Mapping[str, Any]) -> VerificationResult:
        # Sample: Verify document P-1. Return verification result object.
        return verify_pack(document, self.trusted_keys)

    def install(self, document: Mapping[str, Any], license_token: Optional[Mapping[str, Any]] = None) -> dict:
        # Sample: Install pack P-1 version 2. Check state INSTALLED and audit row logged.
        pack_id = document.get('id', '')
        version = document.get('version', '')
        doc_digest = digest(document)

        verification = self.verify(document)
        if not verification.valid or verification.state == FAILED:
            entry = {
                'id': pack_id,
                'version': version,
                'digest': doc_digest,
                'document': dict(document),
                'state': FAILED,
                'tier': 'COMMUNITY',
                'label': FAILED,
                'records': [],
                'active': False,
                'error': verification.error,
                'created_at': utc_iso()
            }
            self.store.add_audit({
                'id': uid(),
                'action': 'PACK_INSTALL_FAILED',
                'pack_id': pack_id,
                'version': version,
                'digest': doc_digest,
                'state': FAILED,
                'error': verification.error,
                'timestamp': utc_iso()
            })
            self._mark_dirty()
            return entry

        if license_token:
            self.record_license(license_token)

        existing = self.store.get_pack(pack_id, version)
        if existing and existing.get('state') in (INSTALLED, VERIFIED_COMMUNITY, ACTIVE):
            if existing.get('digest') == doc_digest:
                self.store.add_audit({
                    'id': uid(),
                    'action': 'PACK_INSTALL_REPLAY',
                    'pack_id': pack_id,
                    'version': version,
                    'digest': doc_digest,
                    'state': existing['state'],
                    'timestamp': utc_iso()
                })
                return existing
            raise Conflict('Pack version is immutable')

        final_state = verification.state if verification.state == VERIFIED_COMMUNITY else INSTALLED
        final_label = verification.label

        entry = {
            'id': pack_id,
            'version': version,
            'digest': doc_digest,
            'document': dict(document),
            'state': final_state,
            'tier': verification.tier,
            'label': final_label,
            'records': document.get('records', []),
            'active': True,
            'created_at': utc_iso()
        }
        self.store.put_pack(entry)
        self.store.add_audit({
            'id': uid(),
            'action': 'PACK_INSTALL',
            'pack_id': pack_id,
            'version': version,
            'digest': doc_digest,
            'state': final_state,
            'label': final_label,
            'timestamp': utc_iso()
        })
        self._mark_dirty()
        return entry

    def activate(self, pack_id: str, version: str) -> dict:
        # Sample: Activate pack P-1 version 2. Mark previous version SUPERSEDED.
        entry = self.store.get_pack(pack_id, version)
        if not entry:
            raise KeyError(f'Pack {pack_id} version {version} not found')
        if entry.get('state') == REVOKED:
            raise ValueError(f'Cannot activate revoked pack {pack_id}')

        for pack in self.store.list_packs():
            if pack['id'] == pack_id and pack['version'] != version and pack['state'] == ACTIVE:
                self.store.update_pack_state(pack_id, pack['version'], SUPERSEDED)
                self.store.add_audit({
                    'id': uid(),
                    'action': 'PACK_SUPERSEDED',
                    'pack_id': pack_id,
                    'version': pack['version'],
                    'digest': pack['digest'],
                    'state': SUPERSEDED,
                    'timestamp': utc_iso()
                })

        self.store.update_pack_state(pack_id, version, ACTIVE)
        entry['state'] = ACTIVE
        entry['active'] = True
        self.store.add_audit({
            'id': uid(),
            'action': 'PACK_ACTIVATE',
            'pack_id': pack_id,
            'version': version,
            'digest': entry['digest'],
            'state': ACTIVE,
            'timestamp': utc_iso()
        })
        self._mark_dirty()
        return entry

    def revoke(self, pack_id: str, version: Optional[str] = None) -> None:
        # Sample: Revoke pack P-1. Mark entry REVOKED and inactive.
        packs = self.store.list_packs()
        for p in packs:
            if p['id'] == pack_id and (version is None or p['version'] == version):
                self.store.update_pack_state(pack_id, p['version'], REVOKED)
                p['state'] = REVOKED
                p['active'] = False
                self.store.add_audit({
                    'id': uid(),
                    'action': 'PACK_REVOKE',
                    'pack_id': pack_id,
                    'version': p['version'],
                    'digest': p['digest'],
                    'state': REVOKED,
                    'timestamp': utc_iso()
                })
        self._mark_dirty()

    def get(self, pack_id: str, version: Optional[str] = None) -> Optional[dict]:
        # Sample: Retrieve pack P-1. Return active or latest version entry.
        matches = [p for p in self.store.list_packs() if p['id'] == pack_id]
        if not matches:
            return None
        if version:
            for p in matches:
                if p['version'] == version:
                    return p
            return None
        actives = [p for p in matches if p['state'] in (ACTIVE, INSTALLED, VERIFIED_COMMUNITY)]
        if actives:
            return actives[-1]
        return matches[-1]

    def check_updates(self, pack_id: str, candidate_document: Mapping[str, Any]) -> Optional[dict]:
        # Sample: Check if update for P-1 is permitted under registered license.
        pack_license = self.get_license(pack_id)
        if pack_license:
            ver = verify_license_token(pack_license, self.trusted_keys)
            if ver['expired']:
                return None
        return dict(candidate_document)

    def is_usable_for_build(self, pack_id: str, version: Optional[str] = None) -> bool:
        # Sample: Check if pack P-1 can be used in a new build. Refuse if REVOKED.
        entry = self.get(pack_id, version)
        if not entry:
            return False
        if entry.get('state') == REVOKED:
            return False
        return True
