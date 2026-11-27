"""Sample: Load the Hermes plugin folder with a fake ctx. A listed call passes. An unlisted call, an error and a timeout each block. Nothing returns approve or modify."""
import importlib.util
import json
import os
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from durai.adapters.governance.local import LocalGovernance

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'integrations/hermes'
sys.path.insert(0, str(FOLDER))
import durai_governance as plugin  # noqa: E402

HAS_YAML = importlib.util.find_spec('yaml') is not None
LISTED = {'url': 'https://api.deepinfra.com/v1/chat/completions'}
UNLISTED = {'url': 'https://example.com/data'}


def fake_secret():
    return 'sk' + '-' + 'sample' + 'x' * 12


class FakeCtx:
    def __init__(self):
        self.hooks = {}
        self.tools = []

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_tool(self, *args, **kwargs):
        self.tools.append(args)


class FixedPort:
    def __init__(self, verdict=None, error=None, delay=0):
        self.verdict, self.error, self.delay, self.receipts = verdict, error, delay, []

    def check_tool_call(self, tool_name, args, workflow_id=None, session_id=None, tool_call_id=None):
        time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.verdict

    def record_result(self, tool_name, args, result, decision=None, model=None, session_id=None, tool_call_id=None):
        self.receipts.append({'tool': tool_name, 'decision': decision})
        return self.receipts[-1]


