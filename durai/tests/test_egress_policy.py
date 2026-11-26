"""Sample: Run EgressPolicyTests. Check that the starter policy allows only the model host and refuses unsafe hosts."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.egress_policy import MODEL_HOST, EgressPolicyError, build_policy, endpoint_argument, parse_allow, rule_name, to_yaml  # noqa: E402


class EgressPolicyTests(unittest.TestCase):
    def test_empty_policy_has_no_network_rule(self):
        # Sample: Build with no allow entry. network_policies is empty. Landlock is hard_requirement. User is the string "1000".
        policy = build_policy()
        self.assertEqual({}, policy['network_policies'])
        self.assertEqual('hard_requirement', policy['landlock']['compatibility'])
        self.assertEqual({'run_as_user': '1000', 'run_as_group': '1000'}, policy['process'])

    def test_starter_preset_denies_all_but_model_host(self):
        # Sample: Allow api.deepinfra.com:443. One rule exists. It lists that host and port 443 for python3 and curl.
        policy = build_policy(['%s:%s' % (MODEL_HOST['host'], MODEL_HOST['port'])])
        (name, rule), = policy['network_policies'].items()
        self.assertEqual('egress_api_deepinfra_com_443', name)
        self.assertEqual([('api.deepinfra.com', 443)], [(endpoint['host'], endpoint['port']) for endpoint in rule['endpoints']])
        self.assertEqual(['/usr/bin/python3', '/usr/bin/curl'], [binary['path'] for binary in rule['binaries']])

    def test_unsafe_hosts_are_refused(self):
        # Sample: localhost, 127.0.0.1, 169.254.169.254, 0.0.0.0, a bare name and a wildcard with two labels raise EgressPolicyError.
        for host in ('localhost', '127.0.0.1', '169.254.169.254', '0.0.0.0', 'intranet', '*.com', 'a b.example.com'):
            with self.assertRaises(EgressPolicyError, msg=host):
                parse_allow(host + ':443')

    def test_bad_ports_and_words_are_refused(self):
        # Sample: Port 0, port 70000, port abc, access "root" and a relative binary path raise EgressPolicyError.
        for item in ('example.com:0', 'example.com:70000', 'example.com:abc',
                     {'host': 'example.com', 'port': 443, 'access': 'root'},
                     {'host': 'example.com', 'port': 443, 'binaries': ['curl']}):
            with self.assertRaises(EgressPolicyError):
                parse_allow(item)

    def test_yaml_text_round_trips(self):
        # Sample: Dump the policy. Parse the text as JSON. The result equals the policy.
        policy = build_policy(['example.com:443'])
        self.assertEqual(policy, json.loads(to_yaml(policy)))

    def test_endpoint_argument_matches_the_cli_form(self):
        # Sample: example.com:443 returns example.com:443:read-write:rest:enforce. An audit entry ends with audit.
        self.assertEqual('example.com:443:read-write:rest:enforce', endpoint_argument('example.com:443'))
        self.assertEqual('example.com:443:read-only:rest:audit', endpoint_argument({'host': 'example.com', 'port': 443, 'access': 'read-only', 'enforcement': 'audit'}))

    def test_rule_name_is_stable(self):
        # Sample: The same host and port give the same rule name twice.
        entry = parse_allow('Api.Example.com:8443')
        self.assertEqual(rule_name(entry), rule_name(parse_allow('api.example.com:8443')))


if __name__ == '__main__':
    unittest.main()
