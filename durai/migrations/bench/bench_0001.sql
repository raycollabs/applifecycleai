-- Sample: Import run R-1 for DeepSeek Flash with 2 attempts and price 0.30 per million input tokens. Read its cost and the model scorecard.
DO $owner$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='durai_bench_owner') THEN
  CREATE ROLE durai_bench_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
 END IF;
END $owner$;
CREATE SCHEMA durai_bench AUTHORIZATION durai_bench_owner;

CREATE TABLE durai_bench.model_profile(
 id text PRIMARY KEY CHECK(id ~ '^[a-z0-9][a-z0-9._-]{1,79}$'),
 provider text NOT NULL, model text NOT NULL, endpoint_kind text NOT NULL CHECK(endpoint_kind IN ('direct','router','local')),
 role text NOT NULL DEFAULT 'execution' CHECK(role IN ('execution','planning','verification','advisor')),
 settings jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(provider, model, role, settings));

CREATE TABLE durai_bench.price(
 provider text NOT NULL, model text NOT NULL, effective_from timestamptz NOT NULL,
 period text NOT NULL DEFAULT 'standard' CHECK(period IN ('standard','peak','off-peak')),
 verification text NOT NULL CHECK(verification IN ('OFFICIAL','INVOICE','AGGREGATOR')),
 input_per_million numeric(12,6) NOT NULL CHECK(input_per_million >= 0),
 cached_input_per_million numeric(12,6) CHECK(cached_input_per_million >= 0),
 output_per_million numeric(12,6) NOT NULL CHECK(output_per_million >= 0),
 currency text NOT NULL DEFAULT 'USD' CHECK(currency ~ '^[A-Z]{3}$'),
 sources jsonb NOT NULL CHECK(jsonb_typeof(sources)='array' AND jsonb_array_length(sources) > 0),
 checked_at date NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(provider, model, effective_from, period, verification));

CREATE TABLE durai_bench.task_case(
 id text PRIMARY KEY, family text NOT NULL, case_name text NOT NULL, variant text NOT NULL, template text NOT NULL,
 UNIQUE(family, case_name, variant));

CREATE TABLE durai_bench.run(
 run_id text PRIMARY KEY, task_case_id text NOT NULL REFERENCES durai_bench.task_case,
 label text NOT NULL, model_profile_id text NOT NULL REFERENCES durai_bench.model_profile,
 binding text, pack jsonb NOT NULL DEFAULT '{}', advisor_profile text,
 outcome text NOT NULL, pass_attempt int CHECK(pass_attempt > 0), model_calls int NOT NULL CHECK(model_calls >= 0),
 stop_reason text, task_digest text, billed_cost numeric(12,6) CHECK(billed_cost >= 0), billed_currency text CHECK(billed_currency ~ '^[A-Z]{3}$'),
 retries int CHECK(retries >= 0), receipt_path text NOT NULL, receipt_digest text NOT NULL CHECK(receipt_digest ~ '^[a-f0-9]{64}$'),
 git_commit text, started_at timestamptz, finished_at timestamptz,
 wall_seconds numeric(10,3) GENERATED ALWAYS AS (EXTRACT(EPOCH FROM finished_at - started_at)) STORED,
 imported_at timestamptz NOT NULL DEFAULT clock_timestamp(), importer name NOT NULL DEFAULT session_user,
 UNIQUE(receipt_path), CHECK(finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at));
CREATE INDEX bench_run_label ON durai_bench.run(label);
CREATE INDEX bench_run_model ON durai_bench.run(model_profile_id, task_case_id);

CREATE TABLE durai_bench.attempt(
 run_id text NOT NULL REFERENCES durai_bench.run, number int NOT NULL CHECK(number > 0),
 verdict text, model_calls int, tool_calls int, read_calls int, query_calls int, edit_calls int, refused_writes int,
 calls_before_first_write int, input_tokens bigint, cached_input_tokens bigint, output_tokens bigint, reasoning_tokens bigint,
 usage_complete boolean NOT NULL DEFAULT true, pack_characters int, worker_seconds numeric(10,3), failed_goals text[] NOT NULL DEFAULT '{}',
 fault_origin text, reason_code text,
 PRIMARY KEY(run_id, number), CHECK(cached_input_tokens IS NULL OR input_tokens IS NULL OR cached_input_tokens <= input_tokens));

