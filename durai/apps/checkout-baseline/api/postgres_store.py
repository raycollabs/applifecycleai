"""Sample: Connect PostgresCheckoutService to PostgreSQL 17. Seed SKU-1, create idempotent intent, capture, refund, and verify persisted journal rows."""
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
        CheckoutError,
        IdempotencyConflictError,
        InsufficientStockError,
        OverRefundError,
        _canonical_digest,
    )
except ImportError:
    from domain import (
        AccessDeniedError,
        CheckoutError,
        IdempotencyConflictError,
        InsufficientStockError,
        OverRefundError,
        _canonical_digest,
    )

MIGRATION = Path(__file__).resolve().parents[1] / 'migrations' / '001_checkout.sql'


class PostgresCheckoutService:
    def __init__(self, dsn: str):
        # Sample: Store PostgreSQL DSN and initialize schema check flag.
        self.dsn = dsn

    def _connect(self):
        # Sample: Open one psycopg 3 connection with dict_row row factory.
        return psycopg.connect(self.dsn, row_factory=dict_row, autocommit=False)

    def ensure_schema(self) -> None:
        # Sample: Apply migrations/001_checkout.sql idempotently on PostgreSQL.
        sql = MIGRATION.read_text(encoding='utf-8')
        with self._connect() as conn:
            conn.execute(sql)

    def _next_id(self, prefix: str) -> str:
        # Sample: Generate unique identifier PI-1a2b3c4d for an intent or journal row.
        return f'{prefix}-{secrets.token_hex(4).upper()}'

    def _expire_holds(self, conn) -> None:
        # Sample: Mark HELD reservations whose expires_at <= now() as EXPIRED.
        conn.execute("UPDATE checkout.reservation SET status = 'EXPIRED' WHERE status = 'HELD' AND expires_at <= now()")

    def seed_stock(self, merchant: str, sku: str, total_units: int) -> dict:
        # Sample: Upsert 25 units of SKU-WIDGET-01 for merchant M-ACME in checkout.inventory.
        if total_units < 0:
            raise CheckoutError('Stock cannot be negative')
        with self._connect() as conn:
            conn.execute(
                'INSERT INTO checkout.inventory (merchant, sku, total_units) VALUES (%s, %s, %s) '
                'ON CONFLICT (merchant, sku) DO UPDATE SET total_units = EXCLUDED.total_units',
                (merchant, sku, int(total_units)),
            )
        return self.get_sku(merchant, sku)

    def get_sku(self, merchant: str, sku: str) -> dict:
        # Sample: Read total, active HELD reserved units, and available units for M-ACME SKU-WIDGET-01.
        with self._connect() as conn:
            self._expire_holds(conn)
            inv = conn.execute('SELECT total_units FROM checkout.inventory WHERE merchant = %s AND sku = %s', (merchant, sku)).fetchone()
            total = int(inv['total_units']) if inv else 0
            res = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS qty FROM checkout.reservation WHERE merchant = %s AND sku = %s AND status = 'HELD'",
                (merchant, sku),
            ).fetchone()
            reserved = int(res['qty'])
        return {'merchant': merchant, 'sku': sku, 'total': total, 'reserved': reserved, 'available': total - reserved}

    def list_inventory(self, merchant: str) -> List[dict]:
        # Sample: List all SKUs for merchant M-ACME ordered by sku name.
        with self._connect() as conn:
            self._expire_holds(conn)
            rows = conn.execute(
                "SELECT i.sku, i.total_units AS total, "
                "COALESCE((SELECT SUM(r.quantity) FROM checkout.reservation r "
                "WHERE r.merchant = i.merchant AND r.sku = i.sku AND r.status = 'HELD'), 0) AS reserved "
                'FROM checkout.inventory i WHERE i.merchant = %s ORDER BY i.sku',
                (merchant,),
            ).fetchall()
        return [
            {'merchant': merchant, 'sku': r['sku'], 'total': int(r['total']), 'reserved': int(r['reserved']), 'available': int(r['total']) - int(r['reserved'])}
            for r in rows
        ]

    def _reserve_in_tx(self, conn, merchant: str, sku: str, quantity: int, order_id: str, ttl_seconds: int = 600) -> str:
        # Sample: Lock inventory row FOR UPDATE in transaction. Insert HELD reservation when available >= quantity.
        if quantity <= 0:
            raise CheckoutError('Quantity must be positive')
        self._expire_holds(conn)
        inv = conn.execute('SELECT total_units FROM checkout.inventory WHERE merchant = %s AND sku = %s FOR UPDATE', (merchant, sku)).fetchone()
        total = int(inv['total_units']) if inv else 0
        res = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS qty FROM checkout.reservation WHERE merchant = %s AND sku = %s AND status = 'HELD'",
            (merchant, sku),
        ).fetchone()
        available = total - int(res['qty'])
        if available < quantity:
            raise InsufficientStockError(f'Insufficient stock for {sku}')
        res_id = self._next_id('RES')
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        conn.execute(
            'INSERT INTO checkout.reservation (reservation_id, merchant, sku, quantity, order_id, expires_at, status) '
            "VALUES (%s, %s, %s, %s, %s, %s, 'HELD')",
            (res_id, merchant, sku, int(quantity), order_id, expires_at),
        )
        return res_id

    def create_intent(
        self,
        merchant: str,
        idempotency_key: str,
        amount_cents: int,
        currency: str = 'USD',
        sku: Optional[str] = None,
        quantity: int = 0,
    ) -> tuple:
        # Sample: Lock (merchant, idempotency_key) with pg_advisory_xact_lock. Replay same digest or raise IdempotencyConflictError on mismatch.
        if not idempotency_key:
            raise CheckoutError('Idempotency key is required')
        if amount_cents <= 0:
            raise CheckoutError('Amount in minor units must be positive')
        payload = {'amount_cents': int(amount_cents), 'currency': currency.upper(), 'sku': sku, 'quantity': int(quantity)}
        req_digest = _canonical_digest(payload)

        with self._connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (f'{merchant}:{idempotency_key}',))
            held = conn.execute(
                'SELECT request_digest, intent_id FROM checkout.idempotency WHERE merchant = %s AND idempotency_key = %s',
                (merchant, idempotency_key),
            ).fetchone()
            if held:
                if held['request_digest'] != req_digest:
                    raise IdempotencyConflictError('Idempotency key reused with different payload')
                row = conn.execute('SELECT * FROM checkout.payment_intent WHERE intent_id = %s', (held['intent_id'],)).fetchone()
                return dict(row), True

            intent_id = self._next_id('PI')
            res_id = self._reserve_in_tx(conn, merchant, sku, quantity, intent_id) if (sku and quantity > 0) else None
            conn.execute(
                'INSERT INTO checkout.payment_intent '
                '(intent_id, merchant, amount_cents, currency, captured_cents, refunded_cents, status, sku, quantity, reservation_id) '
                "VALUES (%s, %s, %s, %s, 0, 0, 'AUTHORIZED', %s, %s, %s)",
                (intent_id, merchant, int(amount_cents), currency.upper(), sku, int(quantity), res_id),
            )
            conn.execute(
                'INSERT INTO checkout.idempotency (merchant, idempotency_key, request_digest, intent_id) VALUES (%s, %s, %s, %s)',
                (merchant, idempotency_key, req_digest, intent_id),
            )
            row = conn.execute('SELECT * FROM checkout.payment_intent WHERE intent_id = %s', (intent_id,)).fetchone()
            return dict(row), False

    def _post_journal_in_tx(self, conn, merchant: str, tx_id: str, reason: str, lines: List[tuple]) -> dict:
        # Sample: Insert balanced journal row into checkout.journal_entry inside the active transaction.
        debit_total = sum(amt for (_, side, amt) in lines if side == 'DEBIT')
        credit_total = sum(amt for (_, side, amt) in lines if side == 'CREDIT')
        if debit_total != credit_total:
            raise CheckoutError('Unbalanced journal entry')
        journal_id = self._next_id('JRN')
        postings = [{'account': acct, 'side': side, 'amount_cents': amt} for (acct, side, amt) in lines]
        conn.execute(
            'INSERT INTO checkout.journal_entry '
            '(journal_id, tx_id, merchant, reason, debit_total_cents, credit_total_cents, postings) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)',
            (journal_id, tx_id, merchant, reason, debit_total, credit_total, json.dumps(postings)),
        )
        return {'journal_id': journal_id, 'tx_id': tx_id, 'merchant': merchant, 'reason': reason, 'debit_total_cents': debit_total, 'credit_total_cents': credit_total, 'postings': postings}

    def capture(self, merchant: str, intent_id: str, amount_cents: Optional[int] = None, fee_cents: int = 0) -> dict:
        # Sample: Lock payment_intent FOR UPDATE, capture amount, confirm reservation, and post balanced journal entry.
        with self._connect() as conn:
            intent = conn.execute('SELECT * FROM checkout.payment_intent WHERE intent_id = %s FOR UPDATE', (intent_id,)).fetchone()
            if not intent or intent['merchant'] != merchant:
                raise AccessDeniedError('Payment intent unavailable for merchant')
            target = int(intent['amount_cents']) - int(intent['captured_cents']) if amount_cents is None else int(amount_cents)
            if target <= 0 or int(intent['captured_cents']) + target > int(intent['amount_cents']):
                raise CheckoutError('Invalid capture amount')
            new_captured = int(intent['captured_cents']) + target
            new_status = 'CAPTURED' if new_captured == int(intent['amount_cents']) else 'PARTIALLY_CAPTURED'
            conn.execute('UPDATE checkout.payment_intent SET captured_cents = %s, status = %s WHERE intent_id = %s', (new_captured, new_status, intent_id))
            if intent.get('reservation_id'):
                res = conn.execute('SELECT * FROM checkout.reservation WHERE reservation_id = %s FOR UPDATE', (intent['reservation_id'],)).fetchone()
                if res and res['status'] == 'HELD':
                    conn.execute("UPDATE checkout.reservation SET status = 'CONFIRMED' WHERE reservation_id = %s", (res['reservation_id'],))
                    conn.execute('UPDATE checkout.inventory SET total_units = total_units - %s WHERE merchant = %s AND sku = %s', (res['quantity'], merchant, res['sku']))
            lines = [('receivable', 'DEBIT', target), ('merchant_revenue', 'CREDIT', target)]
            if fee_cents > 0:
                lines.extend([('processing_fee', 'DEBIT', int(fee_cents)), ('fee_payable', 'CREDIT', int(fee_cents))])
            self._post_journal_in_tx(conn, merchant, intent_id, 'CAPTURE', lines)
            updated = conn.execute('SELECT * FROM checkout.payment_intent WHERE intent_id = %s', (intent_id,)).fetchone()
            return dict(updated)

    def refund(self, merchant: str, intent_id: str, amount_cents: int) -> dict:
        # Sample: Lock payment_intent FOR UPDATE, refuse over-refund, update refunded_cents, and post reversal journal entry.
        amount = int(amount_cents)
        if amount <= 0:
            raise CheckoutError('Refund amount must be positive')
        with self._connect() as conn:
            intent = conn.execute('SELECT * FROM checkout.payment_intent WHERE intent_id = %s FOR UPDATE', (intent_id,)).fetchone()
            if not intent or intent['merchant'] != merchant:
                raise AccessDeniedError('Payment intent unavailable for merchant')
            if int(intent['refunded_cents']) + amount > int(intent['captured_cents']):
                raise OverRefundError('Refund exceeds captured amount')
            new_refunded = int(intent['refunded_cents']) + amount
            new_status = 'REFUNDED' if new_refunded == int(intent['captured_cents']) else 'PARTIALLY_REFUNDED'
            conn.execute('UPDATE checkout.payment_intent SET refunded_cents = %s, status = %s WHERE intent_id = %s', (new_refunded, new_status, intent_id))
            self._post_journal_in_tx(conn, merchant, intent_id, 'REFUND', [('merchant_revenue', 'DEBIT', amount), ('receivable', 'CREDIT', amount)])
            updated = conn.execute('SELECT * FROM checkout.payment_intent WHERE intent_id = %s', (intent_id,)).fetchone()
            return dict(updated)

    def list_intents(self, merchant: str) -> List[dict]:
        # Sample: List payment intents for merchant M-ACME ordered newest first.
        with self._connect() as conn:
            rows = conn.execute('SELECT * FROM checkout.payment_intent WHERE merchant = %s ORDER BY created_at DESC, intent_id DESC', (merchant,)).fetchall()
            return [dict(r) for r in rows]

    def list_ledger(self, merchant: str) -> List[dict]:
        # Sample: List balanced double-entry journal rows for merchant M-ACME ordered newest first.
        with self._connect() as conn:
            rows = conn.execute('SELECT * FROM checkout.journal_entry WHERE merchant = %s ORDER BY created_at DESC, journal_id DESC', (merchant,)).fetchall()
            return [dict(r) for r in rows]

    def save_session(self, token_digest: bytes, merchant: str, ttl_seconds: int = 28800) -> None:
        # Sample: Persist session token digest in checkout.session so sign-in survives API restarts.
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        with self._connect() as conn:
            conn.execute('INSERT INTO checkout.session (token_digest, merchant, expires_at) VALUES (%s, %s, %s)', (token_digest, merchant, expires_at))

    def lookup_session(self, token_digest: bytes) -> Optional[str]:
        # Sample: Resolve non-expired session token digest to merchant identifier.
        with self._connect() as conn:
            row = conn.execute('SELECT merchant FROM checkout.session WHERE token_digest = %s AND expires_at > now()', (token_digest,)).fetchone()
            return row['merchant'] if row else None

    def delete_session(self, token_digest: bytes) -> None:
        # Sample: Delete session token digest on POST sign-out.
        with self._connect() as conn:
            conn.execute('DELETE FROM checkout.session WHERE token_digest = %s', (token_digest,))
