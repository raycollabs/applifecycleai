"""Sample: Upsert 50 vectors. Search with k 3. Get the 3 closest ids, best score first, ties by id."""
from typing import Protocol, Sequence


class VectorPort(Protocol):
    def upsert(self, collection: str, items: Sequence[tuple], key: str) -> int:
        # Sample: Upsert 2 items (id, text, vector) with key V-1. Return 2. A vector of another size raises DimensionMismatch.
        ...

    def search(self, collection: str, query_vector: Sequence[float], k: int, allowed_ids: Sequence[str] | None = None) -> list[tuple]:
        # Sample: Search the collection with k 3. Return [(id, score)] best first. Search a missing collection. Raise NotFound.
        ...

    def drop(self, collection: str, key: str) -> None:
        # Sample: Drop the collection with key V-9. Search it. Raise NotFound.
        ...
