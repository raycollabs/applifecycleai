"""Sample: Run python tools/verify_egress_pack.py D:/plans/durai-egress-starter-signed.json. Check the signature with design/trusted-publishers-v1.json. Print VALID or FAILED. Exit 0 only for VALID."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai import trusted_publishers  # noqa: E402
from durai.egress_presets import validate_body  # noqa: E402
from durai.pack_signing import verify_pack  # noqa: E402


def main(argv=None):
    # Sample: Read the pack. Verify the signature and the one sandbox.egress body. Print the state and the record digest.
    document = json.loads(Path((argv or sys.argv[1:])[0]).read_text(encoding='utf-8'))
    result = verify_pack(document, trusted_publishers.load())
    records = document.get('records', [])
    shape = len(records) == 1 and records[0]['id'] == 'sandbox.egress'
    if shape:
        validate_body(records[0]['body'])
    ok = bool(result.valid and shape)
    print(('VALID ' if ok else 'FAILED ') + str(result.state) + ' ' + str(result.error or '') + ' ' + (records[0]['digest'] if records else ''))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
