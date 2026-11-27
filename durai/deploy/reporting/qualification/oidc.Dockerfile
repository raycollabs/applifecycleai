ARG BASE_IMAGE=durai-reporting-superset-pg:6.1.0
FROM ${BASE_IMAGE}
USER root
RUN uv pip install --python /app/.venv/bin/python Authlib==1.9.10 joserfc==1.7.7
USER superset
