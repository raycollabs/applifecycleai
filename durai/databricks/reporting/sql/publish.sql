CREATE SCHEMA IF NOT EXISTS __SCHEMA__;

CREATE TABLE IF NOT EXISTS __LEDGER__ (
  application STRING NOT NULL,
  business_date DATE NOT NULL,
  book STRING NOT NULL,
  revision BIGINT NOT NULL,
  status STRING NOT NULL,
  content_sha256 STRING NOT NULL,
  payload STRING NOT NULL,
  published_at TIMESTAMP NOT NULL
) USING DELTA TBLPROPERTIES ('delta.isolationLevel' = 'Serializable');

MERGE INTO __LEDGER__ AS stored
USING (
  __INCOMING__
) AS incoming
ON stored.application = incoming.application
AND stored.business_date = incoming.business_date
AND stored.book = incoming.book
AND stored.revision = incoming.revision
WHEN MATCHED AND (
  stored.content_sha256 <> incoming.content_sha256
  OR stored.payload <> incoming.payload
  OR stored.status <> incoming.status
) THEN UPDATE SET stored.content_sha256 = CAST(raise_error('Report revision conflict') AS STRING)
WHEN NOT MATCHED THEN INSERT (
  application, business_date, book, revision, status, content_sha256, payload, published_at
) VALUES (
  incoming.application, incoming.business_date, incoming.book, incoming.revision,
  incoming.status, incoming.content_sha256, incoming.payload, current_timestamp()
);

CREATE VIEW IF NOT EXISTS __ACCEPTED__ AS
__ACCEPTED_SELECT__;

CREATE VIEW IF NOT EXISTS __DESK_VIEW__ AS
SELECT __COLUMNS__
FROM __ACCEPTED__
WHERE (book = 'SYN-OIL' AND is_account_group_member(__OIL_GROUP_SQL__))
   OR (book = 'SYN-GAS' AND is_account_group_member(__GAS_GROUP_SQL__));
