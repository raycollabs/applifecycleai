-- Sample: Import a run with providerRequests of 1,000,000 input (800,000 cached) and 10,000 output at 2.00/0.10/10.00. Read estimated_cost 0.58. A run without strategy reads SINGLE.
ALTER TABLE durai_bench.run
 ADD COLUMN strategy text NOT NULL DEFAULT 'SINGLE' CHECK(strategy IN ('SINGLE','SUBAGENTS','TEAM')),
 ADD COLUMN endpoint text,
 ADD COLUMN provider_input_tokens bigint CHECK(provider_input_tokens >= 0),
 ADD COLUMN provider_cached_input_tokens bigint CHECK(provider_cached_input_tokens >= 0),
 ADD COLUMN provider_output_tokens bigint CHECK(provider_output_tokens >= 0),
 ADD COLUMN provider_reasoning_tokens bigint CHECK(provider_reasoning_tokens >= 0),
 ADD CONSTRAINT run_provider_cached_le_input CHECK(provider_cached_input_tokens IS NULL OR provider_input_tokens IS NULL OR provider_cached_input_tokens <= provider_input_tokens);

DROP VIEW durai_bench.model_scorecard;
DROP VIEW durai_bench.run_cost;

CREATE VIEW durai_bench.run_cost AS
SELECT r.run_id, r.label, r.model_profile_id, p.provider, p.model, r.started_at,
       coalesce(r.provider_input_tokens, sum(a.input_tokens)) AS input_tokens,
       coalesce(r.provider_cached_input_tokens, sum(a.cached_input_tokens)) AS cached_input_tokens,
       coalesce(r.provider_output_tokens, sum(a.output_tokens)) AS output_tokens,
       count(*) FILTER (WHERE NOT a.usage_complete) AS incomplete_attempts, pr.verification AS price_verification, pr.currency,
       round((CASE WHEN r.provider_input_tokens IS NOT NULL THEN
                (r.provider_input_tokens - coalesce(r.provider_cached_input_tokens, 0)) * pr.input_per_million
              + coalesce(r.provider_cached_input_tokens, 0) * coalesce(pr.cached_input_per_million, pr.input_per_million)
              + coalesce(r.provider_output_tokens, 0) * pr.output_per_million
              ELSE
                sum(coalesce(a.input_tokens,0) - coalesce(a.cached_input_tokens,0)) * pr.input_per_million
              + sum(coalesce(a.cached_input_tokens,0)) * coalesce(pr.cached_input_per_million, pr.input_per_million)
              + sum(coalesce(a.output_tokens,0)) * pr.output_per_million
              END
            + coalesce(ap.advisor_cost_micro, 0)) / 1000000, 6) AS estimated_cost,
       round(ap.advisor_cost_micro / 1000000, 6) AS advisor_cost
FROM durai_bench.run r
JOIN durai_bench.model_profile p ON p.id = r.model_profile_id
JOIN durai_bench.attempt a ON a.run_id = r.run_id
LEFT JOIN LATERAL (
  SELECT * FROM durai_bench.price x
  WHERE x.provider = p.provider AND x.model = p.model AND x.period IN ('standard','peak')
    AND x.effective_from <= coalesce(r.started_at, r.imported_at)
  ORDER BY x.effective_from DESC, CASE x.verification WHEN 'INVOICE' THEN 0 WHEN 'OFFICIAL' THEN 1 ELSE 2 END
  LIMIT 1) pr ON true
LEFT JOIN LATERAL (
  SELECT coalesce(r.advisor_input_tokens,0) * y.input_per_million + coalesce(r.advisor_output_tokens,0) * y.output_per_million AS advisor_cost_micro
  FROM durai_bench.model_profile q
  JOIN durai_bench.price y ON y.provider = q.provider AND y.model = q.model AND y.period IN ('standard','peak')
   AND y.effective_from <= coalesce(r.started_at, r.imported_at)
  WHERE q.id = r.advisor_profile
  ORDER BY y.effective_from DESC, CASE y.verification WHEN 'INVOICE' THEN 0 WHEN 'OFFICIAL' THEN 1 ELSE 2 END
  LIMIT 1) ap ON true
GROUP BY r.run_id, r.label, r.model_profile_id, p.provider, p.model, r.started_at, pr.verification, pr.currency,
         pr.input_per_million, pr.cached_input_per_million, pr.output_per_million, ap.advisor_cost_micro,
         r.provider_input_tokens, r.provider_cached_input_tokens, r.provider_output_tokens;

CREATE VIEW durai_bench.model_scorecard AS
SELECT r.model_profile_id, t.family, count(*) AS runs,
       round(avg((r.outcome = 'PASSED')::int), 4) AS pass_rate,
       round(avg((coalesce(r.pass_attempt, 0) = 1)::int), 4) AS first_attempt_pass_rate,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY r.model_calls) AS median_calls,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY r.model_calls) AS p90_calls,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY r.wall_seconds) AS median_wall_seconds,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY c.estimated_cost) AS median_cost,
       count(*) FILTER (WHERE c.incomplete_attempts > 0) AS incomplete_usage_runs,
       round(sum(c.estimated_cost) / nullif(sum((r.outcome = 'PASSED')::int), 0), 6) AS cost_per_pass,
       min(c.price_verification) AS price_verification,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY r.advisor_calls) AS median_advisor_calls
FROM durai_bench.run r
JOIN durai_bench.task_case t ON t.id = r.task_case_id
LEFT JOIN durai_bench.run_cost c ON c.run_id = r.run_id
GROUP BY r.model_profile_id, t.family;

CREATE VIEW durai_bench.strategy_scorecard AS
SELECT r.model_profile_id, r.strategy, t.family, count(*) AS runs,
       round(avg((r.outcome = 'PASSED')::int), 4) AS pass_rate,
       round(avg((coalesce(r.pass_attempt, 0) = 1)::int), 4) AS first_attempt_pass_rate,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY r.model_calls) AS median_calls,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY r.wall_seconds) AS median_wall_seconds,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY c.estimated_cost) AS median_cost,
       round(sum(c.estimated_cost) / nullif(sum((r.outcome = 'PASSED')::int), 0), 6) AS cost_per_pass
FROM durai_bench.run r
JOIN durai_bench.task_case t ON t.id = r.task_case_id
LEFT JOIN durai_bench.run_cost c ON c.run_id = r.run_id
GROUP BY r.model_profile_id, r.strategy, t.family;

GRANT SELECT ON ALL TABLES IN SCHEMA durai_bench TO durai_runtime, durai_evaluator, durai_reviewer;
