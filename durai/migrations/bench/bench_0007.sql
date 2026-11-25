-- Sample: Insert review R-1 trade-booking FUNCTION APPROVED. Update its comment. Check refusal. Insert R-2 that supersedes R-1 with CHANGES_REQUESTED. Check the view human_review_latest shows R-2 only. Connect as durai_build_reader. Select human_review. Check permission denied.
CREATE TABLE durai_bench.human_review(
 review_id text PRIMARY KEY DEFAULT 'R-' || replace(gen_random_uuid()::text, '-', '')
  CHECK(review_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
 app_key text NOT NULL CHECK(app_key ~ '^[a-z0-9][a-z0-9-]{1,63}$'),
 app_version text CHECK(app_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
 attempt_id text CHECK(attempt_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
 component_key text CHECK(component_key ~ '^[a-z0-9][a-z0-9-]{1,63}$'),
 aspect text NOT NULL CHECK(aspect IN ('FUNCTION','USABILITY','CORRECTNESS','DESIGN','OVERALL')),
 decision text NOT NULL CHECK(decision IN ('APPROVED','CHANGES_REQUESTED','REJECTED','COMMENT_ONLY')),
 score smallint CHECK(score BETWEEN 0 AND 4),
 comment text NOT NULL DEFAULT '' CHECK(length(comment) <= 4000),
 reviewer text NOT NULL DEFAULT 'user' CHECK(reviewer ~ '^[a-z][a-z0-9_-]{1,31}$'),
 supersedes_review_id text REFERENCES durai_bench.human_review(review_id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((app_version IS NULL) <> (attempt_id IS NULL)));

CREATE UNIQUE INDEX human_review_one_successor ON durai_bench.human_review(supersedes_review_id)
 WHERE supersedes_review_id IS NOT NULL;
CREATE INDEX human_review_target ON durai_bench.human_review(app_key, component_key, aspect, created_at DESC);

CREATE TABLE durai_bench.review_comment(
 comment_id text PRIMARY KEY DEFAULT 'C-' || replace(gen_random_uuid()::text, '-', ''),
 review_id text NOT NULL REFERENCES durai_bench.human_review(review_id),
 author text NOT NULL DEFAULT 'user' CHECK(author ~ '^[a-z][a-z0-9_-]{1,31}$'),
 body text NOT NULL CHECK(btrim(body) <> '' AND length(body) <= 4000),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE FUNCTION durai_bench.human_review_refuse_change() RETURNS trigger LANGUAGE plpgsql AS $refuse$
BEGIN
 RAISE EXCEPTION 'Human review rows are immutable; insert a new row that supersedes the old one' USING ERRCODE = '23001';
END
$refuse$;

CREATE FUNCTION durai_bench.human_review_check_supersede() RETURNS trigger LANGUAGE plpgsql AS $check$
DECLARE old durai_bench.human_review%ROWTYPE;
BEGIN
 IF NEW.supersedes_review_id IS NULL THEN RETURN NEW; END IF;
 SELECT * INTO old FROM durai_bench.human_review WHERE review_id = NEW.supersedes_review_id;
 IF old.app_key IS DISTINCT FROM NEW.app_key OR old.app_version IS DISTINCT FROM NEW.app_version
    OR old.attempt_id IS DISTINCT FROM NEW.attempt_id OR old.component_key IS DISTINCT FROM NEW.component_key
    OR old.aspect IS DISTINCT FROM NEW.aspect THEN
  RAISE EXCEPTION 'A superseding review must keep the same app, target, component and aspect' USING ERRCODE = '23514';
 END IF;
 RETURN NEW;
END
$check$;

CREATE TRIGGER human_review_immutable BEFORE UPDATE OR DELETE ON durai_bench.human_review
 FOR EACH ROW EXECUTE FUNCTION durai_bench.human_review_refuse_change();
CREATE TRIGGER human_review_no_truncate BEFORE TRUNCATE ON durai_bench.human_review
 FOR EACH STATEMENT EXECUTE FUNCTION durai_bench.human_review_refuse_change();
CREATE TRIGGER human_review_supersede BEFORE INSERT ON durai_bench.human_review
 FOR EACH ROW EXECUTE FUNCTION durai_bench.human_review_check_supersede();
CREATE TRIGGER review_comment_immutable BEFORE UPDATE OR DELETE ON durai_bench.review_comment
 FOR EACH ROW EXECUTE FUNCTION durai_bench.human_review_refuse_change();
CREATE TRIGGER review_comment_no_truncate BEFORE TRUNCATE ON durai_bench.review_comment
 FOR EACH STATEMENT EXECUTE FUNCTION durai_bench.human_review_refuse_change();

CREATE VIEW durai_bench.human_review_latest AS
SELECT DISTINCT ON (h.app_key, h.app_version, h.attempt_id, h.component_key, h.aspect)
       h.review_id, h.app_key, h.app_version, h.attempt_id, h.component_key, h.aspect, h.decision, h.score,
       h.comment, h.reviewer, h.supersedes_review_id, h.created_at
FROM durai_bench.human_review h
WHERE NOT EXISTS(SELECT 1 FROM durai_bench.human_review newer WHERE newer.supersedes_review_id = h.review_id)
ORDER BY h.app_key, h.app_version, h.attempt_id, h.component_key, h.aspect, h.created_at DESC, h.review_id DESC;

REVOKE ALL ON durai_bench.human_review, durai_bench.review_comment, durai_bench.human_review_latest FROM PUBLIC;
GRANT SELECT ON durai_bench.human_review, durai_bench.review_comment, durai_bench.human_review_latest
      TO durai_evaluator, durai_reviewer;
GRANT INSERT ON durai_bench.human_review, durai_bench.review_comment TO durai_evaluator;
