CREATE TABLE durai_context.session_node(
 application_id text NOT NULL,
 conversation_id text NOT NULL,
 level integer NOT NULL CHECK(level BETWEEN 0 AND 8),
 first_turn bigint NOT NULL,
 last_turn bigint NOT NULL,
 body jsonb NOT NULL CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=16384),
 profile jsonb NOT NULL CHECK(jsonb_typeof(profile)='object' AND octet_length(profile::text)<=4096),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(application_id,conversation_id,level,first_turn,last_turn),
 CHECK(first_turn<=last_turn),
 FOREIGN KEY(application_id,conversation_id) REFERENCES durai_context.session_conversation);
CREATE TRIGGER session_node_no_update BEFORE UPDATE ON durai_context.session_node
 FOR EACH ROW EXECUTE FUNCTION durai_core.immutable();

CREATE OR REPLACE FUNCTION durai_context.claim_session_summary(p_lease_seconds integer, p_max_turns integer,p_max_tokens integer,
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
 RETURN jsonb_build_object('conversationId',item.conversation_id,'generation',item.generation,'revision',item.head_revision,
  'firstTurn',item.covered_turn+1,'lastTurn',through_turn,'summary',prior_summary,'turns',source_rows);
END $$;

CREATE OR REPLACE FUNCTION durai_context.withdraw_session_turn(p_conversation text,p_turn bigint) RETURNS boolean
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
 DELETE FROM durai_context.session_node WHERE application_id=app AND conversation_id=p_conversation;
 UPDATE durai_context.session_conversation SET covered_turn=0,head_revision=head_revision+1
 WHERE application_id=app AND conversation_id=p_conversation;
 UPDATE durai_context.session_pending SET covered_turn=0,head_revision=head_revision+1,pending_tokens=uncovered_tokens,
  lease_until=NULL,claimed_turn=NULL,claimed_tokens=NULL,due_at=clock_timestamp()
 WHERE application_id=app AND conversation_id=p_conversation;
 PERFORM pg_notify('durai_session_head','');
 RETURN true;
END $$;

CREATE FUNCTION durai_context.publish_session_segment(p_conversation text,p_generation bigint,p_revision bigint,
 p_node jsonb,p_profile jsonb,p_head jsonb) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE app text:=durai_core.application_id(); item durai_context.session_pending; inputs jsonb;
BEGIN
 PERFORM 1 FROM durai_context.session_conversation
 WHERE application_id=app AND conversation_id=p_conversation FOR UPDATE;
 SELECT * INTO item FROM durai_context.session_pending
 WHERE application_id=app AND conversation_id=p_conversation FOR UPDATE;
 IF NOT FOUND OR item.generation IS DISTINCT FROM p_generation OR item.head_revision IS DISTINCT FROM p_revision OR
    item.lease_until IS NULL OR item.lease_until<=clock_timestamp() THEN
  RAISE EXCEPTION 'The session summary claim is stale' USING ERRCODE='23514';
 END IF;
 SELECT jsonb_agg(digest ORDER BY turn_number) INTO inputs FROM durai_context.session_turn
 WHERE application_id=app AND conversation_id=p_conversation AND turn_number>item.covered_turn
  AND turn_number<=item.claimed_turn;
 IF jsonb_array_length(inputs) IS DISTINCT FROM item.claimed_turn-item.covered_turn THEN
  RAISE EXCEPTION 'The session turn range is not contiguous' USING ERRCODE='23514';
 END IF;
 INSERT INTO durai_context.session_node(application_id,conversation_id,level,first_turn,last_turn,body,profile)
 VALUES(app,p_conversation,0,item.covered_turn+1,item.claimed_turn,p_node,p_profile);
 UPDATE durai_context.session_conversation
  SET covered_turn=item.claimed_turn,head_revision=head_revision+1
  WHERE application_id=app AND conversation_id=p_conversation;
 INSERT INTO durai_context.session_summary(application_id,conversation_id,revision,covered_turn,input_digest,body,profile)
 VALUES(app,p_conversation,item.head_revision+1,item.claimed_turn,
  durai_context.value_digest(jsonb_build_array(item.head_revision,inputs)),p_head,p_profile);
 PERFORM pg_notify('durai_session_head','');
 UPDATE durai_context.session_attempt SET state='succeeded',finished_at=clock_timestamp()
 WHERE application_id=app AND conversation_id=p_conversation AND generation=item.generation;
 UPDATE durai_context.session_pending SET lease_until=NULL,claimed_turn=NULL,claimed_tokens=NULL,
  covered_turn=item.claimed_turn,head_revision=head_revision+1,pending_tokens=pending_tokens-item.claimed_tokens
 WHERE application_id=app AND conversation_id=p_conversation RETURNING * INTO item;
 RETURN jsonb_build_object('state','succeeded','revision',item.head_revision,'coveredTurn',item.covered_turn);
END $$;

CREATE FUNCTION durai_context.publish_session_merge(p_conversation text,p_expected_revision bigint,p_level integer,
 p_first bigint,p_last bigint,p_node jsonb,p_profile jsonb,p_head jsonb) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE app text:=durai_core.application_id(); conv durai_context.session_conversation; children bigint;
 spans bigint;
BEGIN
 SELECT * INTO conv FROM durai_context.session_conversation
 WHERE application_id=app AND conversation_id=p_conversation FOR UPDATE;
 IF NOT FOUND OR conv.head_revision IS DISTINCT FROM p_expected_revision OR p_level IS NULL OR p_level<1 THEN
  RAISE EXCEPTION 'The session merge is stale' USING ERRCODE='23514';
 END IF;
 SELECT count(*),coalesce(sum(last_turn-first_turn+1),0) INTO children,spans FROM durai_context.session_node
 WHERE application_id=app AND conversation_id=p_conversation AND level=p_level-1
  AND first_turn>=p_first AND last_turn<=p_last;
 IF children<2 OR spans<>p_last-p_first+1 THEN
  RAISE EXCEPTION 'The session merge children differ' USING ERRCODE='23514';
 END IF;
 INSERT INTO durai_context.session_node(application_id,conversation_id,level,first_turn,last_turn,body,profile)
 VALUES(app,p_conversation,p_level,p_first,p_last,p_node,p_profile);
 UPDATE durai_context.session_conversation SET head_revision=head_revision+1
 WHERE application_id=app AND conversation_id=p_conversation;
 UPDATE durai_context.session_pending SET head_revision=head_revision+1
 WHERE application_id=app AND conversation_id=p_conversation;
 INSERT INTO durai_context.session_summary(application_id,conversation_id,revision,covered_turn,input_digest,body,profile)
 VALUES(app,p_conversation,conv.head_revision+1,conv.covered_turn,
  durai_context.value_digest(jsonb_build_array(conv.head_revision,p_level,p_first,p_last)),p_head,p_profile);
 PERFORM pg_notify('durai_session_head','');
 RETURN jsonb_build_object('state','merged','revision',conv.head_revision+1,'coveredTurn',conv.covered_turn);
END $$;

CREATE FUNCTION durai_context.read_session_nodes(p_conversation text) RETURNS jsonb
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('revision',c.head_revision,'coveredTurn',c.covered_turn,'nodes',
  coalesce((SELECT jsonb_agg(jsonb_build_object('level',n.level,'first',n.first_turn,'last',n.last_turn,
   'body',n.body,'profile',n.profile) ORDER BY n.first_turn,n.level DESC) FROM durai_context.session_node n
   WHERE n.application_id=c.application_id AND n.conversation_id=c.conversation_id),'[]'::jsonb))
 FROM durai_context.session_conversation c
 WHERE c.application_id=durai_core.application_id() AND c.conversation_id=p_conversation
$$;

CREATE FUNCTION durai_context.read_session_spend(p_conversation text) RETURNS jsonb
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object(
  'conversationCalls',coalesce((SELECT sum(calls) FROM durai_context.session_spend WHERE
   application_id=durai_core.application_id() AND conversation_id=p_conversation
   AND day=(clock_timestamp() AT TIME ZONE 'UTC')::date),0),
  'dayCalls',coalesce((SELECT sum(calls) FROM durai_context.session_spend WHERE
   application_id=durai_core.application_id() AND day=(clock_timestamp() AT TIME ZONE 'UTC')::date),0),
  'dayCost',coalesce((SELECT sum(cost_usd) FROM durai_context.session_spend WHERE
   application_id=durai_core.application_id() AND day=(clock_timestamp() AT TIME ZONE 'UTC')::date),0))
$$;

DO $scope$ DECLARE f regprocedure; BEGIN
 ALTER TABLE durai_context.session_node OWNER TO durai_context_owner;
 ALTER TABLE durai_context.session_node ENABLE ROW LEVEL SECURITY;
 ALTER TABLE durai_context.session_node FORCE ROW LEVEL SECURITY;
 CREATE POLICY application_scope ON durai_context.session_node
  USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id());
 FOR f IN SELECT p.oid::regprocedure FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid
  WHERE n.nspname='durai_context' AND p.proname=ANY(ARRAY['publish_session_segment','publish_session_merge',
   'read_session_nodes','read_session_spend']) LOOP
  EXECUTE format('ALTER FUNCTION %s OWNER TO durai_context_owner',f);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f);
 END LOOP;
END $scope$;

GRANT EXECUTE ON FUNCTION durai_context.read_session_nodes(text),durai_context.read_session_spend(text),
 durai_context.publish_session_segment(text,bigint,bigint,jsonb,jsonb,jsonb),
 durai_context.publish_session_merge(text,bigint,integer,bigint,bigint,jsonb,jsonb,jsonb) TO durai_evaluator;
