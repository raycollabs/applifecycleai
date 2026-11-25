"""Sample: Make MemoryAnalyticsSink. Append a build receipt. Query it. Get the row."""
from ...ports.analytics_sink import TABLES
from ...ports.errors import NotFound, SchemaMismatch


class MemoryAnalyticsSink:
    def __init__(self):
        # Sample: Start with empty tables and no applied key.
        self.rows = {name: [] for name in TABLES}
        self.keys = {}

    def append(self, table, rows, key):
        # Sample: Append 2 rows with key R-1. Return 2. Append again with key R-1. Return 2 and add nothing. A row without a required column adds nothing and raises SchemaMismatch.
        if table not in TABLES:
            raise SchemaMismatch('Unknown table ' + str(table))
        if key in self.keys:
            return self.keys[key]
        for row in rows:
            missing = [name for name in TABLES[table] if name not in row]
            if missing:
                raise SchemaMismatch('Missing ' + ', '.join(missing))
        self.rows[table].extend(dict(row) for row in rows)
        self.keys[key] = len(rows)
        return len(rows)

    def query(self, table, filter=None, limit=100):
        # Sample: Query build_receipt with filter {"label": "L-1"}. Return only rows with label L-1, in append order, at most limit.
        if table not in TABLES:
            raise NotFound(table)
        wanted = filter or {}
        return [dict(row) for row in self.rows[table] if all(row.get(k) == v for k, v in wanted.items())][:limit]
