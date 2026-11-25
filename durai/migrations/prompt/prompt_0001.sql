-- Sample: Insert template T-IDEMP-DESIGN revision 1. Update its body and read a refusal. Insert revision 2 and read it. The builder reads REVIEWED and PUBLISHED rows only and cannot read enhancement.
DO $owner$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='durai_prompt_owner') THEN
  CREATE ROLE durai_prompt_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='durai_build_reader') THEN
  CREATE ROLE durai_build_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
 END IF;
END $owner$;
CREATE SCHEMA durai_prompt AUTHORIZATION durai_prompt_owner;

CREATE TABLE durai_prompt.component_kind(
 kind_key text PRIMARY KEY CHECK(kind_key ~ '^[a-z][a-z0-9-]{1,63}$'),
 name text NOT NULL CHECK(btrim(name) <> ''),
 description text NOT NULL CHECK(btrim(description) <> ''),
 parent_kind_key text REFERENCES durai_prompt.component_kind(kind_key),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')),
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 author text NOT NULL CHECK(author <> ''), reviewer text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(status NOT IN ('REVIEWED','PUBLISHED') OR reviewer IS NOT NULL));

CREATE TABLE durai_prompt.concept(
 concept_key text NOT NULL CHECK(concept_key ~ '^[a-z][a-z0-9-]{1,79}$'),
 revision int NOT NULL CHECK(revision > 0),
 name text NOT NULL CHECK(btrim(name) <> ''),
 definition text NOT NULL CHECK(btrim(definition) <> ''),
 kind_keys text[] NOT NULL CHECK(cardinality(kind_keys) > 0),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')),
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 author text NOT NULL CHECK(author <> ''), reviewer text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 search tsvector GENERATED ALWAYS AS (to_tsvector('english', name || ' ' || definition)) STORED,
 PRIMARY KEY(concept_key, revision),
 CHECK(status NOT IN ('REVIEWED','PUBLISHED') OR reviewer IS NOT NULL));
CREATE INDEX concept_search ON durai_prompt.concept USING gin(search);

CREATE TABLE durai_prompt.concept_link(
 link_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 from_concept text NOT NULL, to_concept text NOT NULL,
 relation text NOT NULL CHECK(relation IN ('REQUIRES','CONFLICTS','REFINES','TESTED_BY')),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')),
 author text NOT NULL CHECK(author <> ''), reviewer text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(from_concept, to_concept, relation),
 CHECK(from_concept <> to_concept),
 CHECK(status NOT IN ('REVIEWED','PUBLISHED') OR reviewer IS NOT NULL));

CREATE TABLE durai_prompt.template(
 template_key text NOT NULL CHECK(template_key ~ '^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$'),
 revision int NOT NULL CHECK(revision > 0),
 kind_key text NOT NULL REFERENCES durai_prompt.component_kind(kind_key),
 stage text NOT NULL CHECK(stage IN ('DESIGN','PLAN','IMPLEMENTATION','TESTS')),
 body text NOT NULL CHECK(btrim(body) <> ''),
 required_sections text[] NOT NULL CHECK(cardinality(required_sections) > 0),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')),
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 author text NOT NULL CHECK(author <> ''), reviewer text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(template_key, revision), UNIQUE(kind_key, stage, revision),
 CHECK(status NOT IN ('REVIEWED','PUBLISHED') OR reviewer IS NOT NULL));

CREATE TABLE durai_prompt.sample_prompt(
 sample_key text NOT NULL CHECK(sample_key ~ '^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$'),
 revision int NOT NULL CHECK(revision > 0),
 kind_key text NOT NULL REFERENCES durai_prompt.component_kind(kind_key),
 request_text text NOT NULL CHECK(btrim(request_text) <> ''),
 enhanced_example text NOT NULL CHECK(btrim(enhanced_example) <> ''),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')),
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 author text NOT NULL CHECK(author <> ''), reviewer text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(sample_key, revision),
 CHECK(starts_with(enhanced_example, request_text)),
 CHECK(status NOT IN ('REVIEWED','PUBLISHED') OR reviewer IS NOT NULL));

