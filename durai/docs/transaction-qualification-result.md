# Transaction blueprint qualification status

Phase 3 reference implementation is complete. The capture and refund suite passed 142 tests. The full affected blueprint suite passed 148 tests.

The Phase 4 payment recovery fixture checked the deliberately incomplete shell and the trusted reference in the sandbox evaluator. The shell failed its business goal. The reference passed six protected cases. The protected suite was outside the worker input. The combined affected and failure-triage run passed 173 tests with no skips.

The first combined run found synthetic location rows from an earlier test. The fixture now uses a separate latitude and removes only its own points. Both run receipts remain available.

The Phase 5 local operating probe held 20 units with four concurrent clients. The measured elapsed time was 0.1796 seconds, with 57.47 milliseconds at the 95th percentile. This is a small isolated sample. Use representative workloads for capacity planning.

The probe observed a lock wait and recorded queue age, retry count, unresolved payments, settlement differences, and expired owners. The isolated server reported zero replicas, so replica lag was unavailable.

The backup restored to a fresh isolated database in 0.559 seconds. All row counts and digests matched across 13 selected business tables. The private archive and the evidence receipt retain the backup digest.

The protected fixture qualifies payment recovery. Each remaining pattern still needs its own protected evaluation when used to qualify generated code. Live providers, brokers, storage signers, production capacity, and replica measurements need their operating environment. The primary business schema was outside these runs.

The capture and refund work is commit 3ac335f. The rest of Phase 3 is commit 92d0dde. The local qualification tools are commit 7d70747.
