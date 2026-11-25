DO $owner$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='durai_context_owner') THEN
  CREATE ROLE durai_context_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
 END IF;
END $owner$;
CREATE SCHEMA durai_context AUTHORIZATION durai_context_owner;
GRANT USAGE ON SCHEMA durai_core TO durai_context_owner;
GRANT EXECUTE ON FUNCTION durai_core.application_id() TO durai_context_owner;

CREATE TABLE durai_context.plan(
 application_id text NOT NULL, id text NOT NULL, title text NOT NULL,
 next_sequence bigint NOT NULL DEFAULT 0, counters jsonb NOT NULL DEFAULT '{}',
 author name NOT NULL DEFAULT session_user, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,id));
CREATE TABLE durai_context.record(
 application_id text NOT NULL, plan_id text NOT NULL, key text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('plan','status','decision','finding','resolution','approval','progress','evidence','delivery','research')),
 ordinal bigint NOT NULL, sequence bigint NOT NULL, revision bigint NOT NULL DEFAULT 1,
 value jsonb NOT NULL, digest text NOT NULL, previous_digest text,
 request_key text NOT NULL, request_digest text NOT NULL, author name NOT NULL DEFAULT session_user,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 terms tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig,value)) STORED,
 PRIMARY KEY(application_id,plan_id,key), UNIQUE(application_id,plan_id,sequence),
 UNIQUE(application_id,plan_id,request_key), FOREIGN KEY(application_id,plan_id) REFERENCES durai_context.plan);
CREATE INDEX context_record_terms ON durai_context.record USING gin(terms);
CREATE TABLE durai_context.status_revision(
 application_id text NOT NULL, plan_id text NOT NULL, revision bigint NOT NULL, sequence bigint NOT NULL,
 value jsonb NOT NULL, digest text NOT NULL, previous_digest text,
 author name NOT NULL, created_at timestamptz NOT NULL,
 request_key text NOT NULL, request_digest text NOT NULL,
 PRIMARY KEY(application_id,plan_id,revision), UNIQUE(application_id,plan_id,request_key),
 FOREIGN KEY(application_id,plan_id) REFERENCES durai_context.plan);
CREATE TABLE durai_context.reference(
 application_id text NOT NULL, kind text NOT NULL CHECK(kind IN ('commit','receipt','verdict')),
 id text NOT NULL, digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 locator jsonb NOT NULL, author name NOT NULL DEFAULT session_user,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,kind,id,digest));

CREATE FUNCTION durai_context.value_digest(v jsonb) RETURNS text LANGUAGE sql IMMUTABLE STRICT
 SET search_path=pg_catalog AS $$ SELECT encode(sha256(convert_to(v::text,'UTF8')),'hex') $$;

CREATE FUNCTION durai_context.guard_record() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.kind<>'status' THEN
  RAISE EXCEPTION 'Context records are immutable' USING ERRCODE='23514';
 END IF;
 IF NEW.application_id<>OLD.application_id OR NEW.plan_id<>OLD.plan_id OR NEW.key<>OLD.key OR
    NEW.kind<>OLD.kind OR NEW.ordinal<>OLD.ordinal OR NEW.revision<>OLD.revision+1 OR NEW.previous_digest<>OLD.digest THEN
  RAISE EXCEPTION 'Invalid status revision' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER context_record_immutable BEFORE UPDATE OR DELETE ON durai_context.record
 FOR EACH ROW EXECUTE FUNCTION durai_context.guard_record();
CREATE TRIGGER context_status_immutable BEFORE UPDATE OR DELETE ON durai_context.status_revision
 FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();
CREATE TRIGGER context_reference_immutable BEFORE UPDATE OR DELETE ON durai_context.reference
 FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();

