"""Sample: Install an unsigned pack that claims tier PAID. Read state VERIFIED_COMMUNITY and label COMMUNITY. The registry is owned by agent A2 (durai/pack_registry.py)."""
from typing import Any, Mapping, Optional, Protocol


class PackPort(Protocol):
    def install(self, document: Mapping[str, Any], license_token: Optional[Mapping[str, Any]] = None) -> dict:
        # Sample: Install signed pack P-1 version 1. Return an entry with state INSTALLED. Install it again with the same digest. Return the first entry and one replay audit row. A changed document with the same version raises Conflict.
        ...

    def activate(self, pack_id: str, version: str) -> dict:
        # Sample: Activate P-1 version 2. P-1 version 1 moves to SUPERSEDED. Activate a revoked pack. Raise an error.
        ...

    def revoke(self, pack_id: str, version: Optional[str] = None) -> None:
        # Sample: Revoke P-1. Every version of P-1 moves to REVOKED and is no longer active.
        ...

    def get(self, pack_id: str, version: Optional[str] = None) -> Optional[dict]:
        # Sample: Get P-1. Return the active or latest entry. Get P-9. Return None.
        ...

    def check_updates(self, pack_id: str, candidate_document: Mapping[str, Any]) -> Optional[dict]:
        # Sample: P-1 has an expired license. Return None. P-1 has a valid license. Return the candidate.
        ...

    def is_usable_for_build(self, pack_id: str, version: Optional[str] = None) -> bool:
        # Sample: P-1 is ACTIVE. Return True. P-1 is REVOKED or unknown. Return False.
        ...
