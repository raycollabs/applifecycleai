WITH latest AS (
  SELECT business_date, book, revision, payload
  FROM __SOURCE__
  WHERE status = 'COMPLETE'
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY application, business_date, book ORDER BY revision DESC
  ) = 1
)
SELECT latest.business_date, latest.book, item.contract, item.currency, latest.revision,
       __DECIMAL_COLUMNS__
FROM latest
LATERAL VIEW explode(from_json(get_json_object(payload, '$.rows'), '__ROW_TYPE__')) lines AS item
