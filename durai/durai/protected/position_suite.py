"""Evaluator-owned synthetic position goals. Never mount this module for a coder."""
import copy


def cases():
    # Sample: Evaluate 2 lots of 100, a 1-lot correction, a short, and invalid units.
    return copy.deepcopy([
        {'goal': 'opening-shares', 'input': {'commands': [{'action': 'book', 'id': 'T-101', 'lots': 2, 'lot_size': 100}]}, 'expected': 200},
        {'goal': 'correction-shares', 'input': {'commands': [{'action': 'book', 'id': 'T-101', 'lots': 2, 'lot_size': 100}, {'action': 'correct', 'id': 'T-101', 'delta_lots': 1}]}, 'expected': 100},
        {'goal': 'short-shares', 'input': {'commands': [{'action': 'book', 'id': 'T-102', 'lots': -2, 'lot_size': 100}]}, 'expected': -200},
        {'goal': 'mixed-net-position', 'input': {'commands': [{'action': 'book', 'id': 'T-101', 'lots': 3, 'lot_size': 50}, {'action': 'book', 'id': 'T-102', 'lots': -1, 'lot_size': 100}]}, 'expected': 50},
        {'goal': 'invalid-lot-size', 'input': {'commands': [{'action': 'book', 'id': 'T-103', 'lots': 2, 'lot_size': 0}]}, 'expectedError': 'INVALID_INPUT'},
        {'goal': 'fractional-lots', 'input': {'commands': [{'action': 'book', 'id': 'T-104', 'lots': 1.5, 'lot_size': 100}]}, 'expectedError': 'INVALID_INPUT'},
        {'goal': 'duplicate-trade-id', 'input': {'commands': [{'action': 'book', 'id': 'T-101', 'lots': 2, 'lot_size': 100}, {'action': 'book', 'id': 'T-101', 'lots': 3, 'lot_size': 100}]}, 'expectedError': 'INVALID_INPUT'},
        {'goal': 'missing-correction-target', 'input': {'commands': [{'action': 'correct', 'id': 'missing', 'delta_lots': 1}]}, 'expectedError': 'INVALID_INPUT'},
    ])


def evaluate(source, runner):
    # Sample: Execute each protected fixture separately. Compare observed quantities.
    from ..common import digest
    suite, checks = cases(), []
    for case in suite:
        result = runner.execute(source, digest(source), case['input'])
        observed = result.get('category') if 'expectedError' in case else result.get('netShares')
        expected = case.get('expectedError', case.get('expected'))
        checks.append({'goal': case['goal'], 'expected': expected, 'observed': observed, 'passed': observed == expected})
    return {'suiteDigest': digest(suite), 'candidateDigest': digest(source), 'checks': checks,
            'verdict': 'PASS' if all(check['passed'] for check in checks) else 'FAIL'}
