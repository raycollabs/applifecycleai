# Named-seat reservation staging result

Date: 2026-11-19.
Status: Local staging pilot passed.
Implementation commit 9684a6a.

The pilot used a loopback HTTP API, a fresh PostgreSQL database, and a real Redis Streams broker. Docker used the local pinned Redis image on a private network. The controller removed the disposable services after the run.

| Check | Result |
| --- | --- |
| Reservation lifecycle | One of two requests held the last seat. Hold retry retained its result. Confirm, release, expiry, and authorization checks passed. |
| Publication recovery | Redis retained the entry after its restart. The replacement relay published the same event ID. |
| Consumer recovery | The replacement consumer reclaimed the pending entry. The original and duplicate messages produced one local effect. |
| HTTP sample | 128 commands across eight clients, with 57.56 ms at the 95th percentile. |
| Outbox drain | 128 pending events before drain. The oldest was 0.676 seconds old. The queue drained in 34.594 seconds through the Redis CLI adapter. |
| Physical standby | Paused replay accumulated 1022504 bytes of lag. Replay caught up to zero lag and matched all 320 selected rows. |
| Completed restore | Restored the completed archive in 0.305 seconds. Counts and digests matched across six tables. |
| Rollback rehearsal | Restored the pre-change archive in 0.254 seconds. The original marker and the earlier schema passed. |

The workload was a local eight-client sample. The intended staging endpoint and peak traffic specification were unavailable. The run used a single Redis broker and a loopback-only token. Production review needs the intended staging network, access controls, broker failover, expected peak traffic, and a rollback policy for writes accepted after deployment.

Use the procedure in docs/reservation-staging-pilot.md.
The receipt is receipts/reservation-staging-2920d65e-6879-4643-a7e6-cfbfde1ca306.json.
