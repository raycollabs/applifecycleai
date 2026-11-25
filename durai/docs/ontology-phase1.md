# Domain profiles and agent guidance

## Domain selection

Select one profile in the domain specification.

| Profile | Required business declarations |
|---|---|
| trading-position | Shares, signed lots, and REMOVE_LOTS correction. |
| transaction | Units, identity fields, invariants, and lifecycle. |
| finance | Transaction declarations, currency, money representation, and scale. |
| retail-order | Transaction declarations and inventory policy. |
| reservation | Transaction declarations, hold expiry, and capacity policy. |

A lifecycle declares states, an initial state, terminal states, and permitted transitions.
Missing business rules return NEEDS_CLARIFICATION. The validator does not choose an inventory or capacity policy.
The historical untagged signed-lot specification still selects trading-position.
The position renderer accepts only that profile. Other profiles use the software task path and their declared acceptance suite.

Task contracts retain the explicit domain. Worker prompts receive its public business declarations.
Worker contracts use format durai-worker-contract/v2. A contract includes the canonical profile and selected policy view digest.
Earlier in-flight contract pins need a fresh contract before dispatch.

## Typed patterns

The pattern format is durai-pattern/v1. Each pattern contains:

- Identity, rule, and explicit audiences.
- Applicable profiles and strength: REQUIRED, DEFAULT, or REFERENCE.
- Invariants, procedures, examples, checks, and evidence.
- Source record identity, digest, profile, and review state.

The exchange catalog converter recognizes the existing exchange-pack-adapter/3 layout.
It produces 30 scoped reference patterns. It preserves the original records and their source digests.
Procedure steps and public test guidance reach the worker with their record references.
Source memory and arbitrary body.text fields are not promoted into policy.
The converter does not invent missing examples or invariants.

normalized_application_pack constructs a new APPLICATION version with stored typed records.
Its identity includes the source and normalized content digests. It retains the original records.
Construction is separate from installation and binding. No primary ontology install is part of this qualification.

## Views

| Audience | Selected tools | Authority |
|---|---|---|
| coding-worker | Read, edit, and write the declared workspace files. | Candidate proposal only. |
| code-reviewer | Read policy, read evidence, propose a finding. | Review data only. |
| support-reviewer | Read policy, read evidence, propose an action. | Support data only. |

ontology_delivery reads the pinned binding and canonical pack in a read-only transaction.
It checks scope, version, and digest. Worker delivery also checks the existing stored policy projection.
Generic-only worker views keep the earlier format and digest.
The approved policy_catalog and policy_store modules remain unchanged.
The execution loop now selects its view through ontology_delivery.

Typed generic and application patterns use the same profile filter.
Controller-only policies stay out of worker contracts. The backend keeps release and mutation authority.
Support evidence returns sequence, stage, and digest. It does not return private evidence documents or run context.
Tool names in a view describe the permitted adapter interface. This phase does not add a support agent executor or a new MCP server.

## Storage boundaries

Keep one GENERIC pack per binding. Keep the existing GENERIC and APPLICATION store kinds.
Reference conversion can read an earlier immutable pack without editing it.
The explicit normalized pack constructor provides a new version for later review and installation.
The legacy graph projection can use that stored normalized version after approval.

The shared primary evidence archive is selected with contextEvidenceRoot in private settings.
It keeps artifact identity independent of the code worktree. Isolated settings use their local default archive.
Recovery uses the same archive selection.

## Next phase

Phase 1 establishes domain selection and delivery.
Phase 2 adds the owner's preferred transaction infrastructure and exception rules.
Payment, ledger, reservation, and outbox implementation blueprints remain later qualification work.
