CREATE TABLE durai_context.session_conversation(
 application_id text NOT NULL,
 conversation_id text NOT NULL CHECK(length(conversation_id) BETWEEN 1 AND 200),
 latest_turn bigint NOT NULL DEFAULT 0,
 covered_turn bigint NOT NULL DEFAULT 0,
 head_revision bigint NOT NULL DEFAULT 0,
 PRIMARY KEY(application_id,conversation_id),
 CHECK(covered_turn BETWEEN 0 AND latest_turn));

CREATE TABLE durai_context.session_turn(
 application_id text NOT NULL,
 conversation_id text NOT NULL,
 turn_number bigint NOT NULL,
 host_message_id text NOT NULL CHECK(length(host_message_id) BETWEEN 1 AND 200),
 role text NOT NULL CHECK(role IN ('user','assistant')),
 eligible_text text CHECK(octet_length(eligible_text) BETWEEN 1 AND 65536),
 token_count integer NOT NULL CHECK(token_count BETWEEN 1 AND 2000),
 plan_id text,
 digest text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,conversation_id,turn_number),
 UNIQUE(application_id,conversation_id,host_message_id),
 FOREIGN KEY(application_id,conversation_id) REFERENCES durai_context.session_conversation,
 FOREIGN KEY(application_id,plan_id) REFERENCES durai_context.plan);

CREATE TABLE durai_context.session_summary(
 application_id text NOT NULL,
 conversation_id text NOT NULL,
 revision bigint NOT NULL,
 covered_turn bigint NOT NULL,
 input_digest text NOT NULL,
 body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=16384),
 profile jsonb NOT NULL CHECK(jsonb_typeof(profile)='object' AND octet_length(profile::text)<=4096),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,conversation_id,revision),
 FOREIGN KEY(application_id,conversation_id) REFERENCES durai_context.session_conversation);
CREATE TRIGGER session_summary_immutable BEFORE UPDATE OR DELETE ON durai_context.session_summary
 FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();

CREATE TABLE durai_context.session_pending(
 application_id text NOT NULL,
 conversation_id text NOT NULL,
 covered_turn bigint NOT NULL,
 target_turn bigint NOT NULL,
 pending_tokens bigint NOT NULL,
 head_revision bigint NOT NULL,
 generation bigint NOT NULL DEFAULT 0,
 due_at timestamptz NOT NULL,
 lease_until timestamptz,
 claimed_turn bigint,
 claimed_tokens bigint,
 PRIMARY KEY(application_id,conversation_id),
 FOREIGN KEY(application_id,conversation_id) REFERENCES durai_context.session_conversation,
 CHECK(covered_turn<=target_turn AND pending_tokens>=0),
 CHECK((lease_until IS NULL AND claimed_turn IS NULL AND claimed_tokens IS NULL)
   OR (lease_until IS NOT NULL AND claimed_turn IS NOT NULL AND claimed_tokens IS NOT NULL
    AND claimed_turn BETWEEN covered_turn+1 AND target_turn AND claimed_tokens>=0)));
CREATE INDEX session_pending_due ON durai_context.session_pending(application_id,due_at,conversation_id)
 WHERE target_turn>covered_turn;

CREATE TABLE durai_context.session_attempt(
 application_id text NOT NULL,
 conversation_id text NOT NULL,
 generation bigint NOT NULL,
 first_turn bigint NOT NULL,
 last_turn bigint NOT NULL,
 state text NOT NULL CHECK(state IN ('running','succeeded','failed','lost','dead')),
 finished_at timestamptz,
 PRIMARY KEY(application_id,conversation_id,generation),
 FOREIGN KEY(application_id,conversation_id) REFERENCES durai_context.session_conversation,
 CHECK((state='running')=(finished_at IS NULL)));

CREATE TABLE durai_context.session_spend(
 application_id text NOT NULL,
 conversation_id text NOT NULL,
 day date NOT NULL,
 calls integer NOT NULL DEFAULT 0 CHECK(calls>=0),
 input_tokens bigint NOT NULL DEFAULT 0 CHECK(input_tokens>=0),
 output_tokens bigint NOT NULL DEFAULT 0 CHECK(output_tokens>=0),
 cost_usd numeric(12,6) NOT NULL DEFAULT 0 CHECK(cost_usd>=0),
 PRIMARY KEY(application_id,conversation_id,day),
 FOREIGN KEY(application_id,conversation_id) REFERENCES durai_context.session_conversation);

