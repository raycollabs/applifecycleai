-- Keep approved release identity separate from a deployment attempt and a run.
CREATE TABLE durai_agents.release(
 application_id text NOT NULL, id text NOT NULL, version_id text NOT NULL,
 digest text NOT NULL, suite_digest text NOT NULL, approval_id text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,id),
 FOREIGN KEY(application_id,version_id) REFERENCES durai_agents.version,
 FOREIGN KEY(application_id,approval_id) REFERENCES durai_governance.approval);
ALTER TABLE durai_agents.release ENABLE ROW LEVEL SECURITY;
ALTER TABLE durai_agents.release FORCE ROW LEVEL SECURITY;
CREATE POLICY application_scope ON durai_agents.release
 USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id());
CREATE TRIGGER immutable_release BEFORE UPDATE OR DELETE ON durai_agents.release
 FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();
GRANT SELECT ON durai_agents.release TO durai_runtime,durai_foreign,durai_reviewer,durai_release,durai_evaluator;
GRANT INSERT ON durai_agents.release TO durai_release;
ALTER TABLE durai_governance.activation ADD COLUMN release_id text;
ALTER TABLE durai_governance.activation ADD CONSTRAINT activation_release
 FOREIGN KEY(application_id,release_id) REFERENCES durai_agents.release;
