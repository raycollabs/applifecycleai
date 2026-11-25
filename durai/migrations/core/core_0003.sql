-- LangGraph owns its pinned third-party tables in a dedicated local schema.
-- The trading worker login is its sole non-admin user. Foreign callers get no access.
CREATE SCHEMA durai_checkpoints;
GRANT USAGE ON SCHEMA durai_checkpoints TO durai_runtime;

-- AGE remains a derived projection. No client gets a generic graph query surface.
SET LOCAL search_path=ag_catalog,public;
SELECT ag_catalog.create_graph('durai_trading_ontology');
REVOKE ALL ON SCHEMA durai_trading_ontology FROM PUBLIC;
SET LOCAL search_path=public;