CREATE FUNCTION durai_context.validate_value(k text,v jsonb) RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE required text[]; field text;
BEGIN
 IF jsonb_typeof(v)<>'object' OR octet_length(v::text)>1048576 THEN
  RAISE EXCEPTION 'Context value must be an object within 1 MiB' USING ERRCODE='22023';
 END IF;
 required := CASE k
  WHEN 'plan' THEN ARRAY['text','sourcePath','textDigest','previousRevisionDigest']
  WHEN 'status' THEN ARRAY['phase','state','nextAction','planRevisionDigest']
  WHEN 'decision' THEN ARRAY['decision','alternatives','evidence','authority']
  WHEN 'finding' THEN ARRAY['finding','severity','files','proposedCorrection']
  WHEN 'resolution' THEN ARRAY['findingDigest','outcome','evidence']
  WHEN 'approval' THEN ARRAY['subjectDigest','decision','scope']
  WHEN 'progress' THEN ARRAY['step','result','evidence']
  WHEN 'evidence' THEN ARRAY['references']
  WHEN 'delivery' THEN ARRAY['attemptId','records','setDigest']
  WHEN 'research' THEN ARRAY['question','finding','sources','checkedDate'] END;
 IF required IS NULL OR NOT v ?& required THEN
  RAISE EXCEPTION 'Missing context value fields' USING ERRCODE='22023';
 END IF;
 FOR field IN SELECT jsonb_object_keys(v) LOOP
  IF field NOT IN ('previousRevisionDigest','alternatives','evidence','files','references','records','sources') AND
     (jsonb_typeof(v->field)<>'string' OR length(v->>field)=0) THEN
   RAISE EXCEPTION 'Invalid context string field' USING ERRCODE='22023';
  END IF;
 END LOOP;
 FOREACH field IN ARRAY ARRAY['alternatives','evidence','files','references','records','sources'] LOOP
  IF v ? field AND jsonb_typeof(v->field)<>'array' THEN
   RAISE EXCEPTION 'Invalid context array field' USING ERRCODE='22023';
  END IF;
 END LOOP;
 IF k='decision' AND v->>'authority'<>'PROPOSED' THEN
  RAISE EXCEPTION 'Decisions cannot assert approval authority' USING ERRCODE='23514';
 END IF;
 IF k='approval' AND v->>'decision' NOT IN ('APPROVE','REJECT') OR
    k='resolution' AND v->>'outcome' NOT IN ('FIXED','ACCEPTED','REJECTED') OR
    k='finding' AND v->>'severity' NOT IN ('LOW','MEDIUM','HIGH','CRITICAL') OR
    k='status' AND v->>'state' NOT IN ('PLANNED','RUNNING','BLOCKED','WAITING_FOR_REVIEW','COMPLETED','ABANDONED','SUPERSEDED') THEN
  RAISE EXCEPTION 'Invalid context enum value' USING ERRCODE='22023';
 END IF;
 IF v::text ~* '(nvapi-[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{12,}|Bearer [A-Za-z0-9._-]{12,})' OR
    v ?| ARRAY['apiKey','password','secret','token','rawLog','rawError','protectedTestValues'] THEN
  RAISE EXCEPTION 'Context cannot contain credentials or raw protected evidence' USING ERRCODE='22023';
 END IF;
END $$;

CREATE FUNCTION durai_context.check_references(p_plan text,refs jsonb) RETURNS void LANGUAGE plpgsql
 SET search_path=pg_catalog AS $$
DECLARE ref jsonb; ok boolean;
BEGIN
 FOR ref IN SELECT value FROM jsonb_array_elements(refs) LOOP
  IF jsonb_typeof(ref)<>'object' OR NOT ref ?& ARRAY['kind','id','digest'] OR ref->>'digest' !~ '^[a-f0-9]{64}$' THEN
   RAISE EXCEPTION 'Invalid evidence reference' USING ERRCODE='22023';
  END IF;
  ok := false;
  IF ref->>'kind'='record' THEN
   SELECT EXISTS(SELECT 1 FROM durai_context.record WHERE plan_id=p_plan AND key=ref->>'id' AND digest=ref->>'digest') INTO ok;
  ELSIF ref->>'kind'='run' THEN
   SELECT EXISTS(SELECT 1 FROM durai_engineering.run WHERE id=ref->>'id' AND context_digest=ref->>'digest') INTO ok;
  ELSIF ref->>'kind'='event' THEN
   SELECT EXISTS(SELECT 1 FROM durai_coordination.event WHERE id=ref->>'id' AND durai_context.value_digest(payload)=ref->>'digest') INTO ok;
  ELSIF ref->>'kind' IN ('commit','receipt','verdict') THEN
   SELECT EXISTS(SELECT 1 FROM durai_context.reference WHERE kind=ref->>'kind' AND id=ref->>'id' AND digest=ref->>'digest') INTO ok;
  END IF;
  IF NOT ok THEN RAISE EXCEPTION 'Evidence reference does not resolve in this scope' USING ERRCODE='23514'; END IF;
 END LOOP;
END $$;

CREATE FUNCTION durai_context.open_plan(p_id text,p_title text) RETURNS durai_context.plan LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog AS $$
DECLARE result durai_context.plan;
BEGIN
 IF p_id !~ '^[A-Za-z0-9_.-]{1,160}$' OR length(p_title) NOT BETWEEN 1 AND 300 THEN
  RAISE EXCEPTION 'Invalid plan identity or title' USING ERRCODE='22023'; END IF;
 INSERT INTO durai_context.plan(application_id,id,title) VALUES(durai_core.application_id(),p_id,p_title) ON CONFLICT DO NOTHING;
 SELECT * INTO STRICT result FROM durai_context.plan WHERE id=p_id;
 IF result.title<>p_title THEN RAISE EXCEPTION 'Plan title differs' USING ERRCODE='23514'; END IF;
 RETURN result;
