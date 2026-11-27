# Superset on ClickHouse

## Start the local proof

Use an isolated Docker engine. The proof sets new local passwords and uses
ports 58123 and 58061. It creates one project and removes its data
volumes at the end. Run it from the Durai repository root.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy/reporting/clickhouse/prove.ps1 -Dashboard -EvidenceDirectory D:/plans/durai_reporting_ch_evidence_2026-11-26
```

Omit `-Dashboard` to run the database proof only. For a separate adapter
worktree, supply `-Workload`, `-ReferenceBundle`, and
`-DashboardReference` from the integrated and PostgreSQL worktrees.

Use `close bootstrap` against a fresh, dedicated ClickHouse database.
Set `DURAI_CH_HTTP`, `DURAI_CH_ADMIN_PASSWORD`,
`DURAI_CH_PUBLISHER_PASSWORD`, `DURAI_CH_OIL_PASSWORD`, and
`DURAI_CH_GAS_PASSWORD`. Use the same publisher lock file for all loads to this
target. Use a local filesystem path.

```powershell
python -m deploy.reporting.clickhouse.close bootstrap
python -m deploy.reporting.clickhouse.close load design/reporting/energy_futures_close.json --lock private/reporting-clickhouse/publisher.lock
python -m deploy.reporting.clickhouse.close dump oil --business-date 2026-11-03
python -m deploy.reporting.clickhouse.close dump gas --business-date 2026-11-03
```

## Publish revisions

Use one publisher host and one dedicated publisher credential per
target. The publisher checks the contract and stores the original
envelope as a staged record. It reads that record and verifies the
digest, grain, count, and decimal values again. It writes one immutable
row with the typed report snapshot in a synchronous insert. When an
HTTP response fails, the fixed server session prevents an overlapping
query on that connection identity. Repeat the load after the prior
query ends. The journal returns the first publication ID after a lost
response.

The curated view reads the highest published revision for each report
key. Background merges can run at any time. An incomplete report keeps
its issue codes in the journal. It does not replace the prior report.
Use a new revision to correct the input. The publisher refuses a
different digest under the same revision, including a staged revision.

Use the same input file to resume after a stop. Check the output phase
and operation ID. If the file has a different digest, find the original
envelope in the journal. Start a new revision for changed content.
The pilot limits each report to 10,000 rows and 8 MiB. Use a separate
writer design and qualify it before enabling multiple publisher hosts.

## Bind the dashboards

The rebind tool reads the shared dashboard bundle. It preserves the
chart queries and metrics. It assigns new asset IDs and sets a separate
ClickHouse connection for each desk. Desk accounts have read permission
on the applicable curated view. The views fix the application and book.
The shared Superset setup assigns dataset and dashboard access to the
same fixture users. Dashboard query data cache is disabled. The local
test stack uses a dedicated PostgreSQL database for Superset metadata.
The proof uses local Superset accounts. Single sign-on and identity
provider group mappings need a separate check. The proof reads the
report date, book, contract, currency, revision, and seven measures.

```powershell
python -m deploy.reporting.clickhouse.rebind design/reporting/superset_energy_futures_close_v1.zip private/reporting-clickhouse/dashboard.zip
```

Keep both profiles on the same fixture and query boundary. Use the
latency receipt as an initial local check. Qualify report load lag,
restart recovery, cost, and administration time before a release
choice. Use the platform release process to set the production target,
identities, and service limits.

## Test goals

| Class | Function | Goal |
| --- | --- | --- |
| ClickHouseInputTests | test_content_digest_protects_input | Check the envelope digest. |
| ClickHouseInputTests | test_decimal_never_rounds_on_publication | Refuse values outside the exact decimal range. |
| ClickHouseInputTests | test_duplicate_report_grain_is_refused | Refuse duplicate rows. |
| ClickHouseReportingTests | test_changed_bytes_conflict_after_stage | Refuse changed input for a staged key. |
| ClickHouseReportingTests | test_concurrent_replay_one_terminal_record | Publish one result for two attempts. |
| ClickHouseReportingTests | test_correction_failure_keeps_old_result_until_replay | Keep the first result until the correction is complete. |
| ClickHouseReportingTests | test_dashboard_query_uses_decimal_and_latest_revision | Match the dashboard totals to the fixture. |
| ClickHouseReportingTests | test_incomplete_close_is_evidence_only | Store the issue code with no published gas correction. |
| ClickHouseReportingTests | test_retry_after_lost_publication_ack | Return the first publication ID. |
| ClickHouseReportingTests | test_scope_and_grant_removal | Restrict database reads to the permitted app and desk. |
| ClickHouseDashboardTests | test_desk_filters_export_and_repeat | Check desk access in chart filters, repeated queries, and CSV. |
| ClickHouseDashboardTests | test_author_creates_scoped_chart_reader_is_denied | Allow scoped chart creation by a desk author. |
| ClickHouseDashboardTests | test_removed_desk_role_refuses_prior_chart_and_export | Refuse chart access after role removal. |

- Sample: Change the oil fee. Keep the old digest. Check the refusal.
- Sample: Set fees to 0.0000000000001 USD. Check the decimal range error.
- Sample: Add CLF27 twice to one revision. Check the duplicate error.
- Sample: Stage oil revision 1. Change the cutoff time. Check the conflict.
- Sample: Start two loads of oil revision 1. Check one publication ID.
- Sample: Stage oil revision 2 and stop. Check 300 USD. Repeat the load.
  Check 320 USD.
- Sample: Load the fixture. Check 320 USD for oil and 300 USD for gas.
- Sample: Load gas revision 2 with a missing mark. Check gas revision 1
  and the issue code.
- Sample: Publish oil revision 1. Lose the response. Repeat the load.
  Check the first publication ID.
- Sample: Read gas as the oil database user. Check access denied. Remove
  the oil grant. Check access denied on oil.
- Sample: Open and export oil twice. Check CLF27. Open gas. Check NGF27.
  Request oil data as the gas reader. Check access denied.
- Sample: Save a chart as the oil author. Check the new chart. Save the
  chart as the oil reader. Check access denied.
- Sample: Remove the oil role from the oil reader. Sign in again.
  Check access denied for the oil dashboard, chart, and CSV.
