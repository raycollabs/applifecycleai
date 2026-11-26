"""Sample: Run SandboxContract on each adapter. Check idempotent create, bounded exec, scoped files, no secret names, safe destroy and ordered events."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.adapters.sandbox.docker_runner import DockerSandbox, image_ready  # noqa: E402
from durai.adapters.sandbox.local_process import LocalProcessSandbox  # noqa: E402
from durai.common import digest  # noqa: E402
from durai.ports.errors import InvalidPath, InvalidSpec, NotFound, Unsupported  # noqa: E402


def py(code):
    # Sample: Return the argv for python -I -c "print(200)".
    return ['python', '-I', '-c', code]


class SandboxContract:
    """Sample: Mix into a test case. Set self.port in make_port. Run the goals."""

    def make_port(self):
        raise NotImplementedError

    def setUp(self):
        # Sample: Make the port. Create sandbox S-1 with key K-1. Destroy every sandbox after the test.
        self.port = self.make_port()
        self.sandbox = self.port.create({}, 'K-1')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        # Sample: Destroy each sandbox the test made. A second destroy returns without error.
        for index, sandbox_id in enumerate(list(self.port.boxes)):
            self.port.destroy(sandbox_id, 'cleanup-' + str(index))

    def test_create_twice_same_key_one_sandbox(self):
        # Sample: Create with K-1 again. Get the same id. Create with K-2. Get a new id. Two sandboxes exist.
        self.assertEqual(self.sandbox, self.port.create({}, 'K-1'))
        self.assertNotEqual(self.sandbox, self.port.create({}, 'K-2'))
        self.assertEqual(2, len(self.port.boxes))

    def test_unknown_spec_field_is_refused(self):
        # Sample: Create with {"bogus": 1}. Raise InvalidSpec. Create with seconds 0. Raise InvalidSpec.
        with self.assertRaises(InvalidSpec):
            self.port.create({'bogus': 1}, 'K-3')
        with self.assertRaises(InvalidSpec):
            self.port.create({'seconds': 0}, 'K-4')

    def test_exec_returns_exit_code_and_output(self):
        # Sample: Run print(200). Check SUCCEEDED, exit 0 and output 200.
        result = self.port.exec(self.sandbox, py('print(200)'))
        self.assertEqual(('SUCCEEDED', 0, b'200'), (result['state'], result['exitCode'], result['output'].strip()))

    def test_nonzero_exit_is_failed(self):
        # Sample: Run sys.exit(3). Check FAILED and exit 3.
        result = self.port.exec(self.sandbox, py('import sys; sys.exit(3)'))
        self.assertEqual(('FAILED', 3), (result['state'], result['exitCode']))

    def test_stdin_reaches_the_program(self):
        # Sample: Send {"lots": 2} on stdin. The program prints lots times 100. Check 200.
        code = 'import sys,json; print(json.load(sys.stdin)["lots"] * 100)'
        result = self.port.exec(self.sandbox, py(code), stdin=json.dumps({'lots': 2}).encode())
        self.assertEqual(b'200', result['output'].strip())

    def test_output_over_cap_fails(self):
        # Sample: Cap output at 1000 bytes. Print 5000 bytes. Check FAILED, reason output-limit, truncated, at most 1000 bytes kept.
        sandbox = self.port.create({'outputBytes': 1000}, 'K-5')
        result = self.port.exec(sandbox, py('print("x" * 5000)'))
        self.assertEqual(('FAILED', 'output-limit', True), (result['state'], result['reason'], result['truncated']))
        self.assertLessEqual(len(result['output']), 1000)

    def test_deadline_ends_in_failed(self):
        # Sample: Run a 30 second sleep with deadline 1. Check FAILED and reason deadline. The state is never lost.
        result = self.port.exec(self.sandbox, py('import time; time.sleep(30)'), deadline=1)
        self.assertEqual(('FAILED', 'deadline'), (result['state'], result['reason']))

    def test_written_file_is_visible_to_the_program(self):
        # Sample: Write program.py that prints 200. Run it by path /workspace/program.py or program.py. Check 200 and read-back equals the source.
        source = b'print(200)\n'
        self.port.write_file(self.sandbox, 'program.py', source)
        self.assertEqual(source, self.port.read_file(self.sandbox, 'program.py'))
        result = self.port.exec(self.sandbox, ['python', '-I', 'program.py'])
        self.assertEqual(b'200', result['output'].strip())

    def test_write_outside_scope_refused(self):
        # Sample: Write ../x, /etc/x, C:/x and an empty path. Each raises InvalidPath.
        for path in ('../x', '/etc/x', 'C:/x', 'a/../../x', ''):
            with self.assertRaises(InvalidPath):
                self.port.write_file(self.sandbox, path, b'x')

    def test_read_missing_file_is_not_found(self):
        # Sample: Read missing.py. Raise NotFound.
        with self.assertRaises(NotFound):
            self.port.read_file(self.sandbox, 'missing.py')

    def test_no_secret_names_in_environment(self):
        # Sample: Set DURAI_TEST_TOKEN and DURAI_TEST_API_KEY in this process. The program lists names with API_KEY, TOKEN, PASSWORD, SECRET or DSN. Check an empty list. The image variable GPG_KEY is a public fingerprint and is not a secret.
        for name in ('DURAI_TEST_TOKEN', 'DURAI_TEST_API_KEY'):
            os.environ[name] = 'secret-value'
            self.addCleanup(os.environ.pop, name, None)
        code = 'import os; print([k for k in os.environ if any(x in k for x in ("API_KEY", "TOKEN", "PASSWORD", "SECRET", "DSN"))])'
        self.assertEqual(b'[]', self.port.exec(self.sandbox, py(code))['output'].strip())

    def test_destroy_twice_is_safe_and_ends_use(self):
        # Sample: Destroy S-1 with key D-1 and with D-2. Both return. Exec on S-1 raises NotFound. Destroy of an unknown id raises NotFound.
        self.port.destroy(self.sandbox, 'D-1')
        self.port.destroy(self.sandbox, 'D-2')
        with self.assertRaises(NotFound):
            self.port.exec(self.sandbox, py('print(1)'))
        with self.assertRaises(NotFound):
            self.port.destroy('sbx-unknown', 'D-3')

    def test_events_are_ordered_and_digested(self):
        # Sample: Create, one exec, destroy. Events are CREATED, EXEC_STARTED, EXEC_ENDED, DESTROYED with sequence 1 to 4 and each digest equals the digest of its payload.
        self.port.exec(self.sandbox, py('print(200)'))
        self.port.destroy(self.sandbox, 'D-1')
        events = self.port.events(self.sandbox)
        self.assertEqual(['CREATED', 'EXEC_STARTED', 'EXEC_ENDED', 'DESTROYED'], [event['kind'] for event in events])
        self.assertEqual([1, 2, 3, 4], [event['sequence'] for event in events])
        for event in events:
            self.assertEqual(digest(event['payload']), event['digest'])

    def test_policy_none_accepted_allow_list_applied_or_refused(self):
        # Sample: Apply {"network": "none"}. Return. Apply an allow-list for example.com:443. An adapter that enforces egress applies it. Any other adapter raises Unsupported. A policy is never ignored.
        self.port.apply_policy(self.sandbox, {'network': 'none'}, 'P-1')
        if getattr(self.port, 'SUPPORTS_EGRESS', False):
            self.port.apply_policy(self.sandbox, {'network': {'allow': ['example.com:443']}}, 'P-2')
            self.assertIn('POLICY_APPLIED', [event['kind'] for event in self.port.events(self.sandbox)])
        else:
            with self.assertRaises(Unsupported):
                self.port.apply_policy(self.sandbox, {'network': {'allow': ['example.com:443']}}, 'P-2')

    def test_unsupported_policy_shape_is_refused(self):
        # Sample: Apply {"filesystem": "any"}. Every adapter raises Unsupported.
        with self.assertRaises(Unsupported):
            self.port.apply_policy(self.sandbox, {'filesystem': 'any'}, 'P-3')


class LocalProcessSandboxTests(SandboxContract, unittest.TestCase):
    def make_port(self):
        # Sample: Make the test adapter. It gives no isolation.
        return LocalProcessSandbox()


@unittest.skipUnless(image_ready(), 'Docker or the pinned sandbox image is not available')
class DockerSandboxTests(SandboxContract, unittest.TestCase):
    def make_port(self):
        # Sample: Make the container adapter from the pinned image.
        return DockerSandbox()

    def test_container_boundary(self):
        # Sample: The program reports uid 65534, a read-only root, a read-only workspace, no network and no secret names.
        code = ('import os,socket,json\n'
                'r={"uid":os.getuid()}\n'
                'def w(p):\n'
                '    try:\n        open(p,"w").write("x"); return True\n    except OSError:\n        return False\n'
                'r["root"]=w("/root-test"); r["workspace"]=w("/workspace/x")\n'
                'try:\n    socket.create_connection(("1.1.1.1",53),timeout=2); r["net"]=True\n'
                'except OSError:\n    r["net"]=False\n'
                'print(json.dumps(r))\n')
        out = json.loads(self.port.exec(self.sandbox, py(code))['output'])
        self.assertEqual({'uid': 65534, 'root': False, 'workspace': False, 'net': False}, out)


def openshell_ready():
    # Sample: Return True when DURAI_OPENSHELL_CLI is set and openshell status exits 0 against a running gateway.
    if not os.environ.get('DURAI_OPENSHELL_CLI'):
        return False
    try:
        from durai.adapters.sandbox.openshell import OpenShellSandbox
        code, _, _ = OpenShellSandbox().call(['status'], seconds=30)
        return code == 0
    except Exception:
        return False


OPENSHELL_IMAGE = os.environ.get('DURAI_OPENSHELL_IMAGE', 'durai-spike-sandbox:2')


@unittest.skipUnless(openshell_ready(), 'Set DURAI_OPENSHELL_CLI and start an OpenShell gateway to run these tests')
class OpenShellSandboxTests(SandboxContract, unittest.TestCase):
    def make_port(self):
        # Sample: Make the OpenShell adapter with a local image that holds python3 and curl.
        from durai.adapters.sandbox.openshell import OpenShellSandbox
        return OpenShellSandbox(image=OPENSHELL_IMAGE)

    def test_other_host_is_blocked_then_allowed_by_live_policy(self):
        # Sample: curl example.com gives HTTP 000. Apply the allow-list example.com:443. curl gives HTTP 200 without a restart.
        probe = ['curl', '-s', '-m', '15', '-o', '/dev/null', '-w', '%{http_code}', 'https://example.com']
        self.assertEqual(b'000', self.port.exec(self.sandbox, probe)['output'].strip()[-3:])
        self.port.apply_policy(self.sandbox, {'network': {'allow': ['example.com:443']}}, 'P-9')
        self.assertEqual(b'200', self.port.exec(self.sandbox, probe)['output'].strip()[-3:])

    def test_writes_outside_the_work_folder_are_denied_by_the_kernel(self):
        # Sample: Write /etc/x and /usr/x. Both are denied. Write /tmp/work/x. Allowed.
        code = ('import json\nr={}\n'
                'for p in ("/etc/x", "/usr/x", "/tmp/work/x"):\n'
                '    try:\n        open(p, "w").write("x"); r[p] = True\n    except OSError:\n        r[p] = False\n'
                'print(json.dumps(r))\n')
        self.assertEqual({'/etc/x': False, '/usr/x': False, '/tmp/work/x': True}, json.loads(self.port.exec(self.sandbox, py(code))['output']))

    def test_workload_is_uid_1000_and_unlisted_paths_are_unreadable(self):
        # Sample: id -u prints 1000. Reading /proc/self/status fails with Permission denied, because the policy does not list /proc and Landlock is hard_requirement.
        script = 'id -u; cat /proc/self/status 2>&1 | head -1'
        lines = self.port.exec(self.sandbox, ['sh', '-c', script])['output'].decode().strip().splitlines()
        self.assertEqual('1000', lines[0])
        self.assertIn('Permission denied', lines[1])


if __name__ == '__main__':
    unittest.main()