CREATE TABLE durai_bench.experiment(
 id text PRIMARY KEY, question text NOT NULL, plan_file text NOT NULL,
 baseline_labels text[] NOT NULL, treatment_labels text[] NOT NULL,
 family text NOT NULL, variants text[] NOT NULL DEFAULT '{contract,plain}',
 decision text, result jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE VIEW durai_bench.run_cost AS
SELECT r.run_id, r.label, r.model_profile_id, p.provider, p.model, r.started_at,
       sum(a.input_tokens) AS input_tokens, sum(a.cached_input_tokens) AS cached_input_tokens,
       sum(a.output_tokens) AS output_tokens, count(*) FILTER (WHERE NOT a.usage_complete) AS incomplete_attempts, pr.verification AS price_verification, pr.currency,
       round((sum(coalesce(a.input_tokens,0) - coalesce(a.cached_input_tokens,0)) * pr.input_per_million
            + sum(coalesce(a.cached_input_tokens,0)) * coalesce(pr.cached_input_per_million, pr.input_per_million)
            + sum(coalesce(a.output_tokens,0)) * pr.output_per_million) / 1000000, 6) AS estimated_cost
FROM durai_bench.run r
JOIN durai_bench.model_profile p ON p.id = r.model_profile_id
JOIN durai_bench.attempt a ON a.run_id = r.run_id
LEFT JOIN LATERAL (
  SELECT * FROM durai_bench.price x
  WHERE x.provider = p.provider AND x.model = p.model AND x.period IN ('standard','peak')
    AND x.effective_from <= coalesce(r.started_at, r.imported_at)
  ORDER BY x.effective_from DESC, CASE x.verification WHEN 'INVOICE' THEN 0 WHEN 'OFFICIAL' THEN 1 ELSE 2 END
  LIMIT 1) pr ON true
GROUP BY r.run_id, r.label, r.model_profile_id, p.provider, p.model, r.started_at, pr.verification, pr.currency,
         pr.input_per_million, pr.cached_input_per_million, pr.output_per_million;

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
       min(c.price_verification) AS price_verification
FROM durai_bench.run r
JOIN durai_bench.task_case t ON t.id = r.task_case_id
LEFT JOIN durai_bench.run_cost c ON c.run_id = r.run_id
GROUP BY r.model_profile_id, t.family;

CREATE VIEW durai_bench.experiment_pairs AS
SELECT e.id AS experiment_id, t.case_name, t.variant,
       bool_or(r.outcome = 'PASSED') FILTER (WHERE r.label = ANY(e.baseline_labels)) AS baseline_pass,
       bool_or(r.outcome = 'PASSED') FILTER (WHERE r.label = ANY(e.treatment_labels)) AS treatment_pass,
       max(r.model_calls) FILTER (WHERE r.label = ANY(e.baseline_labels)) AS baseline_calls,
       max(r.model_calls) FILTER (WHERE r.label = ANY(e.treatment_labels)) AS treatment_calls
FROM durai_bench.experiment e
JOIN durai_bench.task_case t ON t.family = e.family AND t.variant = ANY(e.variants)
JOIN durai_bench.run r ON r.task_case_id = t.id AND (r.label = ANY(e.baseline_labels) OR r.label = ANY(e.treatment_labels))
GROUP BY e.id, t.case_name, t.variant;

GRANT USAGE ON SCHEMA durai_bench TO durai_runtime, durai_evaluator, durai_reviewer;
GRANT SELECT ON ALL TABLES IN SCHEMA durai_bench TO durai_runtime, durai_evaluator, durai_reviewer;
GRANT INSERT ON durai_bench.model_profile, durai_bench.price, durai_bench.task_case, durai_bench.run, durai_bench.attempt,
      durai_bench.experiment TO durai_evaluator;
