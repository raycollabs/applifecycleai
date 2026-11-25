-- Sample: Insert sample S-TRADE-BOOK revision 1 REVIEWED. Update its request_text and read a refusal. Insert revision 2 and read it. The builder reads REVIEWED and PUBLISHED rows only.
DO $owner$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='durai_samples_owner') THEN
  CREATE ROLE durai_samples_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='durai_build_reader') THEN
  CREATE ROLE durai_build_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
 END IF;
END $owner$;
CREATE SCHEMA durai_samples AUTHORIZATION durai_samples_owner;

CREATE TABLE durai_samples.sample_request(
 sample_id text NOT NULL CHECK(sample_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$'),
 revision int NOT NULL CHECK(revision > 0),
 domain text NOT NULL CHECK(domain <> ''),
 scope text NOT NULL CHECK(scope IN ('COMPONENT','APP')),
 parent_sample_id text, sort_order int NOT NULL DEFAULT 0,
 title text NOT NULL CHECK(btrim(title) <> ''),
 request_text text NOT NULL CHECK(btrim(request_text) <> ''),
 tags text[] NOT NULL DEFAULT '{}', language text NOT NULL DEFAULT 'en',
 status text NOT NULL CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')),
 text_digest text NOT NULL CHECK(text_digest ~ '^[a-f0-9]{64}$'),
 author text NOT NULL CHECK(author <> ''), reviewer text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(sample_id, revision),
 CHECK(scope = 'COMPONENT' OR parent_sample_id IS NULL),
 CHECK(status NOT IN ('REVIEWED','PUBLISHED') OR reviewer IS NOT NULL));

CREATE FUNCTION durai_samples.refuse_revision_change() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
 IF NEW.request_text IS DISTINCT FROM OLD.request_text OR NEW.title IS DISTINCT FROM OLD.title
    OR NEW.sample_id IS DISTINCT FROM OLD.sample_id OR NEW.revision IS DISTINCT FROM OLD.revision THEN
  RAISE EXCEPTION 'Sample revision % of % is immutable', OLD.revision, OLD.sample_id USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 RETURN NEW;
END $fn$;
CREATE FUNCTION durai_samples.set_text_digest() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
 NEW.text_digest := encode(sha256(convert_to(NEW.request_text, 'UTF8')), 'hex');
 RETURN NEW;
END $fn$;
CREATE TRIGGER sample_request_digest BEFORE INSERT ON durai_samples.sample_request
 FOR EACH ROW EXECUTE FUNCTION durai_samples.set_text_digest();
CREATE TRIGGER sample_request_immutable BEFORE UPDATE ON durai_samples.sample_request
 FOR EACH ROW EXECUTE FUNCTION durai_samples.refuse_revision_change();

CREATE TABLE durai_samples.sample_use(
 use_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 sample_id text NOT NULL, revision int NOT NULL,
 started_by text NOT NULL CHECK(started_by IN ('USER','BENCHMARK')),
 attempt_id text, started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(sample_id, revision) REFERENCES durai_samples.sample_request,
 CHECK(started_by <> 'BENCHMARK' OR attempt_id IS NOT NULL));

CREATE VIEW durai_samples.published_sample AS
SELECT sample_id, revision, domain, scope, parent_sample_id, sort_order, title, request_text, tags, language, status, text_digest
FROM durai_samples.sample_request WHERE status IN ('REVIEWED','PUBLISHED');

GRANT USAGE ON SCHEMA durai_samples TO durai_evaluator, durai_reviewer, durai_runtime, durai_build_reader;
GRANT SELECT ON durai_samples.sample_request, durai_samples.sample_use TO durai_evaluator, durai_reviewer;
GRANT INSERT ON durai_samples.sample_request, durai_samples.sample_use TO durai_evaluator;
GRANT UPDATE(status, reviewer) ON durai_samples.sample_request TO durai_evaluator;
GRANT SELECT ON durai_samples.published_sample TO durai_runtime, durai_build_reader;
