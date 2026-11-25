from dataclasses import dataclass, field
import hashlib
import hmac
from importlib.util import find_spec
import json
import os
import re
import secrets
from typing import Annotated

import psycopg
from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, StringConstraints
from psycopg.types.json import Jsonb
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

try:
    from .database import connect
except ImportError:
    from database import connect

COOKIE = 'durai_session'
SESSION_SECONDS = 28_800
REQUEST_KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


@dataclass(frozen=True)
class Settings:
    trader: str
    signin_secret: str = field(repr=False)
    secure_cookie: bool = True

    @classmethod
    def environment(cls):
        # Sample: Read trader T-1 from the environment. Check that secure cookies are on by default.
        return cls(os.environ['DURAI_SHELL_TRADER'], os.environ['DURAI_SHELL_SIGNIN_SECRET'],
                   os.environ.get('DURAI_SHELL_COOKIE_SECURE', '1') != '0')


class SignIn(BaseModel):
    trader: Text
    secret: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ReceiptRequest(BaseModel):
    label: Text


def token_digest(token):
    # Sample: Hash token A twice. Check equal digests.
    return hashlib.sha256(token.encode()).digest()


def error(status, code):
    # Sample: Return status 409 and error code idempotency_conflict.
    return JSONResponse(status_code=status, content={'error': {'code': code}})


def actor_for(connection, request):
    # Sample: Read a token for trader A. Return A only while its session and account are active.
    token = request.cookies.get(COOKIE, '')
    if not token or len(token) > 128:
        raise HTTPException(401, 'signed_out')
    actor = connection.execute('SELECT a.actor_id FROM shell.session AS s JOIN shell.actor AS a USING(actor_id) '
                               'WHERE token_digest=%s AND expires_at > now() AND a.active',
                               (token_digest(token),)).fetchone()
    if actor is None:
        raise HTTPException(401, 'signed_out')
    return actor['actor_id']


def write_once(connection, actor, operation, key, payload, create):
    # Sample: Submit the same trade request twice with key K-1. Return the first trade both times.
    checksum = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
    lock = json.dumps([actor, operation, key])
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (lock,))
    row = connection.execute('SELECT request_digest,response FROM shell.idempotency '
                             'WHERE actor_id=%s AND operation=%s AND request_key=%s', (actor,operation,key)).fetchone()
    if row:
        if row['request_digest'] != checksum:
            raise HTTPException(409, 'idempotency_conflict')
        return row['response']
    response = create()
    connection.execute('INSERT INTO shell.idempotency VALUES(%s,%s,%s,%s,%s)',
                       (actor,operation,key,checksum,Jsonb(response)))
    return response


def write_receipt(connection, actor, label):
    # Sample: Write label R-1. Keep its receipt, audit row, and event in the active transaction.
    receipt = connection.execute('INSERT INTO shell.receipt(actor_id,label) VALUES(%s,%s) RETURNING receipt_id,label',
                                 (actor,label)).fetchone()
    identity = receipt['receipt_id']
    connection.execute("INSERT INTO shell.audit(actor_id,action,receipt_id) VALUES(%s,'receipt.created',%s)", (actor,identity))
    connection.execute("INSERT INTO shell.outbox(receipt_id,event_type) VALUES(%s,'receipt.created')", (identity,))
    return receipt


