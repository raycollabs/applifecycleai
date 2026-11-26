"""Sample: Run OpenShellAdapterTests with a fake command. Check the telemetry guard, command shapes, deadline mapping and policy calls."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.adapters.sandbox.openshell import OpenShellSandbox, cli_path, telemetry_off  # noqa: E402
from durai.ports.errors import InvalidPath, TelemetryEnabled, ToolMissing, Unsupported  # noqa: E402


class FakeProcess:
    returncode = None
    stdout = None

    def poll(self):
        # Sample: The background create process is still running. Return None.
        return None

    def kill(self):
        # Sample: Record that the process was stopped.
        self.returncode = -9

    def wait(self, timeout=None):
        # Sample: Return at once.
        return self.returncode


class FakeCli:
    """Sample: Record each call. Answer get with Ready. Answer exec with the scripted result."""

    def __init__(self, exec_result=(0, b'200\n', None)):
        self.calls, self.exec_result = [], exec_result

    def __call__(self, command, stdin, seconds, cap, env=None, **options):
        # Sample: Return phase Ready for sandbox get. Return the scripted result for exec. Return exit 0 for anything else.
        self.calls.append((command[1:], stdin, seconds))
        if command[1:3] == ['sandbox', 'get']:
            return 0, b'Phase: Ready', None
        if command[1:3] == ['sandbox', 'exec']:
            return (0, b'', None) if 'mkdir' in command else self.exec_result
        return 0, b'', None


def make(allow=(), result=(0, b'200\n', None), environ=None):
    # Sample: Make the adapter with a fake command, a fake process and an empty environment.
    fake = FakeCli(result)
    port = OpenShellSandbox(cli=['openshell'], allow=allow, environ=environ if environ is not None else {}, runner=fake, spawn=lambda *a, **k: FakeProcess())
    return port, fake


class OpenShellAdapterTests(unittest.TestCase):
    def test_telemetry_flag_forced_off(self):
        # Sample: Environment says true. Construction raises TelemetryEnabled. An empty environment gives flag false in the adapter environment.
        with self.assertRaises(TelemetryEnabled):
            make(environ={'OPENSHELL_TELEMETRY_ENABLED': 'true'})
        port, _ = make()
        self.assertEqual('false', port.env['OPENSHELL_TELEMETRY_ENABLED'])
        self.assertIn('OPENSHELL_TELEMETRY_ENABLED', port.env['WSLENV'])
        self.assertTrue(telemetry_off({}) and telemetry_off({'OPENSHELL_TELEMETRY_ENABLED': 'False'}) and not telemetry_off({'OPENSHELL_TELEMETRY_ENABLED': '1'}))

    def test_missing_command_names_the_tool(self):
        # Sample: The command definitely-not-installed does not exist. Create raises ToolMissing that names it.
        port = OpenShellSandbox(cli=['definitely-not-installed'], environ={})
        with self.assertRaises(ToolMissing) as caught:
            port.create({}, 'K-1')
        self.assertIn('definitely-not-installed', str(caught.exception))
        self.assertEqual({}, port.create_keys)

    def test_windows_paths_become_wsl_paths(self):
        # Sample: With the wsl prefix, C:\Users\a\p.yaml becomes /mnt/c/Users/a/p.yaml. Without it the path is unchanged.
        self.assertEqual('/mnt/c/Users/a/p.yaml', cli_path('C:\\Users\\a\\p.yaml', ['wsl', '--', 'openshell']))
        self.assertEqual('C:\\Users\\a\\p.yaml', cli_path('C:\\Users\\a\\p.yaml', ['openshell']))

    def test_create_starts_a_named_sandbox_with_policy_image_and_sleep(self):
        # Sample: Create runs sandbox create with --name, --from, --policy, --provider and ends with sleep. Then it makes /tmp/work.
        fake = FakeCli()
        spawned = []
        port = OpenShellSandbox(cli=['openshell'], image='img:1', providers=['deepinfra'], environ={}, runner=fake,
                                spawn=lambda command, **k: spawned.append(command) or FakeProcess())
        sandbox = port.create({'memoryMiB': 256}, 'K-1')
        command = spawned[0]
        self.assertEqual(['sandbox', 'create', '--name', sandbox, '--from', 'img:1'], command[1:7])
        self.assertIn('--provider', command)
        self.assertIn('256Mi', command)
        self.assertEqual(['--', 'sleep', '86400'], command[-3:])
        self.assertTrue(any('mkdir' in call[0] for call in fake.calls))

    def test_python_is_sent_as_python3_with_workdir_and_timeout(self):
        # Sample: Run ["python", "-I", "-c", "print(200)"] with deadline 3. The command has python3, --workdir /tmp/work and --timeout 3.
        port, fake = make()
        sandbox = port.create({}, 'K-1')
        port.exec(sandbox, ['python', '-I', '-c', 'print(200)'], deadline=3)
        args = fake.calls[-1][0]
        self.assertEqual(['python3', '-I', '-c', 'print(200)'], args[args.index('--') + 1:])
        self.assertEqual('/tmp/work', args[args.index('--workdir') + 1])
        self.assertEqual('3', args[args.index('--timeout') + 1])

    def test_exit_124_is_a_deadline_failure(self):
        # Sample: The command exits 124. The result is FAILED with reason deadline.
        port, _ = make(result=(124, b'', None))
        result = port.exec(port.create({}, 'K-1'), ['sleep', '9'])
        self.assertEqual(('FAILED', 'deadline'), (result['state'], result['reason']))

    def test_allow_list_calls_policy_update_with_wait_and_binaries(self):
        # Sample: Apply example.com:443. The call is policy update with --add-endpoint example.com:443:read-write:rest:enforce, --wait and two --binary values.
        port, fake = make()
        sandbox = port.create({}, 'K-1')
        port.apply_policy(sandbox, {'network': {'allow': ['example.com:443']}}, 'P-1')
        args = fake.calls[-1][0]
        self.assertEqual(['policy', 'update', sandbox], args[:3])
        self.assertEqual('example.com:443:read-write:rest:enforce', args[args.index('--add-endpoint') + 1])
        self.assertIn('--wait', args)
        self.assertEqual(['/usr/bin/python3', '/usr/bin/curl'], [args[i + 1] for i, part in enumerate(args) if part == '--binary'])

    def test_other_policy_shapes_are_refused(self):
        # Sample: Apply {"filesystem": "x"} and {"network": {"allow": [...], "deny": [...]}}. Both raise Unsupported.
        port, _ = make()
        sandbox = port.create({}, 'K-1')
        for policy in ({'filesystem': 'x'}, {'network': {'allow': ['example.com:443'], 'deny': []}}):
            with self.assertRaises(Unsupported):
                port.apply_policy(sandbox, policy, 'P-1')

    def test_paths_outside_the_work_folder_are_refused_before_any_call(self):
        # Sample: Write ../x. Raise InvalidPath. No exec call is made for it.
        port, fake = make()
        sandbox = port.create({}, 'K-1')
        before = len(fake.calls)
        with self.assertRaises(InvalidPath):
            port.write_file(sandbox, '../x', b'x')
        self.assertEqual(before, len(fake.calls))

    def test_unsafe_allow_entry_is_refused_at_construction(self):
        # Sample: Allow localhost:80 in the constructor. Raise EgressPolicyError.
        from durai.egress_policy import EgressPolicyError
        with self.assertRaises(EgressPolicyError):
            make(allow=['localhost:80'])


if __name__ == '__main__':
    unittest.main()
