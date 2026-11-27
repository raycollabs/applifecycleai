set -eu
superset db upgrade
superset init
python /app/durai-reporting/configure_superset.py
exec gunicorn --bind 0.0.0.0:8088 --workers 2 --timeout 60 'superset.app:create_app()'
