"""Sample: Make MemoryOntologyStore. Put R1, R2 and R3 with edges R3 to R2 to R1. Ask the ancestors of R3. Get R2 then R1."""
from collections import deque

from ...common import digest
from ...ports.errors import Conflict, DigestMismatch, NotFound


class MemoryOntologyStore:
    def __init__(self):
        # Sample: Start with no pack, no record and no applied key.
        self.records = {}
        self.retired = set()
        self.keys = {}

    def put_records(self, pack_id, version, records, key):
        # Sample: Put R1 and R2 with key K-1. Return 2. Put them again with key K-1. Return 2 and write nothing. Put R1 with a changed body under key K-2. Raise Conflict.
        if key in self.keys:
            return self.keys[key]
        for record in records:
            if record['digest'] != digest(record['body']):
                raise DigestMismatch(record['id'])
            held = self.records.get(record['id'])
            if held and held['pack'] == (pack_id, version) and held['record']['digest'] != record['digest']:
                raise Conflict(record['id'])
        for record in records:
            self.records[record['id']] = {'pack': (pack_id, version), 'record': dict(record)}
        self.keys[key] = len(records)
        return len(records)

    def visible(self, record_id, view):
        # Sample: Record R1 in pack P-1 version 1. View ["P-1@1"] shows it. View ["P-2@1"] hides it. A retired pack hides it.
        held = self.records.get(record_id)
        if held is None or held['pack'] in self.retired:
            return None
        if view is not None and '%s@%s' % held['pack'] not in view:
            return None
        return held

    def get_record(self, record_id, view=None):
        # Sample: Get R1. Return the record. Get R9. Raise NotFound.
        held = self.visible(record_id, view)
        if held is None:
            raise NotFound(record_id)
        return dict(held['record'])

    def edges(self, view):
        # Sample: Records R3 and R2 with edges R3 to R2. Return the pair (R3, PARENT, R2) when both records are visible.
        result = []
        for record_id, held in self.records.items():
            if self.visible(record_id, view) is None:
                continue
            for edge in held['record'].get('edges', []):
                if self.visible(edge['to'], view) is not None:
                    result.append((record_id, edge['kind'], edge['to']))
        return result

    def walk(self, record_id, view, depth, forward):
        # Sample: Edges R3 to R2 and R2 to R1. Walk forward from R3 to depth 10. Return R2 at depth 1 and R1 at depth 2 in that order.
        if self.visible(record_id, view) is None:
            raise NotFound(record_id)
        graph = {}
        for source, _, target in self.edges(view):
            graph.setdefault(source if forward else target, []).append(target if forward else source)
        seen, order, queue = {record_id}, [], deque([(record_id, 0)])
        while queue:
            node, level = queue.popleft()
            if level >= depth:
                continue
            for nxt in sorted(set(graph.get(node, []))):
                if nxt not in seen:
                    seen.add(nxt)
                    order.append((level + 1, nxt))
                    queue.append((nxt, level + 1))
        return [name for _, name in sorted(order)]

    def ancestors(self, record_id, view=None, depth=10):
        # Sample: Edges R3 to R2 and R2 to R1. Ancestors of R3 return [R2, R1].
        return self.walk(record_id, view, depth, True)

    def descendants(self, record_id, view=None, depth=10):
        # Sample: Edges R3 to R2 and R2 to R1. Descendants of R1 return [R2, R3].
        return self.walk(record_id, view, depth, False)

    def neighbors(self, record_id, view=None, link_kind=None):
        # Sample: Edge R2 to R1 with kind PARENT. Neighbors of R1 with kind PARENT return [R2]. Neighbors of R2 return [R1].
        if self.visible(record_id, view) is None:
            raise NotFound(record_id)
        found = set()
        for source, kind, target in self.edges(view):
            if link_kind is not None and kind != link_kind:
                continue
            if source == record_id:
                found.add(target)
            if target == record_id:
                found.add(source)
        return sorted(found)

    def retire_pack(self, pack_id, version, key):
        # Sample: Retire P-1 version 1 with key K-9. Retire it again with key K-9. Check one retired pack.
        self.retired.add((pack_id, version))
        self.keys[key] = 0
