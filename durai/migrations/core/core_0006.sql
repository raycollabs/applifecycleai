-- Ontology packs are data, not executable authority. Applied migrations stay immutable.
CREATE SCHEMA durai_ontology;
CREATE SCHEMA durai_engineering;
CREATE TABLE durai_ontology.pack(
 scope text NOT NULL, id text NOT NULL, version text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('GENERIC','APPLICATION')),
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'), document jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(scope,id,version), UNIQUE(scope,id,version,digest),
 CHECK((kind='GENERIC' AND scope='*') OR (scope<>'*')));
CREATE TABLE durai_ontology.record(
 scope text NOT NULL, pack_id text NOT NULL, version text NOT NULL,
 id text NOT NULL, layer text NOT NULL CHECK(layer IN ('DESIGN','ENTITY','DAO','SERVICE','UI','TEST')),
 type text NOT NULL CHECK(type IN ('KNOWLEDGE','MEMORY','PROCEDURE')),
 digest text NOT NULL, document jsonb NOT NULL,
 PRIMARY KEY(scope,pack_id,version,id),
 FOREIGN KEY(scope,pack_id,version) REFERENCES durai_ontology.pack);
CREATE TABLE durai_ontology.binding(
 application_id text NOT NULL, id text NOT NULL, revision bigint NOT NULL,
 document jsonb NOT NULL, digest text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,id,revision));
CREATE TABLE durai_engineering.run(
 application_id text NOT NULL, id text NOT NULL, mode text NOT NULL CHECK(mode IN ('GREENFIELD','REPLICA')),
 binding_digest text NOT NULL, context_digest text NOT NULL,
 document jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,id));
CREATE TABLE durai_engineering.evidence(
 application_id text NOT NULL, run_id text NOT NULL, sequence integer NOT NULL,
 stage text NOT NULL CHECK(stage IN ('DESIGN_READY','EXECUTION_SHELL_READY',
 'BASELINE_TEST_FAILURE_CONFIRMED','IMPLEMENTATION_READY','WAITING_FOR_REVIEW','FAILED')),
 digest text NOT NULL, document jsonb NOT NULL,
 actor name NOT NULL DEFAULT session_user,
 PRIMARY KEY(application_id,run_id,sequence),
 FOREIGN KEY(application_id,run_id) REFERENCES durai_engineering.run);

-- SQL also guards order and evaluator ownership; bypassing the Python API is insufficient.
CREATE FUNCTION durai_engineering.guard_evidence() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $$
DECLARE previous text; expected text; BEGIN
 PERFORM 1 FROM durai_engineering.run WHERE application_id=NEW.application_id AND id=NEW.run_id FOR UPDATE;
 SELECT stage INTO previous FROM durai_engineering.evidence
  WHERE application_id=NEW.application_id AND run_id=NEW.run_id ORDER BY sequence DESC LIMIT 1;
 expected := CASE previous WHEN NULL THEN 'DESIGN_READY' WHEN 'DESIGN_READY' THEN 'EXECUTION_SHELL_READY'
  WHEN 'EXECUTION_SHELL_READY' THEN 'BASELINE_TEST_FAILURE_CONFIRMED'
  WHEN 'BASELINE_TEST_FAILURE_CONFIRMED' THEN 'IMPLEMENTATION_READY'
  WHEN 'IMPLEMENTATION_READY' THEN 'WAITING_FOR_REVIEW' ELSE NULL END;
 IF previous IS NULL THEN expected := 'DESIGN_READY'; END IF;
 IF NEW.stage <> 'FAILED' AND NEW.stage IS DISTINCT FROM expected THEN
  RAISE EXCEPTION 'Engineering gate order differs' USING ERRCODE='23514'; END IF;
 IF previous IN ('WAITING_FOR_REVIEW','FAILED') THEN
  RAISE EXCEPTION 'Engineering run is terminal' USING ERRCODE='23514'; END IF;
 IF NEW.stage IN ('BASELINE_TEST_FAILURE_CONFIRMED','WAITING_FOR_REVIEW') AND session_user <> 'durai_evaluator' THEN
  RAISE EXCEPTION 'Independent evaluator is required' USING ERRCODE='42501'; END IF;
 IF NEW.stage='BASELINE_TEST_FAILURE_CONFIRMED' AND
  (NEW.document->>'category' IS DISTINCT FROM 'BUSINESS_ASSERTION' OR
   NEW.document->>'baselineDigest' IS NULL OR NEW.document->>'suiteDigest' IS NULL OR
   NEW.document->>'assertion' IS NULL OR NEW.document->'expected'=NEW.document->'observed') THEN
  RAISE EXCEPTION 'Meaningful business-red evidence is required' USING ERRCODE='23514'; END IF;
 IF NEW.stage='WAITING_FOR_REVIEW' AND NEW.document->>'verdict' IS DISTINCT FROM 'PASS' THEN
  RAISE EXCEPTION 'Passing independent evaluation is required' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_evidence BEFORE INSERT ON durai_engineering.evidence
 FOR EACH ROW EXECUTE FUNCTION durai_engineering.guard_evidence();

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['pack','record'] LOOP
  EXECUTE format('ALTER TABLE durai_ontology.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE durai_ontology.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY visible_scope ON durai_ontology.%I FOR SELECT USING(scope=''*'' OR scope=durai_core.application_id())',t);
  EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON durai_ontology.%I FOR EACH ROW EXECUTE FUNCTION durai_core.immutable()',t);
 END LOOP;
END $$;
ALTER TABLE durai_ontology.binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE durai_ontology.binding FORCE ROW LEVEL SECURITY;
CREATE POLICY application_scope ON durai_ontology.binding USING(application_id=durai_core.application_id());
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON durai_ontology.binding FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['run','evidence'] LOOP
  EXECUTE format('ALTER TABLE durai_engineering.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE durai_engineering.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY application_scope ON durai_engineering.%I USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id())',t);
  EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON durai_engineering.%I FOR EACH ROW EXECUTE FUNCTION durai_core.immutable()',t);
 END LOOP;
END $$;
GRANT USAGE ON SCHEMA durai_ontology,durai_engineering TO durai_runtime,durai_foreign,durai_reviewer,durai_evaluator;
GRANT SELECT ON ALL TABLES IN SCHEMA durai_ontology,durai_engineering TO durai_runtime,durai_foreign,durai_reviewer,durai_evaluator;
-- Operator installation owns pack writes. Reviewers own application binding changes.
GRANT INSERT ON durai_ontology.binding TO durai_reviewer;
GRANT INSERT ON durai_engineering.run TO durai_runtime,durai_foreign;
GRANT INSERT ON durai_engineering.evidence TO durai_runtime,durai_evaluator;
SET LOCAL search_path=ag_catalog,public;
SELECT ag_catalog.create_graph('durai_pack_graph');
REVOKE ALL ON SCHEMA durai_pack_graph FROM PUBLIC;
SET LOCAL search_path=public;
