-- Sample: Insert rubric R-DESIGN revision 1. Update its levels. Check refusal. Insert a score of 4 for IMPLEMENTATION with one FAIL goal check. Check refusal. Insert scores 4 and 2 from two reviewers. Read NEEDS_ANCHOR.
CREATE TABLE durai_bench.review_rubric(
 rubric_id text NOT NULL CHECK(rubric_id ~ '^[A-Z0-9][A-Z0-9-]{1,63}$'),
 revision int NOT NULL CHECK(revision > 0),
 stage text NOT NULL CHECK(stage IN ('DESIGN','PLAN','IMPLEMENTATION','TESTS')),
 levels jsonb NOT NULL CHECK(jsonb_typeof(levels) = 'object' AND levels ?& ARRAY['0','1','2','3','4']
   AND jsonb_array_length(jsonb_path_query_array(levels, '$.* ? (@.type() == "string" && @ != "")')) = 5),
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 status text NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','RETIRED')),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), recorder name NOT NULL DEFAULT session_user,
 PRIMARY KEY(rubric_id, revision), UNIQUE(stage, revision));

CREATE TABLE durai_bench.review_skill(
 skill_id text NOT NULL CHECK(skill_id ~ '^[A-Z0-9][A-Z0-9-]{1,63}$'),
 revision int NOT NULL CHECK(revision > 0),
 stage text NOT NULL CHECK(stage IN ('DESIGN','PLAN','IMPLEMENTATION','TESTS')),
 prompt_text text NOT NULL CHECK(btrim(prompt_text) <> ''),
 required_facts jsonb NOT NULL CHECK(jsonb_typeof(required_facts) = 'array' AND jsonb_array_length(required_facts) > 0),
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 status text NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','RETIRED')),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), recorder name NOT NULL DEFAULT session_user,
 PRIMARY KEY(skill_id, revision), UNIQUE(stage, revision));

CREATE FUNCTION durai_bench.review_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'Review revisions cannot be deleted' USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
  RAISE EXCEPTION 'Review revision is immutable; only status may change' USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 RETURN NEW;
END $fn$;
CREATE TRIGGER review_rubric_immutable BEFORE UPDATE OR DELETE ON durai_bench.review_rubric
 FOR EACH ROW EXECUTE FUNCTION durai_bench.review_revision_guard();
CREATE TRIGGER review_skill_immutable BEFORE UPDATE OR DELETE ON durai_bench.review_skill
 FOR EACH ROW EXECUTE FUNCTION durai_bench.review_revision_guard();

CREATE TABLE durai_bench.goal_check(
 check_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 attempt_id text,
 app_key text NOT NULL, app_version text NOT NULL, component_key text NOT NULL,
 goal_index int NOT NULL CHECK(goal_index >= 0),
 verdict text NOT NULL CHECK(verdict IN ('PASS','FAIL','UNTESTABLE')),
 method text NOT NULL CHECK(method IN ('EXECUTED','BASELINE_RECEIPT')),
 check_path text, check_sha256 text CHECK(check_sha256 ~ '^[a-f0-9]{64}$'), output_sha256 text CHECK(output_sha256 ~ '^[a-f0-9]{64}$'),
 guidance_overlap boolean NOT NULL DEFAULT false,
 reviewer text NOT NULL CHECK(reviewer <> ''),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(attempt_id, app_key, app_version) REFERENCES durai_bench.build_attempt(attempt_id, app_key, app_version),
 FOREIGN KEY(app_key, app_version, component_key) REFERENCES durai_bench.app_component,
 CHECK((method = 'BASELINE_RECEIPT') = (attempt_id IS NULL)),
 CHECK(method = 'BASELINE_RECEIPT' OR (check_sha256 IS NOT NULL AND output_sha256 IS NOT NULL AND check_path IS NOT NULL)),
 CHECK(method = 'EXECUTED' OR check_sha256 IS NOT NULL));
CREATE UNIQUE INDEX goal_check_once ON durai_bench.goal_check(coalesce(attempt_id, ''), app_key, app_version, component_key, goal_index, reviewer);

