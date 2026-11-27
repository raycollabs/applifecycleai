"""Sample: Seed 40 seats in EVT-SUMMIT-2026 ORCHESTRA, place idempotent 600s hold for 2 seats, confirm and settle 24000 cents, cancel to release seats, and check organizer isolation."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Dict, List, Optional


class ReservationError(ValueError):
    status_code = 400
    code = 'reservation_error'


class NoOverbookError(ReservationError):
    status_code = 409
    code = 'NO_OVERBOOK'


class IdempotencyConflictError(ReservationError):
    status_code = 409
    code = 'idempotency_conflict'


class ExpiredHoldError(ReservationError):
    status_code = 409
    code = 'expired_hold'


class InvalidHoldStateError(ReservationError):
    status_code = 409
    code = 'invalid_hold_state'


class AccessDeniedError(ReservationError):
    status_code = 403
    code = 'access_denied'


def _canonical_digest(payload: dict) -> str:
    # Sample: Hash {"event_id": "EVT-1", "seat_count": 2}. Return 64-character SHA-256 hex digest.
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _hold_payload(event_id: str, section: str, seat_count: int, customer_ref: str, currency: str) -> dict:
    # Sample: Build canonical hold request dictionary for idempotency digest comparison.
    return {
        'event_id': event_id,
        'section': section,
        'seat_count': int(seat_count),
        'customer_ref': customer_ref.strip(),
        'currency': currency.upper(),
    }


@dataclass
class SeatHold:
    hold_id: str
    organizer: str
    event_id: str
    section: str
    customer_ref: str
    seat_count: int
    amount_cents: int
    settled_cents: int
    currency: str
    status: str
    expires_at: datetime
    created_at: datetime


class ReservationService:
    def __init__(self, now_fn=None):
        # Sample: Initialize empty event seat pools, seat holds, idempotency keys, and audit log.
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._events: Dict[tuple, dict] = {}
        self._holds: Dict[str, SeatHold] = {}
        self._idempotency: Dict[tuple, tuple] = {}
        self._audit: List[dict] = []
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        # Sample: Increment sequence counter from 0 to 1. Return HLD-000001.
        self._seq += 1
        return f'{prefix}-{self._seq:06d}'

    def _serialize_hold(self, hold: SeatHold) -> dict:
        # Sample: Convert SeatHold dataclass into JSON-ready dictionary with ISO timestamps.
        return {
            'hold_id': hold.hold_id,
            'organizer': hold.organizer,
            'event_id': hold.event_id,
            'section': hold.section,
            'customer_ref': hold.customer_ref,
            'seat_count': hold.seat_count,
            'amount_cents': hold.amount_cents,
            'settled_cents': hold.settled_cents,
            'currency': hold.currency,
            'status': hold.status,
            'expires_at': hold.expires_at.isoformat(),
            'created_at': hold.created_at.isoformat(),
        }

    def _append_audit(
        self,
        organizer: str,
        hold_id: str,
        action: str,
        event_id: str,
        section: str,
        seat_count: int,
        amount_cents: int,
        detail: dict,
        when: Optional[datetime] = None,
    ) -> dict:
        # Sample: Append audit row HOLD_PLACED for HLD-000001 to organizer audit log.
        entry = {
            'audit_id': self._next_id('AUD'),
            'hold_id': hold_id,
            'organizer': organizer,
            'action': action,
            'event_id': event_id,
            'section': section,
            'seat_count': int(seat_count),
            'amount_cents': int(amount_cents),
            'detail': dict(detail),
            'created_at': (when or self._now()).isoformat(),
        }
        self._audit.append(entry)
        return entry

    def _expire_holds(self) -> None:
        # Sample: Hold HLD-000001 expires at 12:10:00Z. Mark EXPIRED and log HOLD_EXPIRED at 12:10:01Z.
        now = self._now()
        for hold in self._holds.values():
            if hold.status == 'HELD' and hold.expires_at <= now:
                hold.status = 'EXPIRED'
                self._append_audit(
                    hold.organizer,
                    hold.hold_id,
                    'HOLD_EXPIRED',
                    hold.event_id,
                    hold.section,
                    hold.seat_count,
                    hold.amount_cents,
                    {'customer_ref': hold.customer_ref},
                    when=now,
                )

    def seed_event(
        self,
        organizer: str,
        event_id: str,
        section: str,
        total_seats: int,
        unit_price_cents: int = 5000,
    ) -> dict:
        # Sample: Seed 40 seats at 12000 cents for ORG-LIVE EVT-SUMMIT-2026 ORCHESTRA.
        if not organizer or not event_id or not section:
            raise ReservationError('Organizer, event_id, and section are required')
        if int(total_seats) < 0 or int(unit_price_cents) < 0:
            raise ReservationError('Seat capacity and unit price cannot be negative')
        key = (organizer, event_id, section)
        existing = self._events.get(key)
        confirmed = existing['confirmed_seats'] if existing else 0
        if int(total_seats) < confirmed:
            raise NoOverbookError('Total seats cannot be less than confirmed seats')
        self._events[key] = {
            'organizer': organizer,
            'event_id': event_id,
            'section': section,
            'total_seats': int(total_seats),
            'confirmed_seats': confirmed,
            'unit_price_cents': int(unit_price_cents),
        }
        return self.get_event_seat(organizer, event_id, section)

    def get_event_seat(self, organizer: str, event_id: str, section: str) -> dict:
        # Sample: Read EVT-SUMMIT-2026 ORCHESTRA with 40 total, 2 confirmed, 3 held. Return 35 available.
        self._expire_holds()
        key = (organizer, event_id, section)
        ev = self._events.get(key)
        if not ev:
            if any(ev_id == event_id and sec == section for (_, ev_id, sec) in self._events):
                raise AccessDeniedError('Event section unavailable for organizer')
            raise ReservationError(f'Unknown event section {event_id}/{section}')
        held = sum(
            h.seat_count for h in self._holds.values()
            if h.organizer == organizer and h.event_id == event_id and h.section == section and h.status == 'HELD'
        )
        available = ev['total_seats'] - ev['confirmed_seats'] - held
        return {
            'organizer': organizer,
            'event_id': event_id,
            'section': section,
            'total_seats': ev['total_seats'],
            'held_seats': held,
            'confirmed_seats': ev['confirmed_seats'],
            'available_seats': available,
            'unit_price_cents': ev['unit_price_cents'],
        }

    def list_events(self, organizer: str) -> List[dict]:
        # Sample: List all event sections belonging to ORG-LIVE ordered by event_id and section.
        self._expire_holds()
        keys = sorted((ev_id, sec) for (org, ev_id, sec) in self._events if org == organizer)
        return [self.get_event_seat(organizer, ev_id, sec) for (ev_id, sec) in keys]

    def place_hold(
        self,
        organizer: str,
        idempotency_key: str,
        event_id: str,
        section: str,
        seat_count: int,
        customer_ref: str = 'GUEST',
        ttl_seconds: int = 600,
        currency: str = 'USD',
    ) -> tuple:
        # Sample: Place hold with key K-1 for 2 seats. Replay K-1 for same hold. Reuse K-1 with 3 seats for 409 conflict.
        if not idempotency_key:
            raise ReservationError('Idempotency key is required')
        if int(seat_count) <= 0:
            raise ReservationError('Seat count must be positive')
        if int(ttl_seconds) <= 0:
            raise ReservationError('Hold TTL must be positive')
        customer = (customer_ref or '').strip()
        if not customer:
            raise ReservationError('Customer reference is required')

        payload = _hold_payload(event_id, section, seat_count, customer, currency)
        req_digest = _canonical_digest(payload)
        idem_key = (organizer, idempotency_key)
        if idem_key in self._idempotency:
            old_digest, hold_id = self._idempotency[idem_key]
            if old_digest != req_digest:
                raise IdempotencyConflictError('Idempotency key reused with different payload')
            self._expire_holds()
            return self._serialize_hold(self._holds[hold_id]), True

        info = self.get_event_seat(organizer, event_id, section)
        if info['available_seats'] < int(seat_count):
            raise NoOverbookError(f'Insufficient seats available for {event_id}/{section}')

        now = self._now()
        hold_id = self._next_id('HLD')
        amount_cents = int(seat_count) * int(info['unit_price_cents'])
        hold = SeatHold(
            hold_id=hold_id,
            organizer=organizer,
            event_id=event_id,
            section=section,
            customer_ref=customer,
            seat_count=int(seat_count),
            amount_cents=amount_cents,
            settled_cents=0,
            currency=currency.upper(),
            status='HELD',
            expires_at=now + timedelta(seconds=int(ttl_seconds)),
            created_at=now,
        )
        self._holds[hold_id] = hold
        self._idempotency[idem_key] = (req_digest, hold_id)
        self._append_audit(
            organizer,
            hold_id,
            'HOLD_PLACED',
            event_id,
            section,
            hold.seat_count,
            amount_cents,
            {'customer_ref': customer, 'idempotency_key': idempotency_key},
            when=now,
        )
        return self._serialize_hold(hold), False

    def confirm_hold(self, organizer: str, hold_id: str, payment_cents: Optional[int] = None) -> dict:
        # Sample: Confirm HELD hold HLD-000001 with 24000 cents. Transition to CONFIRMED and increment confirmed_seats.
        self._expire_holds()
        hold = self._holds.get(hold_id)
        if not hold or hold.organizer != organizer:
            raise AccessDeniedError('Hold unavailable for organizer')
        if hold.status == 'EXPIRED':
            raise ExpiredHoldError('Hold has expired')
        if hold.status != 'HELD':
            raise InvalidHoldStateError(f'Cannot confirm hold in status {hold.status}')
        settled = hold.amount_cents if payment_cents is None else int(payment_cents)
        if settled < 0 or settled != hold.amount_cents:
            raise ReservationError('Settlement amount must match hold total')
        ev = self._events[(organizer, hold.event_id, hold.section)]
        if ev['confirmed_seats'] + hold.seat_count > ev['total_seats']:
            raise NoOverbookError('Confirmed seats exceed section capacity')
        ev['confirmed_seats'] += hold.seat_count
        hold.settled_cents = settled
        hold.status = 'CONFIRMED'
        self._append_audit(
            organizer,
            hold_id,
            'HOLD_CONFIRMED',
            hold.event_id,
            hold.section,
            hold.seat_count,
            settled,
            {'customer_ref': hold.customer_ref, 'settled_cents': settled},
        )
        return self._serialize_hold(hold)

    def cancel_hold(self, organizer: str, hold_id: str) -> dict:
        # Sample: Cancel HELD or CONFIRMED hold HLD-000001. Transition to CANCELLED and release seats back to pool.
        self._expire_holds()
        hold = self._holds.get(hold_id)
        if not hold or hold.organizer != organizer:
            raise AccessDeniedError('Hold unavailable for organizer')
        if hold.status == 'EXPIRED':
            raise ExpiredHoldError('Cannot cancel an expired hold')
        if hold.status == 'CANCELLED':
            return self._serialize_hold(hold)
        if hold.status == 'CONFIRMED':
            ev = self._events[(organizer, hold.event_id, hold.section)]
            ev['confirmed_seats'] = max(0, ev['confirmed_seats'] - hold.seat_count)
        hold.status = 'CANCELLED'
        self._append_audit(
            organizer,
            hold_id,
            'HOLD_CANCELLED',
            hold.event_id,
            hold.section,
            hold.seat_count,
            hold.amount_cents,
            {'customer_ref': hold.customer_ref},
        )
        return self._serialize_hold(hold)

    def get_hold(self, organizer: str, hold_id: str) -> dict:
        # Sample: Read hold HLD-000001 for organizer ORG-LIVE. Refuse cross-organizer reads.
        self._expire_holds()
        hold = self._holds.get(hold_id)
        if not hold or hold.organizer != organizer:
            raise AccessDeniedError('Hold unavailable for organizer')
        return self._serialize_hold(hold)

    def list_holds(self, organizer: str) -> List[dict]:
        # Sample: List all seat holds belonging to ORG-LIVE ordered newest first.
        self._expire_holds()
        rows = [self._serialize_hold(h) for h in self._holds.values() if h.organizer == organizer]
        rows.reverse()
        return rows

    def list_audit(self, organizer: str) -> List[dict]:
        # Sample: List all audit log rows belonging to ORG-LIVE ordered newest first.
        self._expire_holds()
        rows = [dict(entry) for entry in self._audit if entry['organizer'] == organizer]
        rows.reverse()
        return rows
