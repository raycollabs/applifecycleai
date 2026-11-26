# Durai

**An ontology-driven software factory with execution and governance for creating
new applications and evolving existing systems.**

Durai brings software-generation knowledge, application meaning, integrated
context, coding agents, sandbox execution, and release governance into one
versioned system. Its design supports two equally important paths:

- **Create greenfield applications.** Start with Durai's customizable generic
  software-generation ontology. Define your own application ontology or add an
  optional domain pack, such as exchange trading, as a starting point or
  reference. Drive specifications, architecture, runnable shells, useful business
  tests, code, and documentation through the approved generation procedure.
  Evaluate the application in an isolated environment before release review.
- **Evolve existing systems.** Capture versioned code, topology, contracts, and a
  permitted data subset. Connect the application ontology and engineering policy.
  Use Durai's **production-replica harness** to assemble an isolated system replica
  and evaluate features, refactors, and rewrites against explicit business goals.

The generic and application-specific ontology store kinds both retain
**Knowledge, Memory, and Procedure** across **Design, Entity, DAO, Service, UI,
and Test**. The generic ontology defines how software is generated and governed;
application ontology defines what the software means and must accomplish.
See the [ontology distribution plan](../plans/plan_durai_two_kind_ontology_distribution_2026-11-15.md).

Durai's architectural thesis is integrated whole-system context: a monorepo for
code, docs, specs, and tests linked to a unified context database, ontology graph,
agent registry, and governance. It anticipates billion-token or larger model
windows while retaining exact, lexical, semantic, and graph retrieval. Logical
integration can span physical storage tiers and execution nodes. Model inference,
durable context, and sandbox execution have separate resource budgets.
See the [creation and evolution positioning](../plans/plan_durai_greenfield_and_evolution_positioning_2026-11-15.md)
and [production-replica architecture](../plans/plan_durai_production_replica_harness_2026-11-15.md).

**Today:** local synthetic trading repair and human-approved activation are
demonstrated. Installable ontology packs, full greenfield generation, whole-system
production replicas, integrated semantic retrieval, and billion-token operation
remain design targets to qualify.

## Local trading milestone and ontology design

Ontology distribution design: [GENERIC and APPLICATION store kinds](../plans/plan_durai_two_kind_ontology_distribution_2026-11-15.md), each retaining Knowledge, Memory, and Procedure across all six earlier SDLC layers. The [layout contract](design/ontology-store-layout-v1.json) targets a bundled, customizable generic software-generation pack and optional application packs. The existing trading catalog is retained as a design/reference candidate; full live migration and a turnkey downloadable distribution are not complete.

Durai is an opinionated software factory and execution harness with a
PostgreSQL-led control plane, application-specific business ontology, and planned
reusable engineering-policy ontology. Code, docs, specs, tests, Registry records,
and governance are linked through immutable versions and evidence. See the
[architecture revision](../plans/plan_durai_architecture_positioning_scale_context_2026-11-15.md)
for context-lake storage, co-location, scale, and failing-business-test-first gates.
Large-scale storage, replica operation, and billion-token repository context are
future profiles to qualify, not current measured capabilities.

Planned engineering policy: [deliberate design and fresh-context implementation](../plans/plan_durai_elephant_goldfish_ontology_2026-11-15.md), with a [declarative ontology design pack](design/engineering-philosophy-v1.json) and [implementation-brief template](design/templates/implementation-brief.md). The policy requires sample walkthrough comments for all owned functions and tests. It has not yet been loaded into SQL/AGE, enforced in the factory, or backfilled across the current source.

Open **http://127.0.0.1:54420**. The first local Durai DB implementation is running
on this PC. Its separate PostgreSQL/Apache AGE container uses port **54410** and
the Docker volume `durai-local-pgdata`. The existing ontology/trading pilot is
preserved.

This milestone discovers the F-101 defect: 2 lots of 100 were stored as 2 shares.
It binds the finding to the SQL snapshot, source digest, ontology rule/version,
and immutable context manifest. It rejects a repair that returns 199 shares after
a one-lot correction. A complete repair passes opening, correction, short-side,
and single-share-unit checks. The demonstration then waits for a real review.

