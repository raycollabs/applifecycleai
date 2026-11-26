"""Sample: Run python tools/run_escape_suite.py docker. Write receipts/sandbox-escape-docker.json. Exit 0 on PASS and 1 on FAIL."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.sandbox_escape import run_suite  # noqa: E402


def make(name):
    # Sample: docker returns DockerSandbox. openshell returns OpenShellSandbox with the image from DURAI_OPENSHELL_IMAGE. Another name raises ValueError.
    if name == 'docker':
        from durai.adapters.sandbox.docker_runner import DockerSandbox
        return DockerSandbox()
    if name in ('openshell', 'openshell-vm'):
        import os
        from durai.adapters.sandbox.openshell import OpenShellSandbox
        return OpenShellSandbox(image=os.environ.get('DURAI_OPENSHELL_IMAGE', 'durai-spike-sandbox:2'))
    raise ValueError('Adapter must be docker, openshell or openshell-vm')


def main(argv=None):
    # Sample: Run the suite for the named adapter. Print one line per probe. Write the receipt file. Return 0 on PASS.
    name = (argv or sys.argv[1:] or [''])[0]
    receipt = run_suite(make(name), name)
    for probe, item in receipt['probes'].items():
        print(('PASS ' if item['passed'] else 'FAIL ') + probe, json.dumps(item['detail'])[:160])
    target = ROOT / 'receipts' / ('sandbox-escape-' + name + '.json')
    target.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(receipt['state'], target.name)
    return 0 if receipt['state'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
