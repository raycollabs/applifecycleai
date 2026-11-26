"""Sample: Check the shipped Switchyard config and bad copies. A good file has no problems. An inline key or a public bind is named without repeating the secret."""
import unittest
from pathlib import Path

from durai.gateway_config import check_file, check_text

CONFIG = Path(__file__).resolve().parents[1] / 'design/gateway/switchyard-reflection-deepseek.toml'
GOOD = ('schema_version = 1\n[llm_clients.a]\nformat = "openai_chat"\nbase_url = "https://api.example.com/v1"\napi_key_env = "A_API_KEY"\n'
        '[targets.t]\nid = "m"\nllm_client = "a"\n[routes.r]\nid = "r"\ntype = "passthrough"\ntarget = "t"\n')


class GatewayConfigTests(unittest.TestCase):
    def test_shipped_config_is_clean(self):
        # Sample: Check design/gateway/switchyard-reflection-deepseek.toml. No problems.
        self.assertEqual(check_file(CONFIG), [])

    def test_inline_key_refused_without_echo(self):
        # Sample: Add api_key = "FAKE-VALUE" to a client. A problem names llm_clients.a.api_key and the text does not hold the key.
        problems = check_text(GOOD.replace('api_key_env = "A_API_KEY"', 'api_key_env = "A_API_KEY"\napi_key = "FAKE-VALUE"'))
        self.assertTrue(any('llm_clients.a.api_key' in item for item in problems))
        self.assertFalse(any('FAKE-VALUE' in item for item in problems))

    def test_missing_key_variable_refused(self):
        # Sample: A client with no api_key_env and no forward_auth gets a problem.
        self.assertTrue(check_text(GOOD.replace('api_key_env = "A_API_KEY"\n', '')))

    def test_public_bind_refused(self):
        # Sample: Add bind_address = "0.0.0.0" at the top. A problem says it must bind 127.0.0.1. Value 127.0.0.1 passes.
        self.assertTrue(check_text('bind_address = "0.0.0.0"\n' + GOOD))
        self.assertEqual(check_text('bind_address = "127.0.0.1"\n' + GOOD), [])

    def test_plain_http_to_remote_refused(self):
        # Sample: base_url http://api.example.com is refused. http://127.0.0.1:8000/v1 is allowed for a local server.
        self.assertTrue(check_text(GOOD.replace('https://api.example.com/v1', 'http://api.example.com/v1')))
        self.assertEqual(check_text(GOOD.replace('https://api.example.com/v1', 'http://127.0.0.1:8000/v1')), [])

    def test_lower_case_key_variable_refused(self):
        # Sample: api_key_env = "FAKE-VALUE" is not an upper case variable name and is refused.
        self.assertTrue(check_text(GOOD.replace('A_API_KEY', 'FAKE-VALUE')))


if __name__ == '__main__':
    unittest.main()
