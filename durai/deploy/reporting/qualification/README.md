# Local Superset qualification

Use a separate Docker project for each profile. Use ports 55481 and
55482 for PostgreSQL and Superset. Use ports 58123 and 58061 for
ClickHouse and Superset. Use port 58080 for the test identity provider.
Bind all host ports to loopback. Start and seed both profiles with their
reporting instructions. Keep the generated environment files private.

## Browser and identity checks

Install Playwright Core 1.65.1. Set `DURAI_PLAYWRIGHT_MODULE` to its
module path. Set `DURAI_CHROME_PATH` to Chrome or Chromium. Run from the
`durai` folder. Put the screenshots and JSON receipts in a local folder.

```powershell
node deploy/reporting/qualification/browser.cjs $evidence $pgEnv $chEnv
python deploy/reporting/qualification/keycloak_realm.py $identityFolder
```

Use Keycloak 26.8.2. Create Docker network `durai-reporting-qual-oidc`.
Start Keycloak on that network with alias `oidc.durai.test`. Use HTTP
port 58080 and hostname `http://oidc.durai.test:58080`. Mount the generated
realm JSON in `/opt/keycloak/data/import/` and start with `--import-realm`.
Keep the Keycloak development server on the local test target.

Build two images with `oidc.Dockerfile`. Set `BASE_IMAGE` to
`durai-reporting-superset-pg:6.1.0` for PostgreSQL. Set `BASE_IMAGE` to
`durai-reporting-ch-superset:6.1.0` for ClickHouse. Use `oidc.compose.yaml`
after the profile's Compose file. Supply absolute paths for the module
and environment file. Use `/app/durai-reporting` as `DURAI_REPORTING_CONFIG_DIR`
for PostgreSQL and `/app/durai-ch-config` for ClickHouse.

Set `DURAI_REPORTING_OIDC_ISSUER` to
`http://oidc.durai.test:58080/realms/durai-reporting`. Set
`DURAI_REPORTING_OIDC_DEV=1` for this issuer. Set each client ID and its
secret from the realm file. Set `DURAI_REPORTING_SQL_LAB=1` for PostgreSQL.
Start the Superset service with `--no-build`. Wait for `/health`.

```powershell
node deploy/reporting/qualification/oidc_browser.cjs $evidence $realmJson
```

Remove `sso-gas` from the test gas group in Keycloak. Run the browser
command with `revoked` after the realm file argument. Restore the test
group membership. Use a new browser session to check role changes.

## Local workload and recovery

Use fresh stacks. The workload publishes 256 reports with 2,048 rows.
It reads eight business dates per query with one client and four clients.
The recovery check injects a constraint failure in PostgreSQL and a stop
after staging in ClickHouse. It uses the workload data.

```powershell
python -m deploy.reporting.qualification.workload $pgEnv $chEnv "$evidence/workload-receipt.json"
python -m deploy.reporting.qualification.recovery $pgEnv $chEnv "$evidence/recovery-receipt.json"
```

Before release, use the production identity provider, certificate,
hostnames, groups, and workload. Supply the expected publication lag,
query latency, availability, and infrastructure rate. Check existing
sessions after a group change under the chosen session policy.
