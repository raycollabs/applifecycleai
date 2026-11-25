"""Sample: Make MemoryVectorStore. Upsert 3 vectors of size 2. Search with k 2. Get the 2 closest ids, best score first."""
import math

from ...ports.errors import DimensionMismatch, NotFound


class MemoryVectorStore:
    def __init__(self):
        # Sample: Start with no collection and no applied key.
        self.collections = {}
        self.keys = {}

    def upsert(self, collection, items, key):
        # Sample: Upsert A (1,0) and B (0,1) with key V-1. Return 2. Again with key V-1. Return 2 and change nothing. A vector of size 3 in a size 2 collection raises DimensionMismatch.
        if key in self.keys:
            return self.keys[key]
        held = self.collections.setdefault(collection, {'size': None, 'items': {}})
        size = held['size']
        for _, _, vector in items:
            if size is None:
                size = len(vector)
            if len(vector) != size:
                raise DimensionMismatch('Expected %d, got %d' % (size, len(vector)))
        held['size'] = size
        for item_id, text, vector in items:
            held['items'][item_id] = (text, list(vector))
        self.keys[key] = len(items)
        return len(items)

    def search(self, collection, query_vector, k, allowed_ids=None):
        # Sample: Search (1,0) with k 2 over A (1,0), B (0,1) and C (0.9,0.1). Return A then C. A tie on score sorts by id.
        held = self.collections.get(collection)
        if held is None:
            raise NotFound(collection)
        if held['size'] is not None and len(query_vector) != held['size']:
            raise DimensionMismatch('Expected %d, got %d' % (held['size'], len(query_vector)))
        norm = math.sqrt(sum(x * x for x in query_vector)) or 1.0
        scored = []
        for item_id, (_, vector) in held['items'].items():
            if allowed_ids is not None and item_id not in allowed_ids:
                continue
            other = math.sqrt(sum(x * x for x in vector)) or 1.0
            scored.append((item_id, round(sum(a * b for a, b in zip(query_vector, vector)) / (norm * other), 6)))
        return sorted(scored, key=lambda pair: (-pair[1], pair[0]))[:k]

    def drop(self, collection, key):
        # Sample: Drop the collection with key V-9. Search it. Raise NotFound. Drop again with key V-9. Change nothing.
        if key in self.keys:
            return
        self.collections.pop(collection, None)
        self.keys[key] = 0
