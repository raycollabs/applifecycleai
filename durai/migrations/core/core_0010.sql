CREATE TABLE durai_ontology.policy(
 scope text NOT NULL, pack_id text NOT NULL, version text NOT NULL, policy_id text NOT NULL,
 rule text NOT NULL, audience jsonb NOT NULL DEFAULT '[]',
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(scope,pack_id,version,policy_id),
 FOREIGN KEY(scope,pack_id,version) REFERENCES durai_ontology.pack(scope,id,version));

CREATE FUNCTION durai_ontology.guard_policy() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $$
BEGIN
 -- Sample: Insert P-1 with rule "DO NOT X." for pack * durai.software-generation 4. A KNOWLEDGE record of that pack holds P-1 with rule "DO NOT X.". Accept the row. Insert P-1 with rule "DO Y.". Refuse the row with error 23514.
 IF NOT EXISTS(SELECT 1 FROM durai_ontology.record
  WHERE scope=NEW.scope AND pack_id=NEW.pack_id AND version=NEW.version AND type='KNOWLEDGE'
   AND document->'body'->'policies' @> jsonb_build_array(jsonb_build_object('id',NEW.policy_id,'rule',NEW.rule))) THEN
  RAISE EXCEPTION 'Policy is not in a KNOWLEDGE record of its pack' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_policy BEFORE INSERT ON durai_ontology.policy
 FOR EACH ROW EXECUTE FUNCTION durai_ontology.guard_policy();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON durai_ontology.policy
 FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();

ALTER TABLE durai_ontology.policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE durai_ontology.policy FORCE ROW LEVEL SECURITY;
CREATE POLICY visible_scope ON durai_ontology.policy FOR SELECT
 USING(scope='*' OR scope=durai_core.application_id());
CREATE POLICY insert_scope ON durai_ontology.policy FOR INSERT
 WITH CHECK(scope='*' OR scope=durai_core.application_id());
GRANT SELECT ON durai_ontology.policy TO durai_runtime,durai_foreign,durai_reviewer,durai_evaluator;
GRANT INSERT ON durai_ontology.policy TO durai_runtime,durai_foreign;
