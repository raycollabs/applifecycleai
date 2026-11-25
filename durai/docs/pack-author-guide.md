# Durai Pack Author Guide

This guide explains how to author, validate, sign, and test custom Durai ontology packs using the open pack format (`durai-ontology-pack/v2`).

---

## 1. Pack Structure (`durai-ontology-pack/v2`)

Every pack is a JSON document containing metadata, a list of typed ontology `records`, optional `edges`, and an optional Ed25519 `signature`:

```json
{
  "format": "durai-ontology-pack/v2",
  "id": "acme.fintech-rules",
  "version": "1.0.0",
  "kind": "APPLICATION",
  "publisher": "acme-corp",
  "tier": "COMMUNITY",
  "license_ref": "apache-2.0",
  "min_shell_version": "1.0.0",
  "valid_from": null,
  "valid_until": null,
  "records": [],
  "edges": [],
  "signature": null
}
```

### Required Rules
- **`kind`**:
  - `"GENERIC"`: Cross-domain engineering rules. Must cover all **18 cells** (`6 layers × 3 types`), retain `generation.sequence` gates, and restrict `MEMORY` records to `SCHEMA` or `SYNTHETIC_EXAMPLE`.
  - `"APPLICATION"`: Domain-specific patterns, rules, or blueprints. May include only the relevant layers and types.
- **Safety**: Packs cannot include top-level `credentials`, `commands`, `grants`, or `handlers` keys (`validate_pack` rejects them).
- **Size Limits**: At most `5,000` records and `8 MiB` canonical JSON size.

---

## 2. Authoring Records

Each record belongs to one of 6 layers (`DESIGN`, `ENTITY`, `DAO`, `SERVICE`, `UI`, `TEST`) and one of 3 types (`KNOWLEDGE`, `MEMORY`, `PROCEDURE`), and must carry a SHA-256 `digest` of its canonical `body`.

Use `durai.packs.make_record` so digests are computed identically to the validator:

```python
from durai.packs import make_record, validate_pack

rec = make_record(
    identity="acme.idempotency.rule",
    layer="SERVICE",
    kind="KNOWLEDGE",
    body={"rule": "Require an Idempotency-Key header on all POST write endpoints."}
)

pack = {
    "format": "durai-ontology-pack/v2",
    "id": "acme.fintech-rules",
    "version": "1.0.0",
    "kind": "APPLICATION",
    "publisher": "acme-corp",
    "tier": "COMMUNITY",
    "license_ref": "apache-2.0",
    "min_shell_version": "1.0.0",
    "valid_from": None,
    "valid_until": None,
    "records": [rec],
    "edges": [],
    "signature": None,
}

pack_digest = validate_pack(pack)
```

---

## 3. Signing & Community Tier Behaviour

### Unsigned / Third-Party Packs
- Unsigned packs are valid and install through `PackRegistry.install()` as **`COMMUNITY`**.
- If an unsigned pack declares `tier` as `"PAID"` or `"ENTERPRISE"`, `verify_pack()` automatically downgrades it to state `VERIFIED_COMMUNITY` with label `COMMUNITY`.

### Signing Your Own Pack (Ed25519)
Signatures are computed over `canonical([r["digest"] for r in sorted(records, key=lambda r: r["id"])])`:

```python
from durai.pack_signing import (
    TrustedKeyList,
    generate_key_pair,
    sign_pack,
    verify_pack,
)

priv_key, pub_key = generate_key_pair()
signed_pack = sign_pack(pack, priv_key, key_id="acme-key-2026")

keys = TrustedKeyList()
keys.add_key("acme-key-2026", pub_key)
result = verify_pack(signed_pack, keys)
assert result.valid and result.state == "VERIFIED"
```

---

## 4. Testing Locally with `PackRegistry` and `EffectiveView`

```python
from durai.pack_registry import PackRegistry
from durai.effective_view import EffectiveView

registry = PackRegistry(trusted_keys=keys)
view = EffectiveView(registry)

entry = registry.install(signed_pack)
assert entry["state"] == "INSTALLED"
print("Records in effective view:", len(view.list_records()))
```

For a complete end-to-end build pipeline (combining source JSON catalogs and signing with a key under `private/`), see `tools/build_pack.py` and [Pack Format v2](pack-format-v2.md).
