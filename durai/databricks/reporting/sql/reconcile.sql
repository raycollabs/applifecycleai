WITH incoming AS (
  __INCOMING__
), expected AS (
  __EXPECTED__
), actual AS (
  SELECT __COLUMNS__ FROM __ACCEPTED__
), rows_missing AS (
  SELECT * FROM expected EXCEPT ALL SELECT * FROM actual
), rows_extra AS (
  SELECT * FROM actual EXCEPT ALL SELECT * FROM expected
), evidence_missing AS (
  SELECT application, business_date, book, revision, status, content_sha256, payload FROM incoming
  EXCEPT ALL
  SELECT application, business_date, book, revision, status, content_sha256, payload FROM __LEDGER__
), evidence_extra AS (
  SELECT application, business_date, book, revision, status, content_sha256, payload FROM __LEDGER__
  EXCEPT ALL
  SELECT application, business_date, book, revision, status, content_sha256, payload FROM incoming
)
SELECT (SELECT count(*) FROM actual) AS visible_rows,
       (SELECT count(*) FROM __LEDGER__) AS evidence_rows,
       sha2(concat_ws(':', sort_array(collect_list(content_sha256))), 256) AS revision_digest,
       assert_true(
         (SELECT count(*) FROM rows_missing) = 0 AND (SELECT count(*) FROM rows_extra) = 0
         AND (SELECT count(*) FROM evidence_missing) = 0 AND (SELECT count(*) FROM evidence_extra) = 0,
         'Report rows or revision evidence differ from the acceptance fixture'
       ) AS guard
FROM __LEDGER__
