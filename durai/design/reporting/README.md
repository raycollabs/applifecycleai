# Energy futures acceptance fixture

Read the `reports` array in `energy_futures_close.json`. Verify each
envelope with `durai.reporting.contract.verify_envelope` before a load.
Use the four fields of `key` as the report revision key. Keep the content
digest with that key. Return the first result on exact replay. Refuse
a different digest for the same key. Use only COMPLETE rows in the
final dataset. Keep all run statuses and their issue codes in evidence.

The report grain is business date, book, contract, and currency within
one revision. The decimal fields use exact `Decimal(38, 12)` strings.
Settlement cash can be null. Event cutoff includes the business date.
Known-at cutoff limits recorded inputs.

Use a reader and an author for each desk. Use one identity without a
desk grant. Test exports, cached results, and role removal. Record the
fixture SHA-256, job or query IDs, and dashboard revision.

## Superset reference dashboard

Use `superset_energy_futures_close_v1.zip` for both Superset profiles.
The SHA-256 is
`428c09282cc2e7dc1c2ca06ca11e254ca1b3fda8f7c597a20b81e0eab204c2c5`.
Set the connection for each desk at deployment. The archive has no
passwords. Check the report date, book filter, summary, and contract table
for each desk. Use `workload_v1.json` for the local query comparison.

Run `python tools/verify_reporting_offerings.py rows.json --identity oil-reader`
from the `durai` folder. Supply a list of report rows. Omit `--identity`
for a report export with all desks.

## Select a reporting profile

Run these commands from the `durai` project folder.

```powershell
python -m durai.reporting --source postgres
python -m durai.reporting --source postgres --source delta --transfers-allowed
python -m durai.reporting --profile clickhouse --source postgres
```

The command gives the profile and the selection reason in JSON.
Use `--transfers-allowed` when the engineer approves data transfer
between sources. The command gives exit code 2 when the engineer
must select a profile. Save the result with the deployment evidence.
