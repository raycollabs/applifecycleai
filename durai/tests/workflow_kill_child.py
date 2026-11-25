"""Sample: Start workflow W-2 with steps S-1, S-2, S-3. S-1 appends one effect line. S-2 blocks before its effect. The parent test kills this process."""
import json
import sys
import time
from pathlib import Path
from durai.providers import Providers
from durai.workflow_runner import CAPABILITY, build_workflow, start_workflow

DATABASE = 'durai_test'


def effects_executor(effects, block=()):
    # Sample: Read step S-1 with effects file F-1. Append "S-1" to F-1. Return SUCCEEDED. A step listed in block sleeps and appends nothing.
    def run(step, context):
        # Sample: Run S-2 in block. Sleep one hour. The parent kills the process first. Run S-1. Append one line.
        if step['id'] in block:
            time.sleep(3600)
        with open(effects, 'a', encoding='utf-8') as handle:
            handle.write(step['id'] + '\n')
            handle.flush()
        return {'state': 'SUCCEEDED', 'detail': 'applied ' + step['id']}
    return run


def providers_for(effects, block=()):
    # Sample: Register the effects executor as SINGLE in a new registry. Return it.
    providers = Providers()
    providers.register(CAPABILITY, 'SINGLE', effects_executor(effects, block))
    return providers


def main(effects, definition):
    # Sample: Read definition D-1. Start W-1 with S-2 blocked. S-1 commits its checkpoint. The process waits inside S-2.
    spec = json.loads(Path(definition).read_text(encoding='utf-8'))
    start_workflow(build_workflow(spec, providers_for(effects, ('S-2',)), DATABASE), spec['id'], DATABASE)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
