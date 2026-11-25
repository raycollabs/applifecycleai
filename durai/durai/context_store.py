"""Sample: Open plan P-1. Append progress/001. Read its value and digest."""
import re
from pathlib import Path
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
import psycopg
from psycopg.types.json import Jsonb
from .common import Conflict, digest
from .db import ROOT, settings, transaction
from .execution_events import known_secrets

READ_COLUMNS = 'application_id,plan_id,key,kind,ordinal,sequence,revision,value,digest,previous_digest,author,created_at'


def artifact_root():
    # Sample: Use shared archive <plans>/evidence in two worktrees. Resolve the same artifact digest.
    configured = settings().get('contextEvidenceRoot')
    path = Path(configured) if configured else ROOT / 'private/context-evidence'
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('Context evidence requires an absolute regular directory')
    return path


def clean_value(value):
    # Sample: Refuse an apiKey field. Keep a 200-share progress result unchanged.
    def sensitive(item):
        # Sample: Check a nested password field. Refuse it before writing any record.
        if isinstance(item, dict):
            return any(re.fullmatch(r'(api_?key|password|secret|access_?token|authorization|credential)', str(k), re.I) or sensitive(v) for k, v in item.items())
        if isinstance(item, list):
            return any(sensitive(v) for v in item)
        return False
    from .common import canonical
    text = canonical(value).decode()
    if (not isinstance(value, dict) or sensitive(value) or any(secret in text for secret in known_secrets()) or
            re.search(r'(?<![A-Za-z0-9])(nvapi-[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,})', text)):
        raise Conflict('Context value contains a secret or sensitive field')
    return value


