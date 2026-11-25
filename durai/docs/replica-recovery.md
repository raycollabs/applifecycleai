# Restore a synthetic replica package

## Purpose

Restore the four sealed files from a saved local replica. The restore API checks the expected manifest digest before it writes files. Get that digest from the trusted run receipt. Do not calculate trust from the package under test.

Use this API from the Durai Python environment:

```python
from durai.common import digest
from durai.replica_restore import restore_replica

# Set run to the replica record from the trusted five-phase receipt.
result = restore_replica(
    run['outputDirectory'],
    '<plans>/restored-replica',
    digest(run['manifest']),
)
```

The destination parent must exist. The destination must not exist. The API preserves an existing destination. It checks all source bytes before it publishes the folder. It does not execute the restored source. It does not restore database records, credentials, approvals, or volume data.

## Results

Five restore tests passed. Eleven generation and replica tests passed. The saved replica package restored four files. The restored baseline returned two shares. The restored candidate returned 100 shares after the one-lot correction. The Docker sandbox blocked an outbound connection to the test address 192.0.2.1.

The result qualifies one synthetic package and the existing local Docker boundary. Run replay, provider lifecycle, multi-service fidelity, and joint database and file recovery remain open. Use the saved receipt in receipts/replica-recovery.json.
