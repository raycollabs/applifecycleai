"""Sample: Run the ceiling with a fake prover. Exit 0 lets the gate open. Exit 1 refuses with the counterexample. Exit 2 fails closed."""
import unittest

from durai.egress_ceiling import CeilingExceeded, EgressCeiling
from durai.egress_gate import EgressGate


class FakeGates:
    def __init__(self):
        self.opened = []

    def propose_subject(self, *args):
        return None

    def open_gate(self, workflow_id, step, subject, question, deadline, database):
        self.opened.append(subject)
        return {'question': question}


def prover(code, text=b''):
    calls = []

    def runner(args, stdin, seconds, cap, env=None):
        calls.append(args)
        return code, text, None
    runner.calls = calls
    return runner


class CeilingTests(unittest.TestCase):
    def test_within_boundary_opens_gate(self):
        # Sample: Prover exit 0 for api.deepinfra.com:443. The gate opens once.
        gates = FakeGates()
        gate = EgressGate(gates, ceiling=EgressCeiling(['api.deepinfra.com:443'], cli=['prover'], runner=prover(0)))
        gate.propose('W-1', 'api.deepinfra.com:443', 100)
        self.assertEqual(len(gates.opened), 1)

    def test_exceeds_boundary_refuses_before_gate(self):
        # Sample: Prover exit 1 for example.com:443. CeilingExceeded with the counterexample. No gate opens.
        gates = FakeGates()
        gate = EgressGate(gates, ceiling=EgressCeiling(['api.deepinfra.com:443'], cli=['prover'], runner=prover(1, b'counterexample: example.com')))
        with self.assertRaises(CeilingExceeded) as caught:
            gate.propose('W-1', 'example.com:443', 100)
        self.assertIn('counterexample', str(caught.exception))
        self.assertEqual(gates.opened, [])

    def test_prover_error_fails_closed(self):
        # Sample: Prover exit 2. CeilingExceeded. No gate opens.
        gates = FakeGates()
        gate = EgressGate(gates, ceiling=EgressCeiling([], cli=['prover'], runner=prover(2, b'boom')))
        with self.assertRaises(CeilingExceeded):
            gate.propose('W-1', 'example.com:443', 100)
        self.assertEqual(gates.opened, [])

    def test_candidate_holds_current_rules_plus_entry(self):
        # Sample: Current rule a.example.com:443 plus entry b.example.com:443. The prover runs once with check and --boundary.
        runner = prover(0)
        EgressCeiling(['a.example.com:443', 'b.example.com:443'], cli=['prover'], current=['a.example.com:443'], runner=runner).check('b.example.com:443')
        self.assertEqual((len(runner.calls), runner.calls[0][1], runner.calls[0][3]), (1, 'check', '--boundary'))

    def test_no_ceiling_keeps_old_behavior(self):
        # Sample: No ceiling. The gate opens.
        gates = FakeGates()
        EgressGate(gates).propose('W-1', 'api.github.com:443', 100)
        self.assertEqual(len(gates.opened), 1)


if __name__ == '__main__':
    unittest.main()
