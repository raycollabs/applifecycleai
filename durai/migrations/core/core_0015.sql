DO $fix$ DECLARE source text; changed text; BEGIN
 SELECT pg_get_functiondef('durai_context.validate_value(text,jsonb)'::regprocedure) INTO source;
 changed := replace(source,
  '(nvapi-[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{12,}|Bearer [A-Za-z0-9._-]{12,})',
  '(^|[^A-Za-z0-9])(nvapi-[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{12,}|Bearer [A-Za-z0-9._-]{12,})');
 IF changed=source THEN RAISE EXCEPTION 'Context credential guard source differs'; END IF;
 EXECUTE changed;
END $fix$;
