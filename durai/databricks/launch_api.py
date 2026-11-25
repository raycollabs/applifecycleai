import os

from databricks.sdk import WorkspaceClient
from fastapi import FastAPI
import psycopg
from psycopg.rows import dict_row
import uvicorn

from app import create_app


def lakebase_connect():
    # Sample: Get one credential for endpoint E-1. Connect with TLS and the app role.
    credential = WorkspaceClient().postgres.generate_database_credential(endpoint=os.environ['LAKEBASE_ENDPOINT'])
    return psycopg.connect(password=credential.token, sslmode='verify-full',
                           sslrootcert=os.environ.get('PGSSLROOTCERT', 'system'), connect_timeout=5, row_factory=dict_row)


def main():
    # Sample: Start the API on the assigned port. Read /api/healthz.
    router = FastAPI()
    router.mount('/api', create_app(connector=lakebase_connect))
    uvicorn.run(router, host='0.0.0.0', port=int(os.environ['DATABRICKS_APP_PORT']))


if __name__ == '__main__':
    main()
