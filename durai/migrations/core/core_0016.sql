DO $fix$ DECLARE source text; changed text; BEGIN
 SELECT pg_get_functiondef('durai_context.append(text,text,jsonb,text)'::regprocedure) INTO source;
 changed := replace(source,
  'PERFORM durai_context.check_references(p_plan,COALESCE',
  'IF EXISTS(SELECT 1 FROM durai_context.status_revision WHERE plan_id=p_plan AND request_key=p_request) THEN
   RAISE EXCEPTION ''Request key was used for status'' USING ERRCODE=''23514''; END IF;
  PERFORM durai_context.check_references(p_plan,COALESCE');
 IF changed=source THEN RAISE EXCEPTION 'Context append source differs'; END IF;
 EXECUTE changed;
 SELECT pg_get_functiondef('durai_context.check_references(text,jsonb)'::regprocedure) INTO source;
 changed := replace(source,
  'SELECT EXISTS(SELECT 1 FROM durai_context.record WHERE plan_id=p_plan AND key=ref->>''id'' AND digest=ref->>''digest'') INTO ok;',
  'SELECT EXISTS(SELECT 1 FROM durai_context.record WHERE plan_id=p_plan AND key=ref->>''id'' AND digest=ref->>''digest'')
   OR EXISTS(SELECT 1 FROM durai_context.status_revision WHERE plan_id=p_plan AND ref->>''id''=''status'' AND digest=ref->>''digest'') INTO ok;');
 IF changed=source THEN RAISE EXCEPTION 'Context reference source differs'; END IF;
 EXECUTE changed;
END $fix$;
