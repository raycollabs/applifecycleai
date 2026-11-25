# Settlement, journal, jobs, downloads, and location search

| Operation | Public interface | Reference contract |
| --- | --- | --- |
| Settlement review | durai.settlement.reconcile_statement | Compare confirmed local operations with a complete normalized statement for one provider account. Store matches and differences under the statement ID and source digest. |
| Journal posting | durai.journal.post_journal and reverse_journal | Post balanced signed amounts in one currency. Reverse through a separate linked entry. Keep both journals. |
| Durable jobs | durai.job_progress.queue_job, status, report_progress, and cancel_job | Use the existing queue and worker lease. Send progress and cancellation through the outbox. |
| Object download | durai.object_download.download | Check the authenticated subject and object grant. Request an HTTPS signed GET for at most 300 seconds. |
| Nearby lookup | durai.location_search.nearby | Return tenant locations in a radius in meters. Limit and order results by distance and ID. |

Settlement input contains the local operation set and a complete statement for the account. Read the provider amount, fee, net, and reference through a trusted adapter. Use positive minor-unit amounts and a signed net. This reference expects a positive fee and subtracts it from the signed amount. Review mismatches before any business action. The settlement command writes a report only. Use a separate workflow to tie provider statements to bank deposits. [Stripe payout reconciliation](https://docs.stripe.com/reports/payout-reconciliation).

A journal contains 2 to 100 lines. Use positive values for debits and negative values for credits. Configure account permission and account classification in the calling service. The reference retains one reversal for each original. The database checks a zero total before it accepts a journal.

Claim, renew, and complete a job through the existing coordination service. Poll its status or subscribe to progress and cancellation through the event relay. Report progress with the current owner and generation. Cancellation prevents later progress or local completion by the previous owner. The worker must check cancellation before an external effect. Set admission limits in the calling service.

Use the authenticated subject as the subject argument. Keep a signed URL private until its expiry. A qualified signer must enforce the stated GET method and duration. A grant is checked when the service issues the link. [Amazon S3 signed URL contract](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html).

Location search uses a spherical distance and a tenant filter. The query scans the tenant locations in this reference. Apply a qualified spatial index for larger workloads. Use the same distance and units in the exact radius check.

Apply `blueprints/transactions/v1.sql` on the isolated database. Run `python tools/verify_remaining_blueprints.py` for the synthetic qualification.
