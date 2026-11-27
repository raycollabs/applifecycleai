import argparse
import os

from flask import g
from superset import db, security_manager
from superset.app import create_app

from dashboard_bundle import SCOPES, assets, identifier


def get_user(username, role, password_key):
    user = security_manager.find_user(username=username)
    if user is None:
        user = security_manager.add_user(username, username, 'Reporting', f'{username}@example.invalid', role,
                                         password=os.environ[password_key])
    return user


def configure_roles(id_for=identifier):
    # Sample: Sign in as oil-reader. Open the oil dashboard. Check the gas dashboard is refused.
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.dashboard import Dashboard

    gamma = security_manager.find_role('Gamma')
    base = [entry for entry in gamma.permissions if entry.view_menu.name not in {'SQLLab', 'SqlLab'} and
            (entry.permission.name, entry.view_menu.name) not in {('can_write', 'Chart'), ('can_write', 'Dashboard')}]
    author_permission = security_manager.find_permission_view_menu('can_write', 'Chart')
    if author_permission is None:
        raise RuntimeError('Superset 6.1 chart write permission is unavailable')

    for scope in SCOPES:
        dataset = db.session.query(SqlaTable).filter_by(uuid=id_for(scope, 'dataset')).one()
        dashboard = db.session.query(Dashboard).filter_by(uuid=id_for(scope, 'dashboard')).one()
        grant = security_manager.add_permission_view_menu('datasource_access', dataset.perm)
        roles = []
        for level in ('reader', 'author'):
            name = f'{scope}-{level}'
            role = security_manager.find_role(name) or security_manager.add_role(name)
            extra = [author_permission] if level == 'author' else []
            role.permissions = list(dict.fromkeys([*base, grant, *extra]))
            roles.append(role)
            get_user(name, role, 'SUPERSET_TEST_PASSWORD')
        dashboard.roles = roles
        if scope == 'oil' and os.environ.get('DURAI_REPORTING_SQL_LAB') == '1':
            configure_oil_analyst(dataset.database)

    role = security_manager.find_role('unscoped') or security_manager.add_role('unscoped')
    role.permissions = base
    get_user('unscoped', role, 'SUPERSET_TEST_PASSWORD')
    db.session.commit()


def configure_oil_analyst(database):
    # Sample: Give oil-analyst SQL Lab access. Keep the gas connection out of SQL Lab.
    base = security_manager.find_role('sql_lab')
    if base is None:
        raise RuntimeError('Run superset init before assigning the SQL Lab role')
    database.expose_in_sqllab = True
    role = security_manager.find_role('oil-analyst') or security_manager.add_role('oil-analyst')
    allowed = security_manager.add_permission_view_menu('database_access', database.perm)
    role.permissions = list(dict.fromkeys([*base.permissions, allowed]))


def main():
    parser = argparse.ArgumentParser(description='Configure the local Superset reporting proof')
    parser.add_argument('--oil-reader-role', choices=('oil-reader', 'unscoped'))
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        if args.oil_reader_role:
            user = security_manager.find_user(username='oil-reader')
            user.roles = [security_manager.find_role(args.oil_reader_role)]
            db.session.commit()
            return

        from superset.commands.dashboard.importers.v1 import ImportDashboardsCommand

        admin = get_user('admin', security_manager.find_role('Admin'), 'SUPERSET_ADMIN_PASSWORD')
        g.user = admin
        passwords = {scope: os.environ[f'REPORTING_{scope.upper()}_PASSWORD'] for scope in SCOPES}
        ImportDashboardsCommand(assets(passwords), overwrite=True).run()
        configure_roles()


if __name__ == '__main__':
    main()