The sample includes authored synthetic fixtures and a live OpenRouter ERNIE
repair. The first live run exhausted two attempts; the next run, with explicit
correction semantics, passed all four protected checks on its first candidate.
The generated version is `model-b34ce8c9-1-43988067333b`; the user approved it and
the local controller verified and activated it.
Model-generated repair is a separate command. The automated release test uses `TEST_ONLY`
authority in `durai_test`; it does not approve the local demonstration target.

## Run on this PC

From `D:\livebuild\q1-fintech-checkout-flow`:

```powershell
python durai\local.py deps
python durai\local.py up
python durai\local.py sandbox
python durai\local.py factory
python durai\local.py start
```

`deps` installs the hash-locked requirements into `durai/private/venv`. `up`
starts only Durai's image-pinned database and applies digest-checked migrations.
`sandbox` pins and checks the Linux candidate runner. `factory` runs discovery,
candidate refinement, protected evaluation, durable review interrupt, activation,
and verification through one PostgreSQL-checkpointed graph. It reuses the waiting
fixture run on later calls. `demo` exposes the individual deterministic steps for
diagnosis. `start` runs a hidden local server. `restart` verifies its
saved process identity before restarting it. Private credentials and logs are
excluded by Durai's own `.gitignore`.

```powershell
python durai\local.py status
python durai\local.py test
python durai\local.py juicefs
python durai\local.py verify
node durai\tools\browser-proof.cjs
```

The browser proof currently uses the existing pilot's Playwright installation
and installed Chrome. Core Python tests use `durai_test`; each business test
resets only owned tables there. Recovery tests retain new `durai_restore_*`
databases for inspection. These restored fixtures have no activation authority.
Full PostgreSQL/WAL and external-byte-volume recovery remain separate gates.

## Review and activate

Inspect the source and the protected results in the local app. The browser's
review form requires the `reviewKey` in `durai/private/local.json`, a reviewer
name, and the exact candidate digest. Keep that key private. The equivalent CLI
flow asks the human operator to type the reviewed digest:

```powershell
python durai\local.py approve
python durai\local.py rollback
```

Approval binds the digest, target precondition, and policy version. The controller
creates a distinct immutable release and deployment attempt. It verifies opening,
correction, and short-side behavior before setting the target active and resolving
the finding. Rollback restores the prior version and reopens the finding.

The user subsequently approved the live ERNIE candidate. Target verification
recorded 200 opening shares, 100 shares after correction, and -200 short shares.
The local target is ACTIVE and its finding is RESOLVED. The pre-approval receipts
remain historical evidence. See `receipts/local-activation.json` and the exported
approved source in `receipts/approved-source/`.

## Run a model-generated repair

The selected default is `baidu/ernie-5.5-flash-preview:free` on OpenRouter.
Configure your key locally with hidden input, then check readiness:

```powershell
python durai\local.py model-setup
python durai\local.py model-status
python durai\local.py repair
```

`model-setup` stores the key only in ignored `durai/private/model.json`.
`model-status` makes no network or paid requests and never displays the key.
Process environment variables override this file. On Windows, named user
environment variables are also supported, so a newly configured user key can be
read by a later command without restarting the whole app. The model must support
tool calls; catalog support is not yet a live execution qualification.

Select a model before executing this command. For a local OpenAI-compatible server:

```powershell
$env:DURAI_MODEL_BASE_URL = 'http://127.0.0.1:8080/v1'
$env:DURAI_MODEL = '<served-model-name>'
python durai\local.py repair
```

For OpenRouter, set `DURAI_MODEL` and `OPENROUTER_API_KEY` in the local process
environment. Leave `DURAI_MODEL_BASE_URL` unset or set it to
`https://openrouter.ai/api/v1`. Other remote compatible endpoints use
`DURAI_MODEL_API_KEY`. Keys are never copied into candidate containers or receipts.

