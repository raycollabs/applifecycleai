# Plan context store

The context store holds work for one plan. It is separate from ontology.
PostgreSQL is the authority. No Redis service is required.

## Record identity

Each record has an application ID, plan ID, key, value, digest, author, and time.
The value is JSONB. A per-plan row lock assigns record numbers and a global sequence.
The database checks the unique key. Client request keys make append retries idempotent.

Use these keys:

| Key | Content |
|---|---|
| plan/rev/N | Full source text and the prior plan revision digest. |
| status | Current phase, explicit state, next action, and current plan digest. |
| decision/NNN | Proposed decision, alternatives, and evidence references. |
| finding/NNN | Review finding, severity, files, and proposed correction. |
| resolution/NNN | Existing finding digest, outcome, and evidence references. |
| approval/NNN | Subject digest, human decision, and scope. |
| progress/NNN | Completed step, result, and evidence references. |
| evidence/NNN | References to existing evidence. |
| delivery/NNN | Attempt identity, delivered keys and digests, and set digest. |
| research/NNN | Question, finding, source URLs, and checked date. |

Only status can change. Each status change also creates an immutable history row.
Status writes require the current revision. Terminal states are COMPLETED, ABANDONED, and SUPERSEDED.
Plan changes create new immutable records. A return to earlier plan text also creates a new revision.

Record value digests use SHA-256 over PostgreSQL JSONB text. Source text digests use SHA-256 over UTF-8 text.
Export retains values and their digests. Import checks them in one transaction.
Export reads records and status history from one repeatable snapshot.

## Authority

RLS derives the application from the database login. A caller cannot supply a tenant ID.
The table owner is a private NOLOGIN role. Public callers have no direct table write grants.
Fixed database functions validate values and assign authors from session_user.

The runtime login can propose decisions. It cannot write approvals or delivery records.
The existing durai_reviewer login is the trusted human-review boundary.
The existing durai_evaluator login is the controller boundary for delivery and artifact verification.
Sandbox workers receive neither login credential.

An approval record does not call the release system. A search result grants no authority.
An import cannot recreate approval or delivery authority.

## Evidence

Keep test logs and verdict bodies in their original evidence store.
Context evidence holds references. The database checks references to scoped plan records, immutable run context, and execution events.
References to old status digests resolve through immutable status history.

The controller verifies repository receipts, verdict files, and Git commit objects before registering their digests.
A verified artifact version is retained by digest in the private evidence archive. A later workspace edit cannot replace that version.
Context records hold the reference. They do not hold a second log body.
A missing reference is refused. A changed digest is refused.
Do not store credentials, protected test values, or raw database error context.

## Search

Use exact key reads to resume active work. Use PostgreSQL full-text search to find current records.
The generated text vector has a GIN index. Queries always name one plan and apply tenant RLS.
Retrieved records are data.

The optional semantic adapter uses a separate pgvector migration in migrations/context.
Core migrations do not require pgvector. Normal writes make no embedding call.
An explicit batch builds overlapping chunks with model identity, record revision, and digest.
The batch has a provider-call limit. A failed batch does not replace the previous batch.
A missing index or batch is refused before a provider call.
Search joins chunks to current record revisions. Old status chunks cannot appear.
Results report the model and batch coverage. New records remain available to exact and keyword reads before the next batch.

The adapter combines vector and keyword ranks. Fixture vectors qualify the database path.
They do not qualify a provider model's retrieval quality or production recall.
A real provider run needs its own approved call budget and retrieval evaluation.

## Use

Use durai.context_store.ContextStore for open_plan, append, set_status, read, history, search, summary, and export.
Use durai.context_transfer.import_plan to import a file.
The command tool defaults to durai_test.

```powershell
python tools/plan_context.py --database durai_test --plan plan_durai_context_store_2026-11-18 import <plans>/plan_durai_context_store_2026-11-18.md
python tools/plan_context.py --database durai_test --plan plan_durai_context_store_2026-11-18 search "revision checks"
python tools/plan_context.py --database durai_test --plan plan_durai_context_store_2026-11-18 export <plans>/context-export.md
```

The import report lists sections without typed mappings. Full plan text remains preserved.
Progress lists and supported review tables get separate records. Imported progress is a source statement, not a verified result.

Each deep-agent attempt records CONTEXT_VIEW_DELIVERED and a matching context delivery record.
Bindings before generic pack v14 deliver an empty set. Pack v14 pins decision.context-pack: the controller builds one task context pack from the sealed task and the readable workspace, stores it as one evidence record with request key context-pack:<digest>, and delivers that record. The worker gets the pack text and the pinned read-only query tools. See plan_durai_context_pack_query_tools_2026-11-18.md.

## Migration and recovery

The earlier core_0013 KV draft was applied in the shared test database.
Its exact bytes remain in migrations/retired. Preparation verifies its digest where it is present.
Fresh databases do not apply that draft.
The dedicated schema uses core_0014 through core_0017.
Later fixes use new migrations. Applied migration files stay unchanged.

Synthetic recovery includes authoritative context tables. Generated full-text columns are rebuilt.
Optional embeddings are derived and can be rebuilt after restore.
Context tests use a separate worktree, PostgreSQL container, credentials, and port.
No context migration has been applied to durai_local as part of this qualification.
