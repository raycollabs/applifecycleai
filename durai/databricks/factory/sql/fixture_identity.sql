WITH observed AS (
  SELECT COUNT(*) AS row_count,
         COUNT(DISTINCT ordinal) AS unique_ordinals,
         sha2(concat_ws('\n', transform(
           sort_array(collect_list(named_struct('ordinal', ordinal, 'entity', entity, 'payload', payload))),
           item -> sha2(concat(CAST(item.ordinal AS STRING), ':', item.entity, ':', item.payload), 256)
         )), 256) AS fixture_sha256
  FROM __FIXTURE_TABLE__ VERSION AS OF __FIXTURE_VERSION__
)
SELECT row_count, fixture_sha256,
       assert_true(row_count = __EXPECTED_ROWS__ AND unique_ordinals = __EXPECTED_ROWS__
                   AND fixture_sha256 = '__EXPECTED_DIGEST__', 'The fixture differs from the release package') AS guard
FROM observed
