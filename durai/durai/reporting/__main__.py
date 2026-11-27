import argparse
import json

from .contract import PROFILES, select_profile


def main(argv=None):
    # Sample: Select clickhouse with one postgres source. Read profile clickhouse and reason explicit.
    parser = argparse.ArgumentParser(description='Select a Durai reporting profile.')
    parser.add_argument('--profile', choices=PROFILES)
    parser.add_argument('--source', action='append', required=True)
    parser.add_argument('--transfers-allowed', action='store_true')
    args = parser.parse_args(argv)
    result = select_profile(args.profile, sources=args.source, transfers_allowed=args.transfers_allowed)
    print(json.dumps(result, sort_keys=True))
    return 2 if result['profile'] is None else 0


if __name__ == '__main__':
    raise SystemExit(main())
