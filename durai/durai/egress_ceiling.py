"""Sample: The operator boundary allows api.deepinfra.com:443. A proposal for example.com:443 exceeds it. The prover exits 1 and the gate never opens."""
import json
import os
import shlex
import tempfile
from pathlib import Path

from .adapters.sandbox.base import run_bounded
from .adapters.sandbox.openshell import cli_path
from .egress_policy import build_policy, parse_allow, to_yaml
from .ports.errors import ToolMissing

WITHIN, EXCEEDS = 'within_boundary', 'exceeds_boundary'


class CeilingExceeded(Exception):
    pass


class EgressCeiling:
    def __init__(self, boundary_allow, cli=None, current=(), runner=run_bounded, environ=None):
        # Sample: Boundary list api.deepinfra.com:443. Read the prover command from DURAI_PROVER_CLI. An empty boundary allows no host.
        environ = os.environ if environ is None else environ
        command = cli or environ.get('DURAI_PROVER_CLI') or 'openshell-prover'
        self.cli = list(command) if isinstance(command, (list, tuple)) else shlex.split(command, posix=os.name != 'nt')
        self.boundary = [parse_allow(item) for item in boundary_allow]
        self.current = [parse_allow(item) for item in current]
        self.runner, self.environ = runner, dict(environ)

    def check(self, entry):
        # Sample: Entry example.com:443 with boundary api.deepinfra.com:443. Return state exceeds_boundary and the counterexample text. A missing prover raises ToolMissing. An unreadable answer raises CeilingExceeded.
        candidate = self.current + [parse_allow(entry)]
        with tempfile.TemporaryDirectory() as folder:
            files = {}
            for name, allow in (('candidate', candidate), ('boundary', self.boundary)):
                path = Path(folder) / (name + '.yaml')
                path.write_text(to_yaml(build_policy(allow)), encoding='utf-8')
                files[name] = cli_path(str(path), self.cli)
            args = self.cli + ['check', files['candidate'], '--boundary', files['boundary'], '-o', 'json']
            try:
                code, output, _ = self.runner(args, b'', 60, 1048576, env=self.environ)
            except FileNotFoundError:
                raise ToolMissing('The openshell-prover command is not installed: ' + self.cli[0]) from None
        text = output.decode('utf-8', 'replace')
        if code == 0:
            return {'state': WITHIN, 'detail': ''}
        if code == 1:
            return {'state': EXCEEDS, 'detail': text[-800:]}
        raise CeilingExceeded('Prover failed with exit ' + str(code) + ': ' + text[-200:])

    def require(self, entry):
        # Sample: Entry within the boundary returns the check. Entry that exceeds it raises CeilingExceeded with the counterexample.
        result = self.check(entry)
        if result['state'] != WITHIN:
            raise CeilingExceeded('Entry exceeds the operator boundary: ' + result['detail'])
        return result