CREATE TABLE durai_context.session_cap_event(
 application_id text NOT NULL,
 day date NOT NULL,
 cap text NOT NULL CHECK(cap IN ('maxCallsPerConversationPerDay','maxCallsPerDay','maxCostUsdPerDay')),
 conversation_id text NOT NULL DEFAULT '',
 event text NOT NULL DEFAULT 'CAP_REACHED' CHECK(event='CAP_REACHED'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,day,cap,conversation_id));

CREATE FUNCTION durai_context.append_session_turn(
 p_conversation text,p_message text,p_role text,p_text text,p_tokens integer,p_plan text,p_flush boolean
) RETURNS durai_context.session_turn LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE app text:=durai_core.application_id(); turn_row durai_context.session_turn;
 next_turn bigint; request_digest text;
BEGIN
 IF app IS NULL OR p_text IS NULL OR p_tokens IS NULL OR p_flush IS NULL THEN
  RAISE EXCEPTION 'Invalid session turn' USING ERRCODE='22023';
 END IF;
 request_digest:=durai_context.value_digest(jsonb_build_array(p_role,p_text,p_tokens,p_plan));
 INSERT INTO durai_context.session_conversation(application_id,conversation_id)
 VALUES(app,p_conversation) ON CONFLICT DO NOTHING;
 SELECT latest_turn+1 INTO next_turn FROM durai_context.session_conversation
 WHERE application_id=app AND conversation_id=p_conversation FOR UPDATE;
 SELECT * INTO turn_row FROM durai_context.session_turn
 WHERE application_id=app AND conversation_id=p_conversation AND host_message_id=p_message;
 IF FOUND THEN
  IF turn_row.digest<>request_digest THEN
   RAISE EXCEPTION 'The host message changed' USING ERRCODE='23514';
  END IF;
  RETURN turn_row;
 END IF;
 INSERT INTO durai_context.session_turn(application_id,conversation_id,turn_number,host_message_id,role,eligible_text,
  token_count,plan_id,digest)
 VALUES(app,p_conversation,next_turn,p_message,p_role,p_text,p_tokens,p_plan,request_digest) RETURNING * INTO turn_row;
 UPDATE durai_context.session_conversation SET latest_turn=next_turn
 WHERE application_id=app AND conversation_id=p_conversation;
 INSERT INTO durai_context.session_pending AS pending(application_id,conversation_id,covered_turn,target_turn,
  pending_tokens,head_revision,due_at)
 VALUES(app,p_conversation,0,next_turn,p_tokens,0,
  CASE WHEN p_flush OR p_tokens>=2000 THEN clock_timestamp() ELSE clock_timestamp()+interval '30 seconds' END)
 ON CONFLICT(application_id,conversation_id) DO UPDATE SET target_turn=EXCLUDED.target_turn,
  pending_tokens=pending.pending_tokens+p_tokens,
  due_at=CASE WHEN p_flush OR pending.pending_tokens+p_tokens>=2000
   THEN least(pending.due_at,clock_timestamp()) ELSE clock_timestamp()+interval '30 seconds' END;
 RETURN turn_row;
END $$;

CREATE FUNCTION durai_context.claim_session_summary(p_lease_seconds integer, p_max_turns integer,p_max_tokens integer,
 p_max_conversation_calls integer DEFAULT 50,p_max_calls integer DEFAULT 2000,p_max_cost numeric DEFAULT 5.00)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE app text:=durai_core.application_id(); item durai_context.session_pending;
 through_turn bigint; source_tokens bigint; source_rows jsonb; prior_summary jsonb;
 today date:=(clock_timestamp() AT TIME ZONE 'UTC')::date; day_calls bigint; day_cost numeric; due boolean;
