# Context adoption review

Target: durai_local. State: Core migrations and the three plan imports are verified in durai_local.

## Review material

- Core commit: 94b4d66.
- Optional vector adapter commit: 1e77085.
- Core migrations: core_0014.sql through core_0017.sql.
- Plan: plans/plan_durai_context_store_2026-11-18.md in the repository root.
- Protocol: docs/context-store.md in the Durai application folder.
- Read-only target check: receipts/context-adoption-preflight.json.
- Test receipts: receipts/context-verify-*.json. Keep failed receipts as history. Use the latest passing receipt for adoption.

The pre-adoption target check found no durai_context schema and no applied context migrations.
The approved core migrations 0014 through 0017 are now applied and verified.
The receipt is receipts/context-core-migration-adoption-20261118.json.
The selected plan namespaces and their evidence references are imported. Optional vector activation remains pending.
The old core_0013 KV draft is retired. Preserve its checksum where it was applied. Do not apply it to the target.

## Proposed action

1. Confirm the source commit and passing test receipt.
2. Back up durai_local before a schema change. Record the backup digest.
3. Apply the dedicated core migrations in one transaction through the existing migration runner.
4. Verify the private owner, RLS, role grants, immutable record guards, and revision checks.
5. Register the verified artifact references needed by the selected plan exports.
6. Import the context-store, failure-triage, and transaction-ontology plan namespaces.
7. Compare source and target record values and digests. Check foreign-application visibility.
8. Record adoption evidence and the next action through ContextStore.

Keep the optional vector schema and real embedding calls outside this core adoption.
They need a separate activation decision and provider-call budget.

The user's rule requires approval for durai_local changes. The implementation agent does not create a human approval record.
Human approval records use the existing trusted review principal. They do not call the release system.

## Operating limits

The current qualification uses local synthetic workloads.
The vector adapter is tested with fixture embeddings. Real model retrieval quality and production load are not qualified.
Automatic context retrieval into worker prompts is not enabled. Each attempt records its initial empty context set.
The existing human review boundary is the database review login. A new context review UI is outside this change.

If the code must be rolled back, retain the context records and their backup.
Do not delete accepted history as a code rollback step.

After context adoption, resume Phase 1 of the transaction-pattern ontology plan in its own namespace.
