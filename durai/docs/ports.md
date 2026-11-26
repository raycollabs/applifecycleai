# Durai ports (contract v1)

Status: v1 implemented 2026-11-24 (five ports with adapters, pack port aligned to PackRegistry). Owner: agent A1. Plan: <plans>\plan_durai_open_core_finish_2026-11-24.md (section 5).

A port is a small interface. An adapter is one implementation. Each port has one contract test suite. A new adapter is accepted when the suite passes on it.

Rules for every port:
- A call that changes state takes an idempotency key. The same key twice gives the same result and one change.
- A call returns a result or raises one named error. It never returns a half result.
- A state has an explicit end state. Do not infer an end from a missing row.
- Each port has one owner module. Other modules send messages through the outbox.

## 1. OntologyStorePort

Purpose: read and write ontology records and links by pack version.

| Method | Input | Output | Error |
|---|---|---|---|
| `put_records(pack_id, version, records, key)` | pack id, version, list of records | count written | `DigestMismatch`, `Conflict` |
| `get_record(record_id, view)` | record id, effective view id | record or `NotFound` | `NotFound` |
| `ancestors(record_id, view, depth)` | record id, view, max depth | ordered list of record ids | `NotFound` |
| `descendants(record_id, view, depth)` | same | ordered list | `NotFound` |
| `neighbors(record_id, view, link_kind)` | record id, view, link kind | ordered list | `NotFound` |
| `retire_pack(pack_id, version, key)` | pack id, version | none | `Conflict` |

Order rule: a list is ordered by depth, then by record id.
Adapters: `postgres_age` (Apache AGE graph; nodes label R, edges label E; implemented), `postgres_relational` (nodes and edges tables; implemented), `memory` (tests).

AGE adapter notes: the graph name needs 3 to 63 letters, digits or underscores. Pack state and idempotency keys sit in two plain tables (`age_ontology_pack`, `age_ontology_key`). An edge to a record that does not exist yet creates a stub node that stays invisible until that record is put. Traversal runs one Cypher query per level, so the order is depth, then id. Each call sets `LOAD 'age'` and a transaction-local search path; set `load_library=False` where AGE is preloaded. Needs a role that can run `CREATE EXTENSION age` once, or an existing extension.

Sample: Load 6 nodes N1 to N6 and 5 edges. Ask ancestors of N6. Both adapters return N5, N3, N1 in that order.

## 2. VectorPort

| Method | Input | Output | Error |
|---|---|---|---|
| `upsert(collection, items, key)` | collection name, list of (id, text, vector) | count | `DimensionMismatch` |
| `search(collection, query_vector, k, filter)` | vector, top k, filter | list of (id, score) ordered by score then id | `NotFound` |
| `drop(collection, key)` | name | none | none |

Adapters: `pgvector`, `lakebase_search` (later), `databricks_vector_search` (later).

Sample: Upsert 50 vectors. Search k 3. The three ids match on every adapter.

## 3. AnalyticsSinkPort

| Method | Input | Output | Error |
|---|---|---|---|
| `append(table, rows, key)` | table name, list of rows | count | `SchemaMismatch` |
| `query(table, filter, limit)` | name, filter, limit | list of rows | `NotFound` |

Tables in v1: `build_receipt`, `review_score`, `pack_audit`.
Adapters: `postgres_table`, `delta_table`.

Sample: Append a build receipt with key R-1 twice. One row exists.

## 4. BlobPort

| Method | Input | Output | Error |
|---|---|---|---|
| `put(path, bytes, key)` | path, bytes | digest | `Conflict` |
| `get(path)` | path | bytes | `NotFound` |
| `presign(path, seconds)` | path, seconds | url | `NotFound` |

The database holds the path and the digest only. The bytes live in the blob store.
Adapters: `local_folder`, `s3_compatible`, `unity_volume` (later).

Sample: Put a file with 3 bytes. Get it. The digest equals the SHA-256 of the 3 bytes.

## 5. EventPort

| Method | Input | Output | Error |
|---|---|---|---|
| `publish(topic, key, payload)` | topic, partition key, payload | event id | `Conflict` |
| `consume(topic, group, max)` | topic, group, max count | list of events | none |
| `ack(group, event_id)` | group, id | none | `NotFound` |

Delivery: at least once. Order: kept per partition key. A consumer must be idempotent.
Adapters: `outbox_table` (default), `kafka` (enterprise).
Watch for a hot key. Use the pack id or the tenant id as the key.

Sample: Publish 3 events with key P-1. Consume. The three events come in the order published.

## 6. PackPort

Owner: PackRegistry (agent A2, durai/pack_registry.py). This section follows the real API.

| Method | Input | Output | Error |
|---|---|---|---|
| `install(document, license_token=None)` | signed or unsigned pack document, optional license token | entry dict with `state`, `tier`, `label` | `Conflict` (same version, other digest) |
| `activate(pack_id, version)` | id, version | entry with state ACTIVE; the old active version becomes SUPERSEDED | error for a REVOKED pack |
| `revoke(pack_id, version=None)` | id, optional version | none; the entries move to REVOKED | none |
| `get(pack_id, version=None)` | id, optional version | entry or `None` | none |
| `check_updates(pack_id, candidate_document)` | id, candidate | candidate, or `None` when the license is expired | none |
| `is_usable_for_build(pack_id, version=None)` | id, optional version | boolean (False for REVOKED or unknown) | none |