CREATE TABLE durai_prompt.detail_checklist(
 item_key text NOT NULL CHECK(item_key ~ '^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$'),
 revision int NOT NULL CHECK(revision > 0),
 kind_key text NOT NULL REFERENCES durai_prompt.component_kind(kind_key),
 item_text text NOT NULL CHECK(btrim(item_text) <> ''),
 question_text text NOT NULL CHECK(btrim(question_text) <> ''),
 answer_terms text[] NOT NULL DEFAULT '{}',
 weight int NOT NULL DEFAULT 1 CHECK(weight BETWEEN 1 AND 5),
 item_kind text NOT NULL DEFAULT 'ASK' CHECK(item_kind IN ('ASK','OFFER')),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')),
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 author text NOT NULL CHECK(author <> ''), reviewer text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 search tsvector GENERATED ALWAYS AS (to_tsvector('english', item_text)) STORED,
 PRIMARY KEY(item_key, revision),
 CHECK(status NOT IN ('REVIEWED','PUBLISHED') OR reviewer IS NOT NULL));
CREATE INDEX detail_checklist_search ON durai_prompt.detail_checklist USING gin(search);

CREATE TABLE durai_prompt.response_skill(
 skill_key text NOT NULL CHECK(skill_key ~ '^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$'),
 revision int NOT NULL CHECK(revision > 0),
 kind_key text REFERENCES durai_prompt.component_kind(kind_key),
 stage text NOT NULL CHECK(stage IN ('DESIGN','PLAN','IMPLEMENTATION','TESTS','ANY')),
 prompt_text text NOT NULL CHECK(btrim(prompt_text) <> ''),
 required_facts jsonb NOT NULL CHECK(jsonb_typeof(required_facts) = 'array' AND jsonb_array_length(required_facts) > 0),
 status text NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','REVIEWED','PUBLISHED','RETIRED')),
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 author text NOT NULL CHECK(author <> ''), reviewer text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(skill_key, revision),
 CHECK((kind_key IS NULL) = (stage = 'ANY')),
 CHECK(status NOT IN ('REVIEWED','PUBLISHED') OR reviewer IS NOT NULL));
CREATE UNIQUE INDEX response_skill_slot ON durai_prompt.response_skill(coalesce(kind_key, ''), stage, revision);

CREATE TABLE durai_prompt.enhancement(
 enhancement_id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
 request_digest text NOT NULL CHECK(request_digest ~ '^[a-f0-9]{64}$'),
 session_ref text,
 kind_key text NOT NULL CHECK(kind_key <> ''),
 template_digests text[] NOT NULL DEFAULT '{}',
 concept_keys text[] NOT NULL DEFAULT '{}',
 checklist_keys text[] NOT NULL DEFAULT '{}',
 history_refs jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(history_refs) = 'array'),
 skill_digests text[] NOT NULL DEFAULT '{}',
 enhanced_digest text NOT NULL CHECK(enhanced_digest ~ '^[a-f0-9]{64}$'),
 mode text NOT NULL CHECK(mode IN ('RULES','RULES_PLUS_MODEL')),
 model_calls int NOT NULL DEFAULT 0 CHECK(model_calls BETWEEN 0 AND 2),
 cost numeric(12,6) NOT NULL DEFAULT 0 CHECK(cost >= 0),
 sample_id text, build_label text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(mode = 'RULES_PLUS_MODEL' OR (model_calls = 0 AND cost = 0)));

CREATE FUNCTION durai_prompt.set_digest() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE material text;
BEGIN
 IF TG_TABLE_NAME = 'component_kind' THEN material := NEW.name || E'\n' || NEW.description;
 ELSIF TG_TABLE_NAME = 'concept' THEN material := NEW.name || E'\n' || NEW.definition;
 ELSIF TG_TABLE_NAME = 'template' THEN material := NEW.body || E'\n' || array_to_string(NEW.required_sections, E'\n');
 ELSIF TG_TABLE_NAME = 'sample_prompt' THEN material := NEW.request_text || E'\n' || NEW.enhanced_example;
 ELSIF TG_TABLE_NAME = 'detail_checklist' THEN material := NEW.item_text || E'\n' || NEW.question_text;
 ELSE material := NEW.prompt_text;
 END IF;
 NEW.digest := encode(sha256(convert_to(material, 'UTF8')), 'hex');
 RETURN NEW;
