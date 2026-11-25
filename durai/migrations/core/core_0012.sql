DO $graph_owner$ DECLARE existing_owner record; BEGIN
-- Sample: Run on a database without durai_graph_owner. Create the role with NOLOGIN, NOSUPERUSER, NOBYPASSRLS. Add LOGIN to that role and run again. Refuse with error 42501.
IF EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='durai_graph_owner') THEN
 SELECT r.rolcanlogin AS rolcanlogin, r.rolsuper AS rolsuper, r.rolbypassrls AS rolbypassrls,
  r.rolcreatedb AS rolcreatedb, r.rolcreaterole AS rolcreaterole
 INTO existing_owner FROM pg_catalog.pg_roles r WHERE r.rolname='durai_graph_owner';
 IF existing_owner.rolcanlogin OR existing_owner.rolsuper OR existing_owner.rolbypassrls
  OR existing_owner.rolcreatedb OR existing_owner.rolcreaterole THEN
  RAISE EXCEPTION 'Graph owner role has excess privileges' USING ERRCODE='42501'; END IF;
ELSE
 EXECUTE 'CREATE ROLE durai_graph_owner NOLOGIN NOSUPERUSER NOBYPASSRLS';
END IF;
END $graph_owner$;

CREATE TABLE durai_ontology.application_graph(application_id text PRIMARY KEY, graph_name name NOT NULL UNIQUE);

DO $provision$ DECLARE
 application_row record; table_row record; sequence_row record;
 runtime_login name; graph_name text; BEGIN
-- Sample: Read the principal rows for trading and foreign. Create durai_app_ plus md5(trading) with the Policy and SUPERSEDES labels. Record one APPLICATION_GRAPH_PROVISIONED event for each application.
SET LOCAL search_path=pg_catalog,ag_catalog,pg_temp;
FOR application_row IN SELECT DISTINCT p.application_id AS application_id FROM durai_core.principal p WHERE p.capability='runtime' LOOP
 graph_name := 'durai_app_' || md5(application_row.application_id);
 EXECUTE format('SELECT ag_catalog.create_graph(%L)',graph_name);
 EXECUTE format('SELECT ag_catalog.create_vlabel(%L,%L)',graph_name,'Policy');
 EXECUTE format('SELECT ag_catalog.create_elabel(%L,%L)',graph_name,'SUPERSEDES');
 EXECUTE format('REVOKE ALL ON SCHEMA %I FROM PUBLIC',graph_name);
 EXECUTE format('ALTER SCHEMA %I OWNER TO durai_graph_owner',graph_name);
 FOR table_row IN SELECT c.relname AS table_name FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname=graph_name AND c.relkind='r' LOOP
  EXECUTE format('ALTER TABLE %I.%I OWNER TO durai_graph_owner',graph_name,table_row.table_name);
  EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM PUBLIC',graph_name,table_row.table_name);
 END LOOP;
 FOR sequence_row IN SELECT c.relname AS sequence_name FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname=graph_name AND c.relkind='S' LOOP
  EXECUTE format('ALTER SEQUENCE %I.%I OWNER TO durai_graph_owner',graph_name,sequence_row.sequence_name);
  EXECUTE format('GRANT USAGE ON SEQUENCE %I.%I TO durai_graph_owner',graph_name,sequence_row.sequence_name);
 END LOOP;
 EXECUTE format('GRANT SELECT ON TABLE %I.%I TO durai_graph_owner',graph_name,'_ag_label_vertex');
 EXECUTE format('GRANT SELECT ON TABLE %I.%I TO durai_graph_owner',graph_name,'_ag_label_edge');
 FOR runtime_login IN SELECT DISTINCT q.role_name AS role_name FROM durai_core.principal q
  WHERE q.application_id=application_row.application_id AND q.capability='runtime' LOOP
  EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I',graph_name,runtime_login);
  EXECUTE format('GRANT SELECT ON TABLE %I.%I TO %I',graph_name,'Policy',runtime_login);
  EXECUTE format('GRANT SELECT ON TABLE %I.%I TO %I',graph_name,'SUPERSEDES',runtime_login);
  EXECUTE format('GRANT SELECT ON TABLE %I.%I TO %I',graph_name,'_ag_label_vertex',runtime_login);
  EXECUTE format('GRANT SELECT ON TABLE %I.%I TO %I',graph_name,'_ag_label_edge',runtime_login);
 END LOOP;
 INSERT INTO durai_ontology.application_graph(application_id,graph_name)
  VALUES(application_row.application_id,graph_name) ON CONFLICT DO NOTHING;
 INSERT INTO durai_governance.audit(application_id,action,payload)
  VALUES(application_row.application_id,'APPLICATION_GRAPH_PROVISIONED',
   jsonb_build_object('applicationId',application_row.application_id,'graphName',graph_name));
