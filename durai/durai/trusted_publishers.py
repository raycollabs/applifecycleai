"""Sample: Load design/trusted-publishers-v1.json. Return a TrustedKeyList that holds key durai-publisher-2026. A bad key length raises ValueError."""
import json
from pathlib import Path

from .pack_signing import TrustedKeyList, load_public_key_hex

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'design/trusted-publishers-v1.json'


def load(path=None):
    # Sample: The file lists one ACTIVE key. Return a list where is_trusted("durai-publisher-2026") is True and is_trusted("other") is False.
    data = json.loads(Path(path or DEFAULT_PATH).read_text(encoding='utf-8'))
    if data.get('format') != 'durai-trusted-publishers/v1':
        raise ValueError('Unknown trusted publisher file format')
    trusted = TrustedKeyList()
    for item in data['publishers']:
        if item.get('algorithm') != 'ed25519' or len(item['publicKeyHex']) != 64:
            raise ValueError('Publisher key must be a 64 character ed25519 hex key: ' + str(item.get('keyId')))
        trusted.add_key(item['keyId'], load_public_key_hex(item['publicKeyHex']), status=item.get('status', 'ACTIVE'))
    return trusted
