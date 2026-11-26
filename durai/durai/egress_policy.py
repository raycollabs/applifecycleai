"""Sample: Build an OpenShell policy that allows api.deepinfra.com:443 for python3 and nothing else. Write it as policy.yaml. Refuse localhost and 169.254.169.254."""
import ipaddress
import json
import re

SCHEMA_VERSION = 1
MODEL_HOST = {'host': 'api.deepinfra.com', 'port': 443}
READ_ONLY = ('/usr', '/lib', '/etc', '/bin', '/sbin', '/opt')
READ_WRITE = ('/tmp',)
BINARIES = ('/usr/bin/python3', '/usr/bin/curl')
ACCESS = ('read-only', 'read-write', 'full')
PROTOCOLS = ('rest', 'tcp')
ENFORCEMENT = ('enforce', 'audit')
HOST_PATTERN = re.compile(r'^(\*\*?\.)?[A-Za-z0-9*]([A-Za-z0-9*.-]*[A-Za-z0-9])?$')


class EgressPolicyError(ValueError):
    """Sample: Ask for host localhost. Raise this error that names the host."""


def parse_allow(item):
    # Sample: "api.deepinfra.com:443" returns host api.deepinfra.com, port 443, access read-write, protocol rest, enforcement enforce. A dict may set access, protocol, enforcement and binaries.
    entry = dict(item) if isinstance(item, dict) else None
    if entry is None:
        host, _, port = str(item).rpartition(':')
        entry = {'host': host, 'port': port}
    host, port = str(entry.get('host', '')).strip().lower(), entry.get('port')
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise EgressPolicyError('Port is not a number for host ' + host) from None
    if not host or not HOST_PATTERN.match(host) or not 1 <= port <= 65535:
        raise EgressPolicyError('Host or port is invalid: ' + host + ':' + str(port))
    if host == 'localhost' or host.endswith('.localhost') or host == 'metadata.google.internal':
        raise EgressPolicyError('Host is not allowed: ' + host)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_loopback or address.is_link_local or address.is_unspecified):
        raise EgressPolicyError('Address is not allowed: ' + host)
    if host.count('.') < 1 or (host.startswith('*') and host.count('.') < 2):
        raise EgressPolicyError('Host needs at least two labels, and a wildcard needs three: ' + host)
    parsed = {'host': host, 'port': port, 'access': entry.get('access', 'read-write'), 'protocol': entry.get('protocol', 'rest'),
              'enforcement': entry.get('enforcement', 'enforce'), 'binaries': list(entry.get('binaries') or BINARIES)}
    for name, allowed in (('access', ACCESS), ('protocol', PROTOCOLS), ('enforcement', ENFORCEMENT)):
        if parsed[name] not in allowed:
            raise EgressPolicyError(name + ' must be one of ' + ', '.join(allowed))
    if not all(isinstance(path, str) and path.startswith('/') for path in parsed['binaries']):
        raise EgressPolicyError('Binary paths must be absolute')
    return parsed


def rule_name(entry):
    # Sample: host api.deepinfra.com port 443 returns egress_api_deepinfra_com_443.
    return 'egress_' + re.sub(r'[^a-z0-9]+', '_', entry['host']) + '_' + str(entry['port'])


def build_policy(allow=(), read_only=READ_ONLY, read_write=READ_WRITE, user='1000'):
    # Sample: No allow entries returns a policy with no network rule, Landlock hard_requirement and user "1000". One entry adds one named rule with its binaries.
    rules = {}
    for item in allow:
        entry = parse_allow(item)
        endpoint = {'host': entry['host'], 'port': entry['port'], 'protocol': entry['protocol']}
        if entry['protocol'] == 'rest':
            endpoint.update(access=entry['access'], enforcement=entry['enforcement'])
        rules[rule_name(entry)] = {'endpoints': [endpoint], 'binaries': [{'path': path} for path in entry['binaries']]}
    return {'version': SCHEMA_VERSION,
            'filesystem_policy': {'include_workdir': False, 'read_only': list(read_only), 'read_write': list(read_write)},
            'landlock': {'compatibility': 'hard_requirement'},
            'process': {'run_as_user': str(user), 'run_as_group': str(user)},
            'network_policies': rules}


def to_yaml(policy):
    # Sample: Dump the policy as indented JSON. JSON is valid YAML, so OpenShell reads it as policy.yaml.
    return json.dumps(policy, indent=2) + '\n'


def endpoint_argument(item):
    # Sample: "example.com:443" returns example.com:443:read-write:rest:enforce for openshell policy update --add-endpoint.
    entry = parse_allow(item)
    return ':'.join([entry['host'], str(entry['port']), entry['access'], entry['protocol'], 'enforce' if entry['enforcement'] == 'enforce' else 'audit'])
