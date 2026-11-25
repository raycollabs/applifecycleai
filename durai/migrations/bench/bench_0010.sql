-- Sample: Add stage UI_UX. Insert a UI_UX score of 3 for an attempt with frontend_stack NONE. Check refusal. Insert a UI_UX score of 1 for the same attempt. Check acceptance. Insert a UI_UX score of 3 for an attempt with frontend_stack NEXTJS_TS. Check acceptance.
ALTER TABLE durai_bench.build_attempt ADD COLUMN frontend_stack text NOT NULL DEFAULT 'NONE' CHECK(frontend_stack IN ('NEXTJS_TS','OTHER','NONE'));

ALTER TABLE durai_bench.build_stage DROP CONSTRAINT build_stage_stage_check;
ALTER TABLE durai_bench.build_stage ADD CONSTRAINT build_stage_stage_check
 CHECK(stage IN ('DESIGN','PLAN','IMPLEMENTATION','TESTS','DEPLOYED','UI_UX'));

ALTER TABLE durai_bench.review_rubric DROP CONSTRAINT review_rubric_stage_check;
ALTER TABLE durai_bench.review_rubric ADD CONSTRAINT review_rubric_stage_check
 CHECK(stage IN ('DESIGN','PLAN','IMPLEMENTATION','TESTS','UI_UX'));

ALTER TABLE durai_bench.review_skill DROP CONSTRAINT review_skill_stage_check;
ALTER TABLE durai_bench.review_skill ADD CONSTRAINT review_skill_stage_check
 CHECK(stage IN ('DESIGN','PLAN','IMPLEMENTATION','TESTS','UI_UX'));

CREATE OR REPLACE FUNCTION durai_bench.build_score_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, durai_bench AS $fn$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM review_rubric r WHERE r.stage = NEW.stage AND r.status = 'ACTIVE'
               AND NEW.rubric_revision IN (r.revision::text, 'r' || r.revision::text)) THEN
  RAISE EXCEPTION 'Rubric revision % is not loaded for stage %', NEW.rubric_revision, NEW.stage USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 IF NEW.stage = 'IMPLEMENTATION' AND NEW.score = 4
    AND EXISTS(SELECT 1 FROM goal_check g WHERE g.attempt_id = NEW.attempt_id AND g.verdict = 'FAIL') THEN
  RAISE EXCEPTION 'IMPLEMENTATION score 4 is refused: attempt % has a FAIL goal check', NEW.attempt_id USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 IF NEW.stage = 'UI_UX' AND NEW.score > 1
    AND NOT EXISTS(SELECT 1 FROM build_attempt a WHERE a.attempt_id = NEW.attempt_id AND a.frontend_stack = 'NEXTJS_TS') THEN
  RAISE EXCEPTION 'UI_UX score % is refused: attempt % is not Next.js with TypeScript', NEW.score, NEW.attempt_id USING ERRCODE = 'integrity_constraint_violation';
 END IF;
 RETURN NEW;
END $fn$;
