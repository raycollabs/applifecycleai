-- Sample: Claim journal.py of worktree W-1 as member M-1 (generation 1). Release it. Claim it as M-2 (generation 2). Keep both rows.
CREATE TABLE durai_coordination.file_lease(
 application_id text NOT NULL, worktree text NOT NULL, path text NOT NULL,
 generation bigint NOT NULL CHECK(generation>=1),
 workflow_id text, step_id text,
 owner text NOT NULL, role text NOT NULL,
 state text NOT NULL CHECK(state IN ('HELD','RELEASED','LOST')),
 acquired_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 PRIMARY KEY(application_id,worktree,path,generation));
CREATE UNIQUE INDEX file_lease_one_held ON durai_coordination.file_lease(application_id,worktree,path) WHERE state='HELD';
CREATE INDEX file_lease_by_workflow ON durai_coordination.file_lease(application_id,workflow_id,step_id);

CREATE FUNCTION durai_coordination.file_lease_guard() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN
  IF TG_OP='DELETE' THEN
   RAISE EXCEPTION 'File lease rows are an audit trail. Release the lease instead.' USING ERRCODE='23514';
  END IF;
  IF OLD.state<>'HELD' THEN
   RAISE EXCEPTION 'A RELEASED or LOST lease is final. Claim a new generation.' USING ERRCODE='23514';
  END IF;
  IF (NEW.application_id,NEW.worktree,NEW.path,NEW.generation,NEW.workflow_id,NEW.step_id,NEW.owner,NEW.role,NEW.acquired_at)
     IS DISTINCT FROM (OLD.application_id,OLD.worktree,OLD.path,OLD.generation,OLD.workflow_id,OLD.step_id,OLD.owner,OLD.role,OLD.acquired_at) THEN
   RAISE EXCEPTION 'Only state and expires_at of a HELD lease can change.' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END $$;
CREATE TRIGGER file_lease_guard BEFORE UPDATE OR DELETE ON durai_coordination.file_lease
 FOR EACH ROW EXECUTE FUNCTION durai_coordination.file_lease_guard();

ALTER TABLE durai_coordination.file_lease ENABLE ROW LEVEL SECURITY;
ALTER TABLE durai_coordination.file_lease FORCE ROW LEVEL SECURITY;
CREATE POLICY application_scope ON durai_coordination.file_lease
 USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id());

GRANT SELECT ON durai_coordination.file_lease TO durai_runtime,durai_foreign,durai_reviewer,durai_release,durai_evaluator;
GRANT INSERT,UPDATE ON durai_coordination.file_lease TO durai_runtime,durai_foreign;
