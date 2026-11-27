# PostgreSQL reporting with Superset

## Start the local stack

Run the commands from `durai/`. Use Python with psycopg 3 and requests for the proof tests. Install Docker Compose v2. Generate a new `.env` file for this stack. Keep the file in the local workspace.

```powershell
python deploy/reporting/postgres/local_secrets.py deploy/reporting/postgres/.env
docker compose -f deploy/reporting/postgres/compose.yaml up -d --build --wait
```

PostgreSQL listens on `127.0.0.1:55451`. Superset listens on `http://127.0.0.1:55452`. The metadata database is on the Compose network. To change a host port, add `REPORTING_PORT` or `SUPERSET_PORT` to `.env` before startup. Keep the two service databases and their accounts separate.

## Publish and read the fixture

Use `report_publisher` to publish. Use `report_oil` or `report_gas` to read the related desk. Set `REPORTING_DSN` in the process environment for each operation. Use the related local password from `.env`.

```powershell
python -m deploy.reporting.postgres.publish publish design/reporting/energy_futures_close.json
python -m deploy.reporting.postgres.publish dump oil
python -m deploy.reporting.postgres.publish dump gas
```

Repeat the load. Check that each original result is the same. The database commits a complete revision and its audit record in one transaction. The views read the highest complete revision. The audit table keeps inactive revisions, incomplete results, issue codes, and digests. Use the two desk views in Superset to keep the source credentials read-only.

Open `http://127.0.0.1:55452/superset/dashboard/energy-futures-close-v1-oil/`. Sign in as `oil-reader` with `SUPERSET_TEST_PASSWORD`. Select the date `2026-11-03` and book `SYN-OIL`. Check the close summary and contract detail. Export the oil chart and check net P&L of USD 320.00. Sign in as `gas-reader` to view `energy-futures-close-v1-gas`. Check revision 1 and USD 300.00. Use the matching `oil-author` and `gas-author` users for authoring. The `unscoped` user has no desk access.

Export the dashboard source without passwords.

```powershell
python deploy/reporting/postgres/dashboard_bundle.py energy-futures-close.zip
```

The stack imports the reference dashboards at startup. Superset authorizes data access through dataset grants. The chart data cache is off. If you add a shared cache, test export and access again. Before a shared deployment, set up SSO, TLS, separate identity credentials, scheduled deliveries, and access review. Qualify the deployment with the same fixture.

## Run the proof

The database tests clear the two reporting tables. Use this command only with the disposable stack. Run from `tests/`. The Superset tests load the fixture after the database tests. The role removal test restores the fixture user's role after it checks access.

```powershell
$env:PYTHONPATH='..;.'
$env:DURAI_PG_PROOF='../deploy/reporting/postgres/.env'
$env:DURAI_SUPERSET_PROOF='1'
$env:DURAI_SUPERSET_REVOKE='1'
python -m unittest test_reporting_postgres test_reporting_postgres_superset -v
```

To measure the same database query as the ClickHouse proof, set `REPORTING_DSN` for `report_oil`. Run from `durai/`.

```powershell
python -m deploy.reporting.postgres.measure design/reporting/workload_v1.json
```

Use the receipt to compare the same scope, fixture, and query boundary. Run a larger workload before a production capacity decision.