def create_app(settings=None, connector=connect):
    # Sample: Start the shell with trader T-1 and a scratch database. Check the health route.
    settings = settings or Settings.environment()
    app = FastAPI(title='Durai transaction shell', version='1')

    @app.middleware('http')
    async def idempotency(request: Request, call_next):
        # Sample: Send POST /api/receipts without a key. Check 400.
        route = request.url.path.removeprefix(request.scope.get('root_path', ''))
        if request.method in {'POST','PUT','PATCH','DELETE'} and route.startswith('/api/'):
            key = request.headers.get('Idempotency-Key', '')
            if not REQUEST_KEY.fullmatch(key):
                return error(400, 'invalid_idempotency_key')
            request.state.idempotency_key = key
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def http_error(request, fault):
        # Sample: Ask for an unknown route. Check a JSON error code.
        code = fault.detail if isinstance(fault.detail, str) and fault.detail.isidentifier() else 'http_error'
        return error(fault.status_code,code)

    @app.exception_handler(RequestValidationError)
    async def bad_input(request, fault):
        # Sample: Submit an empty receipt label. Check 422.
        return error(422, 'invalid_input')

    @app.exception_handler(psycopg.Error)
    async def unavailable(request, fault):
        # Sample: Stop the scratch database. Check 503 and store_unavailable.
        return error(503, 'store_unavailable')

    @app.get('/healthz')
    def health():
        # Sample: Send GET /healthz. Check status ok.
        with connector() as db:
            db.execute('SELECT 1')
        return {'status':'ok'}

    @app.post('/session/sign-in')
    def sign_in(body: SignIn, response: Response):
        # Sample: Sign in with the configured trader and secret. Check a session cookie.
        if not (hmac.compare_digest(body.trader.encode(),settings.trader.encode()) and
                hmac.compare_digest(body.secret.encode(),settings.signin_secret.encode())):
            raise HTTPException(401, 'invalid_sign_in')
        token = secrets.token_urlsafe(32)
        with connector() as db:
            row = db.execute('SELECT actor_id FROM shell.actor WHERE actor_id=%s AND active',
                             (settings.trader,)).fetchone()
            if row is None:
                raise HTTPException(401,'invalid_sign_in')
            db.execute("INSERT INTO shell.session VALUES(%s,%s,now()+%s*interval '1 second')",
                       (token_digest(token),settings.trader,SESSION_SECONDS))
            db.execute("INSERT INTO shell.audit(actor_id,action) VALUES(%s,'session.started')",(settings.trader,))
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, secure=settings.secure_cookie,
                            httponly=True, samesite='lax', path='/')
        response.headers['Cache-Control'] = 'no-store'
        return {'trader':settings.trader}

    @app.post('/session/sign-out')
    def sign_out(request: Request, response: Response):
        # Sample: Post the session cookie to sign-out. Check a cookie with zero age.
        with connector() as db:
            actor = actor_for(db,request)
            db.execute('DELETE FROM shell.session WHERE token_digest=%s',(token_digest(request.cookies[COOKIE]),))
            db.execute("INSERT INTO shell.audit(actor_id,action) VALUES(%s,'session.ended')",(actor,))
        response.delete_cookie(COOKIE, secure=settings.secure_cookie, httponly=True, samesite='lax', path='/')
        response.headers['Cache-Control'] = 'no-store'
        return {'status':'signed_out'}

    @app.get('/session')
    def read_session(request: Request, response: Response):
        # Sample: Read the current trader. Check no Set-Cookie header.
        with connector() as db:
            actor = actor_for(db,request)
        response.headers['Cache-Control'] = 'no-store'
        return {'trader':actor}

    @app.post('/api/receipts',status_code=201)
    def create_receipt(body: ReceiptRequest, request: Request):
        # Sample: Post label R-1 with key K-1. Return the receipt and commit its events.
        with connector() as db:
            actor = actor_for(db,request)
            return write_once(db,actor,'receipt',request.state.idempotency_key,body.model_dump(mode='json'),
                              lambda: write_receipt(db,actor,body.label))

    @app.get('/api/receipts')
    def list_receipts(request: Request, response: Response, after: Annotated[int, Query(ge=0)]=0,
                      limit: Annotated[int, Query(ge=1, le=100)]=20):
        # Sample: Read after receipt 3. Return at most 20 of the active trader's receipts.
        with connector() as db:
            actor = actor_for(db,request)
            rows = db.execute('SELECT receipt_id,label FROM shell.receipt WHERE actor_id=%s AND receipt_id>%s '
                              'ORDER BY receipt_id LIMIT %s',(actor,after,limit+1)).fetchall()
        response.headers['Cache-Control'] = 'no-store'
        return {'items':rows[:limit], 'next_after':rows[limit-1]['receipt_id'] if len(rows)>limit else None}

    if find_spec('src') is not None and find_spec('src.routes') is not None:
        from src.routes import router
        app.include_router(router, prefix='/api')
    return app
