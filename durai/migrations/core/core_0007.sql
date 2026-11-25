-- SELECT FOR UPDATE requires UPDATE privilege. The immutable trigger still
-- rejects every actual edit. Grant only the identity column needed for locking.
GRANT UPDATE(id) ON durai_engineering.run TO durai_runtime,durai_evaluator;
