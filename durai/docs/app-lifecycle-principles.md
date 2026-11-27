# Durai App-Lifecycle & Data-Tier Principles

Durai is an **app-lifecycle factory agent**, not a single-file code generator. Every generated application and baseline reference app must follow Durai's full-lifecycle architecture from specification (`S-REV`) and schema design through deployment (`P-REV`), analytics, and reinforced learning.

---

## 1. Data-Tier & Analytics Decision Ladder

| Workload / Stage | Default Choice | When to Escalate |
|---|---|---|
| **OLTP / Transactions** | **PostgreSQL 17** | Always the default. Numbered SQL migrations (`migrations/001_*.sql`), `CHECK` constraints, `pg_advisory_xact_lock` for idempotency, `FOR UPDATE` for stock/funds invariants, and SQL-scoped tenant isolation. |
| **BI / Operational Dashboards** | **Apache Superset on PostgreSQL Read Replica** | Default for reporting and dashboards. Avoids premature ETL or dual-write complexity while isolating BI load from primary OLTP writes. |
| **High-Cardinality Real-Time OLAP** | **Apache Superset on ClickHouse** | Use **only** when a PostgreSQL read replica stops scaling for analytical queries or when the user explicitly requests ClickHouse. |
| **Multi-System Lakehouse Governance** | **Databricks (Unity Catalog + Delta + Lakebase)** | Use when centralizing governed data lakes across multiple systems via `AnalyticsSinkPort` (Delta) and `BlobPort` (Unity Catalog volumes). |

---

## 2. Application Lifecycle Stages & Gates

1. **Specification (`S-REV`):** Draft and hash-seal requirements (`design/samples/*.json`).
2. **Design & Plan (`DESIGN` → `PLAN`):** Define SQL schema, invariants, and React/API contracts before writing code.
3. **Implementation & Tests (`IMPLEMENTATION` → `TESTS`):** Domain services + SQL migrations + React 19 / Next.js 16 UI with explicit `loading`, `empty`, `error`, `ok`, and `signed-out` states and `POST`-only session mutations.
4. **Deployment & Behavioural Verification (`DEPLOYED` + `P-REV`):** Probe live HTTP 200, static web rules, and end-to-end sign-in/navigation before accepting deployment.

---

## 3. Reinforced Learning Store (PostgreSQL)

Durai persists lifecycle outcomes in PostgreSQL (`durai_core` / `AnalyticsSinkPort` / `PostgresRegistryStore`) so future builds—especially by smaller models like DeepSeek—compound rather than repeat mistakes:
- **Build Receipts & Costs:** Every run records calls, tokens, enterprise/list cost, and executed test counts (`bench_0012` refuses scores without executed checks).
- **Failure Clusters → Human-Gated Lessons:** Repeated tool/UI/SQL defects (`durai/lesson_proposals.py`) become versioned `DO NOT` rules and automated checks after human approval.
- **Pack & Ontology Versioning:** `PackRegistry` (`PostgresRegistryStore`) + `EffectiveView` keep signed policies, blueprints, and user overlays auditable across restarts.
