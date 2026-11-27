import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.reporting.contract import MEASURES, digest, verify_envelope

FIXTURE = ROOT / 'design/reporting/energy_futures_close.json'
ROW_KEY = ('business_date', 'book', 'contract', 'currency')


def expected_rows(fixture):
    # Sample: Read oil revisions 1 and 2 and a failed gas revision. Select oil 2 and gas 1.
    accepted = {}
    seen = {}
    for item in fixture['reports']:
        report = verify_envelope(item)
        key = report['key']
        identity = (key['application'], key['business_date'], key['book'])
        revision_key = (*identity, key['revision'])
        previous_digest = seen.setdefault(revision_key, report['content_sha256'])
        if previous_digest != report['content_sha256']:
            raise ValueError(f'Conflicting report revision: {revision_key}')
        if report['status'] != 'COMPLETE':
            continue
        previous = accepted.get(identity)
        if previous is None or key['revision'] > previous['key']['revision']:
            accepted[identity] = report
    return [row | {'revision': report['key']['revision']} for report in accepted.values()
            for row in report['rows']]


def normalized_rows(rows):
    # Sample: Compare 320.00 USD to 320.000 USD. Check equal decimal values.
    result = {}
    for row in rows:
        key = tuple(row[name] for name in ROW_KEY)
        if key in result:
            raise ValueError(f'Duplicate visible row: {key}')
        measures = tuple(Decimal(str(row[field])) if row[field] is not None else None for field in MEASURES)
        result[key] = (int(row['revision']), measures)
    return result


def verify_rows(fixture, actual, *, identity=None):
    # Sample: Return gas revision 2 from a dashboard. Refuse the result.
    rows = expected_rows(fixture)
    if identity is not None:
        scope = fixture['access'][identity]
        rows = [row for row in rows if row['book'] in scope]
    expected = normalized_rows(rows)
    current = normalized_rows(actual)
    if current != expected:
        wrong = sorted(key for key in expected.keys() | current.keys()
                       if expected.get(key) != current.get(key))
        raise ValueError(f'Report rows differ at {wrong}')
    return {'fixture_sha256': digest(fixture), 'identity': identity, 'visible_rows': len(current), 'status': 'PASS'}


def main():
    # Sample: Read an offering export. Check its row keys and decimal measures.
    parser = argparse.ArgumentParser()
    parser.add_argument('actual', type=Path)
    parser.add_argument('--fixture', type=Path, default=FIXTURE)
    parser.add_argument('--identity')
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_bytes())
    actual = json.loads(args.actual.read_bytes())
    print(json.dumps(verify_rows(fixture, actual, identity=args.identity), sort_keys=True))


if __name__ == '__main__':
    main()
