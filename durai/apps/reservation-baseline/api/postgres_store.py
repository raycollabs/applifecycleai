"""Sample: Connect PostgresReservationService to PostgreSQL 17. Seed seats, place idempotent hold, confirm, cancel, and verify audit rows."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
from typing import List, Optional

import psycopg
from psycopg.rows import dict_row

try:
    from .domain import (
        AccessDeniedError,
        ExpiredHoldError,
        IdempotencyConflictError,
        InvalidHoldStateError,
        NoOverbookError,
        ReservationError,
        _canonical_digest,
        _hold_payload,
    )
except ImportError:
    from domain import (
        AccessDeniedError,
        ExpiredHoldError,
        IdempotencyConflictError,
        InvalidHoldStateError,
        NoOverbookError,
        ReservationError,
        _canonical_digest,
        _hold_payload,
    )

MIGRATION = Path(__file__).resolve().parents[1] / 'migrations' / '001_reservations.sql'


class PostgresReservationService:
    def __init__(self, dsn: str, now_fn=None):
        # Sample: Store PostgreSQL DSN and UTC clock provider.
        self.dsn = dsn
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    def _connect(self):
        # Sample: Open one psycopg 3 connection with dict_row row factory.
        return psycopg.connect(self.dsn, row_factory=dict_row, autocommit=False)

    def ensure_schema(self) -> None:
        # Sample: Apply migrations/001_reservations.sql idempotently on PostgreSQL.
        sql = MIGRATION.read_text(encoding='utf-8')
        with self._connect() as conn:
            conn.execute(sql)

    def _next_id(self, prefix: str) -> str:
        # Sample: Generate unique identifier HLD-1A2B3C4D for a hold or audit row.
        return f'{prefix}-{secrets.token_hex(4).upper()}'

    def _serialize_hold_row(self, row: dict) -> dict:
        # Sample: Convert PostgreSQL seat_hold row into JSON-ready dictionary.
        out = dict(row)
        out['seat_count'] = int(out['seat_count'])
        out['amount_cents'] = int(out['amount_cents'])
        out['settled_cents'] = int(out['settled_cents'])
        if isinstance(out.get('expires_at'), datetime):
            out['expires_at'] = out['expires_at'].isoformat()
        if isinstance(out.get('created_at'), datetime):
            out['created_at'] = out['created_at'].isoformat()
        return out

    def _serialize_audit_row(self, row: dict) -> dict:
        # Sample: Convert PostgreSQL audit_log row into JSON-ready dictionary.
        out = dict(row)
        out['seat_count'] = int(out['seat_count'])
        out['amount_cents'] = int(out['amount_cents'])
        if isinstance(out.get('created_at'), datetime):
            out['created_at'] = out['created_at'].isoformat()
        return out

    def _append_audit_in_tx(
        self,
        conn,
        organizer: str,
        hold_id: str,
        action: str,
        event_id: str,
        section: str,
        seat_count: int,
        amount_cents: int,
        detail: dict,
        when: Optional[datetime] = None,
    ) -> None:
        # Sample: Insert audit row into reservation.audit_log inside active transaction.
        audit_id = self._next_id('AUD')
        conn.execute(
            'INSERT INTO reservation.audit_log '
            '(audit_id, hold_id, organizer, action, event_id, section, seat_count, amount_cents, detail, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)',
            (
                audit_id,
                hold_id,
                organizer,
                action,
                event_id,
                section,
                int(seat_count),
                int(amount_cents),
                json.dumps(detail),
                when or self._now(),
            ),
        )

    def _expire_holds(self, conn) -> None:
        # Sample: Mark HELD seat holds whose expires_at <= now as EXPIRED and record audit entries.
        now = self._now()
        expired = conn.execute(
            "UPDATE reservation.seat_hold SET status = 'EXPIRED' "
            "WHERE status = 'HELD' AND expires_at <= %s "
            'RETURNING hold_id, organizer, event_id, section, seat_count, amount_cents, customer_ref',
            (now,),
        ).fetchall()
        for row in expired:
            self._append_audit_in_tx(
                conn,
                row['organizer'],
                row['hold_id'],
                'HOLD_EXPIRED',
                row['event_id'],
                row['section'],
                row['seat_count'],
                row['amount_cents'],
                {'customer_ref': row['customer_ref']},
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
        # Sample: Upsert 40 seats at 12000 cents for ORG-LIVE EVT-SUMMIT-2026 ORCHESTRA.
        if not organizer or not event_id or not section:
            raise ReservationError('Organizer, event_id, and section are required')
        if int(total_seats) < 0 or int(unit_price_cents) < 0:
            raise ReservationError('Seat capacity and unit price cannot be negative')
        with self._connect() as conn:
            existing = conn.execute(
                'SELECT confirmed_seats FROM reservation.event_seat '
                'WHERE organizer = %s AND event_id = %s AND section = %s FOR UPDATE',
                (organizer, event_id, section),
            ).fetchone()
            if existing and int(existing['confirmed_seats']) > int(total_seats):
                raise NoOverbookError('Total seats cannot be less than confirmed seats')
            conn.execute(
                'INSERT INTO reservation.event_seat (organizer, event_id, section, total_seats, confirmed_seats, unit_price_cents) '
                'VALUES (%s, %s, %s, %s, 0, %s) '
                'ON CONFLICT (organizer, event_id, section) DO UPDATE '
                'SET total_seats = EXCLUDED.total_seats, unit_price_cents = EXCLUDED.unit_price_cents',
                (organizer, event_id, section, int(total_seats), int(unit_price_cents)),
            )
        return self.get_event_seat(organizer, event_id, section)

    def _read_event_seat_in_tx(self, conn, organizer: str, event_id: str, section: str, lock: bool = False) -> dict:
        # Sample: Read event_seat capacity and active HELD seats for organizer, event_id, and section.
        suffix = ' FOR UPDATE' if lock else ''
        ev = conn.execute(
            'SELECT total_seats, confirmed_seats, unit_price_cents FROM reservation.event_seat '
            'WHERE organizer = %s AND event_id = %s AND section = %s' + suffix,
            (organizer, event_id, section),
        ).fetchone()
        if not ev:
            other = conn.execute(
                'SELECT 1 FROM reservation.event_seat WHERE event_id = %s AND section = %s LIMIT 1',
                (event_id, section),
            ).fetchone()
            if other:
                raise AccessDeniedError('Event section unavailable for organizer')
            raise ReservationError(f'Unknown event section {event_id}/{section}')
        held_row = conn.execute(
            "SELECT COALESCE(SUM(seat_count), 0) AS qty FROM reservation.seat_hold "
            "WHERE organizer = %s AND event_id = %s AND section = %s AND status = 'HELD'",
            (organizer, event_id, section),
        ).fetchone()
        total = int(ev['total_seats'])
        confirmed = int(ev['confirmed_seats'])
        held = int(held_row['qty'])
        return {
            'organizer': organizer,
            'event_id': event_id,
            'section': section,
            'total_seats': total,
            'held_seats': held,
            'confirmed_seats': confirmed,
            'available_seats': total - confirmed - held,
            'unit_price_cents': int(ev['unit_price_cents']),
        }

    def get_event_seat(self, organizer: str, event_id: str, section: str) -> dict:
        # Sample: Read EVT-SUMMIT-2026 ORCHESTRA total, held, confirmed, and available seat counts.
        with self._connect() as conn:
            self._expire_holds(conn)
            return self._read_event_seat_in_tx(conn, organizer, event_id, section)

    def list_events(self, organizer: str) -> List[dict]:
        # Sample: List all event sections belonging to ORG-LIVE ordered by event_id and section.
        with self._connect() as conn:
            self._expire_holds(conn)
            rows = conn.execute(
                'SELECT e.event_id, e.section, e.total_seats, e.confirmed_seats, e.unit_price_cents, '
                "COALESCE((SELECT SUM(h.seat_count) FROM reservation.seat_hold h "
                "WHERE h.organizer = e.organizer AND h.event_id = e.event_id AND h.section = e.section AND h.status = 'HELD'), 0) AS held_seats "
                'FROM reservation.event_seat e WHERE e.organizer = %s ORDER BY e.event_id, e.section',
                (organizer,),
            ).fetchall()
        return [
            {
                'organizer': organizer,
                'event_id': r['event_id'],
                'section': r['section'],
                'total_seats': int(r['total_seats']),
                'held_seats': int(r['held_seats']),
                'confirmed_seats': int(r['confirmed_seats']),
                'available_seats': int(r['total_seats']) - int(r['confirmed_seats']) - int(r['held_seats']),
                'unit_price_cents': int(r['unit_price_cents']),
            }
            for r in rows
        ]

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
        # Sample: Lock (organizer, idempotency_key) with pg_advisory_xact_lock. Replay same digest or raise IdempotencyConflictError on mismatch.
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

        with self._connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (f'{organizer}:{idempotency_key}',))
            held = conn.execute(
                'SELECT request_digest, hold_id FROM reservation.idempotency WHERE organizer = %s AND idempotency_key = %s',
                (organizer, idempotency_key),
            ).fetchone()
            if held:
                if held['request_digest'] != req_digest:
                    raise IdempotencyConflictError('Idempotency key reused with different payload')
                self._expire_holds(conn)
                row = conn.execute('SELECT * FROM reservation.seat_hold WHERE hold_id = %s', (held['hold_id'],)).fetchone()
                return self._serialize_hold_row(row), True

            self._expire_holds(conn)
            info = self._read_event_seat_in_tx(conn, organizer, event_id, section, lock=True)
            if info['available_seats'] < int(seat_count):
                raise NoOverbookError(f'Insufficient seats available for {event_id}/{section}')

            now = self._now()
            hold_id = self._next_id('HLD')
            amount_cents = int(seat_count) * int(info['unit_price_cents'])
            expires_at = now + timedelta(seconds=int(ttl_seconds))
            conn.execute(
                'INSERT INTO reservation.seat_hold '
                '(hold_id, organizer, event_id, section, customer_ref, seat_count, amount_cents, settled_cents, currency, status, expires_at, created_at) '
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, 'HELD', %s, %s)",
                (hold_id, organizer, event_id, section, customer, int(seat_count), amount_cents, currency.upper(), expires_at, now),
            )
            conn.execute(
                'INSERT INTO reservation.idempotency (organizer, idempotency_key, request_digest, hold_id) VALUES (%s, %s, %s, %s)',
                (organizer, idempotency_key, req_digest, hold_id),
            )
            self._append_audit_in_tx(
                conn,
                organizer,
                hold_id,
                'HOLD_PLACED',
                event_id,
                section,
                int(seat_count),
                amount_cents,
                {'customer_ref': customer, 'idempotency_key': idempotency_key},
                when=now,
            )
            row = conn.execute('SELECT * FROM reservation.seat_hold WHERE hold_id = %s', (hold_id,)).fetchone()
            return self._serialize_hold_row(row), False

    def confirm_hold(self, organizer: str, hold_id: str, payment_cents: Optional[int] = None) -> dict:
        # Sample: Lock seat_hold FOR UPDATE, refuse expired hold, settle full payment, transition HELD to CONFIRMED, and increment confirmed_seats.
        with self._connect() as conn:
            self._expire_holds(conn)
            hold = conn.execute('SELECT * FROM reservation.seat_hold WHERE hold_id = %s FOR UPDATE', (hold_id,)).fetchone()
            if not hold or hold['organizer'] != organizer:
                raise AccessDeniedError('Hold unavailable for organizer')
            if hold['status'] == 'EXPIRED':
                raise ExpiredHoldError('Hold has expired')
            if hold['status'] != 'HELD':
                raise InvalidHoldStateError(f"Cannot confirm hold in status {hold['status']}")
            amount_cents = int(hold['amount_cents'])
            settled = amount_cents if payment_cents is None else int(payment_cents)
            if settled < 0 or settled != amount_cents:
                raise ReservationError('Settlement amount must match hold total')

            ev = conn.execute(
                'SELECT total_seats, confirmed_seats FROM reservation.event_seat '
                'WHERE organizer = %s AND event_id = %s AND section = %s FOR UPDATE',
                (organizer, hold['event_id'], hold['section']),
            ).fetchone()
            if not ev or int(ev['confirmed_seats']) + int(hold['seat_count']) > int(ev['total_seats']):
                raise NoOverbookError('Confirmed seats exceed section capacity')

            conn.execute(
                'UPDATE reservation.event_seat SET confirmed_seats = confirmed_seats + %s '
                'WHERE organizer = %s AND event_id = %s AND section = %s',
                (int(hold['seat_count']), organizer, hold['event_id'], hold['section']),
            )
            conn.execute(
                "UPDATE reservation.seat_hold SET status = 'CONFIRMED', settled_cents = %s WHERE hold_id = %s",
                (settled, hold_id),
            )
            self._append_audit_in_tx(
                conn,
                organizer,
                hold_id,
                'HOLD_CONFIRMED',
                hold['event_id'],
                hold['section'],
                int(hold['seat_count']),
                settled,
                {'customer_ref': hold['customer_ref'], 'settled_cents': settled},
            )
            updated = conn.execute('SELECT * FROM reservation.seat_hold WHERE hold_id = %s', (hold_id,)).fetchone()
            return self._serialize_hold_row(updated)

    def cancel_hold(self, organizer: str, hold_id: str) -> dict:
        # Sample: Lock seat_hold FOR UPDATE, transition HELD or CONFIRMED to CANCELLED, release seats, and record audit row.
        with self._connect() as conn:
            self._expire_holds(conn)
            hold = conn.execute('SELECT * FROM reservation.seat_hold WHERE hold_id = %s FOR UPDATE', (hold_id,)).fetchone()
            if not hold or hold['organizer'] != organizer:
                raise AccessDeniedError('Hold unavailable for organizer')
            if hold['status'] == 'EXPIRED':
                raise ExpiredHoldError('Cannot cancel an expired hold')
            if hold['status'] == 'CANCELLED':
                return self._serialize_hold_row(hold)
            if hold['status'] == 'CONFIRMED':
                conn.execute(
                    'UPDATE reservation.event_seat SET confirmed_seats = GREATEST(0, confirmed_seats - %s) '
                    'WHERE organizer = %s AND event_id = %s AND section = %s',
                    (int(hold['seat_count']), organizer, hold['event_id'], hold['section']),
                )
            conn.execute("UPDATE reservation.seat_hold SET status = 'CANCELLED' WHERE hold_id = %s", (hold_id,))
            self._append_audit_in_tx(
                conn,
                organizer,
                hold_id,
                'HOLD_CANCELLED',
                hold['event_id'],
                hold['section'],
                int(hold['seat_count']),
                int(hold['amount_cents']),
                {'customer_ref': hold['customer_ref']},
            )
            updated = conn.execute('SELECT * FROM reservation.seat_hold WHERE hold_id = %s', (hold_id,)).fetchone()
            return self._serialize_hold_row(updated)

    def get_hold(self, organizer: str, hold_id: str) -> dict:
        # Sample: Read hold HLD-1A2B3C4D for organizer ORG-LIVE. Refuse cross-organizer reads.
        with self._connect() as conn:
            self._expire_holds(conn)
            row = conn.execute('SELECT * FROM reservation.seat_hold WHERE hold_id = %s', (hold_id,)).fetchone()
            if not row or row['organizer'] != organizer:
                raise AccessDeniedError('Hold unavailable for organizer')
            return self._serialize_hold_row(row)

    def list_holds(self, organizer: str) -> List[dict]:
        # Sample: List seat holds for ORG-LIVE ordered newest first.
        with self._connect() as conn:
            self._expire_holds(conn)
            rows = conn.execute(
                'SELECT * FROM reservation.seat_hold WHERE organizer = %s ORDER BY created_at DESC, hold_id DESC',
                (organizer,),
            ).fetchall()
            return [self._serialize_hold_row(r) for r in rows]

    def list_audit(self, organizer: str) -> List[dict]:
        # Sample: List audit log rows for ORG-LIVE ordered newest first.
        with self._connect() as conn:
            self._expire_holds(conn)
            rows = conn.execute(
                'SELECT * FROM reservation.audit_log WHERE organizer = %s ORDER BY created_at DESC, audit_id DESC',
                (organizer,),
            ).fetchall()
            return [self._serialize_audit_row(r) for r in rows]

    def save_session(self, token_digest: bytes, organizer: str, ttl_seconds: int = 28800) -> None:
        # Sample: Persist session token digest in reservation.session so sign-in survives API restarts.
        expires_at = self._now() + timedelta(seconds=ttl_seconds)
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO reservation.session (token_digest, organizer, expires_at) VALUES (%s, %s, %s)',
                (token_digest, organizer, expires_at),
            )

    def lookup_session(self, token_digest: bytes) -> Optional[str]:
        # Sample: Resolve non-expired session token digest to organizer identifier.
        with self._connect() as conn:
            row = conn.execute(
                'SELECT organizer FROM reservation.session WHERE token_digest = %s AND expires_at > %s',
                (token_digest, self._now()),
            ).fetchone()
            return row['organizer'] if row else None

    def delete_session(self, token_digest: bytes) -> None:
        # Sample: Delete session token digest on POST sign-out.
        with self._connect() as conn:
            conn.execute('DELETE FROM reservation.session WHERE token_digest = %s', (token_digest,))
