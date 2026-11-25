# Durai Pack Format v2 Specification

## 1. Overview

Durai Ontology Packs encapsulate domain knowledge, design constraints, schemas, procedures, and architectural blueprints for consumption by the Durai build engine.

The v2 format (`durai-ontology-pack/v2`) introduces cryptographic provenance, entitlement tiers, shell version pinning, validity windows, and offline license enforcement while maintaining backward compatibility with v1 packs (`durai-ontology-pack/v1`).

---

## 2. Specification Fields

A valid Durai Pack v2 document is a JSON object containing the following top-level fields:

| Field | Type | Required | Description |
|---|---|:---:|---|
| `format` | string | Yes | Must be `"durai-ontology-pack/v2"` for v2 packs, or `"durai-ontology-pack/v1"` for legacy packs. |
| `id` | string | Yes | Unique pack identifier matching regex `^[A-Za-z0-9_.-]{1,100}$` (e.g., `"durai.starter"`, `"durai.blueprints.transactions"`). |
| `version` | string | Yes | Version string matching regex `^[A-Za-z0-9_.-]{1,100}$` (e.g., `"1.0.0"` or `"15"`). |
| `kind` | string | Yes | Either `"GENERIC"` (cross-domain engineering rules/defaults) or `"APPLICATION"` (domain/application specific). |
| `publisher` | string | Yes | Identity string of the publisher or signing organization (e.g., `"durai.io"`, `"community"`). |
| `tier` | string | Yes | One of `"FREE"`, `"PAID"`, `"ENTERPRISE"`, or `"COMMUNITY"`. |
| `license_ref` | string | Yes | Identifier for the applicable license terms (e.g., `"durai-pack-license-v1"`, `"apache-2.0"`). |
| `min_shell_version` | string | Yes | Minimum Durai shell version required to load this pack (e.g., `"1.0.0"`). |
| `valid_from` | string \| null | No | ISO-8601 UTC timestamp defining when the pack becomes active (e.g., `"2026-01-01T00:00:00Z"`). Null if immediately valid. |
| `valid_until` | string \| null | No | ISO-8601 UTC timestamp after which the pack expires (e.g., `"2027-01-01T00:00:00Z"`). Null if perpetual. |
| `records` | list | Yes | List of ontology records (1 to 5,000 items). Each record must provide `id`, `layer`, `type`, `body`, and `digest`. |
| `edges` | list | No | Optional list of directed ontology relationships with `from`, `to`, and `relation`. |
| `signature` | object \| null | No | Ed25519 signature object containing `key_id`, `algorithm`, and signature `value`. Unsigned packs set this to `null`. |

---

## 3. Tier & Entitlement Model

The `tier` field defines the commercial class of the pack:
- `FREE`: Official packs provided at no charge (e.g., philosophy, starter defaults).
- `PAID`: Curated commercial packs (e.g., transaction blueprints, prompt catalog). Requires valid license token.
- `ENTERPRISE`: Advanced governance, compliance, and air-gapped packs.
- `COMMUNITY`: Unsigned or third-party open-source packs.

### Signing Enforcement Rule
Only packs signed with a trusted key in the Durai trusted key list can claim `PAID` or `ENTERPRISE`.
If an unsigned pack claims `PAID` or `ENTERPRISE`, the validator and registry downgrade its verified state to `COMMUNITY` with label `COMMUNITY`.

---

## 4. Cryptographic Signature (Ed25519)

### Signed Payload
The signature is calculated over the canonical list of record digests.

1. Sort the pack records lexicographically by their `id` property:
   `sorted_records = sorted(document["records"], key=lambda r: r["id"])`
2. Extract the SHA-256 digest of each record's body:
   `digest_list = [r["digest"] for r in sorted_records]`
3. Produce the canonical bytes of this list:
   `payload = canonical(digest_list)`
4. Sign `payload` with Ed25519 private key.

### Signature Object
```json
"signature": {
  "key_id": "durai-release-2026-1",
  "algorithm": "ed25519",
  "value": "b64_or_hex_encoded_signature"
}
```

---

## 5. Backward Compatibility with v1

Durai shell continues to parse and load `durai-ontology-pack/v1` documents.
When a v1 pack is ingested:
- `publisher` defaults to `"community"`
- `tier` defaults to `"COMMUNITY"` (or `"FREE"` for built-in generic policies)
- `license_ref` defaults to `"unspecified"`
- `min_shell_version` defaults to `"1.0.0"`
- `valid_from` and `valid_until` default to `null`
- `signature` defaults to `null`
- The 18-cell layer/type requirement of v1 is preserved for legacy generic packs.
