# Phase 4 and Phase 5 transaction qualification

## Protected tasks

Read the public contract in qualification/phase4/public-contract.md. The controller reads the operation list in contracts.json. It prepares one task for each pattern. Each task can edit only its declared modules and functions.

The controller gives the worker the shell, public contract, and required public modules. It sends the protected cases and seed rows to the evaluator. Each shell or reference run uses a fresh database. The receipt records the source, suite, image, and verdict digests.

Run `python tools/verify_protected_transactions.py` with the isolated database descriptor. Add `--case events` to qualify one pattern. Check that the shell passes setup and compilation and fails the selected business check. Check that the trusted reference passes. Check the existing payment recovery and failure-triage evidence for the routing rules. Use separate authorization for paid worker runs.

## Operating checks

Run `python tools/measure_replay_qualification.py` for the physical standby check. The tool uses the internal network of a disposable server. It starts a streaming standby, pauses replay, and submits hold and completion commands. It measures the WAL byte distance from the primary to replay. It resumes replay and compares the affected stock and hold rows. It then removes the standby and its temporary access. [PostgreSQL base backup](https://www.postgresql.org/docs/current/app-pgbasebackup.html) and [PostgreSQL replication statistics](https://www.postgresql.org/docs/current/monitoring-stats.html).

Run `python tools/measure_transaction_blueprints.py` for queue age, retries, lock waits, unmatched payments, expired job owners, and backup restore. Run it after the replica probe so that the database has a stable set of test rows. Check the archive digest and the before and after row counts and digests. Follow the service and review steps in docs/transaction-operations.md.

Treat the measured latency, command rate, and replay time as local observations for the stated workload. Use approved production workloads, topology, retention rules, and provider contracts before adopting production targets. Keep the measurements and their evidence in the scoped plan context.
