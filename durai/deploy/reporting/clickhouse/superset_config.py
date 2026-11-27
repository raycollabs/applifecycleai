import os
from urllib.parse import quote_plus

from superset_policy import check_chart_source

if os.environ.get('DURAI_REPORTING_OIDC_ISSUER'):
    from reporting_oidc import settings

    globals().update(settings())

SECRET_KEY = os.environ['SUPERSET_SECRET_KEY']
metadata_password = quote_plus(os.environ['DURAI_CH_METADATA_PASSWORD'])
SQLALCHEMY_DATABASE_URI = f'postgresql+psycopg2://superset_meta:{metadata_password}@metadata:5432/metadata'
FEATURE_FLAGS = {'DASHBOARD_RBAC': False}
FLASK_APP_MUTATOR = check_chart_source
DATA_CACHE_CONFIG = {'CACHE_TYPE': 'NullCache'}
CACHE_CONFIG = {'CACHE_TYPE': 'NullCache'}
FILTER_STATE_CACHE_CONFIG = {'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300}
EXPLORE_FORM_DATA_CACHE_CONFIG = {'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300}
WTF_CSRF_ENABLED = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
