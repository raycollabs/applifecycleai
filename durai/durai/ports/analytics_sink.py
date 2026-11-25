"""Sample: Append a build receipt row with key R-1 twice. Query the table. Get one row."""
from typing import Protocol, Sequence

TABLES = {
    'build_receipt': ('label', 'sample_id', 'calls', 'stop_reason', 'cost_enterprise'),
    'review_score': ('label', 'sample_id', 'reviewer', 'stage', 'score'),
    'pack_audit': ('pack_id', 'version', 'action', 'actor'),
}


class AnalyticsSinkPort(Protocol):
    def append(self, table: str, rows: Sequence[dict], key: str) -> int:
        # Sample: Append 2 rows with key R-1. Return 2. Append again with key R-1. Return 2 and write nothing. A row without a required column raises SchemaMismatch.
        ...

    def query(self, table: str, filter: dict | None = None, limit: int = 100) -> list[dict]:
        # Sample: Query build_receipt with filter {"label": "L-1"} and limit 10. Return the rows in append order.
        ...