CREATE FUNCTION durai_bench.goal_check_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, durai_bench AS $fn$
DECLARE goals int; owner text;
BEGIN
 IF NEW.attempt_id IS NOT NULL THEN
  SELECT component_key INTO owner FROM build_attempt WHERE attempt_id = NEW.attempt_id;
  IF owner IS DISTINCT FROM NEW.component_key THEN
   RAISE EXCEPTION 'Goal check component differs from the component of attempt %', NEW.attempt_id USING ERRCODE = 'integrity_constraint_violation';
  END IF;
 END IF;
 SELECT jsonb_array_length(hidden_goals) INTO goals FROM component_goal
  WHERE app_key = NEW.app_key AND app_version = NEW.app_version AND component_key = NEW.component_key;
 IF goals IS NULL OR NEW.goal_index >= goals THEN
  RAISE EXCEPTION 'Goal index % is outside the hidden goals of %', NEW.goal_index, NEW.component_key USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 RETURN NEW;
END $fn$;
CREATE TRIGGER goal_check_guard BEFORE INSERT ON durai_bench.goal_check
 FOR EACH ROW EXECUTE FUNCTION durai_bench.goal_check_guard();

CREATE TABLE durai_bench.anchor_mark(
 anchor_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 attempt_id text NOT NULL, stage text NOT NULL,
 score smallint NOT NULL CHECK(score BETWEEN 0 AND 4),
 note text NOT NULL DEFAULT '', marker text NOT NULL DEFAULT 'user' CHECK(marker <> ''),
 marked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(attempt_id, stage) REFERENCES durai_bench.build_stage);

CREATE FUNCTION durai_bench.build_score_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, durai_bench AS $fn$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM review_rubric r WHERE r.stage = NEW.stage AND r.status = 'ACTIVE'
               AND NEW.rubric_revision IN (r.revision::text, 'r' || r.revision::text)) THEN
  RAISE EXCEPTION 'Rubric revision % is not loaded for stage %', NEW.rubric_revision, NEW.stage USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 IF NEW.stage = 'IMPLEMENTATION' AND NEW.score = 4
    AND EXISTS(SELECT 1 FROM goal_check g WHERE g.attempt_id = NEW.attempt_id AND g.verdict = 'FAIL') THEN
  RAISE EXCEPTION 'IMPLEMENTATION score 4 is refused: attempt % has a FAIL goal check', NEW.attempt_id USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 RETURN NEW;
END $fn$;
CREATE TRIGGER build_score_guard BEFORE INSERT ON durai_bench.build_score
 FOR EACH ROW EXECUTE FUNCTION durai_bench.build_score_guard();

CREATE VIEW durai_bench.reviewer_agreement AS
WITH items AS (
 SELECT attempt_id, stage,
        max(score) FILTER (WHERE reviewer_profile = 'opus-reviewer') AS score_a,
        max(score) FILTER (WHERE reviewer_profile = 'deepseek-flash-reviewer') AS score_b,
        max(baseline_score) AS baseline_score
 FROM durai_bench.build_score GROUP BY attempt_id, stage),
anchors AS (
 SELECT DISTINCT ON (attempt_id, stage) attempt_id, stage, score AS anchor_score
 FROM durai_bench.anchor_mark ORDER BY attempt_id, stage, marked_at DESC, anchor_id DESC)
SELECT i.attempt_id, i.stage, i.score_a, i.score_b, abs(i.score_a - i.score_b) AS difference, i.baseline_score, a.anchor_score,
       CASE WHEN i.score_a IS NULL OR i.score_b IS NULL THEN 'PENDING'
            WHEN abs(i.score_a - i.score_b) < 2 THEN 'AGREE'
            WHEN a.anchor_score IS NULL THEN 'NEEDS_ANCHOR'
            ELSE 'ANCHORED' END AS flag
FROM items i LEFT JOIN anchors a USING(attempt_id, stage);

GRANT SELECT, INSERT ON durai_bench.review_rubric, durai_bench.review_skill, durai_bench.goal_check, durai_bench.anchor_mark TO durai_evaluator;
GRANT UPDATE(status) ON durai_bench.review_rubric, durai_bench.review_skill TO durai_evaluator;
GRANT SELECT ON durai_bench.review_rubric, durai_bench.review_skill, durai_bench.goal_check, durai_bench.anchor_mark, durai_bench.reviewer_agreement
      TO durai_reviewer;
GRANT SELECT ON durai_bench.reviewer_agreement TO durai_evaluator;
