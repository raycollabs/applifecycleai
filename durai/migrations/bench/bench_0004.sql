-- Sample: Insert app trade-pilot E6 with durai_ranking 8 and component booking BUILT with 1 evidence file. Read the baseline matrix. A ranking of 11 is refused.
DO $reader$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='durai_build_reader') THEN
  CREATE ROLE durai_build_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
 END IF;
END $reader$;

CREATE TABLE durai_bench.app_benchmark(
 app_key text NOT NULL CHECK(app_key ~ '^[a-z0-9][a-z0-9-]{1,63}$'),
 app_version text NOT NULL CHECK(app_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
 display_name text NOT NULL CHECK(display_name <> ''), domain text NOT NULL CHECK(domain <> ''),
 builder_model text NOT NULL CHECK(builder_model <> ''), builder_version text,
 model_profile_id text REFERENCES durai_bench.model_profile,
 durai_ranking int NOT NULL CHECK(durai_ranking BETWEEN 1 AND 10),
 source_root text NOT NULL, source_manifest_digest text NOT NULL CHECK(source_manifest_digest ~ '^[a-f0-9]{64}$'),
 live_url text, live_status text, live_checked_at timestamptz,
 status text NOT NULL DEFAULT 'LOADED' CHECK(status IN ('DRAFT','LOADED','RETIRED')),
 notes text, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), importer name NOT NULL DEFAULT session_user,
 PRIMARY KEY(app_key, app_version));

CREATE TABLE durai_bench.component_category(
 category_key text PRIMARY KEY, name text NOT NULL, description text NOT NULL);
INSERT INTO durai_bench.component_category(category_key, name, description) VALUES
 ('ui-workflow','UI workflow','UI screens that map one to one to business workflows'),
 ('api-service','API service','Typed API handlers and service modules'),
 ('data-model','Data model','Schema, constraints, migrations'),
 ('transactions','Transactions','Idempotency, locking, once-only effects'),
 ('access-control','Access control','Authentication, authorization, RLS, tenant scope'),
 ('async-messaging','Async messaging','Queues, Pub/Sub, outbox, workers'),
 ('external-integration','External integration','Payment, LLM providers, storage, feeds'),
 ('computation','Computation','Valuation, P&L, generation pipeline, parsing'),
 ('export-reporting','Export and reporting','Reports, documents, exports'),
 ('ontology-metadata','Ontology and metadata','Graph, contracts, metadata'),
 ('agent-tools','Agent tools','MCP, loop memory, retrieval'),
 ('observability','Observability','Metrics, logs, health'),
 ('deployment-ops','Deployment and operations','Compose, cloud deployment, runbooks, recovery'),
 ('tests','Tests','Test goals, test code, proofs');

CREATE TABLE durai_bench.app_component(
 app_key text NOT NULL, app_version text NOT NULL,
 component_key text NOT NULL CHECK(component_key ~ '^[a-z0-9][a-z0-9-]{1,63}$'),
 category text NOT NULL REFERENCES durai_bench.component_category,
 goal text NOT NULL CHECK(goal <> ''), design text NOT NULL, built text NOT NULL,
 status text NOT NULL CHECK(status IN ('BUILT','PARTIAL','DESIGNED','MISSING')),
 evidence jsonb NOT NULL DEFAULT '[]',
 test_goal_count int NOT NULL DEFAULT 0 CHECK(test_goal_count >= 0),
 limits text[] NOT NULL DEFAULT '{}',
 PRIMARY KEY(app_key, app_version, component_key),
 FOREIGN KEY(app_key, app_version) REFERENCES durai_bench.app_benchmark,
 CHECK(jsonb_typeof(evidence) = 'array'
   AND jsonb_array_length(evidence) = jsonb_array_length(jsonb_path_query_array(evidence,
        '$[*] ? (@.sha256 like_regex "^[a-f0-9]{64}$" && @.path.type() == "string" && @.path != "")'))
   AND (status NOT IN ('BUILT','PARTIAL') OR jsonb_array_length(evidence) > 0)));

CREATE TABLE durai_bench.component_goal(
 app_key text NOT NULL, app_version text NOT NULL, component_key text NOT NULL,
 sample_id text NOT NULL, sample_revision int NOT NULL CHECK(sample_revision > 0),
 sample_digest text NOT NULL CHECK(sample_digest ~ '^[a-f0-9]{64}$'),
 hidden_goals jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(hidden_goals) = 'array'),
 PRIMARY KEY(app_key, app_version, component_key),
 FOREIGN KEY(app_key, app_version, component_key) REFERENCES durai_bench.app_component);

