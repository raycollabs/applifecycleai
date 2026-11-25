"""Sample: Create an effective view from registry R-1. Read 3 records from pack P-1."""
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from .common import digest
from .pack_registry import (
    ACTIVE,
    INSTALLED,
    VERIFIED_COMMUNITY,
    PackRegistry,
)


def utc_iso():
    # Sample: Return current UTC time formatted as an ISO timestamp.
    return datetime.now(timezone.utc).isoformat()


class EffectiveView:
    def __init__(self, registry: PackRegistry):
        # Sample: Bind effective view to registry R-1. Set dirty flag to True.
        self._registry = registry
        self._user_overlays: Dict[str, dict] = {}
        self._cached_view: Optional[dict] = None
        self._dirty: bool = True
        self._computation_count: int = 0
        self._invalidation_listeners: List[Callable[[], None]] = []

        self._registry.register_dirty_listener(self.mark_dirty)

    @property
    def computation_count(self) -> int:
        # Sample: Return integer count of view computations performed so far.
        return self._computation_count

    @property
    def is_dirty(self) -> bool:
        # Sample: Return True when view cache requires recomputation.
        return self._dirty

    def register_invalidation_listener(self, listener: Callable[[], None]) -> None:
        # Sample: Register downstream listener L-1. Call it when view becomes dirty.
        self._invalidation_listeners.append(listener)

    def mark_dirty(self) -> None:
        # Sample: Mark dirty flag True. Notify all downstream invalidation listeners.
        self._dirty = True
        for listener in self._invalidation_listeners:
            listener()

    def add_user_overlay(self, record_id: str, body: Mapping[str, Any], layer: str = 'DESIGN', record_type: str = 'KNOWLEDGE') -> dict:
        # Sample: Add user overlay for record R-1 with body B-1. Invalidate cache.
        record = {
            'id': record_id,
            'layer': layer,
            'type': record_type,
            'body': dict(body),
            'digest': digest(body),
            'origin': 'USER_OVERLAY'
        }
        self._user_overlays[record_id] = record
        self.mark_dirty()
        return record

    def get_user_overlays(self) -> Mapping[str, dict]:
        # Sample: Return dictionary of all registered user overlays.
        return dict(self._user_overlays)

    def get_view(self) -> dict:
        # Sample: Read effective view. Return cached view when clean. Recompute when dirty.
        if not self._dirty and self._cached_view is not None:
            return self._cached_view

        self._computation_count += 1
        usable_states = (ACTIVE, INSTALLED, VERIFIED_COMMUNITY)
        packs = self._registry.store.list_packs()
        active_packs = [p for p in packs if p.get('state') in usable_states]

        merged_records: Dict[str, dict] = {}
        contributing_packs: List[str] = []

        for p in active_packs:
            contributing_packs.append(p['id'])
            for rec in p.get('records', []):
                merged_records[rec['id']] = rec

        for overlay_id, overlay_rec in self._user_overlays.items():
            merged_records[overlay_id] = overlay_rec

        view = {
            'records': merged_records,
            'records_list': list(merged_records.values()),
            'contributing_packs': contributing_packs,
            'computed_at': utc_iso()
        }
        self._cached_view = view
        self._dirty = False
        return view

    def list_records(self) -> Sequence[dict]:
        # Sample: List all active records in the effective view. Return list of dicts.
        return self.get_view()['records_list']

    def get_record(self, record_id: str) -> Optional[dict]:
        # Sample: Fetch record R-1 from the effective view. Return record or None.
        return self.get_view()['records'].get(record_id)
