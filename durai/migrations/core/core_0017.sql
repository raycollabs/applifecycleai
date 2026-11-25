DO $fix$ DECLARE source text; changed text; BEGIN
 SELECT pg_get_functiondef('durai_context.set_status(text,jsonb,bigint,text)'::regprocedure) INTO source;
 changed := replace(source,
  'result.previous_digest:=saved.previous_digest; result.author:=saved.author; result.created_at:=saved.created_at;',
  'result.previous_digest:=saved.previous_digest; result.author:=saved.author; result.created_at:=saved.created_at;
  result.request_key:=saved.request_key; result.request_digest:=saved.request_digest;
  result.terms:=to_tsvector(''english''::regconfig,saved.value);');
 IF changed=source THEN RAISE EXCEPTION 'Context status source differs'; END IF;
 EXECUTE changed;
END $fix$;