States: STAGED, VERIFIED, VERIFIED_COMMUNITY, INSTALLED, ACTIVE, SUPERSEDED, EXPIRED, REVOKED, FAILED.
A bad digest or signature ends in FAILED. An unsigned pack with tier PAID or ENTERPRISE ends in VERIFIED_COMMUNITY with label COMMUNITY.

Sample: Install an unsigned pack that claims tier PAID. The state is VERIFIED_COMMUNITY and the label is COMMUNITY.

## 7. Contract test names

One test function for each goal. Names are final.

| Port | Test class | Function | Goal |
|---|---|---|---|
| OntologyStorePort | OntologyStoreContractTests | test_put_is_idempotent_on_key | the same key twice writes once |
| OntologyStorePort | OntologyStoreContractTests | test_ancestors_order_is_depth_then_id | both adapters give the same order |
| OntologyStorePort | OntologyStoreContractTests | test_digest_mismatch_is_refused | a changed record is refused |
| VectorPort | VectorContractTests | test_same_top_k_for_same_query | top 3 ids match |
| VectorPort | VectorContractTests | test_dimension_mismatch_is_refused | a wrong size vector is refused |
| AnalyticsSinkPort | AnalyticsSinkContractTests | test_append_is_idempotent_on_key | one row for two appends |
| BlobPort | BlobContractTests | test_digest_matches_bytes | digest equals SHA-256 |
| EventPort | EventContractTests | test_order_kept_per_key | events keep publish order for one key |
| EventPort | EventContractTests | test_unacked_event_is_redelivered | a missing ack gives a second delivery |
| PackPort | PackContractTests | test_unsigned_pack_is_community | an unsigned paid pack gets COMMUNITY |
| PackPort | PackContractTests | test_signed_pack_installs | a signed pack ends INSTALLED with its tier |
| PackPort | PackContractTests | test_install_is_idempotent_on_digest | the same pack twice gives one entry and a replay audit row |
| PackPort | PackContractTests | test_tampered_record_ends_failed | a changed record ends FAILED |
| PackPort | PackContractTests | test_revoked_pack_is_not_usable_for_a_build | a REVOKED pack is refused for builds |
| PackPort | PackContractTests | test_unknown_pack_is_not_usable_for_a_build | an unknown pack is not usable |

## 8. Module layout (planned)

One file for each port and one folder for each adapter:
`durai/ports/ontology_store.py`, `durai/ports/vector.py`, `durai/ports/analytics_sink.py`, `durai/ports/blob.py`, `durai/ports/events.py`, `durai/ports/packs.py`; `durai/adapters/<port>/<adapter>.py`; `tests/contract/test_<port>.py`.
Each file is small so each change is one small commit.

## 9. SandboxPort

Status: v1 implemented 2026-11-26. Plan: <plans>\plan_durai_compare_enhancer_and_sandbox_2026-11-26.md (Part C).

Purpose: run a program in isolation with bounded time, output and files.

| Method | Input | Output | Error |
|---|---|---|---|
| `create(spec, key)` | limits: seconds, outputBytes, memoryMiB, cpus, pids | sandbox id | `InvalidSpec` |
| `exec(id, argv, stdin, deadline)` | argv list, bytes, seconds | state SUCCEEDED or FAILED, exitCode, output, truncated, reason | `NotFound` |
| `write_file(id, path, data)` | relative path, bytes | none | `InvalidPath`, `NotFound` |
| `read_file(id, path)` | relative path | bytes | `NotFound`, `InvalidPath` |
| `apply_policy(id, policy, key)` | policy dict | none | `Unsupported` |
| `events(id)` | id | ordered events with sequence, kind, payload, digest | `NotFound` |
| `destroy(id, key)` | id | none | `NotFound` for an unknown id |

Rules:
- Create with the same key gives the same id. A second destroy returns without error.
- A deadline or an output overflow ends in FAILED with a reason. A missing process is never success.
- A policy the adapter cannot enforce raises `Unsupported`. It is never ignored. Every adapter accepts `{"network": "none"}`.
- A path that leaves the workspace raises `InvalidPath`.
- Event kinds: CREATED, FILE_WRITTEN, EXEC_STARTED, EXEC_ENDED, DESTROYED.

Adapters: `docker_runner` (container: no network, read-only root, no capabilities, user 65534, pinned image; implemented), `local_process` (tests only, no isolation; implemented), `openshell` (Part D), `e2b` (Part H).
The existing `durai/sandbox.py` `DockerRunner` is unchanged. A later commit can move it behind this port.

Sample: Create with key K-1. Run `python -I -c "print(200)"`. Get SUCCEEDED, exit 0 and output 200. Destroy twice. Both calls return.