END $fn$;

CREATE FUNCTION durai_prompt.revision_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'Prompt rows cannot be deleted' USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 IF (to_jsonb(NEW) - 'status' - 'reviewer' - 'search') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'reviewer' - 'search') THEN
  RAISE EXCEPTION 'Prompt revision is immutable; only status and reviewer may change' USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 RETURN NEW;
END $fn$;

CREATE FUNCTION durai_prompt.enhancement_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
 RAISE EXCEPTION 'Enhancement records are immutable' USING ERRCODE = 'integrity_constraint_violation';
END $fn$;

CREATE FUNCTION durai_prompt.template_check() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE section text;
BEGIN
 FOREACH section IN ARRAY NEW.required_sections LOOP
  IF position(E'\n## ' || section || E'\n' IN E'\n' || NEW.body || E'\n') = 0 THEN
   RAISE EXCEPTION 'Template body lacks required section %', section USING ERRCODE = 'integrity_constraint_violation';
  END IF;
 END LOOP;
 RETURN NEW;
END $fn$;

CREATE TRIGGER kind_digest BEFORE INSERT ON durai_prompt.component_kind FOR EACH ROW EXECUTE FUNCTION durai_prompt.set_digest();
CREATE TRIGGER concept_digest BEFORE INSERT ON durai_prompt.concept FOR EACH ROW EXECUTE FUNCTION durai_prompt.set_digest();
CREATE TRIGGER template_digest BEFORE INSERT ON durai_prompt.template FOR EACH ROW EXECUTE FUNCTION durai_prompt.set_digest();
CREATE TRIGGER sample_prompt_digest BEFORE INSERT ON durai_prompt.sample_prompt FOR EACH ROW EXECUTE FUNCTION durai_prompt.set_digest();
CREATE TRIGGER checklist_digest BEFORE INSERT ON durai_prompt.detail_checklist FOR EACH ROW EXECUTE FUNCTION durai_prompt.set_digest();
CREATE TRIGGER skill_digest BEFORE INSERT ON durai_prompt.response_skill FOR EACH ROW EXECUTE FUNCTION durai_prompt.set_digest();
CREATE TRIGGER template_sections BEFORE INSERT ON durai_prompt.template FOR EACH ROW EXECUTE FUNCTION durai_prompt.template_check();

CREATE TRIGGER kind_immutable BEFORE UPDATE OR DELETE ON durai_prompt.component_kind FOR EACH ROW EXECUTE FUNCTION durai_prompt.revision_guard();
CREATE TRIGGER concept_immutable BEFORE UPDATE OR DELETE ON durai_prompt.concept FOR EACH ROW EXECUTE FUNCTION durai_prompt.revision_guard();
CREATE TRIGGER link_immutable BEFORE UPDATE OR DELETE ON durai_prompt.concept_link FOR EACH ROW EXECUTE FUNCTION durai_prompt.revision_guard();
CREATE TRIGGER template_immutable BEFORE UPDATE OR DELETE ON durai_prompt.template FOR EACH ROW EXECUTE FUNCTION durai_prompt.revision_guard();
CREATE TRIGGER sample_prompt_immutable BEFORE UPDATE OR DELETE ON durai_prompt.sample_prompt FOR EACH ROW EXECUTE FUNCTION durai_prompt.revision_guard();
CREATE TRIGGER checklist_immutable BEFORE UPDATE OR DELETE ON durai_prompt.detail_checklist FOR EACH ROW EXECUTE FUNCTION durai_prompt.revision_guard();
CREATE TRIGGER skill_immutable BEFORE UPDATE OR DELETE ON durai_prompt.response_skill FOR EACH ROW EXECUTE FUNCTION durai_prompt.revision_guard();
CREATE TRIGGER enhancement_immutable BEFORE UPDATE OR DELETE ON durai_prompt.enhancement FOR EACH ROW EXECUTE FUNCTION durai_prompt.enhancement_guard();

