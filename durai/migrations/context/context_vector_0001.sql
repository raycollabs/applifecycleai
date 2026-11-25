CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE TABLE durai_context.embedding_batch(
 application_id text NOT NULL, plan_id text NOT NULL, model text NOT NULL,
 through_sequence bigint NOT NULL, chunks integer NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,plan_id,model), FOREIGN KEY(application_id,plan_id) REFERENCES durai_context.plan);
CREATE TABLE durai_context.embedding(
 application_id text NOT NULL, plan_id text NOT NULL, record_key text NOT NULL,
 revision bigint NOT NULL, digest text NOT NULL, ordinal integer NOT NULL,
 model text NOT NULL, text text NOT NULL, vector public.vector(1024) NOT NULL,
 PRIMARY KEY(application_id,plan_id,record_key,model,ordinal),
 FOREIGN KEY(application_id,plan_id,record_key) REFERENCES durai_context.record);
CREATE INDEX context_embedding_scope ON durai_context.embedding(application_id,plan_id,model);
CREATE INDEX context_embedding_hnsw ON durai_context.embedding USING hnsw(vector public.vector_cosine_ops);
DO $scope$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY['embedding','embedding_batch'] LOOP
  EXECUTE format('ALTER TABLE durai_context.%I OWNER TO durai_context_owner',tab);
  EXECUTE format('ALTER TABLE durai_context.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE durai_context.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('CREATE POLICY application_scope ON durai_context.%I USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id())',tab);
 END LOOP;
END $scope$;
GRANT SELECT ON durai_context.embedding,durai_context.embedding_batch TO durai_runtime,durai_foreign,durai_reviewer,durai_evaluator;
GRANT INSERT,UPDATE ON durai_context.embedding_batch TO durai_evaluator;
GRANT INSERT,DELETE ON durai_context.embedding TO durai_evaluator;
