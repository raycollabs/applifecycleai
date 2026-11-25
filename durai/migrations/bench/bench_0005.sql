-- Sample: Connect as durai_build_reader. Select design from app_component for booking. Check permission denied. Select request_text from durai_samples.published_sample for S-TRADE-BOOK. Check one row.
REVOKE SELECT ON durai_bench.app_benchmark FROM durai_build_reader, durai_runtime;
REVOKE SELECT(app_key, app_version, component_key, category, goal, design, built, status, test_goal_count, limits)
       ON durai_bench.app_component FROM durai_build_reader;
REVOKE SELECT(app_key, app_version, component_key, sample_id, sample_revision, sample_digest)
       ON durai_bench.component_goal FROM durai_build_reader, durai_runtime;
