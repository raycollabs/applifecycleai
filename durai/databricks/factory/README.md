# Databricks factory acceptance

This package checks the energy futures fixture against a pinned Delta
version. It gives a reviewer a package ID, a fixture receipt, and a
deployment preview. The acceptance job runs the same SQL checks.

## Stage and check

Run the commands from the Durai folder. Use a new package directory and
the approved Databricks workspace profile.

```powershell
python databricks/factory/package.py stage PATH --workspace WORKSPACE_ID --warehouse WAREHOUSE_ID --catalog workspace
python databricks/factory/package.py verify PATH --expected-package-id PACKAGE_ID --profile DEFAULT --receipt FIXTURE_RECEIPT.json
python databricks/factory/package.py preview PATH --expected-package-id PACKAGE_ID --profile DEFAULT --receipt BUNDLE_RECEIPT.json
```

Read the package ID from the stage result. Keep that ID in the CI build
record. Pass the retained ID to the two checks. Store the receipts outside
the package directory.

The first SQL check verifies the row count and the digest of the ordered
fixture. The second checks contract and trader references, trade sides,
whole lots, and price ticks. The CLI records both statement IDs. The
bundle preview accepts actions for the acceptance job.

## Release review

Review the fixture and bundle receipts with the same package ID. Approve
app deployment and job deployment with the intended identities. Test
the hosted app, the database migrations, and rollback in the test
workspace. Save those results with the release review.