BEGIN
 IF app IS NULL OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 1 AND 600 OR
    p_max_turns IS NULL OR p_max_turns NOT BETWEEN 1 AND 64 OR
    p_max_tokens IS NULL OR p_max_tokens NOT BETWEEN 2000 AND 65536 THEN
  RAISE EXCEPTION 'Invalid session claim limits' USING ERRCODE='22023';
 END IF;
 IF p_max_conversation_calls IS NULL OR p_max_conversation_calls<0 OR p_max_calls IS NULL OR p_max_calls<0
    OR p_max_cost IS NULL OR p_max_cost<0 THEN
  RAISE EXCEPTION 'Invalid session spend caps' USING ERRCODE='22023';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtext('durai-session-spend'||app));
 SELECT coalesce(sum(calls),0),coalesce(sum(cost_usd),0) INTO day_calls,day_cost
 FROM durai_context.session_spend WHERE application_id=app AND day=today;
 IF day_calls>=p_max_calls OR day_cost>=p_max_cost THEN
  SELECT EXISTS(SELECT 1 FROM durai_context.session_pending WHERE application_id=app AND target_turn>covered_turn
   AND due_at<=clock_timestamp() AND (lease_until IS NULL OR lease_until<=clock_timestamp())) INTO due;
  IF due THEN
   INSERT INTO durai_context.session_cap_event(application_id,day,cap)
   VALUES(app,today,CASE WHEN day_calls>=p_max_calls THEN 'maxCallsPerDay' ELSE 'maxCostUsdPerDay' END)
   ON CONFLICT DO NOTHING;
  END IF;
  RETURN NULL;
 END IF;
 INSERT INTO durai_context.session_cap_event(application_id,day,cap,conversation_id)
 SELECT app,today,'maxCallsPerConversationPerDay',p.conversation_id
 FROM durai_context.session_pending p JOIN durai_context.session_spend s
  ON (s.application_id,s.conversation_id,s.day)=(p.application_id,p.conversation_id,today)
 WHERE p.application_id=app AND p.target_turn>p.covered_turn AND p.due_at<=clock_timestamp()
  AND (p.lease_until IS NULL OR p.lease_until<=clock_timestamp()) AND s.calls>=p_max_conversation_calls
 ON CONFLICT DO NOTHING;
 SELECT * INTO item FROM durai_context.session_pending
 WHERE application_id=app AND target_turn>covered_turn AND due_at<=clock_timestamp()
  AND (lease_until IS NULL OR lease_until<=clock_timestamp())
  AND NOT EXISTS(SELECT 1 FROM durai_context.session_spend s WHERE s.application_id=app
   AND s.conversation_id=session_pending.conversation_id AND s.day=today AND s.calls>=p_max_conversation_calls)
 ORDER BY due_at,conversation_id LIMIT 1 FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF item.lease_until IS NOT NULL THEN
  UPDATE durai_context.session_attempt SET state='lost',finished_at=clock_timestamp()
  WHERE application_id=app AND conversation_id=item.conversation_id AND generation=item.generation;
 END IF;
 WITH bounded AS (
  SELECT turn_number,role,eligible_text,plan_id,digest, token_count
  FROM durai_context.session_turn WHERE application_id=app AND conversation_id=item.conversation_id
   AND turn_number>item.covered_turn AND turn_number<=item.target_turn ORDER BY turn_number LIMIT p_max_turns
 ), sized AS (
  SELECT *,sum(token_count) OVER(ORDER BY turn_number) AS total FROM bounded
 )
 SELECT max(turn_number),coalesce(sum(token_count),0),
  jsonb_agg(jsonb_build_object('turn',turn_number,'role',role,'text',eligible_text,'planId',plan_id,'digest',digest)
    ORDER BY turn_number)
 INTO through_turn,source_tokens,source_rows FROM sized
 WHERE total<=p_max_tokens;
 SELECT body INTO prior_summary FROM durai_context.session_summary WHERE application_id=app
  AND conversation_id=item.conversation_id AND revision=item.head_revision;
 UPDATE durai_context.session_pending SET generation=generation+1,
  lease_until=clock_timestamp()+make_interval(secs=>p_lease_seconds),
  claimed_turn=through_turn,claimed_tokens=source_tokens
 WHERE application_id=app AND conversation_id=item.conversation_id RETURNING * INTO item;
 INSERT INTO durai_context.session_attempt(application_id,conversation_id,generation,first_turn,last_turn,state)
 VALUES(app,item.conversation_id,item.generation,item.covered_turn+1,through_turn,'running');
 INSERT INTO durai_context.session_spend AS spend(application_id,conversation_id,day,calls)
 VALUES(app,item.conversation_id,today,1)
 ON CONFLICT(application_id,conversation_id,day) DO UPDATE SET calls=spend.calls+1;
 RETURN jsonb_build_object('conversationId',item.conversation_id,'generation',item.generation,'revision',item.head_revision,
  'firstTurn',item.covered_turn+1,'lastTurn',through_turn,'summary',prior_summary,'turns',source_rows);
END $$;

CREATE FUNCTION durai_context.finish_session_summary(p_conversation text,p_generation bigint,p_revision bigint,
 p_outcome text,p_body jsonb,p_profile jsonb,p_max_attempts integer DEFAULT 3) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE app text:=durai_core.application_id(); item durai_context.session_pending; inputs jsonb;
 outcome text:=p_outcome; attempts bigint;
