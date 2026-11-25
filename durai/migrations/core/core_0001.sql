-- Local Durai schemas. Apply only to the isolated durai-local database.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS age;
CREATE SCHEMA durai_agents;
CREATE SCHEMA durai_files;
CREATE SCHEMA durai_coordination;
CREATE SCHEMA durai_governance;
CREATE SCHEMA durai_improvement;
CREATE SCHEMA dev_trading;
CREATE SCHEMA app_trading;

CREATE TABLE durai_core.principal(role_name name PRIMARY KEY, application_id text NOT NULL, capability text NOT NULL);
INSERT INTO durai_core.principal VALUES
 ('durai_runtime','trading','runtime'),('durai_foreign','foreign','runtime'),
 ('durai_reviewer','trading','review'),('durai_release','trading','release'),
 ('durai_evaluator','trading','evaluate');
CREATE FUNCTION durai_core.application_id() RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
 AS $$ SELECT application_id FROM durai_core.principal WHERE role_name=session_user $$;
REVOKE ALL ON FUNCTION durai_core.application_id() FROM PUBLIC;
CREATE FUNCTION durai_core.immutable() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN RAISE EXCEPTION 'Immutable record. Register a new version.' USING ERRCODE='23514'; END $$;

CREATE TABLE durai_agents.definition(
 application_id text NOT NULL, id text NOT NULL, name text NOT NULL,
 capabilities jsonb NOT NULL, PRIMARY KEY(application_id,id));
CREATE TABLE durai_agents.version(
 application_id text NOT NULL, id text NOT NULL, agent_id text NOT NULL,
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'), manifest jsonb NOT NULL,
 source_bytes bytea NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,id), FOREIGN KEY(application_id,agent_id) REFERENCES durai_agents.definition);
CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON durai_agents.version FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();
CREATE TABLE durai_agents.run(
 application_id text NOT NULL, id text NOT NULL, state text NOT NULL CHECK(state IN
 ('QUEUED','RUNNING','WAITING_FOR_REVIEW','SUCCEEDED','FAILED','CANCELLED','LOST')),
 checkpoint jsonb NOT NULL DEFAULT '{}', revision bigint NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(application_id,id));

CREATE TABLE durai_files.manifest(
 application_id text NOT NULL, digest text NOT NULL, document jsonb NOT NULL,
 PRIMARY KEY(application_id,digest));
CREATE TRIGGER immutable_manifest BEFORE UPDATE OR DELETE ON durai_files.manifest FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();
CREATE TABLE durai_coordination.kv(
 application_id text NOT NULL, namespace text NOT NULL, key text NOT NULL,
 revision bigint NOT NULL, value jsonb NOT NULL, PRIMARY KEY(application_id,namespace,key));
CREATE TABLE durai_coordination.job(
 application_id text NOT NULL, id text NOT NULL, state text NOT NULL DEFAULT 'QUEUED'
 CHECK(state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','LOST')),
 owner text, generation bigint NOT NULL DEFAULT 0, lease_until timestamptz,
 result jsonb, PRIMARY KEY(application_id,id));
CREATE TABLE durai_coordination.quota(
 application_id text NOT NULL, pool text NOT NULL, capacity integer NOT NULL CHECK(capacity>0),
 PRIMARY KEY(application_id,pool));
CREATE TABLE durai_coordination.reservation(
 application_id text NOT NULL, pool text NOT NULL, id text NOT NULL,
 expires_at timestamptz NOT NULL, PRIMARY KEY(application_id,pool,id),
 FOREIGN KEY(application_id,pool) REFERENCES durai_coordination.quota);
CREATE TABLE durai_coordination.operation(
 application_id text NOT NULL, key text NOT NULL, input_digest text NOT NULL, result jsonb NOT NULL,
 PRIMARY KEY(application_id,key));
CREATE TABLE durai_coordination.event(
 application_id text NOT NULL, run_id text NOT NULL, sequence bigint NOT NULL,
 id text NOT NULL UNIQUE, kind text NOT NULL, payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(application_id,run_id,sequence));
CREATE TRIGGER immutable_event BEFORE UPDATE OR DELETE ON durai_coordination.event FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();

CREATE TABLE dev_trading.rule(
 application_id text NOT NULL, id text NOT NULL, version text NOT NULL, document jsonb NOT NULL,
 PRIMARY KEY(application_id,id,version));
CREATE TRIGGER immutable_rule BEFORE UPDATE OR DELETE ON dev_trading.rule FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();
CREATE TABLE app_trading.trade(
 application_id text NOT NULL, id text NOT NULL, lots integer NOT NULL CHECK(lots<>0),
 lot_size integer NOT NULL CHECK(lot_size>0), stored_shares integer NOT NULL,
 source_version text NOT NULL, PRIMARY KEY(application_id,id));
CREATE TABLE durai_improvement.finding(
 application_id text NOT NULL, id text NOT NULL, run_id text NOT NULL,
 state text NOT NULL CHECK(state IN ('OPEN','RESOLVED')), evidence jsonb NOT NULL,
 PRIMARY KEY(application_id,id));
CREATE TABLE durai_governance.evaluation(
 application_id text NOT NULL, version_id text NOT NULL, digest text NOT NULL,
 suite_digest text NOT NULL, verdict text NOT NULL CHECK(verdict IN ('PASS','FAIL')),
 evidence jsonb NOT NULL, PRIMARY KEY(application_id,version_id),
 FOREIGN KEY(application_id,version_id) REFERENCES durai_agents.version);
CREATE TRIGGER immutable_evaluation BEFORE UPDATE OR DELETE ON durai_governance.evaluation FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();
CREATE TABLE durai_governance.approval(
 application_id text NOT NULL, id text NOT NULL, version_id text NOT NULL,
 digest text NOT NULL, target text NOT NULL, precondition_digest text NOT NULL,
 policy_version text NOT NULL CHECK(policy_version='local-v1'),
 authority text NOT NULL CHECK(authority IN ('HUMAN','TEST_ONLY')),
 reviewer text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,id), FOREIGN KEY(application_id,version_id) REFERENCES durai_agents.version);
