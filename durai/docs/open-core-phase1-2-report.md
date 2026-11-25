# Open-core Phase 1 and 2 report (agent A1, 2026-11-24)

Plan: <plans>\plan_durai_open_core_finish_2026-11-24.md. Branch: durai-a1-shell-20261124.

## Phase 1: boundary and hygiene

| Item | Result |
|---|---|
| Boundary rules | design/open-core-boundary-v1.json. Every one of the tracked files has a class. Counts: OPEN 454, PAID_PACK 319, FREE_PACK 7, PAID_FEED 1, PRIVATE 507, NEVER 4. |
| Boundary scanner | tools/boundary_scan.py, 8 tests. Reports unclassified files and hidden goal files in the public set. |
| Secret scan | tools/secret_scan.py, 6 tests. Tree and all 362 commits scanned. No real key found. The public set has 0 findings. Candidate hits were test fixtures. A private Windows path exists in one PRIVATE receipt. |
| Licence files | LICENSE (Apache-2.0, official text), NOTICE, TRADEMARKS.md, CONTRIBUTING.md (DCO), PACK_LICENSE.md. The three policy files are drafts for legal review. NOTICE names raycollabs as the holder. Confirm this. |
| Clean export | tools/export_open_tree.py, 7 tests. Writes the public files to a new folder with one fresh commit. Refuses unclassified files, hidden goals and secret matches. Pushes nothing. Free pack files go under packs/starter with their own folders. |
| Open tree self-test | tools/run_open_tests.py. In a real export: 434 tests, 0 failures, 12 errors, 258 skipped for missing data or settings. All 12 errors also fail in the full tree (5 tests need pytest, 6 read v14 task templates, 1 server test returns 503). tests/needs_packs.json lists the 29 tests that need paid data. |

Finding: about 130 tests need private DB settings and about 100 read pack data (engineering-philosophy-v7, v14, samples, transaction blueprints). Code paths load these files by name from design/. Phase 3 should load them through the installed pack, not by path.

## Phase 2: ports and adapters

| Port | Adapters | Contract tests |
|---|---|---|
| OntologyStorePort | memory, plain PostgreSQL (nodes and edges, recursive queries), Apache AGE (graph with Cypher) | 10 per adapter (AGE has 1 more: graph name check) |
| VectorPort | memory, pgvector | 6 per adapter |
| AnalyticsSinkPort | memory, PostgreSQL table | 6 per adapter |
| BlobPort | local folder | 6 |
| EventPort | memory, outbox table | 6 per adapter |
| PackPort | A2 PackRegistry | 6 (skipped here until A2 is merged; pass against A2's tree) |

Total: the contract tests pass in this tree on the memory, local folder, PostgreSQL 17 and pgvector adapters (the database ones run in disposable containers that are removed after each class). The 6 pack tests pass against A2's tree.

## Not done

- Move the existing direct AGE callers (`packs.py`, `ontology.py`, `policy_graph.py`, `application_policy_graph.py`) behind OntologyStorePort. The new AGE adapter passes the contract, but those callers use their own graphs (`durai_pack_graph`, `durai_trading_ontology`) and still call AGE directly. Moving them is a separate change with its own regression run.
- Delta, Kafka, S3 and Unity Catalog adapters (Phase 5, A3).
- A GitHub repository for the public export. Nothing was published.
- Import of the plan into the context store (needs approval for durai_local).
- Legal review of the policy files; decision on the copyright holder name.