CREATE TABLE durai_bench.build_attempt(
 attempt_id text PRIMARY KEY CHECK(attempt_id <> ''),
 app_key text NOT NULL, app_version text NOT NULL,
 scope text NOT NULL CHECK(scope IN ('COMPONENT','APP')), component_key text,
 model_profile_id text NOT NULL REFERENCES durai_bench.model_profile,
 pack text, git_commit text,
 sample_id text, sample_revision int CHECK(sample_revision > 0),
 request_digest text NOT NULL CHECK(request_digest ~ '^[a-f0-9]{64}$'),
 run_id text REFERENCES durai_bench.run, cost numeric(12,6) CHECK(cost >= 0),
 started_at timestamptz, finished_at timestamptz, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(attempt_id, app_key, app_version),
 FOREIGN KEY(app_key, app_version) REFERENCES durai_bench.app_benchmark,
 FOREIGN KEY(app_key, app_version, component_key) REFERENCES durai_bench.app_component,
 CHECK((scope = 'COMPONENT') = (component_key IS NOT NULL)),
 CHECK(finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at));

CREATE TABLE durai_bench.build_stage(
 attempt_id text NOT NULL REFERENCES durai_bench.build_attempt,
 stage text NOT NULL CHECK(stage IN ('DESIGN','PLAN','IMPLEMENTATION','TESTS','DEPLOYED')),
 artifacts jsonb NOT NULL DEFAULT '[]'
  CHECK(jsonb_typeof(artifacts) = 'array'
   AND jsonb_array_length(artifacts) = jsonb_array_length(jsonb_path_query_array(artifacts,
        '$[*] ? (@.sha256 like_regex "^[a-f0-9]{64}$" && @.path.type() == "string" && @.path != "")'))),
 state text NOT NULL CHECK(state IN ('PENDING','RUNNING','DONE','FAILED','SKIPPED')),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(attempt_id, stage));

CREATE TABLE durai_bench.build_score(
 attempt_id text NOT NULL, stage text NOT NULL,
 app_key text NOT NULL, app_version text NOT NULL, component_key text NOT NULL,
 reviewer_profile text NOT NULL CHECK(reviewer_profile <> ''),
 score smallint NOT NULL CHECK(score BETWEEN 0 AND 4),
 baseline_score smallint CHECK(baseline_score BETWEEN 0 AND 4),
 evidence text NOT NULL CHECK(btrim(evidence) <> ''),
 rubric_revision text NOT NULL CHECK(rubric_revision <> ''),
 prompt_digest text NOT NULL CHECK(prompt_digest ~ '^[a-f0-9]{64}$'),
 scored_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(attempt_id, stage, component_key, reviewer_profile),
 FOREIGN KEY(attempt_id, stage) REFERENCES durai_bench.build_stage,
 FOREIGN KEY(attempt_id, app_key, app_version) REFERENCES durai_bench.build_attempt(attempt_id, app_key, app_version),
 FOREIGN KEY(app_key, app_version, component_key) REFERENCES durai_bench.app_component);

CREATE VIEW durai_bench.baseline_matrix AS
SELECT c.app_key, c.app_version, c.component_key, c.category, c.status AS component_status,
       s.stage, a.model_profile_id,
       max(s.baseline_score) AS baseline_score, max(s.score) AS best_build_score, count(DISTINCT a.attempt_id) AS builds
FROM durai_bench.app_component c
LEFT JOIN durai_bench.build_score s ON s.app_key = c.app_key AND s.app_version = c.app_version AND s.component_key = c.component_key
LEFT JOIN durai_bench.build_attempt a ON a.attempt_id = s.attempt_id
GROUP BY c.app_key, c.app_version, c.component_key, c.category, c.status, s.stage, a.model_profile_id;

GRANT SELECT ON durai_bench.component_category, durai_bench.app_benchmark, durai_bench.app_component, durai_bench.component_goal,
      durai_bench.build_attempt, durai_bench.build_stage, durai_bench.build_score, durai_bench.baseline_matrix
      TO durai_evaluator, durai_reviewer;
GRANT INSERT ON durai_bench.app_benchmark, durai_bench.app_component, durai_bench.component_goal, durai_bench.build_attempt,
      durai_bench.build_stage, durai_bench.build_score TO durai_evaluator;
GRANT SELECT ON durai_bench.component_category, durai_bench.app_benchmark TO durai_runtime;
GRANT SELECT(app_key, app_version, component_key, sample_id, sample_revision, sample_digest) ON durai_bench.component_goal TO durai_runtime;

GRANT USAGE ON SCHEMA durai_bench TO durai_build_reader;
GRANT SELECT ON durai_bench.app_benchmark, durai_bench.component_category TO durai_build_reader;
GRANT SELECT(app_key, app_version, component_key, category, goal, design, built, status, test_goal_count, limits)
      ON durai_bench.app_component TO durai_build_reader;
GRANT SELECT(app_key, app_version, component_key, sample_id, sample_revision, sample_digest)
      ON durai_bench.component_goal TO durai_build_reader;
