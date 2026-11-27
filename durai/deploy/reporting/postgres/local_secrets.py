import argparse
import os
import secrets
from pathlib import Path


NAMES = (
    'REPORTING_ADMIN_PASSWORD', 'REPORTING_PUBLISHER_PASSWORD', 'REPORTING_OIL_PASSWORD',
    'REPORTING_GAS_PASSWORD', 'REPORTING_UNASSIGNED_PASSWORD', 'SUPERSET_METADATA_ADMIN_PASSWORD',
    'SUPERSET_METADATA_PASSWORD', 'SUPERSET_SECRET_KEY', 'SUPERSET_ADMIN_PASSWORD',
    'SUPERSET_TEST_PASSWORD',
)


def main():
    parser = argparse.ArgumentParser(description='Make fresh local reporting secrets')
    parser.add_argument('path', type=Path)
    path = parser.parse_args().path
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='ascii') as output:
        output.writelines(f'{name}={secrets.token_hex(32)}\n' for name in NAMES)


if __name__ == '__main__':
    main()
