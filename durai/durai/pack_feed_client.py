"""Sample: Point PackFeedClient at folder F-1 with feed.json and signed pack P-1. Call sync() and check P-1 is INSTALLED."""
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .common import digest
from .pack_registry import ACTIVE, FAILED, INSTALLED, REVOKED, VERIFIED_COMMUNITY, PackRegistry


def _version_key(version: str):
    # Sample: Parse version 1.2.10 into tuple (0, 1, 0, 2, 0, 10) for numeric ordering.
    parts = re.split(r'([0-9]+)', str(version))
    key = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


class PackFeedClient:
    def __init__(self, registry: PackRegistry, source_folder: Any):
        # Sample: Bind PackFeedClient to registry R-1 and local feed directory F-1.
        self.registry = registry
        self.source_folder = Path(source_folder).resolve()

    def list_available(self) -> List[dict]:
        # Sample: Read feed.json from F-1. Return entries ordered by seq, pack_id, and version.
        feed_path = self.source_folder / 'feed.json'
        if not feed_path.is_file():
            return []
        data = json.loads(feed_path.read_text(encoding='utf-8'))
        entries = list(data.get('entries', []))
        entries.sort(key=lambda e: (int(e.get('seq', 0)), str(e.get('pack_id', '')), _version_key(e.get('version', '0'))))
        return entries

    def _entry_path(self, entry: Mapping[str, Any]) -> Path:
        # Sample: Resolve entry path P-1-v1.json inside F-1. Refuse paths outside F-1.
        pack_id = entry['pack_id']
        version = entry['version']
        rel = entry.get('path') or f'{pack_id}-v{version}.json'
        candidate = (self.source_folder / rel).resolve()
        if candidate != self.source_folder and self.source_folder not in candidate.parents:
            raise ValueError(f'Feed path escapes source folder: {rel}')
        return candidate

    def fetch(self, pack_id: str, version: str) -> dict:
        # Sample: Fetch pack P-1 version 1.0.0 from F-1. Return parsed JSON document.
        for entry in self.list_available():
            if entry.get('pack_id') == pack_id and str(entry.get('version')) == str(version):
                target = self._entry_path(entry)
                if not target.is_file():
                    raise FileNotFoundError(f'Pack file missing: {target}')
                return json.loads(target.read_text(encoding='utf-8'))
        raise KeyError(f'Feed entry not found: {pack_id}@{version}')

    def _has_newer_installed(self, pack_id: str, candidate_version: str) -> bool:
        # Sample: Pack P-1 version 2.0.0 is installed. Candidate is 1.0.0. Return True.
        candidate_key = _version_key(candidate_version)
        for existing in self.registry.store.list_packs():
            if existing.get('id') != pack_id:
                continue
            if existing.get('state') in (INSTALLED, VERIFIED_COMMUNITY, ACTIVE, REVOKED):
                if _version_key(existing.get('version', '0')) > candidate_key:
                    return True
        return False

    def sync(self) -> Dict[str, List[dict]]:
        # Sample: Sync feed entries from F-1 in seq order. Install new packs, mark tampered FAILED, revoke revoked entries, and skip older versions.
        summary: Dict[str, List[dict]] = {
            'installed': [],
            'replayed': [],
            'failed': [],
            'revoked': [],
            'skipped': [],
        }
        for entry in self.list_available():
            pack_id = entry['pack_id']
            version = str(entry.get('version', ''))
            is_revocation = (
                entry.get('action') == 'REVOKE'
                or bool(entry.get('revoked'))
                or entry.get('state') == REVOKED
            )
            if is_revocation:
                self.registry.revoke(pack_id, version or None)
                summary['revoked'].append({'pack_id': pack_id, 'version': version})
                continue

            if self._has_newer_installed(pack_id, version):
                summary['skipped'].append({'pack_id': pack_id, 'version': version, 'reason': 'older_version'})
                continue

            doc = self.fetch(pack_id, version)
            if self.registry.check_updates(pack_id, doc) is None:
                summary['skipped'].append({'pack_id': pack_id, 'version': version, 'reason': 'license_expired'})
                continue

            existing = self.registry.store.get_pack(pack_id, version)
            if existing and existing.get('state') in (INSTALLED, VERIFIED_COMMUNITY, ACTIVE) and existing.get('digest') == digest(doc):
                summary['replayed'].append({'pack_id': pack_id, 'version': version, 'state': existing['state']})
                continue

            result = self.registry.install(doc)
            if result.get('state') == FAILED:
                summary['failed'].append({
                    'pack_id': pack_id,
                    'version': version,
                    'state': FAILED,
                    'error': result.get('error'),
                })
            else:
                summary['installed'].append({
                    'pack_id': pack_id,
                    'version': version,
                    'state': result['state'],
                })
        return summary