The Deep Agents worker edits only a materialized candidate directory through a
virtual file backend. It has no shell execution provider or release tools. It
registers exact candidate bytes and calls the protected evaluation consumer. It
allows two attempts by default and stops before release review. A failed budget
leaves the finding open. Actual provider/model behavior is pending configuration;
the original adapter test uses a scripted test model and no network model calls.
The subsequent live ERNIE qualification is recorded in `receipts/runs/`.

The prompt enhancer also adds a compare answer format and UI theme options. See docs/compare-enhancer.md and docs/themes.md.

## Module boundaries

| Workflow | Owned module | Boundary |
|---|---|---|
| Select agent | `durai/registry.py` | Definitions and immutable versions; registration gives no release permission. |
| Inspect execution | `durai/coordination.py` | Leases, fencing, revisioned KV, separate quota pools, idempotency, and events. |
| Inspect meaning | `durai/ontology.py` | Canonical SQL rule and a fixed AGE projection; no generic Cypher endpoint. |
| Discover finding | `durai/trading.py` | Domain evidence, source mapping, snapshot, and context bindings. |
| Create workspace | `durai/files.py`, `durai/sandbox.py` | SQLite packages, native trusted fixtures, and isolated Linux candidate execution. |
| Improve candidate | `durai/coding_worker.py`, `durai/harness.py` | Deep Agents file edits and LangGraph PostgreSQL checkpoints. |
| Run the factory | `durai/factory.py` | Bounded refinement, protected verdict routing, durable review wait, and exact-candidate resume. |
| Evaluate | `durai/evaluation_consumer.py` | Local deterministic consumer adapter; the full eval service remains track X's responsibility. |
| Review / Deploy / Verify | `durai/release.py` | Separate reviewer and controller logins, release records, generation checks, verification, rollback. |
| Restore fixture | `durai/recovery.py` | Joint SQL/source package, exact-byte comparison, and regenerated derived graph. |
| Replace provider | `durai/providers.py` | Route new work to a replacement; retain pinned work until it drains. |
| Inspect trading sample | `durai/server.py`, `apps/trading-demo/web/` | Loopback HTTP, same-origin mutations, reviewer session, business workflow UI. |

Use the published [v1 contract](contracts/platform-v1.json). Each mutable database
transition has one owner. Event delivery is at least once, ordered by per-run
sequence, with event IDs for replay deduplication. There is one PostgreSQL write
authority. `CompositeTask` computes lazily and invalidates ancestors under a
shared state lock. Small modules keep changes reviewable without a service fleet.

SQLite packages are portable artifacts, not a second live authority. JuiceFS uses
a dedicated `durai_juicefs` metadata database and the separate
`durai-juicefs-bytes` Docker volume. Its basic two-client FUSE proof passed. Git,
crash consistency, byte-volume backup, retention, and HA are not qualified by that
proof. Linux support is supplied by Docker Desktop; no interactive WSL session
is required for the executed milestone.

## Evidence and remaining work

- [Environment and dependency identities](receipts/environment.json)
- [Per-goal test results](receipts/tests.json)
- [Final milestone verification](receipts/final.json)
- [F-101 Docker-executed demonstration](receipts/local-demo.json)
- [Linux sandbox boundary](receipts/sandbox.json)
- [JuiceFS basic contract](receipts/juicefs.json)
- [Browser checks](receipts/browser.json)
- [Workspace source comparison](receipts/workspace-source-comparison.json)
- [Dependency licenses](receipts/dependencies.json)

Remaining work includes broader model-provider qualification, concurrent factory
ownership and fault qualification, the shared track-X context/eval/SDK/Console integrations,
full Registry HTTP/MCP discovery, production authentication, fault-injected
recovery, and operational qualification. This local milestone does not complete
baseline Phases 0–6 or represent a reviewed production release.

Primary references used for adapter boundaries:
[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence),
[Deep Agents backends](https://docs.langchain.com/oss/python/deepagents/backends),
[JuiceFS Docker clients](https://juicefs.com/docs/community/juicefs_on_docker/),
[JuiceFS PostgreSQL metadata](https://juicefs.com/docs/community/databases_for_metadata/).
