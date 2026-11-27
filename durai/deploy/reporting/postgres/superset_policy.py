from flask import abort, g, request
from flask_jwt_extended import current_user as jwt_user, verify_jwt_in_request
from flask_login import current_user as session_user


def check_chart_source(app):
    # Sample: Save a chart with the SYN-GAS source as oil-author. Check HTTP 403.
    @app.before_request
    def require_chart_source_access():
        boundary = '/api/v1/chart'
        path = request.path.rstrip('/')
        is_create = request.method == 'POST' and path == boundary
        chart_id = path.removeprefix(f'{boundary}/')
        is_update = request.method in {'PUT', 'PATCH'} and chart_id.isdigit() and path != chart_id
        if not (is_create or is_update):
            return

        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not {'datasource_id', 'datasource_type'}.intersection(body):
            return

        if request.headers.get('Authorization'):
            verify_jwt_in_request()
            user = jwt_user._get_current_object()
        else:
            user = session_user._get_current_object()
        if user is None or not user.is_authenticated:
            abort(401)
        g.user = user

        from superset import db, security_manager
        from superset.connectors.sqla.models import SqlaTable
        from superset.models.slice import Slice

        chart = db.session.get(Slice, int(chart_id)) if is_update else None
        source_id = body.get('datasource_id', chart.datasource_id if chart else None)
        source_type = body.get('datasource_type', chart.datasource_type if chart else None)
        if type(source_id) is not int or source_type != 'table':
            abort(403)
        dataset = db.session.get(SqlaTable, source_id)
        if dataset is None or not security_manager.can_access_datasource(dataset):
            abort(403)
