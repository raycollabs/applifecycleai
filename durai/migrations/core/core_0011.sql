SET LOCAL search_path=ag_catalog,public;
SELECT ag_catalog.create_vlabel('durai_pack_graph','Policy');
SELECT ag_catalog.create_elabel('durai_pack_graph','SUPERSEDES');
GRANT USAGE ON SCHEMA ag_catalog,durai_pack_graph TO durai_runtime;
GRANT SELECT,INSERT,UPDATE ON durai_pack_graph."Policy",durai_pack_graph."SUPERSEDES" TO durai_runtime;
GRANT SELECT ON durai_pack_graph._ag_label_vertex,durai_pack_graph._ag_label_edge TO durai_runtime;
GRANT USAGE ON SEQUENCE durai_pack_graph."Policy_id_seq",durai_pack_graph."SUPERSEDES_id_seq" TO durai_runtime;
SET LOCAL search_path=public;
