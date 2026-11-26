"""Sample: Run python tools/make_publisher_key.py C:/keys/durai. Write durai-publisher-private.hex there and print the public key hex. Refuse a folder inside this repository or an existing key file."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.pack_signing import export_private_key_hex, export_public_key_hex, generate_key_pair  # noqa: E402


def make(folder):
    # Sample: Folder C:/keys/durai gets the private key file. The return value is the public key hex. A folder under the repository raises ValueError.
    folder = Path(folder).resolve()
    if ROOT in folder.parents or folder == ROOT or Path('D:/plans') in folder.parents:
        raise ValueError('Put the key outside the repository and outside D:/plans')
    target = folder / 'durai-publisher-private.hex'
    if target.exists():
        raise ValueError('Key file exists: ' + str(target))
    folder.mkdir(parents=True, exist_ok=True)
    private_key, public_key = generate_key_pair()
    target.write_text(export_private_key_hex(private_key), encoding='utf-8')
    return export_public_key_hex(public_key)


def main():
    # Sample: Print the public key hex and the path of the private key file. Never print the private key.
    folder = sys.argv[1] if len(sys.argv) > 1 else ''
    if not folder:
        print('Usage: python tools/make_publisher_key.py <folder outside the repository>')
        return 2
    print('PUBLIC KEY (safe to share): ' + make(folder))
    print('Private key file: ' + str(Path(folder).resolve() / 'durai-publisher-private.hex') + '  (keep it secret, make a backup)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
