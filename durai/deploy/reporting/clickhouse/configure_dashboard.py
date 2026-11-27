import json
import os
from pathlib import PurePosixPath
import sys
from urllib.parse import quote_plus
from zipfile import ZipFile

from flask import g
from superset.app import create_app

from configure_superset import configure_roles, get_user

SCOPES = ('oil', 'gas')


def configure(archive_path):
    app = create_app()
    with app.app_context():
        from superset import security_manager
        from superset.commands.dashboard.importers.v1 import ImportDashboardsCommand

        admin = get_user('admin', security_manager.find_role('Admin'), 'SUPERSET_ADMIN_PASSWORD')
        g.user = admin
        assets = {}
        lookup = {}
        with ZipFile(archive_path) as archive:
            for name in archive.namelist():
                parts = PurePosixPath(name).parts
                asset = json.loads(archive.read(name))
                kind = parts[1]
                if kind == 'databases':
                    scope = PurePosixPath(name).stem
                    if scope not in SCOPES:
                        raise ValueError('Choose an oil or gas database')
                    expected = f'clickhousedb://{scope}_conn@clickhouse:8123/{scope}'
                    if asset['sqlalchemy_uri'] != expected:
                        raise ValueError('The bundle must contain the local scoped connection')
                    password = quote_plus(os.environ[f'DURAI_CH_{scope.upper()}_PASSWORD'])
                    asset['sqlalchemy_uri'] = expected.replace('@', f':{password}@', 1)
                elif kind == 'datasets':
                    lookup[(asset['schema'], 'dataset')] = asset['uuid']
                elif kind == 'dashboards':
                    scope = PurePosixPath(name).stem
                    lookup[(scope, 'dashboard')] = asset['uuid']
                assets['/'.join(parts[1:])] = json.dumps(asset)
        ImportDashboardsCommand(assets, overwrite=True).run()
        configure_roles(lambda scope, kind: lookup[(scope, kind)])


if __name__ == '__main__':
    configure(sys.argv[1])
