-- Attach the walkthrough without changing an already-applied function migration.
COMMENT ON FUNCTION durai_engineering.guard_evidence() IS
 'Sample: Record design for a 2-lot trade at lot size 100. Record successful shell smoke. Let the evaluator record observed 2 versus expected 200 shares. Refuse implementation before this business-red evidence. Refuse candidate review without a passing independent evaluation.';