END LOOP;
END $provision$;

ALTER TABLE durai_ontology.application_graph ENABLE ROW LEVEL SECURITY;
ALTER TABLE durai_ontology.application_graph FORCE ROW LEVEL SECURITY;
CREATE POLICY application_scope ON durai_ontology.application_graph
 USING(application_id=durai_core.application_id()) WITH CHECK(application_id=durai_core.application_id());

GRANT USAGE ON SCHEMA ag_catalog,durai_pack_graph TO durai_foreign,durai_graph_owner;
GRANT SELECT ON durai_pack_graph."Policy",durai_pack_graph."SUPERSEDES",
 durai_pack_graph._ag_label_vertex,durai_pack_graph._ag_label_edge TO durai_foreign,durai_graph_owner;
GRANT INSERT,UPDATE ON durai_pack_graph."Policy" TO durai_graph_owner;
GRANT USAGE ON SEQUENCE durai_pack_graph."Policy_id_seq" TO durai_graph_owner;
GRANT SELECT ON durai_ontology.application_graph TO durai_runtime,durai_foreign,durai_graph_owner;
GRANT USAGE ON SCHEMA durai_core,durai_ontology,durai_governance TO durai_graph_owner;
GRANT EXECUTE ON FUNCTION durai_core.application_id() TO durai_graph_owner;
GRANT SELECT ON durai_ontology.pack,durai_ontology.policy,durai_ontology.binding TO durai_graph_owner;
GRANT INSERT ON durai_governance.audit TO durai_graph_owner;

CREATE FUNCTION durai_ontology.project_application_binding(binding_id text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ag_catalog,pg_temp AS $$
DECLARE login_application text; binding_document jsonb; policy_row record;
 target_graph name; parameters jsonb; projected integer := 0; BEGIN
-- Sample: Project binding B-A as the trading runtime login with 18 policies. Return 18 and record APPLICATION_GRAPH_PROJECTED with bindingId B-A and policyCount 18. Project B-A again. Return 18. Project B-A as the foreign runtime login. Refuse with error 22023.
login_application := durai_core.application_id();
IF login_application IS NULL THEN
 RAISE EXCEPTION 'Login has no application identity' USING ERRCODE='42501'; END IF;
SET LOCAL search_path=pg_catalog,ag_catalog,pg_temp;
PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('durai-application-binding:' || login_application || ':' || binding_id));
SELECT b.document INTO binding_document FROM durai_ontology.binding b
 WHERE b.application_id=login_application AND b.id=binding_id ORDER BY b.revision DESC LIMIT 1;
IF binding_document IS NULL THEN
 RAISE EXCEPTION 'Binding is absent' USING ERRCODE='22023'; END IF;
IF jsonb_typeof(binding_document->'packs') IS DISTINCT FROM 'array' THEN
 RAISE EXCEPTION 'Binding has no pack references' USING ERRCODE='22023'; END IF;
IF jsonb_array_length(binding_document->'packs')=0 THEN
 RAISE EXCEPTION 'Binding has no pack references' USING ERRCODE='22023'; END IF;
IF EXISTS(SELECT 1 FROM jsonb_array_elements(binding_document->'packs') AS pack_reference(reference)
 WHERE coalesce(pack_reference.reference->>'scope','') NOT IN ('*',login_application)) THEN
 RAISE EXCEPTION 'Binding reference scope is outside the login application' USING ERRCODE='22023'; END IF;
IF EXISTS(SELECT 1 FROM jsonb_array_elements(binding_document->'packs') AS pack_reference(reference)
 WHERE NOT EXISTS(SELECT 1 FROM durai_ontology.pack k
  WHERE k.scope=pack_reference.reference->>'scope' AND k.id=pack_reference.reference->>'id'
   AND k.version=pack_reference.reference->>'version' AND k.digest=pack_reference.reference->>'digest')) THEN
 RAISE EXCEPTION 'Canonical pack digest differs from the binding reference' USING ERRCODE='22023'; END IF;
IF EXISTS(SELECT 1 FROM jsonb_array_elements(binding_document->'packs') AS pack_reference(reference)
 WHERE NOT EXISTS(SELECT 1 FROM durai_ontology.policy p
  WHERE p.scope=pack_reference.reference->>'scope' AND p.pack_id=pack_reference.reference->>'id'
   AND p.version=pack_reference.reference->>'version')) THEN
 RAISE EXCEPTION 'Canonical policy rows are empty for a binding pack' USING ERRCODE='22023'; END IF;