BEGIN
 IF p_outcome IS NULL OR p_outcome NOT IN ('succeeded','failed','dead') OR p_max_attempts IS NULL OR p_max_attempts<1 THEN
  RAISE EXCEPTION 'Invalid session outcome' USING ERRCODE='22023';
 END IF;
 PERFORM 1 FROM durai_context.session_conversation
 WHERE application_id=app AND conversation_id=p_conversation FOR UPDATE;
 SELECT * INTO item FROM durai_context.session_pending
 WHERE application_id=app AND conversation_id=p_conversation FOR UPDATE;
 IF NOT FOUND OR item.generation IS DISTINCT FROM p_generation OR item.head_revision IS DISTINCT FROM p_revision OR
    item.lease_until IS NULL OR item.lease_until<=clock_timestamp() THEN
  RAISE EXCEPTION 'The session summary claim is stale' USING ERRCODE='23514';
 END IF;
 IF outcome='failed' THEN
  SELECT count(*) INTO attempts FROM durai_context.session_attempt
  WHERE application_id=app AND conversation_id=p_conversation AND first_turn=item.covered_turn+1
   AND (state IN ('failed','lost','dead') OR generation=item.generation);
  IF attempts>=p_max_attempts THEN outcome:='dead'; END IF;
 END IF;
 IF outcome='succeeded' THEN
  SELECT jsonb_agg(digest ORDER BY turn_number) INTO inputs FROM durai_context.session_turn
  WHERE application_id=app AND conversation_id=p_conversation AND turn_number>item.covered_turn
   AND turn_number<=item.claimed_turn;
  IF jsonb_array_length(inputs) IS DISTINCT FROM item.claimed_turn-item.covered_turn THEN
   RAISE EXCEPTION 'The session turn range is not contiguous' USING ERRCODE='23514';
  END IF;
  UPDATE durai_context.session_conversation
   SET covered_turn=item.claimed_turn,head_revision=head_revision+1
   WHERE application_id=app AND conversation_id=p_conversation;
  INSERT INTO durai_context.session_summary(application_id,conversation_id,revision,covered_turn,input_digest,body,profile)
  VALUES(app,p_conversation,item.head_revision+1,item.claimed_turn,
   durai_context.value_digest(jsonb_build_array(item.head_revision,inputs)),p_body,p_profile);
  PERFORM pg_notify('durai_session_head','');
 END IF;
 UPDATE durai_context.session_attempt SET state=outcome,finished_at=clock_timestamp()
 WHERE application_id=app AND conversation_id=p_conversation AND generation=item.generation;
 UPDATE durai_context.session_pending SET lease_until=NULL,claimed_turn=NULL,claimed_tokens=NULL,
  covered_turn=CASE WHEN outcome='succeeded' THEN item.claimed_turn ELSE covered_turn END,
  head_revision=CASE WHEN outcome='succeeded' THEN head_revision+1 ELSE head_revision END,
  pending_tokens=CASE WHEN outcome='succeeded' THEN pending_tokens-item.claimed_tokens ELSE pending_tokens END,
  due_at=CASE WHEN outcome='failed' THEN clock_timestamp()+interval '30 seconds'
   WHEN outcome='dead' THEN 'infinity'::timestamptz ELSE due_at END
 WHERE application_id=app AND conversation_id=p_conversation RETURNING * INTO item;
 RETURN jsonb_build_object('state',outcome,'revision',item.head_revision,'coveredTurn',item.covered_turn);
END $$;

CREATE FUNCTION durai_context.record_session_spend(p_conversation text,p_calls integer,p_input bigint,p_output bigint,
 p_cost numeric) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE app text:=durai_core.application_id(); today date:=(clock_timestamp() AT TIME ZONE 'UTC')::date;
BEGIN
 IF app IS NULL OR p_calls IS NULL OR p_calls<0 OR p_input IS NULL OR p_input<0 OR p_output IS NULL OR p_output<0
    OR p_cost IS NULL OR p_cost<0 THEN
  RAISE EXCEPTION 'Invalid session spend' USING ERRCODE='22023';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtext('durai-session-spend'||app));
 INSERT INTO durai_context.session_spend AS spend(application_id,conversation_id,day,calls,input_tokens,output_tokens,cost_usd)
 VALUES(app,p_conversation,today,p_calls,p_input,p_output,p_cost)
 ON CONFLICT(application_id,conversation_id,day) DO UPDATE SET calls=spend.calls+EXCLUDED.calls,
  input_tokens=spend.input_tokens+EXCLUDED.input_tokens,output_tokens=spend.output_tokens+EXCLUDED.output_tokens,
  cost_usd=spend.cost_usd+EXCLUDED.cost_usd;
