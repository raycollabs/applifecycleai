"""Sample: Serve FastAPI checkout and ledger endpoints on the py-fastapi-next shell contract."""
import hashlib
import os
import secrets
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    from .domain import CheckoutError, CheckoutService
    from .postgres_store import PostgresCheckoutService
except ImportError:
    from domain import CheckoutError, CheckoutService
    from postgres_store import PostgresCheckoutService

COOKIE = 'durai_session'
DATABASE_URL = os.environ.get('DATABASE_URL')
service = PostgresCheckoutService(DATABASE_URL) if DATABASE_URL else CheckoutService()
sessions = {}


def _token_digest(token: str) -> bytes:
    # Sample: Hash session token with SHA-256 to store digest in checkout.session.
    return hashlib.sha256(token.encode('utf-8')).digest()


class SignInRequest(BaseModel):
    trader: str
    secret: str


class StockSeedRequest(BaseModel):
    sku: str
    total_units: int


class IntentCreateRequest(BaseModel):
    amount_cents: int
    currency: str = 'USD'
    sku: Optional[str] = None
    quantity: int = 0


class AmountRequest(BaseModel):
    amount_cents: Optional[int] = None


def create_app() -> FastAPI:
    # Sample: Create FastAPI app with POST-only sign-in/sign-out, inventory, intents, capture, refund, and ledger routes.
    app = FastAPI(title='Durai Checkout & Ledger Baseline')

    @app.exception_handler(CheckoutError)
    async def handle_checkout_error(_: Request, exc: CheckoutError):
        return JSONResponse(status_code=exc.status_code, content={'error': {'code': exc.code, 'message': str(exc)}})

    def current_merchant(request: Request) -> str:
        token = request.cookies.get(COOKIE, '')
        if not token:
            raise HTTPException(status_code=401, detail='signed_out')
        merchant = service.lookup_session(_token_digest(token)) if hasattr(service, 'lookup_session') else sessions.get(token)
        if not merchant:
            raise HTTPException(status_code=401, detail='signed_out')
        return merchant

    @app.get('/health')
    def health():
        return {'status': 'ok', 'backend': 'postgres' if DATABASE_URL else 'memory'}

    @app.post('/session/sign-in')
    def sign_in(body: SignInRequest, response: Response):
        token = secrets.token_urlsafe(32)
        merchant = body.trader.strip()
        if hasattr(service, 'save_session'):
            service.save_session(_token_digest(token), merchant)
        else:
            sessions[token] = merchant
        response.set_cookie(COOKIE, token, path='/', httponly=True, samesite='lax', max_age=28800)
        return {'trader': merchant}

    @app.post('/session/sign-out')
    def sign_out(request: Request, response: Response):
        token = request.cookies.get(COOKIE, '')
        if token and hasattr(service, 'delete_session'):
            service.delete_session(_token_digest(token))
        else:
            sessions.pop(token, None)
        response.delete_cookie(COOKIE, path='/')
        return {'signedOut': True}

    @app.get('/session')
    def session_info(request: Request):
        return {'trader': current_merchant(request)}

    @app.get('/api/inventory')
    def get_inventory(request: Request):
        merchant = current_merchant(request)
        return {'items': service.list_inventory(merchant)}

    @app.post('/api/inventory')
    def seed_inventory(body: StockSeedRequest, request: Request):
        merchant = current_merchant(request)
        return service.seed_stock(merchant, body.sku, body.total_units)

    @app.get('/api/intents')
    def get_intents(request: Request):
        merchant = current_merchant(request)
        return {'items': service.list_intents(merchant)}

    @app.post('/api/intents')
    def post_intent(body: IntentCreateRequest, request: Request, idempotency_key: str = Header(..., alias='Idempotency-Key')):
        merchant = current_merchant(request)
        intent, replayed = service.create_intent(merchant, idempotency_key, body.amount_cents, body.currency, body.sku, body.quantity)
        return JSONResponse(status_code=200 if replayed else 201, content={'intent': intent, 'replayed': replayed})

    @app.post('/api/intents/{intent_id}/capture')
    def post_capture(intent_id: str, body: AmountRequest, request: Request):
        merchant = current_merchant(request)
        return {'intent': service.capture(merchant, intent_id, body.amount_cents)}

    @app.post('/api/intents/{intent_id}/refund')
    def post_refund(intent_id: str, body: AmountRequest, request: Request):
        merchant = current_merchant(request)
        if body.amount_cents is None:
            raise CheckoutError('Refund amount is required')
        return {'intent': service.refund(merchant, intent_id, body.amount_cents)}

    @app.get('/api/ledger')
    def get_ledger(request: Request):
        merchant = current_merchant(request)
        return {'items': service.list_ledger(merchant)}

    return app


def seed_demo_data() -> None:
    # Sample: Ensure schema when using PostgreSQL and seed M-ACME once idempotently.
    if hasattr(service, 'ensure_schema'):
        service.ensure_schema()
    if service.list_inventory('M-ACME'):
        return
    service.seed_stock('M-ACME', 'SKU-WIDGET-01', 25)
    service.seed_stock('M-ACME', 'SKU-LEDGER-PRO', 10)
    intent, replayed = service.create_intent('M-ACME', 'DEMO-INIT-1', 12500, 'USD', 'SKU-WIDGET-01', 2)
    if not replayed and intent.get('captured_cents', 0) == 0:
        service.capture('M-ACME', intent['intent_id'], 12500, fee_cents=350)


seed_demo_data()
app = create_app()
