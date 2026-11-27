"""Sample: Serve FastAPI seat and capacity reservation endpoints on the py-fastapi-next shell contract."""
import hashlib
import os
import secrets
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    from .domain import ReservationError, ReservationService
    from .postgres_store import PostgresReservationService
except ImportError:
    from domain import ReservationError, ReservationService
    from postgres_store import PostgresReservationService

COOKIE = 'durai_session'
DATABASE_URL = os.environ.get('DATABASE_URL')
service = PostgresReservationService(DATABASE_URL) if DATABASE_URL else ReservationService()
sessions = {}


def _token_digest(token: str) -> bytes:
    # Sample: Hash session token with SHA-256 to store digest in reservation.session.
    return hashlib.sha256(token.encode('utf-8')).digest()


class SignInRequest(BaseModel):
    trader: str
    secret: str


class EventSeedRequest(BaseModel):
    event_id: str
    section: str
    total_seats: int
    unit_price_cents: int = 5000


class HoldCreateRequest(BaseModel):
    event_id: str
    section: str
    seat_count: int
    customer_ref: str = 'GUEST'
    ttl_seconds: int = 600
    currency: str = 'USD'


class ConfirmRequest(BaseModel):
    payment_cents: Optional[int] = None


def create_app(active_service=None) -> FastAPI:
    # Sample: Create FastAPI app with POST-only sign-in/sign-out, events, holds, confirm, cancel, and audit routes.
    svc = active_service or service
    app = FastAPI(title='Durai Seat & Capacity Reservation Baseline')

    @app.exception_handler(ReservationError)
    async def handle_reservation_error(_: Request, exc: ReservationError):
        return JSONResponse(status_code=exc.status_code, content={'error': {'code': exc.code, 'message': str(exc)}})

    def current_organizer(request: Request) -> str:
        token = request.cookies.get(COOKIE, '')
        if not token:
            raise HTTPException(status_code=401, detail='signed_out')
        organizer = svc.lookup_session(_token_digest(token)) if hasattr(svc, 'lookup_session') else sessions.get(token)
        if not organizer:
            raise HTTPException(status_code=401, detail='signed_out')
        return organizer

    @app.get('/health')
    def health():
        return {'status': 'ok', 'backend': 'postgres' if DATABASE_URL else 'memory'}

    @app.post('/session/sign-in')
    def sign_in(body: SignInRequest, response: Response):
        organizer = body.trader.strip()
        if not organizer or not body.secret.strip():
            raise HTTPException(status_code=401, detail='invalid_credentials')
        token = secrets.token_urlsafe(32)
        if hasattr(svc, 'save_session'):
            svc.save_session(_token_digest(token), organizer)
        else:
            sessions[token] = organizer
        response.set_cookie(COOKIE, token, path='/', httponly=True, samesite='lax', max_age=28800)
        return {'trader': organizer, 'organizer': organizer}

    @app.post('/session/sign-out')
    def sign_out(request: Request, response: Response):
        token = request.cookies.get(COOKIE, '')
        if token and hasattr(svc, 'delete_session'):
            svc.delete_session(_token_digest(token))
        else:
            sessions.pop(token, None)
        response.delete_cookie(COOKIE, path='/')
        return {'signedOut': True}

    @app.get('/session')
    def session_info(request: Request):
        organizer = current_organizer(request)
        return {'trader': organizer, 'organizer': organizer}

    @app.get('/api/events')
    def get_events(request: Request):
        organizer = current_organizer(request)
        return {'items': svc.list_events(organizer)}

    @app.post('/api/events')
    def post_event(body: EventSeedRequest, request: Request):
        organizer = current_organizer(request)
        return svc.seed_event(organizer, body.event_id, body.section, body.total_seats, body.unit_price_cents)

    @app.get('/api/holds')
    def get_holds(request: Request):
        organizer = current_organizer(request)
        return {'items': svc.list_holds(organizer)}

    @app.post('/api/holds')
    def post_hold(body: HoldCreateRequest, request: Request, idempotency_key: str = Header(..., alias='Idempotency-Key')):
        organizer = current_organizer(request)
        hold, replayed = svc.place_hold(
            organizer,
            idempotency_key,
            body.event_id,
            body.section,
            body.seat_count,
            body.customer_ref,
            body.ttl_seconds,
            body.currency,
        )
        return JSONResponse(status_code=200 if replayed else 201, content={'hold': hold, 'replayed': replayed})

    @app.post('/api/holds/{hold_id}/confirm')
    def post_confirm(hold_id: str, body: ConfirmRequest, request: Request):
        organizer = current_organizer(request)
        return {'hold': svc.confirm_hold(organizer, hold_id, body.payment_cents)}

    @app.post('/api/holds/{hold_id}/cancel')
    def post_cancel(hold_id: str, request: Request):
        organizer = current_organizer(request)
        return {'hold': svc.cancel_hold(organizer, hold_id)}

    @app.get('/api/audit')
    def get_audit(request: Request):
        organizer = current_organizer(request)
        return {'items': svc.list_audit(organizer)}

    return app


def seed_demo_data(active_service=None) -> None:
    # Sample: Ensure schema when using PostgreSQL and seed ORG-LIVE events and demo holds idempotently.
    svc = active_service or service
    if hasattr(svc, 'ensure_schema'):
        svc.ensure_schema()
    if svc.list_events('ORG-LIVE'):
        return
    svc.seed_event('ORG-LIVE', 'EVT-SUMMIT-2026', 'ORCHESTRA', 40, unit_price_cents=12000)
    svc.seed_event('ORG-LIVE', 'EVT-SUMMIT-2026', 'BALCONY', 80, unit_price_cents=6500)
    svc.seed_event('ORG-LIVE', 'EVT-WORKSHOP-01', 'GENERAL', 25, unit_price_cents=5000)
    hold1, replayed1 = svc.place_hold('ORG-LIVE', 'DEMO-HOLD-1', 'EVT-SUMMIT-2026', 'ORCHESTRA', 2, customer_ref='CUST-VIP-01')
    if not replayed1 and hold1.get('status') == 'HELD':
        svc.confirm_hold('ORG-LIVE', hold1['hold_id'])
    svc.place_hold('ORG-LIVE', 'DEMO-HOLD-2', 'EVT-SUMMIT-2026', 'BALCONY', 3, customer_ref='CUST-DEMO-02')


seed_demo_data()
app = create_app()
