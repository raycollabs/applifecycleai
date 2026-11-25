CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TABLE durai_coordination.context_revision(
 application_id text NOT NULL, namespace text NOT NULL, key text NOT NULL,
 revision bigint NOT NULL CHECK(revision > 0), digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 value jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,namespace,key,revision),
 FOREIGN KEY(application_id,namespace,key) REFERENCES durai_coordination.kv);
CREATE TRIGGER immutable_context_revision BEFORE UPDATE OR DELETE ON durai_coordination.context_revision
 FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();

CREATE TABLE durai_coordination.context_chunk(
 application_id text NOT NULL, namespace text NOT NULL, key text NOT NULL,
 revision bigint NOT NULL, ordinal integer NOT NULL CHECK(ordinal >= 0),
 digest text NOT NULL, model text NOT NULL, text text NOT NULL,
 embedding public.vector(1024) NOT NULL,
 terms tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig,text)) STORED,
 PRIMARY KEY(application_id,namespace,key,model,ordinal),
 FOREIGN KEY(application_id,namespace,key,revision)
 REFERENCES durai_coordination.context_revision);
CREATE INDEX context_chunk_scope ON durai_coordination.context_chunk(application_id,namespace,model);
CREATE INDEX context_chunk_terms ON durai_coordination.context_chunk USING gin(terms);
CREATE INDEX context_chunk_embedding ON durai_coordination.context_chunk USING hnsw(embedding public.vector_cosine_ops);

DO $scope$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['context_revision','context_chunk'] LOOP
  EXECUTE format('ALTER TABLE durai_coordination.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE durai_coordination.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('CREATE POLICY application_scope ON durai_coordination.%I USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id())',tab);
 END LOOP;
END $scope$;
GRANT SELECT ON durai_coordination.context_revision,durai_coordination.context_chunk
 TO durai_runtime,durai_foreign,durai_reviewer,durai_evaluator;
GRANT INSERT ON durai_coordination.context_revision TO durai_runtime,durai_foreign;
GRANT INSERT,DELETE ON durai_coordination.context_chunk TO durai_runtime,durai_foreign;
