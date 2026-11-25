"""Sample: Put 3 records in pack P-1 version 1 with key K-1. Ask the ancestors of record R3. Get R2 then R1."""
from typing import Protocol, Sequence


class OntologyStorePort(Protocol):
    def put_records(self, pack_id: str, version: str, records: Sequence[dict], key: str) -> int:
        # Sample: Put records R1 and R2 with key K-1. Return 2. Put them again with key K-1. Return 2 and write nothing.
        ...

    def get_record(self, record_id: str, view: Sequence[str] | None = None) -> dict:
        # Sample: Get R1 in view ["P-1@1"]. Return the record. Get R9. Raise NotFound.
        ...

    def ancestors(self, record_id: str, view: Sequence[str] | None = None, depth: int = 10) -> list[str]:
        # Sample: Edges R3 to R2 and R2 to R1. Ancestors of R3 return [R2, R1]. Order is depth then record id.
        ...

    def descendants(self, record_id: str, view: Sequence[str] | None = None, depth: int = 10) -> list[str]:
        # Sample: Edges R3 to R2 and R2 to R1. Descendants of R1 return [R2, R3].
        ...

    def neighbors(self, record_id: str, view: Sequence[str] | None = None, link_kind: str | None = None) -> list[str]:
        # Sample: Edge R2 to R1 with kind PARENT. Neighbors of R1 with kind PARENT return [R2].
        ...

    def retire_pack(self, pack_id: str, version: str, key: str) -> None:
        # Sample: Retire P-1 version 1. A view that holds P-1@1 returns NotFound for R1.
        ...