END $$;

CREATE FUNCTION durai_context.withdraw_session_turn(p_conversation text,p_turn bigint) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE app text:=durai_core.application_id(); item durai_context.session_pending; uncovered_tokens bigint;
BEGIN
 PERFORM 1 FROM durai_context.session_conversation WHERE application_id=app AND conversation_id=p_conversation FOR UPDATE;
 SELECT * INTO item FROM durai_context.session_pending
 WHERE application_id=app AND conversation_id=p_conversation FOR UPDATE;
 UPDATE durai_context.session_turn SET eligible_text=NULL WHERE application_id=app
  AND conversation_id=p_conversation AND turn_number=p_turn AND eligible_text IS NOT NULL;
 IF NOT FOUND THEN RETURN false; END IF;
 IF item.lease_until IS NOT NULL THEN
  UPDATE durai_context.session_attempt SET state='lost',finished_at=clock_timestamp()
  WHERE application_id=app AND conversation_id=p_conversation AND generation=item.generation;
 END IF;
 SELECT sum(token_count) INTO uncovered_tokens FROM durai_context.session_turn
 WHERE application_id=app AND conversation_id=p_conversation;
 UPDATE durai_context.session_conversation SET covered_turn=0,head_revision=head_revision+1
 WHERE application_id=app AND conversation_id=p_conversation;
 UPDATE durai_context.session_pending SET covered_turn=0,head_revision=head_revision+1,pending_tokens=uncovered_tokens,
  lease_until=NULL,claimed_turn=NULL,claimed_tokens=NULL,due_at=clock_timestamp()
 WHERE application_id=app AND conversation_id=p_conversation;
 PERFORM pg_notify('durai_session_head','');
 RETURN true;
END $$;

CREATE FUNCTION durai_context.read_session_head(p_conversation text) RETURNS jsonb
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('conversationId',c.conversation_id,'latestTurn',c.latest_turn,'coveredTurn',c.covered_turn,
  'revision',c.head_revision,'summary',s.body)
 FROM durai_context.session_conversation c
 LEFT JOIN durai_context.session_summary s ON
  (s.application_id,s.conversation_id,s.revision)=(c.application_id,c.conversation_id,c.head_revision)
 WHERE c.application_id=durai_core.application_id() AND c.conversation_id=p_conversation
$$;

DO $scope$ DECLARE tab text; f regprocedure; BEGIN
 FOREACH tab IN ARRAY ARRAY['session_conversation','session_turn','session_summary','session_pending','session_attempt',
  'session_spend','session_cap_event'] LOOP
  EXECUTE format('ALTER TABLE durai_context.%I OWNER TO durai_context_owner',tab);
  EXECUTE format('ALTER TABLE durai_context.%I ENABLE ROW LEVEL SECURITY',tab);
  EXECUTE format('ALTER TABLE durai_context.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('CREATE POLICY application_scope ON durai_context.%I
   USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id())',tab);
 END LOOP;
 FOR f IN SELECT p.oid::regprocedure FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid
  WHERE n.nspname='durai_context' AND p.proname=ANY(ARRAY['append_session_turn','claim_session_summary',
   'finish_session_summary','read_session_head','withdraw_session_turn','record_session_spend']) LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO durai_context_owner',f);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f);
 END LOOP;
END $scope$;

GRANT SELECT ON durai_context.session_turn TO durai_runtime,durai_foreign,durai_evaluator;
GRANT EXECUTE ON FUNCTION durai_context.read_session_head(text) TO durai_runtime,durai_foreign,durai_evaluator;
GRANT EXECUTE ON FUNCTION durai_context.append_session_turn(text,text,text,text,integer,text,boolean)
 TO durai_runtime,durai_foreign;
GRANT EXECUTE ON FUNCTION durai_context.claim_session_summary(integer,integer,integer,integer,integer,numeric),
 durai_context.record_session_spend(text,integer,bigint,bigint,numeric),
 durai_context.finish_session_summary(text,bigint,bigint,text,jsonb,jsonb,integer),
 durai_context.withdraw_session_turn(text,bigint) TO durai_evaluator;
