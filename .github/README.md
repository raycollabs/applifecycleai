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

## Ontology and current implementation

Planned ontology distribution: ship a customizable generic software-generation
pack, with optional application-specific packs such as exchange trading. Both
store kinds retain Knowledge, Memory, and Procedure across Design, Entity, DAO,
Service, UI, and Test. See the [store-kind and distribution plan](../plans/plan_durai_two_kind_ontology_distribution_2026-11-15.md).

Durai is an opinionated software factory and execution harness with a
PostgreSQL-led control plane. It connects business rules, observed defects,
versioned code/docs/context, generated repairs, protected evaluation, human
review, and verified activation.

The trading demonstration finds a lot/share conversion defect, proposes candidate
code through Deep Agents and OpenRouter, tests it in Docker, and pauses for human
approval before activation. The live ERNIE-generated candidate passed its protected
checks and was approved and verified on the local synthetic target.

See the [implementation and operator guide](../durai/README.md),
[domain contracts](../durai/contracts/platform-v1.json), and
[execution plans](../plans/).

The [architecture revision](../plans/plan_durai_architecture_positioning_scale_context_2026-11-15.md)
defines co-location, a governed context-lake target, scaling qualification, and
separate domain/engineering ontologies. Its planned engineering policy requires
specification readiness, useful failing-business-tests before a fix, and sample
walkthrough comments for every owned function and test function. Those new gates
are not yet enforced by the local prototype.

This repository currently captures the tested Windows/Docker development milestone.
Setup requires the existing custom Supabase/PostgreSQL/AGE image and local services
described in the guide. Fresh-machine image builds and fully standalone browser
test dependencies are not packaged here yet. All sample financial data is synthetic.

Runtime credentials, model keys, local databases, and dependency environments are
excluded from Git. No production-readiness or autonomous reinforcement-learning
claim is made.
