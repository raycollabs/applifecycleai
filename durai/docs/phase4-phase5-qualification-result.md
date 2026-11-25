# Durai Phase 4 and Phase 5 qualification

Date: 2026-11-19.
Status: Complete for the isolated transaction reference.

## Phase 4 results

The evaluator ran 11 protected task pairs. Both versions of each task passed setup and compilation. Each shell failed its selected business check. Each trusted reference passed. The worker file list contained the public contract and source only.

The tasks cover idempotency, outbox and inbox, seat holds, inventory holds, charge lookup, capture and refunds, settlement, journal reversal, job ownership, download grants, and location access. The prior payment recovery suite and the failure-triage suite remain separate evidence. No paid model call was made.

## Phase 5 results

| Check | Observed result |
| --- | --- |
| Concurrent inventory workload | 128 commands across eight clients, with 54.12 ms latency at the 95th percentile. |
| Physical standby | Replay lag was 750336 WAL bytes while replay was paused. Lag returned to zero. All 68 selected workload rows matched. |
| Fresh logical restore | Restore took 0.565 seconds. The counts and digests matched across 13 selected tables. |
| Other operating signals | A controlled lock wait was observed. The receipt contains queue age, retries, unresolved payments, settlement differences, and expired owners. |

The test used isolated PostgreSQL, synthetic data, and simulated external adapters. These measurements apply to this test workload. A production rollout needs deployment approval, qualified adapters, and measurements for its own workload and topology.

Use the procedures in docs/phase4-phase5-operations.md and docs/transaction-operations.md.
Implementation commit 702d8da.
