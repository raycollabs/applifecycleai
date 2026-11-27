\getenv metadata_password SUPERSET_METADATA_PASSWORD
CREATE ROLE superset_meta LOGIN PASSWORD :'metadata_password';
ALTER DATABASE metadata OWNER TO superset_meta;
REVOKE ALL ON DATABASE metadata FROM PUBLIC;
GRANT CONNECT ON DATABASE metadata TO superset_meta;
