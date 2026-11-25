# Owner architecture defaults

## Scope

Use this catalog for transaction systems, finance, retail orders, reservations, and the legacy trading position profile.
Read the conditions before choosing infrastructure. Keep a customer constraint and record its evidence.
Each concern has one DEFAULT preference and one separate REQUIRED correctness rule.
A preferred product can change. Its required invariant remains in the agent view.
The catalog provides guidance. This phase does not qualify a runnable payment, queue, or ledger implementation.

## Defaults

| Concern | Preferred choice | Permitted alternative |
|---|---|---|
| Storage | PostgreSQL | MySQL for a documented customer constraint. |
| Concurrent transitions | Atomic conditional update | Row lock, optimistic revision, or serializable transaction for the stated invariant. |
| Retried commands | Durable database idempotency record | Provider identity with a local intent and reconciliation contract for an external effect. |
| Publication | Transactional outbox with a bounded polling relay | Outbox CDC or an authoritative append-only event store. |
| Consumption | Transactional consumer inbox | A consumer offset committed with its output in the same store. |
| Background work | Bounded PostgreSQL durable jobs for a small existing PostgreSQL service | RabbitMQ, Kafka, Amazon SQS, or a short bounded synchronous handler under the stated conditions. |
| Multi-service recovery | Temporal | AWS Step Functions or a small explicit durable state machine with recovery checks. |

Use measured load and the customer contract to select an alternative.
Preserve simple local transactions when they can complete the whole operation.
Do not add every product to each application.

## Correctness

The seven REQUIRED rules cover local atomicity, concurrent transitions, request identity, retained publication intent, duplicate consumption, durable accepted jobs, and workflow recovery.
Each rule contains explicit invariants and public failure checks.
These checks are goals for later runnable blueprints. The current suite checks catalog delivery and proposal boundaries.

The idempotency record uses an exact scoped unique key. It stores the canonical request digest separately.
A hash index alone cannot establish command identity. Reuse with a changed request must fail.
An external timeout produces an uncertain result. Resolve it with the original operation identity before a new irreversible attempt.

A broker acknowledgment can be lost. Publication and consumption must tolerate a duplicate.
Kafka delivery semantics require coordination with external output. They do not establish an arbitrary external payment effect by themselves. See [Kafka delivery semantics](https://kafka.apache.org/41/design/design/).

A compensation is a new business action. It preserves earlier history and requires the caller's authority.
It does not roll back an earlier external effect as one local database transaction.

## Candidate construction

Build a candidate with this command:

```powershell
python tools/build_architecture_defaults.py --output review-candidates/architecture-defaults-v1.json
```

The default base is the unchanged G-10 pack. Use --base-pack to supply an explicit canonical base file.
The constructor retains all 21 G-10 records, gate edges, comment rules, and control-plane procedures.
It adds 14 typed DESIGN knowledge records. The new version includes a digest of the base and catalog.
The constructor refuses a base that already contains these architecture records.
The command writes a file. It does not install a pack or change a binding.
An equal command replay preserves equal bytes. A different file at the output path is refused.

## Agent delivery

Use the Phase 1 pinned binding and agent views.
The coding contract contains full preference metadata, the paired correctness policy, and source evidence.
Code review and support review receive the same architecture guidance through their declared read interfaces.
The backend retains release and mutation authority.
Old bindings retain their earlier content and digest. A new binding can select one new GENERIC pack after review.
No extra GENERIC pack or physical DOMAIN store kind is introduced.

## Proposed choices

Use durai.architecture_choices.record_architecture_proposal with a scoped ContextStore, plan ID, canonical candidate, profile, constraints, and request key.
An empty constraints map proposes each preferred choice under its stated conditions.
A constraint names the DEFAULT policy ID and supplies selected, reason, and evidence.
The selected value must be a listed alternative. The reason must be nonempty.
Evidence must resolve to an immutable record in the same plan, or to an authorized verified artifact.
Mutable status records are refused. Unknown, foreign, stale, or unavailable references are refused.
The preference must retain its applicable REQUIRED correctness pair.

The result is an immutable context decision in durai-architecture-proposal/v1 format.
Its alternatives array holds the proposed choices by policy ID.
Each choice has DEFAULT_PROPOSED or EXCEPTION_PROPOSED state.
The sourcePackId, sourcePackVersion, and sourcePackDigest fields pin the canonical source.
Authority is PROPOSED. Execution authority is NONE.
No proposal is a human approval. Exception evidence needs human review before adoption.
The request key makes an equal replay idempotent. A changed replay is refused.

## Sources

Checked date: 2026-11-18. The product preferences are owner policy.
The sources support mechanism claims. They do not establish measured Durai production capacity.

- PostgreSQL describes [transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html) and [database constraints](https://www.postgresql.org/docs/current/ddl-constraints.html).
- AWS describes [request identity and idempotent retries](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/).
- AWS describes [retained publication intent and duplicate handling](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html).
- AWS describes [queue backlog controls](https://aws.amazon.com/builders-library/avoiding-insurmountable-queue-backlogs/).
- Temporal describes [durable workflow execution](https://docs.temporal.io/workflow-execution).
- AWS describes [saga orchestration](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/saga-orchestration.html).

Queue alternatives also have explicit delivery limits. See [PostgreSQL locking reads](https://www.postgresql.org/docs/current/sql-select.html), [RabbitMQ confirmations](https://www.rabbitmq.com/docs/confirms), and [SQS redelivery](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/standard-queues-at-least-once-delivery.html).
