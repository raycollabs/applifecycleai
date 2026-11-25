# Databricks transaction shell spike

Cloud status. The Databricks Apps and Lakebase APIs answered in the selected
workspace. The bundle passed strict validation. Live checks need an approved
spike project and two apps.

## Prepare

1. Run python databricks/prepare.py from the Durai folder. Read the staged API
   and web apps. Keep their build and secret files outside source control.
2. Confirm that Databricks Apps is available in the workspace region and tier.
   Check that the workspace permits two apps and Lakebase project creation.
3. Select a PostgreSQL 17 Lakebase project. Record the branch, database and
   endpoint resource names. Set the bundle variables. Give the API service
   principal the database resource. Give the web app permission to use the
   API app. Store the synthetic sign-in secret in the declared secret scope.
4. Validate the bundle in strict mode. Deploy only the Lakebase project first.
   Use its one-CU endpoint. Check its 60-second idle timeout.
5. Create the scratch database and sign-in secret. Deploy the API app and then
   the web app. Stop the two apps after the live checks.

## Check Lakebase

- In a scratch database, query pg_available_extensions for age and vector.
  If AGE is available, verify a graph write and traversal under the app role.
  If it is unavailable, use the agreed plain PostgreSQL ontology adapter.
- Use empty PostgreSQL 17 databases with names that start with durai_a3_parity.
  Set DURAI_PARITY_LOCAL_DSN and DURAI_PARITY_LAKEBASE_DSN in the process.
  The remote connection must use TLS and the approved OAuth role.
- Run python databricks/migration_parity.py. Check matching columns,
  constraints, and indexes. The script rolls back both schema changes.
- Seed the sign-in actor using the approved setup identity. Confirm that the
  API connects using a new OAuth credential after the old one expires.

## Check Next.js

- Start the API and web apps. Read the API health route through app-to-app
  identity. Confirm that a call without that identity is refused.
- Submit the Next.js sign-in server action. Open Positions, Trade book,
  Prices, and Cash. Check the same trader on each page. Send a prefetch GET
  to sign-out. Check the cookie. Submit the sign-out action. Check sign-out.
- Record the Node version and the result of Next.js build and start. If
  server actions fail, record the first failing request and evaluate the
  FastAPI-served fallback with a separate sign-in design.

## Sources

- [Apps resources](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources)
- [Lakebase project bundle](https://docs.databricks.com/aws/en/oltp/projects/dabs-typical-project)
- [Database credentials](https://docs.databricks.com/aws/en/oltp/projects/tutorial-databricks-apps-autoscaling)
- [Apps deployment](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy)
