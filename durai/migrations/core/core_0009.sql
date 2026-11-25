-- Sample: Register task T-1. Create attempt 1 on deepagents 0.7.38. Reserve 1 of 2 attempts. Hide all rows from app foreign.
CREATE SCHEMA durai_tasks;
CREATE TABLE durai_tasks.task(
 application_id text NOT NULL, digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'), id text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('REPAIR','FEATURE','REFACTOR')), document jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(application_id,digest));
CREATE TRIGGER immutable_task BEFORE UPDATE OR DELETE ON durai_tasks.task FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();
CREATE TABLE durai_tasks.budget(
 application_id text NOT NULL, id text NOT NULL, limits jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(application_id,id));
CREATE TRIGGER immutable_budget BEFORE UPDATE OR DELETE ON durai_tasks.budget FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();
CREATE TABLE durai_tasks.budget_entry(
 application_id text NOT NULL, budget_id text NOT NULL, reservation_id text NOT NULL,
 resource text NOT NULL CHECK(resource IN ('modelCalls','toolCalls','seconds','attempts')),
 amount integer NOT NULL CHECK(amount > 0), actual integer CHECK(actual >= 0 AND actual <= amount),
 state text NOT NULL CHECK(state IN ('RESERVED','RECONCILED')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), reconciled_at timestamptz,
 CHECK((state = 'RESERVED') = (actual IS NULL)),
 PRIMARY KEY(application_id,budget_id,reservation_id),
 FOREIGN KEY(application_id,budget_id) REFERENCES durai_tasks.budget);
CREATE TABLE durai_tasks.attempt(
 application_id text NOT NULL, id text NOT NULL, task_digest text NOT NULL, attempt integer NOT NULL CHECK(attempt > 0),
 run_id text NOT NULL, state text NOT NULL CHECK(state IN ('DRAFT','NEEDS_CLARIFICATION','READY','RUNNING','PAUSED',
 'WAITING_FOR_REVIEW','SUCCEEDED','FAILED','CANCELLED','LOST')),
 runtime text NOT NULL, runtime_version text NOT NULL, contract_digest text NOT NULL, source_digest text NOT NULL,
 budget_id text NOT NULL, owner text, generation bigint NOT NULL DEFAULT 0, lease_until timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(application_id,id),
 UNIQUE(application_id,task_digest,attempt),
 FOREIGN KEY(application_id,task_digest) REFERENCES durai_tasks.task,
 FOREIGN KEY(application_id,budget_id) REFERENCES durai_tasks.budget);

DO $policy$ DECLARE t record; BEGIN
 FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='durai_tasks' LOOP
 EXECUTE format('ALTER TABLE durai_tasks.%I ENABLE ROW LEVEL SECURITY',t.tablename);
 EXECUTE format('ALTER TABLE durai_tasks.%I FORCE ROW LEVEL SECURITY',t.tablename);
 EXECUTE format('CREATE POLICY application_scope ON durai_tasks.%I USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id())',t.tablename);
 END LOOP;
END $policy$;

GRANT USAGE ON SCHEMA durai_tasks TO durai_runtime,durai_foreign,durai_reviewer,durai_release,durai_evaluator;
GRANT SELECT ON ALL TABLES IN SCHEMA durai_tasks TO durai_runtime,durai_foreign,durai_reviewer,durai_release,durai_evaluator;
GRANT INSERT ON ALL TABLES IN SCHEMA durai_tasks TO durai_runtime,durai_foreign;
GRANT UPDATE ON durai_tasks.attempt,durai_tasks.budget_entry TO durai_runtime,durai_foreign;
GRANT UPDATE(state) ON durai_tasks.attempt TO durai_release;
