-- Sample: Insert reviewer score 3 under rubric r1 and score 4 under rubric r2 for one attempt and stage. Check both rows stay. Check reviewer_agreement and baseline_matrix read score 4.
ALTER TABLE durai_bench.build_score DROP CONSTRAINT build_score_pkey;
ALTER TABLE durai_bench.build_score ADD PRIMARY KEY(attempt_id, stage, component_key, reviewer_profile, rubric_revision);

CREATE VIEW durai_bench.build_score_current AS
SELECT DISTINCT ON (attempt_id, stage, component_key, reviewer_profile) *
FROM durai_bench.build_score
ORDER BY attempt_id, stage, component_key, reviewer_profile,
         coalesce(nullif(regexp_replace(rubric_revision, '[^0-9]', '', 'g'), '')::int, 0) DESC, scored_at DESC;

CREATE OR REPLACE VIEW durai_bench.reviewer_agreement AS
WITH items AS (
 SELECT attempt_id, stage,
        max(score) FILTER (WHERE reviewer_profile = 'opus-reviewer') AS score_a,
        max(score) FILTER (WHERE reviewer_profile = 'deepseek-flash-reviewer') AS score_b,
        max(baseline_score) AS baseline_score
 FROM durai_bench.build_score_current GROUP BY attempt_id, stage),
anchors AS (
 SELECT DISTINCT ON (attempt_id, stage) attempt_id, stage, score AS anchor_score
 FROM durai_bench.anchor_mark ORDER BY attempt_id, stage, marked_at DESC, anchor_id DESC)
SELECT i.attempt_id, i.stage, i.score_a, i.score_b, abs(i.score_a - i.score_b) AS difference, i.baseline_score, a.anchor_score,
       CASE WHEN i.score_a IS NULL OR i.score_b IS NULL THEN 'PENDING'
            WHEN abs(i.score_a - i.score_b) < 2 THEN 'AGREE'
            WHEN a.anchor_score IS NULL THEN 'NEEDS_ANCHOR'
            ELSE 'ANCHORED' END AS flag
FROM items i LEFT JOIN anchors a USING(attempt_id, stage);

CREATE OR REPLACE VIEW durai_bench.baseline_matrix AS
SELECT c.app_key, c.app_version, c.component_key, c.category, c.status AS component_status,
       s.stage, a.model_profile_id,
       max(s.baseline_score) AS baseline_score, max(s.score) AS best_build_score, count(DISTINCT a.attempt_id) AS builds
FROM durai_bench.app_component c
LEFT JOIN durai_bench.build_score_current s ON s.app_key = c.app_key AND s.app_version = c.app_version AND s.component_key = c.component_key
LEFT JOIN durai_bench.build_attempt a ON a.attempt_id = s.attempt_id
GROUP BY c.app_key, c.app_version, c.component_key, c.category, c.status, s.stage, a.model_profile_id;

GRANT SELECT ON durai_bench.build_score_current TO durai_evaluator, durai_reviewer;
