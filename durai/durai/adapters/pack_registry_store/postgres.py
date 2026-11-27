"""Sample: Make PostgresRegistryStore with a connect function. Call ensure_schema. Install P-1. Create a second store on the same database and read P-1 back."""
import json
from typing import Any, Mapping, Optional, Sequence

from ...pack_registry import ACTIVE, EXPIRED, FAILED, INSTALLED, REVOKED, SUPERSEDED, VERIFIED_COMMUNITY, RegistryStore

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS pack_registry_pack ('
    'pack_id text NOT NULL, version text NOT NULL, digest text NOT NULL, '
    'state text NOT NULL, tier text NOT NULL, label text NOT NULL, active boolean NOT NULL, '
    'document jsonb NOT NULL, records jsonb NOT NULL, created_at text NOT NULL, '
    'PRIMARY KEY (pack_id, version))',
    'CREATE TABLE IF NOT EXISTS pack_registry_audit ('
    'seq bigserial PRIMARY KEY, audit_id text NOT NULL, action text NOT NULL, '
    'pack_id text NOT NULL, version text, digest text, state text, '
    'row_json jsonb NOT NULL, created_at text NOT NULL)',
)

ACTIVE_STATES = {ACTIVE, INSTALLED, VERIFIED_COMMUNITY}
INACTIVE_STATES = {SUPERSEDED, REVOKED, EXPIRED, FAILED}


def _decode_json(value):
    # Sample: Receive a JSON string or Python object from psycopg. Return a Python dict or list.
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _row_to_entry(row):
    # Sample: Convert one row from pack_registry_pack into a registry entry dictionary.
    return {
        'id': row[0],
        'version': row[1],
        'digest': row[2],
        'state': row[3],
        'tier': row[4],
        'label': row[5],
        'active': bool(row[6]),
        'document': _decode_json(row[7]),
        'records': _decode_json(row[8]),
        'created_at': row[9],
    }


class PostgresRegistryStore(RegistryStore):
    def __init__(self, connect):
        # Sample: Store a connect callable for PostgreSQL connections. Open no connection yet.
        self.connect = connect

    def ensure_schema(self) -> None:
        # Sample: Create pack_registry_pack and pack_registry_audit tables. Run twice with no error.
        with self.connect() as conn:
            for statement in SCHEMA:
                conn.execute(statement)

    def put_pack(self, entry: Mapping[str, Any]) -> None:
        # Sample: Put pack P-1 version 1 into pack_registry_pack. Read it back with get_pack.
        with self.connect() as conn:
            conn.execute(
                'INSERT INTO pack_registry_pack '
                '(pack_id, version, digest, state, tier, label, active, document, records, created_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s) '
                'ON CONFLICT (pack_id, version) DO UPDATE SET '
                'digest = EXCLUDED.digest, state = EXCLUDED.state, tier = EXCLUDED.tier, '
                'label = EXCLUDED.label, active = EXCLUDED.active, document = EXCLUDED.document, '
                'records = EXCLUDED.records, created_at = EXCLUDED.created_at',
                (
                    entry['id'],
                    entry['version'],
                    entry['digest'],
                    entry['state'],
                    entry.get('tier', 'COMMUNITY'),
                    entry.get('label', 'COMMUNITY'),
                    bool(entry.get('active', False)),
                    json.dumps(entry.get('document', {})),
                    json.dumps(entry.get('records', [])),
                    entry.get('created_at', ''),
                ),
            )

    def get_pack(self, pack_id: str, version: str) -> Optional[dict]:
        # Sample: Query pack P-1 version 1. Return entry dict or None when absent.
        with self.connect() as conn:
            row = conn.execute(
                'SELECT pack_id, version, digest, state, tier, label, active, document, records, created_at '
                'FROM pack_registry_pack WHERE pack_id = %s AND version = %s',
                (pack_id, version),
            ).fetchone()
            return _row_to_entry(row) if row else None

    def list_packs(self, state: Optional[str] = None) -> Sequence[dict]:
        # Sample: List packs in pack_registry_pack. Filter by state when given.
        with self.connect() as conn:
            if state is None:
                rows = conn.execute(
                    'SELECT pack_id, version, digest, state, tier, label, active, document, records, created_at '
                    'FROM pack_registry_pack ORDER BY pack_id, version'
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT pack_id, version, digest, state, tier, label, active, document, records, created_at '
                    'FROM pack_registry_pack WHERE state = %s ORDER BY pack_id, version',
                    (state,),
                ).fetchall()
            return [_row_to_entry(row) for row in rows]

    def update_pack_state(self, pack_id: str, version: str, new_state: str) -> None:
        # Sample: Update pack P-1 version 1 to REVOKED. Set active False for inactive states.
        with self.connect() as conn:
            if new_state in INACTIVE_STATES:
                conn.execute(
                    'UPDATE pack_registry_pack SET state = %s, active = false WHERE pack_id = %s AND version = %s',
                    (new_state, pack_id, version),
                )
            elif new_state in ACTIVE_STATES:
                conn.execute(
                    'UPDATE pack_registry_pack SET state = %s, active = true WHERE pack_id = %s AND version = %s',
                    (new_state, pack_id, version),
                )
            else:
                conn.execute(
                    'UPDATE pack_registry_pack SET state = %s WHERE pack_id = %s AND version = %s',
                    (new_state, pack_id, version),
                )

    def add_audit(self, audit_row: Mapping[str, Any]) -> None:
        # Sample: Insert one append-only row into pack_registry_audit. Check audit count increases by 1.
        with self.connect() as conn:
            conn.execute(
                'INSERT INTO pack_registry_audit '
                '(audit_id, action, pack_id, version, digest, state, row_json, created_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)',
                (
                    audit_row.get('id', ''),
                    audit_row.get('action', ''),
                    audit_row.get('pack_id', ''),
                    audit_row.get('version'),
                    audit_row.get('digest'),
                    audit_row.get('state'),
                    json.dumps(dict(audit_row)),
                    audit_row.get('timestamp', ''),
                ),
            )

    def list_audit(self) -> Sequence[dict]:
        # Sample: Read all rows from pack_registry_audit in insertion order.
        with self.connect() as conn:
            rows = conn.execute('SELECT row_json FROM pack_registry_audit ORDER BY seq ASC').fetchall()
            return [_decode_json(row[0]) for row in rows]