CREATE VIEW durai_prompt.published_kind AS
SELECT kind_key, name, description, parent_kind_key, status, digest FROM durai_prompt.component_kind WHERE status IN ('REVIEWED','PUBLISHED');

CREATE VIEW durai_prompt.published_concept AS
SELECT concept_key, revision, name, definition, kind_keys, status, digest, search FROM (
 SELECT DISTINCT ON (concept_key) * FROM durai_prompt.concept WHERE status <> 'DRAFT' ORDER BY concept_key, revision DESC) latest
WHERE status IN ('REVIEWED','PUBLISHED');

CREATE VIEW durai_prompt.published_link AS
SELECT l.link_id, l.from_concept, l.to_concept, l.relation, l.status
FROM durai_prompt.concept_link l
WHERE l.status IN ('REVIEWED','PUBLISHED')
  AND EXISTS(SELECT 1 FROM durai_prompt.published_concept c WHERE c.concept_key = l.from_concept)
  AND EXISTS(SELECT 1 FROM durai_prompt.published_concept c WHERE c.concept_key = l.to_concept);

CREATE VIEW durai_prompt.published_template AS
SELECT template_key, revision, kind_key, stage, body, required_sections, status, digest FROM (
 SELECT DISTINCT ON (kind_key, stage) * FROM durai_prompt.template WHERE status <> 'DRAFT' ORDER BY kind_key, stage, revision DESC) latest
WHERE status IN ('REVIEWED','PUBLISHED');

CREATE VIEW durai_prompt.published_sample_prompt AS
SELECT sample_key, revision, kind_key, request_text, enhanced_example, status, digest FROM (
 SELECT DISTINCT ON (sample_key) * FROM durai_prompt.sample_prompt WHERE status <> 'DRAFT' ORDER BY sample_key, revision DESC) latest
WHERE status IN ('REVIEWED','PUBLISHED');

CREATE VIEW durai_prompt.published_checklist AS
SELECT item_key, revision, kind_key, item_text, question_text, answer_terms, weight, item_kind, status, digest, search FROM (
 SELECT DISTINCT ON (item_key) * FROM durai_prompt.detail_checklist WHERE status <> 'DRAFT' ORDER BY item_key, revision DESC) latest
WHERE status IN ('REVIEWED','PUBLISHED');

CREATE VIEW durai_prompt.published_skill AS
SELECT skill_key, revision, kind_key, stage, prompt_text, required_facts, status, digest FROM (
 SELECT DISTINCT ON (coalesce(kind_key, ''), stage) * FROM durai_prompt.response_skill WHERE status <> 'DRAFT'
 ORDER BY coalesce(kind_key, ''), stage, revision DESC) latest
WHERE status IN ('REVIEWED','PUBLISHED');

GRANT USAGE ON SCHEMA durai_prompt TO durai_evaluator, durai_runtime, durai_build_reader;
GRANT SELECT, INSERT ON durai_prompt.component_kind, durai_prompt.concept, durai_prompt.concept_link, durai_prompt.template,
 durai_prompt.sample_prompt, durai_prompt.detail_checklist, durai_prompt.response_skill, durai_prompt.enhancement TO durai_evaluator;
GRANT UPDATE(status, reviewer) ON durai_prompt.component_kind, durai_prompt.concept, durai_prompt.concept_link, durai_prompt.template,
 durai_prompt.sample_prompt, durai_prompt.detail_checklist, durai_prompt.response_skill TO durai_evaluator;
GRANT SELECT ON durai_prompt.published_kind, durai_prompt.published_concept, durai_prompt.published_link, durai_prompt.published_template,
 durai_prompt.published_sample_prompt, durai_prompt.published_checklist, durai_prompt.published_skill TO durai_runtime, durai_build_reader;
