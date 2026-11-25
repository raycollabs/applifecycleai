# Transaction blueprint operating checks

Use an authorized scoped service role for business state. Use an authorized database observer for session, lock, and replica data. A support role can read evidence and propose an action. Apply the action through the service that owns the state and its approval rules.

| Signal | Evidence to read | Proposal and verification |
| --- | --- | --- |
| Outbox delay | Count unconfirmed events and read the age of the oldest event. Read its stream head and last attempt. | Review the relay or its broker. Check the original event ID and confirm the next event can proceed after the head succeeds. |
| Repeated publication | Read the attempt count, the acknowledgment, and the consumer receipt. | Review the provider timeout and lease duration. Check that a duplicate has one committed consumer effect. |
| Uncertain payment | Read the tenant, account, operation ID, request terms, and local status. Read the matching provider evidence. | Propose lookup under the original identity. If proof is absent, route the case to the authorized payment operator. Check the final local result and event after resolution. |
| Settlement difference | Read the source statement, exact operation, local amount, and currency. | Propose an investigation. Keep any correction in a separately approved journal or payment command. Verify original rows and evidence remain available. |
| Expired job owner | Read the lease deadline, job state, and generation. | Propose a fenced reclaim or cancellation. Check that an older owner cannot publish progress or complete the job. |
| Lock wait | Read the wait count, affected operation, and approved database diagnostics. | Review transaction duration and lock order. Check the wait clears and the result remains correct. |
| Replica delay | Read the replica count and replay lag. | Check replica health before directing reads there. Treat an absent replica as an unavailable measurement. |
| Restore | Read the backup digest, restore duration, and table digests. | Restore to a fresh approved database. Compare the selected tables before routing requests. |

For local qualification, first run `python tools/verify_transaction_qualification.py` with the isolated server descriptor. Then run `python tools/measure_transaction_blueprints.py` on the same isolated server. The script runs a small inventory sample, observes synthetic operating signals, and checks a fresh restore against the original database. It stores the archive privately and records table counts and digests in the evidence receipt.

The protected payment recovery task has separate public workspace files and controller tests. Its reference uses lookup for an uncertain operation. The fixture checks that the shell fails the business goal and that the reference passes. The larger suite also runs public blueprint checks and existing failure triage checks. Use separate protected tasks for each pattern when evaluating generated implementations.

The inventory sample measures one isolated workload and includes connection and transaction time. Use production topology and representative workload data for a capacity target. Collect replica and recovery measurements in that environment before a production change.
