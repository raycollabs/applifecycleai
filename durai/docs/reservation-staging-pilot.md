# Named-seat reservation staging pilot

Run `python tools/run_reservation_staging.py` from the Durai directory in the staging worktree. Use local Docker and the locked PostgreSQL image. The controller resolves the local Redis 7.4 image once and runs that image ID. It creates a fresh PostgreSQL database, a Redis Streams broker, and a loopback HTTP service with a private token.

The controller applies the idempotency, events, reservations, and pilot projection schemas. It backs up the baseline first. It loads synthetic seat IDs in one tenant. The HTTP service calls the named-seat blueprint through the tenant login.

The relay sends its event to Redis. It settles the PostgreSQL outbox claim after Redis returns the stream entry ID. Redis uses the append-only file and fsync before replying. The consumer commits its PostgreSQL inbox receipt and effect. It then acknowledges the stream entry. To exercise recovery, the controller exits the relay after the Redis write and exits the consumer after the PostgreSQL commit. Another process resumes the outbox claim or reclaims the pending stream entry. [Redis append-only persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/) and [Redis pending entry recovery](https://redis.io/docs/latest/commands/xautoclaim/).

The load sample sends 64 holds and 64 terminal commands across eight clients. API latency includes HTTP and PostgreSQL command time. The separate drain measurement includes Redis CLI process overhead. The physical standby receives and replays the workload. The controller compares selected order, outbox, and consumer rows after replay.

The completed backup is restored to a fresh database and compared by table count and digest. A second restore uses the pre-change archive and checks the earlier schema and marker. This rehearsal uses the earlier archive as a rollback target. A later deployment needs its own traffic, write-loss, and rollback approval rules.

The controller stores both archives privately and saves their digests in the receipt. It removes its disposable services after the run. The local sample uses one host, a single Redis broker, and synthetic traffic. Qualify the intended staging topology, access controls, peak workload, and broker failover before deciding on a production rollout.
