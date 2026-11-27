WITH typed AS (
  SELECT entity, from_json(payload,
    'STRUCT<instrument_code: STRING, trader_code: STRING, side: STRING, quantity: BIGINT, price: STRING, minimum_tick: STRING>'
  ) AS record
  FROM __FIXTURE_TABLE__ VERSION AS OF __FIXTURE_VERSION__
),
contracts AS (
  SELECT record.instrument_code AS code, try_cast(record.minimum_tick AS DECIMAL(18, 6)) AS tick
  FROM typed WHERE entity = 'contracts'
),
traders AS (
  SELECT record.trader_code AS code FROM typed WHERE entity = 'traders'
),
bookings AS (
  SELECT record.instrument_code AS contract, record.trader_code AS trader,
         record.side AS side, record.quantity AS lots,
         try_cast(record.price AS DECIMAL(18, 6)) AS price
  FROM typed WHERE entity = 'bookings'
),
report AS (
  SELECT COUNT(*) AS booking_count,
         COUNT_IF(c.code IS NULL OR t.code IS NULL OR b.side IS NULL OR b.side NOT IN ('BUY', 'SELL')
           OR b.lots IS NULL OR b.lots < 1 OR b.price IS NULL OR b.price <= 0
           OR c.tick IS NULL OR c.tick <= 0 OR b.price % nullif(c.tick, 0) <> 0) AS failed_bookings
  FROM bookings b
  LEFT JOIN contracts c ON b.contract = c.code
  LEFT JOIN traders t ON b.trader = t.code
)
SELECT booking_count, failed_bookings,
       assert_true(booking_count = __EXPECTED_BOOKINGS__ AND failed_bookings = 0,
                   'The fixture contains invalid bookings') AS guard
FROM report