END $$;

CREATE FUNCTION durai_context.append(p_plan text,p_kind text,p_value jsonb,p_request text) RETURNS durai_context.record
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result durai_context.record; p durai_context.plan; n bigint; rd text; prior text; item jsonb;
BEGIN
 IF p_kind='status' OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION 'Invalid append kind or request key' USING ERRCODE='22023'; END IF;
 IF p_kind='approval' AND session_user<>'durai_reviewer' OR p_kind='delivery' AND session_user<>'durai_evaluator' THEN
  RAISE EXCEPTION 'Context record authority refused' USING ERRCODE='42501'; END IF;
 PERFORM durai_context.validate_value(p_kind,p_value);
 SELECT * INTO STRICT p FROM durai_context.plan WHERE id=p_plan FOR UPDATE;
 rd := durai_context.value_digest(jsonb_build_object('kind',p_kind,'value',p_value));
 SELECT * INTO result FROM durai_context.record WHERE plan_id=p_plan AND request_key=p_request;
 IF FOUND THEN
  IF result.request_digest<>rd THEN RAISE EXCEPTION 'Request key has different input' USING ERRCODE='23514'; END IF;
  RETURN result;
 END IF;
 PERFORM durai_context.check_references(p_plan,COALESCE(p_value->'evidence',p_value->'references','[]'::jsonb));
 IF p_kind='plan' THEN
  SELECT digest INTO prior FROM durai_context.record WHERE plan_id=p_plan AND kind='plan' ORDER BY ordinal DESC LIMIT 1;
  IF (p_value->>'previousRevisionDigest') IS DISTINCT FROM prior OR
     p_value->>'textDigest'<>encode(sha256(convert_to(p_value->>'text','UTF8')),'hex') THEN
   RAISE EXCEPTION 'Plan revision digest differs' USING ERRCODE='23514'; END IF;
 END IF;
 IF p_kind='resolution' AND NOT EXISTS(SELECT 1 FROM durai_context.record WHERE plan_id=p_plan AND kind='finding' AND digest=p_value->>'findingDigest') THEN
  RAISE EXCEPTION 'Resolution requires an existing finding' USING ERRCODE='23514'; END IF;
 IF p_kind='approval' AND NOT EXISTS(SELECT 1 FROM durai_context.record WHERE plan_id=p_plan AND digest=p_value->>'subjectDigest') THEN
  RAISE EXCEPTION 'Approval subject is unavailable' USING ERRCODE='23514'; END IF;
 IF p_kind='delivery' THEN
  IF NOT EXISTS(SELECT 1 FROM durai_tasks.attempt WHERE id=p_value->>'attemptId') THEN
   RAISE EXCEPTION 'Delivery attempt is unavailable' USING ERRCODE='23514'; END IF;
  FOR item IN SELECT value FROM jsonb_array_elements(p_value->'records') LOOP
   IF NOT EXISTS(SELECT 1 FROM durai_context.record WHERE plan_id=p_plan AND key=item->>'key' AND digest=item->>'digest') THEN
    RAISE EXCEPTION 'Delivered record is unavailable' USING ERRCODE='23514'; END IF;
  END LOOP;
  IF p_value->>'setDigest'<>durai_context.value_digest(p_value->'records') THEN
   RAISE EXCEPTION 'Delivery set digest differs' USING ERRCODE='23514'; END IF;
 END IF;
 n := COALESCE((p.counters->>p_kind)::bigint,0)+1;
 UPDATE durai_context.plan SET next_sequence=next_sequence+1,counters=jsonb_set(counters,ARRAY[p_kind],to_jsonb(n)) WHERE id=p_plan;
 INSERT INTO durai_context.record(application_id,plan_id,key,kind,ordinal,sequence,value,digest,previous_digest,request_key,request_digest)
 VALUES(durai_core.application_id(),p_plan,CASE WHEN p_kind='plan' THEN 'plan/rev/'||n ELSE p_kind||'/'||lpad(n::text,greatest(3,length(n::text)),'0') END,
  p_kind,n,p.next_sequence+1,p_value,durai_context.value_digest(p_value),CASE WHEN p_kind='plan' THEN prior END,p_request,rd) RETURNING * INTO result;
 RETURN result;
END $$;