class PluginTests(unittest.TestCase):
    def setUp(self):
        plugin.set_port(LocalGovernance())
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        os.environ.pop('DURAI_GOVERNANCE', None)
        self.addCleanup(patcher.stop)
        self.addCleanup(plugin.set_port, None)

    @unittest.skipUnless(HAS_YAML, 'PyYAML not installed')
    def test_manifest_matches_registered(self):
        # Sample: Read plugin.yaml. Register with a fake ctx. The declared hooks equal the registered hooks, the declared tools are empty, and the declared env vars equal ENV_VARS.
        import yaml
        manifest = yaml.safe_load((FOLDER / 'durai_governance/plugin.yaml').read_text(encoding='utf-8'))
        ctx = FakeCtx()
        plugin.register(ctx)
        capabilities = manifest['capabilities']
        self.assertEqual(sorted(ctx.hooks), sorted(capabilities['hooks']))
        self.assertEqual(ctx.tools, capabilities['tools'])
        self.assertEqual(sorted(capabilities['env_vars']), sorted(plugin.ENV_VARS))
        self.assertEqual(sorted(ctx.hooks), sorted(plugin.HOOKS))

    def test_listed_call_passes(self):
        # Sample: A call to api.deepinfra.com returns None, so Hermes runs it.
        self.assertIsNone(plugin.pre_tool_call(tool_name='fetch', args=LISTED, session_id='S-1', tool_call_id='C-1', conversation_id='X'))

    def test_denied_call_blocks(self):
        # Sample: A call to example.com returns a block dict with the reason egress-not-listed.
        result = plugin.pre_tool_call(tool_name='fetch', args=UNLISTED, session_id='S-1', tool_call_id='C-2')
        self.assertEqual(result['action'], 'block')
        self.assertIn('egress-not-listed', result['message'])

    def test_gate_wait_blocks_with_gate_id(self):
        # Sample: The port says gate for workflow W-1 and subject D-1. The block message holds W-1/D-1 and asks the agent to retry.
        plugin.set_port(FixedPort({'decision': 'gate', 'reason': 'gate-waiting', 'workflow': 'W-1', 'subjectDigest': 'D-1'}))
        result = plugin.pre_tool_call(tool_name='fetch', args=UNLISTED, session_id='S-1', tool_call_id='C-3')
        self.assertEqual(result['action'], 'block')
        self.assertIn('W-1/D-1', result['message'])

    def test_text_arguments_are_checked(self):
        # Sample: Hermes sends the arguments as JSON text with a URL on example.com. The hook still blocks.
        result = plugin.pre_tool_call(tool_name='fetch', args=json.dumps(UNLISTED), session_id='S-1', tool_call_id='C-14')
        self.assertEqual(result['action'], 'block')

    def test_port_error_blocks(self):
        # Sample: The port raises an error with a key in the text. The hook blocks with governance-error and the text is not in the message.
        plugin.set_port(FixedPort(error=RuntimeError('connection string has ' + fake_secret())))
        result = plugin.pre_tool_call(tool_name='fetch', args=LISTED, session_id='S-1', tool_call_id='C-4')
        self.assertEqual(result['action'], 'block')
        self.assertNotIn(fake_secret(), result['message'])
        self.assertIn('governance-error', result['message'])

    def test_port_build_error_blocks(self):
        # Sample: Building the port raises ImportError because durai is missing. The hook blocks.
        plugin.set_port(None)
        with mock.patch.object(plugin, 'build_port', side_effect=ImportError('no durai')):
            result = plugin.pre_tool_call(tool_name='fetch', args=LISTED, session_id='S-1', tool_call_id='C-5')
        self.assertEqual(result['action'], 'block')

    def test_timeout_blocks(self):
        # Sample: The port sleeps 2 s and the deadline is 0.2 s. The hook returns a block in under 1.5 s.
        plugin.set_port(FixedPort({'decision': 'allow', 'reason': 'egress-listed'}, delay=2))
        with mock.patch.object(plugin, 'DEADLINE_SECONDS', 0.2):
            start = time.monotonic()
            result = plugin.pre_tool_call(tool_name='fetch', args=LISTED, session_id='S-1', tool_call_id='C-6')
        self.assertEqual(result['action'], 'block')
        self.assertLess(time.monotonic() - start, 1.5)

    def test_default_deadline_is_five_seconds_or_less(self):
        # Sample: The default deadline is 5 seconds or less.
        self.assertLessEqual(plugin.DEADLINE_SECONDS, 5.0)

    def test_nothing_returns_approve_or_modify(self):
        # Sample: Feed allow, deny, gate, approve, modify, a missing key and None as port answers. Each result is None or a dict with action block.
        answers = [{'decision': 'allow', 'reason': 'x'}, {'decision': 'deny', 'reason': 'x'}, {'decision': 'gate', 'reason': 'x'},
                   {'decision': 'approve', 'reason': 'x'}, {'decision': 'modify', 'reason': 'x'}, {'reason': 'x'}, None]
        for answer in answers:
            plugin.set_port(FixedPort(answer))
            result = plugin.pre_tool_call(tool_name='fetch', args=LISTED, session_id='S-1', tool_call_id='C-7')
            self.assertTrue(result is None or result.get('action') == 'block', answer)

    def test_unknown_decision_blocks(self):
        # Sample: The port answers with decision approve. The hook does not pass the call. It blocks.
        plugin.set_port(FixedPort({'decision': 'approve', 'reason': 'x'}))
        result = plugin.pre_tool_call(tool_name='fetch', args=LISTED, session_id='S-1', tool_call_id='C-8')
        self.assertEqual(result['action'], 'block')

    def test_block_message_has_no_arguments(self):
        # Sample: A denied call with a key in the URL query. The block message holds neither the URL nor the key.
        args = {'url': 'https://example.com/data?key=' + fake_secret()}
        result = plugin.pre_tool_call(tool_name='fetch', args=args, session_id='S-1', tool_call_id='C-9')
        self.assertNotIn(fake_secret(), result['message'])
        self.assertNotIn('example.com', result['message'])

    def test_off_switch_passes_and_writes_no_receipt(self):
        # Sample: DURAI_GOVERNANCE=off. An unlisted call returns None. The post hook writes no receipt.
        port = LocalGovernance()
        plugin.set_port(port)
        with mock.patch.dict(os.environ, {'DURAI_GOVERNANCE': 'off'}):
            self.assertIsNone(plugin.pre_tool_call(tool_name='fetch', args=UNLISTED, session_id='S-1', tool_call_id='C-10'))
            plugin.post_tool_call(tool_name='fetch', args=UNLISTED, result='ok', session_id='S-1', tool_call_id='C-10')
        self.assertEqual(port.receipts, [])

    def test_post_hook_writes_one_receipt(self):
        # Sample: A listed call finishes. The post hook writes one receipt with the decision allow, digests and the model name.
        port = LocalGovernance()
        plugin.set_port(port)
        plugin.pre_tool_call(tool_name='fetch', args=LISTED, session_id='S-1', tool_call_id='C-11')
        plugin.post_tool_call(tool_name='fetch', args=LISTED, result='body', session_id='S-1', tool_call_id='C-11', model='Beam-1.0-501B-A23B')
        self.assertEqual(len(port.receipts), 1)
        receipt = port.receipts[0]
        self.assertEqual((receipt['decision'], receipt['model']), ('allow', 'Beam-1.0-501B-A23B'))
        self.assertTrue(receipt['argsDigest'] and receipt['resultDigest'])

    def test_receipts_hold_no_secrets(self):
        # Sample: The call args and the result both hold a fake key. The receipt file has one line and holds no key.
        secret = fake_secret()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipts.jsonl'
            with mock.patch.dict(os.environ, {'DURAI_RECEIPT_FILE': str(path)}):
                port = LocalGovernance()
                port.sink = plugin.receipt_sink()
                plugin.set_port(port)
                args = {'url': 'https://api.deepinfra.com/v1/x', 'headers': {'Authorization': 'Bearer ' + secret}}
                plugin.pre_tool_call(tool_name='fetch', args=args, session_id='S-1', tool_call_id='C-12')
                plugin.post_tool_call(tool_name='fetch', args=args, result={'echo': secret}, session_id='S-1', tool_call_id='C-12')
            text = path.read_text(encoding='utf-8')
        self.assertEqual(len(text.strip().splitlines()), 1)
        self.assertNotIn(secret, text)
        self.assertNotIn(secret, json.dumps(port.receipts))

    def test_post_hook_error_does_not_raise(self):
        # Sample: The port raises while recording. The post hook returns None and raises nothing.
        class Broken(FixedPort):
            def record_result(self, *args, **kwargs):
                raise RuntimeError('boom')
        plugin.set_port(Broken({'decision': 'allow', 'reason': 'x'}))
        self.assertIsNone(plugin.post_tool_call(tool_name='fetch', args=LISTED, result='ok', session_id='S-1', tool_call_id='C-13'))

    def test_decision_memory_is_bounded(self):
        # Sample: Remember 1500 calls. The memory keeps at most 1000.
        for number in range(1500):
            plugin.remember('C-' + str(number), 'allow')
        self.assertLessEqual(len(plugin.STATE['decisions']), plugin.MAX_DECISIONS)


class PackagingTests(unittest.TestCase):
    def test_entry_point_declared(self):
        # Sample: Read pyproject.toml. The package is durai-hermes-governance. The entry point group lists durai_governance. The plugin.yaml file is package data.
        data = tomllib.loads((FOLDER / 'pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(data['project']['name'], 'durai-hermes-governance')
        group = data['project']['entry-points']['hermes_agent.plugins']
        self.assertEqual(group['durai_governance'], 'durai_governance')
        self.assertIn('plugin.yaml', data['tool']['setuptools']['package-data']['durai_governance'])
        self.assertEqual(data['project']['dependencies'], [])


if __name__ == '__main__':
    unittest.main()
