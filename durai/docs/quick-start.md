# Durai Quick Start

Durai is an open-core, ontology-driven software factory. The **open shell** (Apache-2.0) provides the build driver, stage gates, reviewer roles (`S-REV`, `C-REV`, `P-REV`), port interfaces, `py-fastapi-next` application shell, and pack validator. Curated domain knowledge ships in signed **ontology packs**.

---

## 1. Prerequisites

- **Python 3.11+** (with `psycopg` 3 and `cryptography` for pack verification)
- **Docker** (for isolated sandbox test/app checks and PostgreSQL 17 / pgvector containers)
- **Node.js 22+** (for Next.js 16 application shell builds and Vitest checks)

---

## 2. Verify the Open Shell & Run Contract Tests

Run the port contract tests and pack tests directly from the `tests/` folder using the in-memory adapters (no database required):

```bash
cd tests
PYTHONPATH="..;." python -m unittest \
  test_contract_packs.py \
  test_pack_signature.py \
  test_license_token.py \
  test_effective_view.py \
  test_pack_builder.py \
  test_build_tools_gates.py \
  test_shell_api.py \
  test_shell_sign_in_behavior.py \
  test_shell_databricks.py
```

To verify open-core boundary hygiene (ensuring no private files, hidden goals, or keys appear in the open tree):

```bash
python tools/boundary_scan.py ..
python tools/secret_scan.py ..
```

---

## 3. Build, Verify, and Install Ontology Packs

The free starter pack (`durai.starter-v1.0.0.json`, combining `engineering-philosophy-v15.json` and `architecture-defaults-v1.json`) installs through the same open `PackRegistry` path as paid packs:

```bash
# Build and sign the starter pack (and paid packs when present in private/)
python tools/build_pack.py
```

In Python, verify and install a pack into `PackRegistry` and read the lazy cached `EffectiveView`:

```python
import json
from pathlib import Path
from durai.pack_signing import TrustedKeyList, load_public_key_hex
from durai.pack_registry import PackRegistry
from durai.effective_view import EffectiveView

keys = TrustedKeyList()
keys.add_key("durai-test-signer-2026", load_public_key_hex(Path("packs/test_public_key.hex").read_text().strip()))

registry = PackRegistry(trusted_keys=keys)
view = EffectiveView(registry)

starter = json.loads(Path("packs/durai.starter-v1.0.0.json").read_text(encoding="utf-8"))
registry.install(starter)
print(f"Active records: {len(view.list_records())}")
```

See [Pack Format v2](pack-format-v2.md) and [Pack Update Feed Spec](pack-feed.md) for signing, key rotation, offline license tokens, and pull-feed details.

---

## 4. Seed & Run the `py-fastapi-next` App Shell

Durai ships a reference transaction app shell in `apps/shell/` (FastAPI API + Next.js 16 App Router + PostgreSQL 17 migrations) so builders write domain entities, rules, and screens instead of plumbing:

```bash
# Seed a fresh workspace from apps/shell/ with read-only shell-manifest protection
python tools/deploy_shell.py --workspace /path/to/workspace

# Run a blind build against the workspace
python tools/run_blind_build.py \
  --workspace /path/to/workspace \
  --components S-TRADE-BOOK \
  --label my-run \
  --provider deepinfra \
  --calls-per-component 50 \
  --cost-cap 5.0
```

Deploy and probe the built Next.js + FastAPI app locally:

```bash
python tools/deploy_next_build.py up --workspace /path/to/workspace --project my-app --port 54370
python tools/product_review.py --url http://127.0.0.1:54370 --output receipts/product-review-my-app.json
```

---

## 5. Databricks & Lakebase Deployment (Preview)

Databricks Asset Bundle and Lakebase parity tools live in `databricks/`:
- Bundle configuration: `databricks/databricks.yml`, `databricks/resources/apps.yml`, `databricks/resources/lakebase.yml`
- Migration parity check (PostgreSQL 17 ↔ Lakebase): `python databricks/migration_parity.py`
- Trial workspace spike checklist: [databricks/SPIKE.md](../databricks/SPIKE.md)

---

## 6. Further Reading

- [Port Interfaces & Adapters](ports.md)
- [Pack Format v2](pack-format-v2.md)
- [Pack Update Feed](pack-feed.md)
- [Rounds 1–3 Benchmark Scorecard](benchmark_rounds_1_2_3_scorecard.md)
