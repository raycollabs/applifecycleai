-- Sample: Insert R-1 APPROVED for trade-booking FUNCTION. Insert R-2 COMMENT_ONLY "Looks slow" later. Check human_review_latest shows R-1 APPROVED, and human_review still holds R-2.
CREATE OR REPLACE VIEW durai_bench.human_review_latest AS
SELECT DISTINCT ON (h.app_key, h.app_version, h.attempt_id, h.component_key, h.aspect)
       h.review_id, h.app_key, h.app_version, h.attempt_id, h.component_key, h.aspect, h.decision, h.score,
       h.comment, h.reviewer, h.supersedes_review_id, h.created_at
FROM durai_bench.human_review h
WHERE h.decision <> 'COMMENT_ONLY'
  AND NOT EXISTS(SELECT 1 FROM durai_bench.human_review newer WHERE newer.supersedes_review_id = h.review_id)
ORDER BY h.app_key, h.app_version, h.attempt_id, h.component_key, h.aspect, h.created_at DESC, h.review_id DESC;