class ContextStore:
    def __init__(self, database='durai_local', role='durai_runtime'):
        # Sample: Open the trading context as durai_runtime. Keep no database connection.
        self.database, self.role, self._summaries = database, role, {}
        self._active = ContextVar('context_transaction', default=None)

    @contextmanager
    def atomic(self, read_only=False):
        # Sample: Import 3 records and fail record 3. Roll back all 3 records.
        if self._active.get() is not None:
            yield self
            return
        with transaction(self.role, self.database) as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ' + (' READ ONLY' if read_only else ''))
            token = self._active.set(db)
            try:
                yield self
            finally:
                self._active.reset(token)
                self._summaries.clear()

    @contextmanager
    def _connection(self, read_only=False):
        # Sample: Read an uncommitted imported plan inside its active transaction.
        current = self._active.get()
        if current is not None:
            yield current
        else:
            with transaction(self.role, self.database) as db:
                if read_only:
                    db.execute('SET TRANSACTION READ ONLY')
                yield db

    def _call(self, statement, args):
        # Sample: Submit an invalid status revision. Return Conflict without a partial write.
        try:
            with self._connection() as db:
                return db.execute(statement, args).fetchone()
        except psycopg.errors.InsufficientPrivilege:
            raise PermissionError('Context write authority refused') from None
        except (psycopg.errors.CheckViolation, psycopg.errors.InvalidParameterValue, psycopg.errors.UniqueViolation,
                psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
            raise Conflict('Context value, revision, request, or reference differs') from None
        except psycopg.errors.NoDataFound:
            raise KeyError('Plan is unavailable in this scope') from None

    def open_plan(self, plan_id, title):
        # Sample: Open P-1 twice with title Inventory. Return the same plan identity.
        return self._call('SELECT * FROM durai_context.open_plan(%s,%s)', (plan_id, title))

    def append(self, plan_id, kind, value, request_key):
        # Sample: Append a proposed decision with request K-1 twice. Return decision/001 both times.
        return self._call('SELECT * FROM durai_context.append(%s,%s,%s,%s)',
                          (plan_id, kind, Jsonb(clean_value(value)), request_key))

    def set_status(self, plan_id, value, expected_revision, request_key):
        # Sample: Save RUNNING with revision 1. Check revision 2 and its prior digest.
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Expected revision requires a nonnegative integer')
        return self._call('SELECT * FROM durai_context.set_status(%s,%s,%s,%s)',
                          (plan_id, Jsonb(clean_value(value)), expected_revision, request_key))

    def read(self, plan_id, key=None):
        # Sample: Read progress/001 from P-1. Read all P-1 records in sequence order.
        with self._connection(read_only=True) as db:
            rows = db.execute('SELECT ' + READ_COLUMNS + ' FROM durai_context.record '
                'WHERE plan_id=%s AND (%s::text IS NULL OR key=%s) ORDER BY sequence', (plan_id, key, key)).fetchall()
        if key is not None:
            if not rows:
                raise KeyError('Context record unavailable in this scope')
            return rows[0]
        return rows

    def history(self, plan_id):
        # Sample: Read status revisions 1 and 2 for P-1. Keep both values and authors.
        with self._connection(read_only=True) as db:
            return db.execute('SELECT revision,sequence,value,digest,previous_digest,author,created_at '
                'FROM durai_context.status_revision WHERE plan_id=%s ORDER BY revision', (plan_id,)).fetchall()

    def search(self, plan_id, query, limit=10, adapter=None):
        # Sample: Search P-1 for graphid_ops. Return data without executing any result text.
        if adapter is not None:
            return adapter.search(self, plan_id, query, limit)
        if not isinstance(query, str) or not query.strip() or len(query) > 2000 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Search requires a short query and 1 to 50 results')
        with self._connection(read_only=True) as db:
            rows = db.execute('SELECT ' + READ_COLUMNS + ',ts_rank_cd(terms,plainto_tsquery(\'english\',%s)) AS score '
                'FROM durai_context.record WHERE plan_id=%s AND terms @@ plainto_tsquery(\'english\',%s) '
                'ORDER BY score DESC,sequence LIMIT %s', (query, plan_id, query, limit)).fetchall()
        return {'planId': plan_id, 'method': 'postgres-full-text/v1', 'dataOnly': True, 'results': rows}

    def search_resolved_findings(self, plan_id, query, limit=3):
        # Sample: Search plan P-1 for seat locks. Return one fixed finding and its resolution keys.
        if not isinstance(query, str) or not query.strip() or len(query) > 240 or type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('Finding search requires a short query and 1 to 10 results')
        with self._connection(read_only=True) as db:
            return db.execute("""
                WITH input AS (SELECT websearch_to_tsquery('english', %s) AS query),
                matches AS (
                SELECT DISTINCT ON (f.digest) f.plan_id, f.key, f.digest, f.value, resolved.key AS resolution_key,
                       resolved.digest AS resolution_digest, f.sequence,
                       ts_rank_cd(f.terms,input.query) AS score
                FROM input CROSS JOIN durai_context.record AS f
                JOIN LATERAL (
                    SELECT r.key, r.digest, r.value FROM durai_context.record AS r
                    WHERE r.application_id=f.application_id AND r.plan_id=f.plan_id
                      AND r.kind='resolution' AND r.value->>'findingDigest'=f.digest
                    ORDER BY r.sequence DESC LIMIT 1
                ) AS resolved ON true
                WHERE f.plan_id=%s AND f.kind='finding' AND f.terms @@ input.query
                  AND resolved.value->>'outcome'='FIXED'
                  AND jsonb_array_length(resolved.value->'evidence') > 0
                ORDER BY f.digest, f.sequence, f.key
                )
                SELECT plan_id, key, digest, value, resolution_key, resolution_digest FROM matches
                ORDER BY score DESC, sequence DESC, key
                LIMIT %s
            """, (query, plan_id, limit)).fetchall()

    def summary(self, plan_id):
        # Sample: Append one finding to P-1. Invalidate the cached count with the new plan sequence.
        with transaction(self.role, self.database) as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            plan = db.execute('SELECT application_id,next_sequence FROM durai_context.plan WHERE id=%s', (plan_id,)).fetchone()
            if not plan:
                raise KeyError('Plan is unavailable in this scope')
            token = (plan['application_id'], plan_id, plan['next_sequence'])
            if token in self._summaries:
                return dict(self._summaries[token])
            counts = db.execute('SELECT kind,count(*) AS n FROM durai_context.record WHERE plan_id=%s GROUP BY kind', (plan_id,)).fetchall()
            unresolved = db.execute("SELECT count(*) AS n FROM durai_context.record f WHERE f.plan_id=%s AND f.kind='finding' "
                "AND NOT EXISTS(SELECT 1 FROM durai_context.record r WHERE r.plan_id=f.plan_id AND r.kind='resolution' "
                "AND r.value->>'findingDigest'=f.digest)", (plan_id,)).fetchone()['n']
            state = db.execute("SELECT value FROM durai_context.record WHERE plan_id=%s AND key='status'", (plan_id,)).fetchone()
        result = {'planId': plan_id, 'sequence': token[-1], 'counts': {r['kind']: r['n'] for r in counts},
                  'openFindings': unresolved, 'status': state['value'] if state else None}
        self._summaries = {token: result}
        return dict(result)

    def verify_artifact(self, kind, identifier, expected_digest, root=ROOT):
        # Sample: Verify receipt R-1 from its exact bytes. Register its digest through the controller identity.
        root = Path(root).resolve()
        if self.role != 'durai_evaluator':
            raise PermissionError('Artifact verification requires the controller')
        if kind == 'commit':
            if not re.fullmatch(r'[a-f0-9]{40,64}', identifier):
                raise ValueError('Commit requires a complete object ID')
            result = subprocess.run(['git', 'cat-file', 'commit', identifier], cwd=root, capture_output=True, timeout=10)
            if result.returncode:
                raise KeyError('Commit is unavailable')
            content, locator = result.stdout, {'commit': identifier}
        elif kind in ('receipt', 'verdict'):
            relative = Path(identifier)
            target = (root / relative).resolve()
            if relative.is_absolute() or not target.is_relative_to(root) or target.is_symlink() or not target.is_file():
                raise ValueError('Artifact must be a regular file inside the repository')
            content, locator = target.read_bytes(), {'path': relative.as_posix()}
        else:
            raise ValueError('Unsupported repository evidence kind')
        if digest(content) != expected_digest:
            raise Conflict('Artifact digest differs')
        archive = artifact_root() / expected_digest
        archive.parent.mkdir(parents=True, exist_ok=True)
        try:
            with archive.open('xb') as output:
                output.write(content)
        except FileExistsError:
            if archive.is_symlink() or digest(archive.read_bytes()) != expected_digest:
                raise Conflict('Immutable evidence archive differs') from None
        locator['archive'] = str(archive)
        self._call('SELECT durai_context.register_reference(%s,%s,%s,%s)',
                   (kind, identifier, expected_digest, Jsonb(locator)))
        return {'kind': kind, 'id': identifier, 'digest': expected_digest}

    def resolve_artifact(self, reference):
        # Sample: Change receipt R-1 after verification. Read its original bytes by digest.
        with self._connection(read_only=True) as db:
            row = db.execute('SELECT digest FROM durai_context.reference WHERE kind=%s AND id=%s AND digest=%s',
                (reference['kind'], reference['id'], reference['digest'])).fetchone()
        if not row:
            raise KeyError('Artifact reference unavailable in this scope')
        archive = artifact_root() / row['digest']
        if archive.is_symlink() or not archive.is_file():
            raise KeyError('Verified artifact bytes are unavailable')
        content = archive.read_bytes()
        if digest(content) != row['digest']:
            raise Conflict('Verified artifact bytes differ')
        return content

    def record_delivery(self, plan_id, attempt_id, records, request_key):
        # Sample: Deliver no context records to attempt A-1. Store the empty set digest.
        selected = sorted([{'key': r['key'], 'digest': r['digest']} for r in records], key=lambda r: r['key'])
        with transaction(self.role, self.database) as db:
            checksum = db.execute('SELECT durai_context.value_digest(%s) AS digest', (Jsonb(selected),)).fetchone()['digest']
        return self.append(plan_id, 'delivery', {'attemptId': attempt_id, 'records': selected, 'setDigest': checksum}, request_key)

    def export(self, plan_id):
        # Sample: Export P-1 to a Markdown document with an exact JSON record payload.
        from .context_transfer import export_plan
        return export_plan(self, plan_id)
