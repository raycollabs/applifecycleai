# Security Policy

## Supported Versions

Security fixes apply to the current `durai` open-core shell and pack validator (`durai-ontology-pack/v2` and `v1`).

## Security Architecture

1. **Cryptographic Pack & License Verification**
   - Commercial and official packs are signed with Ed25519 over the canonical list of record SHA-256 digests (`durai/pack_signing.py`).
   - Any tampered record breaks the SHA-256 body digest check and transitions the install to `FAILED` with zero active records.
   - Unsigned packs cannot claim `PAID` or `ENTERPRISE` tiers and are downgraded to `COMMUNITY`.
   - Signing keys support rotation (`RETIRED`) and immediate revocation (`REVOKED`) via `TrustedKeyList`.

2. **Execution & Sandbox Isolation**
   - Packs cannot declare execution authority (`credentials`, `commands`, `grants`, or `handlers` are rejected by `validate_pack`).
   - Generated application tests and HTTP probes run inside isolated Docker sandboxes with `--network none`, `--read-only`, and `--cap-drop ALL` (`durai/build_run_node.py`).
   - `BuildTools` enforces read-only protection on `shell-manifest.json` paths.

3. **Data & Secret Hygiene**
   - User overlays and application ontology stay local and are never uploaded.
   - Private keys, provider tokens, and local configs belong exclusively under `private/` or `set-*-key.cmd` (classified `NEVER` in `design/open-core-boundary-v1.json`).
   - Run `python tools/secret_scan.py ..` and `python tools/boundary_scan.py ..` before exporting or publishing changes.

## Reporting a Vulnerability

Do **not** open a public GitHub issue with exploit details, private keys, or customer traces.

Report security vulnerabilities privately to the repository maintainers (`raycollabs`) with:
- Affected module or pack version
- Minimal reproduction steps (using synthetic data only)
- Impact assessment

If a signing key is suspected compromised, it will be marked `REVOKED` in the trusted key list so new builds refuse affected packs while preserving existing audit records.