CREATE TRIGGER immutable_approval BEFORE UPDATE OR DELETE ON durai_governance.approval FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();
CREATE TABLE durai_governance.target(
 application_id text NOT NULL, id text NOT NULL, kind text NOT NULL CHECK(kind IN ('LOCAL_DEMO','TEST')),
 active_version text NOT NULL, previous_version text, state text NOT NULL CHECK(state IN ('ACTIVE','VERIFYING')),
 generation bigint NOT NULL DEFAULT 0, PRIMARY KEY(application_id,id),
 FOREIGN KEY(application_id,active_version) REFERENCES durai_agents.version);
CREATE TABLE durai_governance.activation(
 application_id text NOT NULL, id text NOT NULL, target text NOT NULL,
 version_id text NOT NULL, approval_id text NOT NULL, generation bigint NOT NULL,
 state text NOT NULL CHECK(state IN ('VERIFYING','VERIFIED','ROLLED_BACK')),
 evidence jsonb NOT NULL DEFAULT '{}', PRIMARY KEY(application_id,id));
CREATE TABLE durai_governance.audit(
 application_id text NOT NULL, id bigint GENERATED ALWAYS AS IDENTITY,
 actor name NOT NULL DEFAULT session_user, action text NOT NULL, payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(application_id,id));
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON durai_governance.audit FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();

-- RLS derives application identity from the login. A caller cannot set another app ID.
DO $policy$ DECLARE t record; BEGIN
 FOR t IN SELECT tablename,schemaname FROM pg_tables WHERE schemaname IN
 ('durai_agents','durai_files','durai_coordination','durai_governance','durai_improvement','dev_trading','app_trading') LOOP
 EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',t.schemaname,t.tablename);
 EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',t.schemaname,t.tablename);
 EXECUTE format('CREATE POLICY application_scope ON %I.%I USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id())',t.schemaname,t.tablename);
 END LOOP;
END $policy$;

GRANT USAGE ON SCHEMA durai_core,durai_agents,durai_files,durai_coordination,durai_governance,durai_improvement,dev_trading,app_trading
 TO durai_runtime,durai_foreign,durai_reviewer,durai_release,durai_evaluator;
GRANT EXECUTE ON FUNCTION durai_core.application_id() TO durai_runtime,durai_foreign,durai_reviewer,durai_release,durai_evaluator;
GRANT SELECT ON ALL TABLES IN SCHEMA durai_agents,durai_files,durai_coordination,durai_governance,durai_improvement,dev_trading,app_trading
 TO durai_runtime,durai_foreign,durai_reviewer,durai_release,durai_evaluator;
GRANT INSERT ON durai_agents.definition,durai_agents.version,durai_agents.run,durai_files.manifest,
 durai_coordination.kv,durai_coordination.job,durai_coordination.quota,durai_coordination.reservation,
 durai_coordination.operation,durai_coordination.event,durai_improvement.finding,dev_trading.rule,app_trading.trade,
 durai_governance.audit TO durai_runtime,durai_foreign;
GRANT UPDATE ON durai_agents.run,durai_coordination.kv,durai_coordination.job,durai_improvement.finding,app_trading.trade
 TO durai_runtime,durai_foreign;
GRANT DELETE ON durai_coordination.reservation TO durai_runtime,durai_foreign;
GRANT INSERT ON durai_governance.evaluation,durai_governance.audit TO durai_evaluator;
GRANT INSERT ON durai_governance.approval,durai_governance.audit TO durai_reviewer;
GRANT INSERT,UPDATE ON durai_governance.target,durai_governance.activation TO durai_release;
GRANT INSERT ON durai_governance.audit TO durai_release;
GRANT UPDATE ON durai_improvement.finding,durai_agents.run,app_trading.trade TO durai_release;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA durai_governance TO durai_runtime,durai_foreign,durai_reviewer,durai_release,durai_evaluator;
