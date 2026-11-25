-- Only the trusted release controller may resolve a verified finding.
REVOKE UPDATE ON durai_improvement.finding FROM durai_runtime,durai_foreign;
