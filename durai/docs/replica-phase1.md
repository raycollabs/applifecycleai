# Qualify the synthetic replica

## Scope

Use the installed Durai Python environment. Use a reviewed SYNTHETIC_LOCAL ontology binding. This workflow copies the existing position application. It creates a runnable baseline that retains lots until share conversion is applied. This is a synthetic reference replica. It does not capture a production database or a broker.

The historical producer comparison remains available through local.py replica. This command adds schema, fixture, replay, and recovery qualification.

## Run

From the durai folder, run:

```powershell
private\venv\Scripts\python.exe tools\replica_phase1.py qualify --binding YOUR_BINDING --key YOUR_REQUEST_KEY
```

Use the same binding and key to read a saved result. Replay does not run the candidate again. A changed manifest with the same key is refused. A concurrent request is refused while the owner is active. The owner deadline is 15 minutes. After that deadline, replay records LOST and marks the unfinished engineering run FAILED. Use a new key for a new authorized attempt.

The command returns FAILED for a provider error. It returns WAITING_FOR_REVIEW after the eight sealed goals pass. It does not approve or activate a target.

## Restore

Get packageDigest and outputDirectory from the trusted result. Use the same binding ID as the scope. The destination parent must exist. The destination must not exist.

```powershell
private\venv\Scripts\python.exe tools\replica_phase1.py restore --package SAVED_PACKAGE --output NEW_FOLDER --digest TRUSTED_PACKAGE_DIGEST --scope YOUR_BINDING
```

Restore checks all six files before publication. It restores the baseline, candidate, schema, fixtures, replica manifest, and comparison. It does not execute source or restore credentials. The runner recreates the declared SQLite table and fixture commands for each test. It does not restore PostgreSQL or JuiceFS volumes.

## Provider and evidence

The provider ID is synthetic-position-fixture/v1. It returns only authored fixtures for the requested binding scope. PostgreSQL policies also restrict the binding and request records. The workflow rejects an image that differs from the pinned Docker image. Docker uses no network, a non-root user, bounded CPU and memory, and a read-only source mount.

The package binds source, schema, fixtures, ontology pack versions, provider, and image identities. Each case records expected and observed values. Evidence is append-only in the engineering store. Runtime capacity and model capacity remain separate. This workflow makes no model calls.

The local scope has one process and one SQLite schema. Multi-service topology, production credentials, production volume recovery, HA, and scale qualification remain later work.
