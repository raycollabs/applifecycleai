"""Sample: Run EscapeSuiteTests. Check that the suite passes on isolated adapters and fails on a leaky one."""
import os
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.adapters.sandbox.docker_runner import DockerSandbox, image_ready  # noqa: E402
from durai.adapters.sandbox.local_process import LocalProcessSandbox  # noqa: E402
from durai.sandbox_escape import PROBES, parse, run_suite  # noqa: E402


class SuiteShapeTests(unittest.TestCase):
    def test_parse_reads_the_last_json_line(self):
        # Sample: Output "noise\n{"a": 1}\n" returns {"a": 1}. Output with no JSON returns None.
        self.assertEqual({'a': 1}, parse(b'noise\n{"a": 1}\n'))
        self.assertIsNone(parse(b'nothing here'))

    def test_every_probe_returns_argv_and_a_check(self):
        # Sample: Each probe returns a python argv and a callable check.
        for name, make in PROBES.items():
            argv, check = make(()) if name == 'network_blocked' else make(None)
            self.assertEqual(['python', '-I', '-c'], argv[:3], name)
            self.assertTrue(callable(check), name)


class LeakyAdapterTests(unittest.TestCase):
    def test_suite_detects_a_leaky_adapter(self):
        # Sample: Listen on 127.0.0.1. Run network_blocked and secret_names on the local-process adapter. The secret probe passes. The network probe fails, so the receipt state is FAIL.
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(5)
        threading.Thread(target=lambda: [server.accept() for _ in range(3)], daemon=True).start()
        self.addCleanup(server.close)
        os.environ['DURAI_ESCAPE_TOKEN'] = 'x'
        self.addCleanup(os.environ.pop, 'DURAI_ESCAPE_TOKEN', None)
        receipt = run_suite(LocalProcessSandbox(), 'local', ['secret_names', 'network_blocked'], network_targets=[('127.0.0.1', server.getsockname()[1])])
        self.assertEqual('FAIL', receipt['state'])
        self.assertTrue(receipt['probes']['secret_names']['passed'])
        self.assertFalse(receipt['probes']['network_blocked']['passed'])
        self.assertFalse(receipt['isolated'])


@unittest.skipUnless(image_ready(), 'Docker or the pinned sandbox image is not available')
class DockerEscapeTests(unittest.TestCase):
    def test_scripted_escapes_all_blocked_docker(self):
        # Sample: Run every probe on the Docker adapter. Every probe passes and the receipt state is PASS.
        receipt = run_suite(DockerSandbox(), 'docker')
        failed = {name: item['detail'] for name, item in receipt['probes'].items() if not item['passed']}
        self.assertEqual({}, failed)
        self.assertEqual('PASS', receipt['state'])
        self.assertEqual(64, len(receipt['digest']))


def openshell_ready():
    # Sample: Return True when DURAI_OPENSHELL_CLI is set and the gateway answers.
    if not os.environ.get('DURAI_OPENSHELL_CLI'):
        return False
    try:
        from durai.adapters.sandbox.openshell import OpenShellSandbox
        return OpenShellSandbox().call(['status'], seconds=30)[0] == 0
    except Exception:
        return False


@unittest.skipUnless(openshell_ready(), 'Set DURAI_OPENSHELL_CLI and start an OpenShell gateway to run this test')
class OpenShellEscapeTests(unittest.TestCase):
    def test_scripted_escapes_all_blocked_openshell(self):
        # Sample: Run every probe on the OpenShell adapter. Every probe passes and the receipt state is PASS.
        from durai.adapters.sandbox.openshell import OpenShellSandbox
        receipt = run_suite(OpenShellSandbox(image=os.environ.get('DURAI_OPENSHELL_IMAGE', 'durai-spike-sandbox:2')), 'openshell')
        failed = {name: item['detail'] for name, item in receipt['probes'].items() if not item['passed']}
        self.assertEqual({}, failed)


if __name__ == '__main__':
    unittest.main()