IF EXISTS(SELECT 1 FROM jsonb_array_elements(binding_document->'packs') AS pack_reference(reference)
 JOIN durai_ontology.policy p ON p.scope=pack_reference.reference->>'scope'
  AND p.pack_id=pack_reference.reference->>'id' AND p.version=pack_reference.reference->>'version'
 WHERE p.policy_id='' OR p.rule='' OR p.digest !~ '^[a-f0-9]{64}$') THEN
 RAISE EXCEPTION 'Canonical policy row is incomplete' USING ERRCODE='22023'; END IF;
IF EXISTS(
 SELECT 1 FROM jsonb_array_elements(binding_document->'packs') AS refs(reference)
 JOIN durai_ontology.pack k ON k.scope=refs.reference->>'scope'
  AND k.id=refs.reference->>'id' AND k.version=refs.reference->>'version'
 WHERE (SELECT count(*) FROM durai_ontology.policy p
         WHERE p.scope=k.scope AND p.pack_id=k.id AND p.version=k.version) <>
       (SELECT count(DISTINCT definition->>'id') FROM jsonb_array_elements(k.document->'records') AS records(item)
        CROSS JOIN LATERAL jsonb_array_elements(coalesce(item->'body'->'policies','[]'::jsonb)) AS policies(definition)
        WHERE item->>'type'='KNOWLEDGE')) THEN
 RAISE EXCEPTION 'Canonical policy count differs from the pack' USING ERRCODE='22023'; END IF;
FOR policy_row IN
 SELECT DISTINCT p.scope AS scope, p.pack_id AS pack_id, p.version AS version,
  p.policy_id AS policy_id, p.digest AS digest, k.digest AS pack_digest,
  coalesce(k.document->'provenance'->>'forkOf','') AS source_reference
 FROM jsonb_array_elements(binding_document->'packs') AS pack_reference(reference)
 JOIN durai_ontology.pack k ON k.scope=pack_reference.reference->>'scope'
  AND k.id=pack_reference.reference->>'id' AND k.version=pack_reference.reference->>'version'
 JOIN durai_ontology.policy p ON p.scope=k.scope AND p.pack_id=k.id AND p.version=k.version
 ORDER BY p.scope,p.pack_id,p.version,p.policy_id LOOP
 IF policy_row.scope='*' THEN
  target_graph := 'durai_pack_graph';
 ELSE
  SELECT g.graph_name INTO target_graph FROM durai_ontology.application_graph g
   WHERE g.application_id=login_application;
 END IF;
 IF target_graph IS NULL THEN
  RAISE EXCEPTION 'Application graph is absent for the login' USING ERRCODE='22023'; END IF;
 parameters := jsonb_build_object(
  'key',policy_row.scope || ':' || policy_row.pack_id || '@' || policy_row.version || '#' || policy_row.policy_id,
  'digest',policy_row.digest,'packDigest',policy_row.pack_digest,'scope',policy_row.scope,
  'packId',policy_row.pack_id,'packVersion',policy_row.version,
  'policyId',policy_row.policy_id,'sourceReference',policy_row.source_reference);
 EXECUTE format($sql$SELECT * FROM ag_catalog.cypher(%L, $q$ MERGE (p:Policy {key: $key}) SET p.digest = $digest, p.packDigest = $packDigest, p.scope = $scope, p.packId = $packId, p.packVersion = $packVersion, p.policyId = $policyId, p.sourceReference = $sourceReference RETURN p.key $q$, $1::ag_catalog.agtype) AS (key ag_catalog.agtype)$sql$,target_graph)
  USING parameters::text::ag_catalog.agtype;
 projected := projected + 1;
END LOOP;
INSERT INTO durai_governance.audit(application_id,action,payload)
 VALUES(login_application,'APPLICATION_GRAPH_PROJECTED',
  jsonb_build_object('bindingId',binding_id,'policyCount',projected));
RETURN projected;
END $$;

GRANT CREATE ON SCHEMA durai_ontology TO durai_graph_owner;
ALTER FUNCTION durai_ontology.project_application_binding(text) OWNER TO durai_graph_owner;
REVOKE CREATE ON SCHEMA durai_ontology FROM durai_graph_owner;
REVOKE ALL ON FUNCTION durai_ontology.project_application_binding(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION durai_ontology.project_application_binding(text) TO durai_runtime,durai_foreign;
