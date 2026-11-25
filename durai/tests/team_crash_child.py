"""Sample: Start workflow W-1 with a TEAM step. The implementer finishes. The test writer blocks. The parent test kills this process."""
import json
import sys
import time
from pathlib import Path
from test_workflow_wiring import DATABASE, scripted
from durai.workflow_registry import default_providers
from durai.workflow_runner import CAPABILITY, build_workflow, start_workflow


def blocked(step, context):
    # Sample: Test writer T-1 starts and never returns. The parent kills the process while T-1 holds its lease.
    time.sleep(3600)


def main(scratch, definition):
    # Sample: Read definition D-1. Build the default registry with leaseSeconds 3. Replace the test writer with a blocker. Start W-1.
    spec = json.loads(Path(definition).read_text(encoding='utf-8'))
    providers = default_providers(DATABASE, {'scratch': scratch, 'leaseSeconds': 3}, model_factory=scripted)
    with providers.resolve(CAPABILITY, 'TEAM') as executor:
        executor.members['test-writer'] = blocked
    start_workflow(build_workflow(spec, providers, DATABASE), spec['id'], DATABASE)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
