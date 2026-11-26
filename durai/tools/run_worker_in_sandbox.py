"""Sample: Run python tools/run_worker_in_sandbox.py. Build the worker image. Run coding_worker.repair_workspace inside an OpenShell sandbox. Write receipts/worker-in-openshell.json. Exit 0 when program.py gained comments."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai import provider_config  # noqa: E402
from durai.adapters.sandbox.openshell import OpenShellSandbox  # noqa: E402
from durai.common import digest  # noqa: E402

IMAGE = 'durai-worker-sandbox:1'
SOURCE = b'def total(prices):\n    return sum(prices)\n\n\ndef average(prices):\n    return total(prices) / len(prices)\n'


def build_image():
    # Sample: Copy durai/durai and the Dockerfile to a temp folder. Run docker build. Return the image tag.
    with tempfile.TemporaryDirectory() as folder:
        shutil.copytree(ROOT / 'durai', Path(folder) / 'durai', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy(ROOT / 'tools/worker_sandbox/Dockerfile', folder)
        subprocess.run(['docker', 'build', '-t', IMAGE, folder], check=True)
    return IMAGE


def main():
    # Sample: Create the sandbox with provider deepinfra and egress to api.deepinfra.com. Write the job files. Run the runner. Read program.py back.
    if '--skip-build' not in sys.argv:
        build_image()
    model = provider_config.load('deepinfra-deepseek')['models']['execution']
    port = OpenShellSandbox(image=IMAGE, providers=('deepinfra',), allow=('api.deepinfra.com:443',))
    sandbox = port.create({'memoryMiB': 1024, 'seconds': 600}, 'worker-in-openshell')
    try:
        port.write_file(sandbox, 'job/workspace/program.py', SOURCE)
        port.write_file(sandbox, 'job_runner.py', (ROOT / 'tools/worker_sandbox/job_runner.py').read_bytes())
        result = port.exec(sandbox, ['python3', 'job_runner.py', model], deadline=560)
        after = port.read_file(sandbox, 'job/workspace/program.py')
    finally:
        port.destroy(sandbox, 'worker-in-openshell-end')
    receipt = {'image': IMAGE, 'model': model, 'state': result['state'], 'exitCode': result['exitCode'], 'output': result['output'].decode('utf-8', 'replace')[-1500:],
               'beforeDigest': digest(SOURCE), 'afterDigest': digest(after), 'changed': after != SOURCE, 'sampleComments': after.count(b'# Sample:')}
    receipt['passed'] = bool(result['state'] == 'SUCCEEDED' and receipt['changed'] and receipt['sampleComments'] >= 2)
    (ROOT / 'receipts' / 'worker-in-openshell.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print('PASS' if receipt['passed'] else 'FAIL', json.dumps({k: receipt[k] for k in ('state', 'exitCode', 'changed', 'sampleComments')}))
    print(receipt['output'][-600:])
    return 0 if receipt['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