CREATE FUNCTION durai_context.set_status(p_plan text,p_value jsonb,p_expected bigint,p_request text) RETURNS durai_context.record
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p durai_context.plan; old durai_context.record; result durai_context.record; saved durai_context.status_revision; rd text; current_plan text;
BEGIN
 PERFORM durai_context.validate_value('status',p_value);
 IF p_expected IS NULL OR p_expected<0 OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 200 THEN
  RAISE EXCEPTION 'Invalid status request' USING ERRCODE='22023'; END IF;
 SELECT * INTO STRICT p FROM durai_context.plan WHERE id=p_plan FOR UPDATE;
 rd := durai_context.value_digest(p_value);
 SELECT * INTO saved FROM durai_context.status_revision WHERE plan_id=p_plan AND request_key=p_request;
 IF FOUND THEN
  IF saved.request_digest<>rd THEN RAISE EXCEPTION 'Request key has different input' USING ERRCODE='23514'; END IF;
  SELECT * INTO result FROM durai_context.record WHERE plan_id=p_plan AND key='status';
  result.value:=saved.value; result.digest:=saved.digest; result.revision:=saved.revision; result.sequence:=saved.sequence;
  result.previous_digest:=saved.previous_digest; result.author:=saved.author; result.created_at:=saved.created_at;
  RETURN result;
 END IF;
 SELECT * INTO old FROM durai_context.record WHERE plan_id=p_plan AND key='status';
 IF COALESCE(old.revision,0)<>p_expected THEN RAISE EXCEPTION 'Status revision differs' USING ERRCODE='23514'; END IF;
 IF old.value->>'state' IN ('COMPLETED','ABANDONED','SUPERSEDED') THEN
  RAISE EXCEPTION 'Plan status is terminal' USING ERRCODE='23514'; END IF;
 SELECT digest INTO current_plan FROM durai_context.record WHERE plan_id=p_plan AND kind='plan' ORDER BY ordinal DESC LIMIT 1;
 IF current_plan IS NULL OR p_value->>'planRevisionDigest'<>current_plan THEN
  RAISE EXCEPTION 'Status requires the current plan revision digest' USING ERRCODE='23514'; END IF;
 UPDATE durai_context.plan SET next_sequence=next_sequence+1 WHERE id=p_plan;
 IF p_expected=0 THEN
  INSERT INTO durai_context.record(application_id,plan_id,key,kind,ordinal,sequence,value,digest,request_key,request_digest)
   VALUES(durai_core.application_id(),p_plan,'status','status',1,p.next_sequence+1,p_value,rd,p_request,rd) RETURNING * INTO result;
 ELSE
  UPDATE durai_context.record SET value=p_value,digest=rd,previous_digest=old.digest,revision=revision+1,
   sequence=p.next_sequence+1,author=session_user,created_at=clock_timestamp(),request_key=p_request,request_digest=rd
   WHERE plan_id=p_plan AND key='status' RETURNING * INTO result;
 END IF;
 INSERT INTO durai_context.status_revision VALUES(result.application_id,result.plan_id,result.revision,result.sequence,result.value,
  result.digest,result.previous_digest,result.author,result.created_at,p_request,rd);
 RETURN result;
END $$;

CREATE FUNCTION durai_context.register_reference(p_kind text,p_id text,p_digest text,p_locator jsonb) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF session_user<>'durai_evaluator' THEN RAISE EXCEPTION 'Reference verification requires the controller' USING ERRCODE='42501'; END IF;
 INSERT INTO durai_context.reference(application_id,kind,id,digest,locator)
 VALUES(durai_core.application_id(),p_kind,p_id,p_digest,p_locator) ON CONFLICT DO NOTHING;
END $$;

DO $scope$ DECLARE tab text; f regprocedure; BEGIN
 FOREACH tab IN ARRAY ARRAY['plan','record','status_revision','reference'] LOOP
  EXECUTE format('ALTER TABLE durai_context.%I OWNER TO durai_context_owner',tab);
  EXECUTE format('ALTER TABLE durai_context.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE durai_context.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('CREATE POLICY application_scope ON durai_context.%I USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id())',tab);
 END LOOP;
 FOR f IN SELECT p.oid::regprocedure FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='durai_context' LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO durai_context_owner',f);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f);
 END LOOP;
END $scope$;
GRANT USAGE ON SCHEMA durai_engineering,durai_coordination,durai_tasks TO durai_context_owner;
GRANT SELECT ON durai_engineering.run,durai_coordination.event,durai_tasks.attempt TO durai_context_owner;
GRANT USAGE ON SCHEMA durai_context TO durai_runtime,durai_foreign,durai_reviewer,durai_evaluator;
GRANT SELECT ON ALL TABLES IN SCHEMA durai_context TO durai_runtime,durai_foreign,durai_reviewer,durai_evaluator;
GRANT EXECUTE ON FUNCTION durai_context.value_digest(jsonb) TO durai_runtime,durai_foreign,durai_reviewer,durai_evaluator;
GRANT EXECUTE ON FUNCTION durai_context.open_plan(text,text),durai_context.append(text,text,jsonb,text),
 durai_context.set_status(text,jsonb,bigint,text) TO durai_runtime,durai_foreign,durai_reviewer,durai_evaluator;
GRANT EXECUTE ON FUNCTION durai_context.register_reference(text,text,text,jsonb) TO durai_evaluator;
