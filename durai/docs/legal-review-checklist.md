# Pre-Release Legal Review Checklist (Phase 7)

Status: **Awaiting Counsel Review**. This checklist summarizes the open legal questions across the draft policy files at the repository root before public release.

| File | Current Status | Items for Legal Review |
|---|---|---|
| `LICENSE` | Standard Apache-2.0 text | Confirm Apache-2.0 for the open shell (Decision D1) and verify third-party dependencies in `requirements.*` / `package.json` have compatible licenses. |
| `NOTICE` | Draft (`Copyright 2026 raycollabs`) | Confirm exact legal entity name for `raycollabs` and whether any third-party attribution notices must be appended. |
| `PACK_LICENSE.md` | Draft (`Status: DRAFT`) | 1. Confirm enforceability of the free starter pack non-redistribution clause ("may not sell it, or publish it as a pack that competes with the paid packs").<br>2. Confirm enforceability of the model-training restriction on paid packs.<br>3. Confirm perpetual local-use terms after license expiry ("keep using an installed pack... receive no new updates").<br>4. Add governing law, venue, and export-control / limitation-of-liability clauses. |
| `TRADEMARKS.md` | Draft | Confirm ownership and registration status of the `"Durai"` mark and logo under `raycollabs`, and permissible nominative use wording ("works with Durai"). |
| `CONTRIBUTING.md` | Draft (DCO 1.1, Decision D3) | Confirm DCO 1.1 (`Signed-off-by`) vs. CLA for external contributions. |
