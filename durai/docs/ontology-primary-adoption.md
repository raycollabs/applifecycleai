# Reviewed ontology adoption

Date: 2026-11-18. State: PASS.

The reviewed generic version 11-038995ba2b05d1a9 and application version 2-bcec5f8c54e1 are installed in durai_local.
The foundation-exchange-v2 binding moved from revision 1 to revision 2. Its SYNTHETIC_LOCAL mode is unchanged.
The new binding digest is eca2c0b1aab182dc4d2538a1d59fa546a7b3dac61b071a589880b6bcafe9cfdc.
Coding, code review, and support views each receive 14 architecture patterns and 30 trading patterns.
The views retain their declared tools and no release authority. Foreign binding reads are refused.

All earlier pack versions and binding revisions are preserved. Other bindings are unchanged.
Generic versions 11, 12, and 13 from other work remain installed with their original pins.
Equal install and binding replay keeps revision 2.

A private database backup was created before adoption. Its archive listing passed.
The receipt retains its path, byte count, digest, installed pins, checks, and exact rollback references.
To roll back the selection, create a new revision with rollbackPins from the receipt. Keep all history.

Use the Phase 1 and Phase 2 runtime code and a fresh durai-worker-contract/v2 contract for the new binding.
This operation did not deploy runtime code into the other session's branch.
No core migration, paid model call, embedding call, or application release was made.

Receipt: receipts/ontology-primary-adoption-20261118.json.
Next: Qualify the local idempotency blueprint under plan_durai_phase3_idempotency_2026-11-18.
