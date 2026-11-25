# Durai Pack Update Feed Specification

## 1. Overview & Architectural Principles

The Durai Pack Update Feed provides a secure, version-controlled distribution mechanism for official, commercial, and community ontology packs and curated lesson feeds.

The feed is built on three core design invariants:
1. **Pull Client Only (No Server Push)**: The Durai shell initiates all feed queries via standard HTTP GET. The server never pushes updates to clients. This ensures natural client-side backpressure, air-gapped compatibility, and resilience against network partitioning.
2. **Deterministic Ordering Per Pack ID**: Update entries are sequenced monotonically per `pack_id`. Clients ingest updates strictly in ascending version/sequence order.
3. **Idempotent Consumers**: Every mutating operation in `PackRegistry` is idempotent on `(pack_id, version, digest)`. Replays are safe and audited.

---

## 2. Feed Manifest Format

### Endpoint
`GET /v1/feed`

#### Query Parameters
- `since_seq` (integer, optional): The highest sequence number previously acknowledged by the client. Default: `0`.
- `channel` (string, optional): Update channel (`"stable"`, `"beta"`, or `"nightly"`). Default: `"stable"`.
- `pack_ids` (string, optional): Comma-separated list of subscribed pack identifiers.

#### Response Schema
```json
{
  "format": "durai-pack-feed/v1",
  "channel": "stable",
  "feed_seq": 452,
  "generated_at": "2026-11-24T18:00:00Z",
  "entries": [
    {
      "seq": 451,
      "pack_id": "durai.starter",
      "version": "1.0.1",
      "kind": "GENERIC",
      "tier": "FREE",
      "digest": "88f1a481a3347d104a637764617175eb0b82fdc7855c9ace8a8af27fd0a91c5a",
      "url": "https://feed.durai.io/packs/durai.starter-v1.0.1.json",
      "size_bytes": 48210,
      "published_at": "2026-11-24T12:00:00Z",
      "min_shell_version": "1.0.0"
    },
    {
      "seq": 452,
      "pack_id": "durai.blueprints.transactions",
      "version": "1.0.1",
      "kind": "APPLICATION",
      "tier": "PAID",
      "digest": "a3f83e87115768868100fa2ad73a734ee39d2c0fe82f95e4706bfd28bc079073",
      "url": "https://feed.durai.io/packs/durai.blueprints.transactions-v1.0.1.json",
      "size_bytes": 112040,
      "published_at": "2026-11-24T14:30:00Z",
      "min_shell_version": "1.0.0"
    }
  ]
}
```

---

## 3. Client Ingestion Lifecycle

When the Durai shell queries the feed:
```
           +-----------------------------+
           | Client Polls /v1/feed       |
           +--------------+--------------+
                          |
                          v
           +-----------------------------+
           | Filter by Active Licenses   |
           +--------------+--------------+
                          |
           +--------------v--------------+
           | Download Candidate Pack     |
           +--------------+--------------+
                          |
                          v
           +-----------------------------+
           | Stage Pack (State: STAGED)  |
           +--------------+--------------+
                          |
                          v
           +-----------------------------+
           | Verify Ed25519 Signature &  |
           | SHA-256 Record Digests      |
           +--------------+--------------+
                          |
            +-------------+-------------+
            |                           |
         Valid                       Invalid
            v                           v
+-----------------------+   +-----------------------+
| Install & Activate    |   | Set State: FAILED     |
| (State: INSTALLED)    |   | Write Audit Log       |
| Invalidate View Cache |   +-----------------------+
+-----------------------+
```

1. **License Check**: If a pack is `PAID` or `ENTERPRISE`, the client checks its local offline license token. If the license is expired, the update is skipped.
2. **Download & Stage**: The pack document is downloaded into local storage and registered as `STAGED`.
3. **Cryptographic Verification**:
   - The shell verifies that the signing key is active in `TrustedKeyList`.
   - The shell verifies the Ed25519 signature over `canonical([r['digest'] for r in sorted_records])`.
   - The shell checks that each record's body matches its claimed SHA-256 digest.
4. **Activation**: On verification success, `PackRegistry.install()` records the installation, writes an audit row, and marks `EffectiveView` dirty.

---

## 4. Resilience and Disconnected Operation

- **Feed Downtime**: If the feed server is unreachable, the client logs a warning and continues executing with currently installed packs and user overlays. No builds or running applications are interrupted.
- **License Expiry**: An expired license prevents downloading new updates for paid packs. It **never deletes** existing packs or user overlays, and does not alter previously generated receipts.
- **Revocation**: If a security notice revokes a specific pack version, the feed marks that version `REVOKED`. The local registry updates the state to `REVOKED`, refusing new builds while preserving historical evidence.
