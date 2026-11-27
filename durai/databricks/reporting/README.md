# Databricks reporting acceptance

Use this package for the synthetic energy futures close. It uses the
factory's Delta fixture at version 0 and the shared report envelopes.
The release manifest gives the fixture hashes, report revision hashes,
workspace, warehouse, catalog, schema, identities, and file hashes.

## Stage and check

Run the commands from the Durai repository. Supply a new local directory
and an isolated Unity Catalog test schema.

```powershell
python databricks/reporting/package.py stage $package --workspace 7474651325426508 --warehouse 4e3aa3d5f38a3fb7 --catalog workspace --schema durai_reporting_energy_futures_acceptance --publisher $publisher --oil-group $oilGroup --gas-group $gasGroup
python databricks/reporting/package.py preview $package --expected-package-id $id --profile DEFAULT --receipt $previewReceipt
python databricks/reporting/package.py verify $package --expected-package-id $id --profile DEFAULT --receipt $sqlReceipt --rows $observedRows
```

Set `$id` to the package ID from the stage result. Use the workspace
email or application ID of the approved test publisher. Set the group
names to two separate account groups. Check the manifest before you
approve a release. The preview runs `databricks bundle validate --strict`
and `databricks bundle plan`. It checks the job, dashboard, and dashboard
permissions. The read-only verification checks the Delta fixture and
compares every measure in the report projection with the expected rows.

## Publication

The native Lakeflow job checks the fixture, inserts report revisions,
replays the same revisions, and checks the results. Keep one publisher
for this schema. Give only that principal write access to the revision
table. Keep the job concurrency limit at one. The revision key contains
application, business date, book, and revision. A Delta merge keeps
the first record for an exact replay and refuses different content for
the same key.

The revision table stores the original envelope with its input digest,
event cutoff, known-at cutoff, marks, and issues. The accepted view
shows the latest COMPLETE revision for each book and date. Gas
revision 2 has a missing mark. The accepted gas result stays at
revision 1 and 300 USD. Oil shows revision 2 and 320 USD.

## Cloud acceptance after approval

1. Confirm the target, the publisher, the desk groups, and five test
   identities. Use one reader and one author for each desk. Use one
   identity without a desk group. Check all inherited grants at catalog
   and schema level. Give the publisher access to the source fixture and
   test schema. Give desk users warehouse access and the desk view grants
   from `sql/test_grants.sql`. Give authors the workspace permission to
   create their own draft. Keep edit permission on the release dashboard
   with the release owner. Keep the revision table and accepted view
   private to the publisher and release operator.
2. Deploy the reviewed package. Run the report acceptance job. Save the
   job ID, run ID, task results, and reconciliation output. Check two
   accepted rows and four revision rows. Repeat the run. Check the same
   revision digest and publication timestamps. In a separate test
   package for this schema, give an existing revision key new content.
   Check that the merge fails and that the first record stays in the
   table.
3. Confirm that the published dashboard uses individual data
   permissions. For each reader and author, run
   `SELECT session_user()` and then run `sql/visible.sql` with that
   identity. Check one row with the correct desk, revision, and amount.
   Try to query the revision table and accepted view. Check that access
   is denied. Record statement IDs and results.
4. Open the published dashboard as oil, then open it as gas in a
   separate browser session. Check the table and CSV export against the
   SQL results for each identity. Check the dashboard again after both
   desks have queried it. Open it with the unscoped identity and check
   that access is denied. Remove one test reader from the desk group.
   Open the dashboard in a fresh session and check that the reader
   cannot see or export those rows. Record the dashboard ID, published
   revision, and result for each check.

## Test goals

| Class | Function | Goal |
| --- | --- | --- |
| DatabricksReportingTests | test_stage_pins_the_factory_and_report_fixture | Check the staged file and fixture hashes. |
| DatabricksReportingTests | test_revision_replay_has_one_key_and_conflict_guard | Check the merge key and replay task. |
| DatabricksReportingTests | test_latest_complete_rows_and_separate_failure_evidence | Check the accepted rows and failed revision evidence. |
| DatabricksReportingTests | test_invalid_fixture_revision_is_refused | Refuse a duplicate revision in the source fixture. |
| DatabricksReportingTests | test_dashboard_viewer_binding | Check the desk dataset and individual permissions. |
| DatabricksReportingTests | test_modified_artifact_stops_verification | Refuse an artifact that differs from the manifest. |
| DatabricksReportingTests | test_bundle_plan_scope | Check the resources in the preview. |
| DatabricksReportingTests | test_preview_checks_workspace_before_bundle_commands | Stop before preview when the profile has a different workspace. |
| DatabricksReportingTests | test_read_only_receipt_checks_the_full_projection | Check exact row values and capture the statement IDs. |

- Sample: Stage the same fixture twice. Check equal package IDs.
- Sample: Submit oil revision 2 twice. Check that the definition has one insert path.
- Sample: Add an incomplete gas revision 2. Check gas revision 1 and 300 USD.
- Sample: Give oil revision 1 twice in the fixture. Check that staging stops.
- Sample: Open the staged dashboard query. Check the desk view and seven measures.
- Sample: Add one byte to the view query. Check that verification stops.
- Sample: Add a warehouse to the preview. Check that the preview fails.
- Sample: Open a profile for workspace 1. Check that the preview stops.
- Sample: Compare two accepted rows. Check three SQL statement IDs.

## Databricks references

- [SQL tasks in Lakeflow Jobs](https://docs.databricks.com/aws/en/jobs/tasks/sql)
- [Databricks bundle resources](https://docs.databricks.com/aws/en/dev-tools/bundles/resources#dashboards)
- [Unity Catalog dynamic views](https://docs.databricks.com/aws/en/views/dynamic)
- [Dashboard sharing and data permissions](https://docs.databricks.com/aws/en/dashboards/share/share)
