-- Controller events use the same locked per-run sequence as worker checkpoints.
GRANT INSERT ON durai_coordination.event TO durai_release;
