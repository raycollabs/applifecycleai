"""Sample: Reserve 2 units of SKU-1, create idempotent intent for 5000 cents, capture 5000, refund 1500, and check balanced debit and credit ledger totals."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Dict, List, Optional


class CheckoutError(ValueError):
    status_code = 400
    code = 'checkout_error'


class InsufficientStockError(CheckoutError):
    status_code = 409
    code = 'insufficient_stock'


class IdempotencyConflictError(CheckoutError):
    status_code = 409
    code = 'idempotency_conflict'


class OverRefundError(CheckoutError):
    status_code = 409
    code = 'over_refund'


class AccessDeniedError(CheckoutError):
    status_code = 403
    code = 'access_denied'


def _canonical_digest(payload: dict) -> str:
    # Sample: Hash {"amount": 5000, "sku": "SKU-1"}. Return 64-character SHA-256 hex digest.
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


@dataclass
class Reservation:
    reservation_id: str
    merchant: str
    sku: str
    quantity: int
    order_id: str
    expires_at: datetime
    status: str = 'HELD'


class CheckoutService:
    def __init__(self, now_fn=None):
        # Sample: Initialize empty inventory, intents, reservations, and double-entry ledger tables.
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._stock: Dict[tuple, int] = {}
        self._reservations: Dict[str, Reservation] = {}
        self._idempotency: Dict[tuple, tuple] = {}
        self._intents: Dict[str, dict] = {}
        self._ledger: List[dict] = []
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        # Sample: Increment sequence counter from 0 to 1. Return CHK-000001.
        self._seq += 1
        return f'{prefix}-{self._seq:06d}'

    def _expire_holds(self) -> None:
        # Sample: Hold R-1 expires before current time. Mark R-1 EXPIRED and exclude it from reserved units.
        now = self._now()
        for res in self._reservations.values():
            if res.status == 'HELD' and res.expires_at <= now:
                res.status = 'EXPIRED'

    def seed_stock(self, merchant: str, sku: str, total_units: int) -> dict:
        # Sample: Seed 10 units of SKU-1 for merchant M-1. Return SKU summary with available 10.
        if total_units < 0:
            raise CheckoutError('Stock cannot be negative')
        self._stock[(merchant, sku)] = int(total_units)
        return self.get_sku(merchant, sku)

    def get_sku(self, merchant: str, sku: str) -> dict:
        # Sample: Seed 10 units and hold 3 units for M-1 SKU-1. Return total 10, reserved 3, available 7.
        self._expire_holds()
        total = self._stock.get((merchant, sku), 0)
        reserved = sum(
            r.quantity for r in self._reservations.values()
            if r.merchant == merchant and r.sku == sku and r.status == 'HELD'
        )
        return {'merchant': merchant, 'sku': sku, 'total': total, 'reserved': reserved, 'available': total - reserved}

    def list_inventory(self, merchant: str) -> List[dict]:
        # Sample: List SKUs belonging to merchant M-1. Exclude merchant M-2 SKUs.
        skus = sorted(sku for (m, sku) in self._stock if m == merchant)
        return [self.get_sku(merchant, sku) for sku in skus]

    def reserve_stock(self, merchant: str, sku: str, quantity: int, order_id: str, ttl_seconds: int = 600) -> dict:
        # Sample: Reserve 2 units of SKU-1 with 7 available. Return HELD reservation. Reserve 8 units. Raise InsufficientStockError.
        if quantity <= 0:
            raise CheckoutError('Quantity must be positive')
        info = self.get_sku(merchant, sku)
        if info['available'] < quantity:
            raise InsufficientStockError(f'Insufficient stock for {sku}')
        res_id = self._next_id('RES')
        res = Reservation(
            reservation_id=res_id,
            merchant=merchant,
            sku=sku,
            quantity=int(quantity),
            order_id=order_id,
            expires_at=self._now() + timedelta(seconds=ttl_seconds),
        )
        self._reservations[res_id] = res
        return {
            'reservation_id': res.reservation_id,
            'merchant': merchant,
            'sku': sku,
            'quantity': res.quantity,
            'order_id': order_id,
            'status': res.status,
        }

    def release_reservation(self, merchant: str, reservation_id: str) -> dict:
        # Sample: Release HELD reservation RES-000001 for M-1. Return status RELEASED and restore available stock.
        res = self._reservations.get(reservation_id)
        if not res or res.merchant != merchant:
            raise AccessDeniedError('Reservation unavailable for merchant')
        if res.status == 'HELD':
            res.status = 'RELEASED'
        return {'reservation_id': res.reservation_id, 'status': res.status}

    def create_intent(
        self,
        merchant: str,
        idempotency_key: str,
        amount_cents: int,
        currency: str = 'USD',
        sku: Optional[str] = None,
        quantity: int = 0,
    ) -> tuple:
        # Sample: Create intent with key K-1 for 5000 cents. Replay K-1. Return same intent and replayed True. Reuse K-1 with 6000 cents. Raise IdempotencyConflictError.
        if not idempotency_key:
            raise CheckoutError('Idempotency key is required')
        if amount_cents <= 0:
            raise CheckoutError('Amount in minor units must be positive')
        payload = {
            'amount_cents': int(amount_cents),
            'currency': currency.upper(),
            'sku': sku,
            'quantity': int(quantity),
        }
        req_digest = _canonical_digest(payload)
        idem_key = (merchant, idempotency_key)
        if idem_key in self._idempotency:
            old_digest, intent_id = self._idempotency[idem_key]
            if old_digest != req_digest:
                raise IdempotencyConflictError('Idempotency key reused with different payload')
            return dict(self._intents[intent_id]), True

        intent_id = self._next_id('PI')
        reservation_id = None
        if sku and quantity > 0:
            res = self.reserve_stock(merchant, sku, quantity, intent_id)
            reservation_id = res['reservation_id']

        intent = {
            'intent_id': intent_id,
            'merchant': merchant,
            'amount_cents': int(amount_cents),
            'currency': currency.upper(),
            'captured_cents': 0,
            'refunded_cents': 0,
            'status': 'AUTHORIZED',
            'sku': sku,
            'quantity': int(quantity),
            'reservation_id': reservation_id,
        }
        self._intents[intent_id] = intent
        self._idempotency[idem_key] = (req_digest, intent_id)
        return dict(intent), False

    def _post_journal(self, merchant: str, tx_id: str, reason: str, lines: List[tuple]) -> dict:
        # Sample: Post debit 5000 to receivable and credit 5000 to revenue. Check debits equal credits.
        debit_total = sum(amt for (_, side, amt) in lines if side == 'DEBIT')
        credit_total = sum(amt for (_, side, amt) in lines if side == 'CREDIT')
        if debit_total != credit_total:
            raise CheckoutError('Unbalanced journal entry')
        entry = {
            'journal_id': self._next_id('JRN'),
            'tx_id': tx_id,
            'merchant': merchant,
            'reason': reason,
            'postings': [{'account': acct, 'side': side, 'amount_cents': amt} for (acct, side, amt) in lines],
            'debit_total_cents': debit_total,
            'credit_total_cents': credit_total,
        }
        self._ledger.append(entry)
        return entry

    def capture(self, merchant: str, intent_id: str, amount_cents: Optional[int] = None, fee_cents: int = 0) -> dict:
        # Sample: Capture 5000 cents on PI-000001 with 150 cents fee. Confirm reservation and post balanced journal entries.
        intent = self._intents.get(intent_id)
        if not intent or intent['merchant'] != merchant:
            raise AccessDeniedError('Payment intent unavailable for merchant')
        target = intent['amount_cents'] - intent['captured_cents'] if amount_cents is None else int(amount_cents)
        if target <= 0 or intent['captured_cents'] + target > intent['amount_cents']:
            raise CheckoutError('Invalid capture amount')
        intent['captured_cents'] += target
        intent['status'] = 'CAPTURED' if intent['captured_cents'] == intent['amount_cents'] else 'PARTIALLY_CAPTURED'
        if intent.get('reservation_id'):
            res = self._reservations.get(intent['reservation_id'])
            if res and res.status == 'HELD':
                res.status = 'CONFIRMED'
                self._stock[(merchant, res.sku)] -= res.quantity
        lines = [('receivable', 'DEBIT', target), ('merchant_revenue', 'CREDIT', target)]
        if fee_cents > 0:
            lines.extend([('processing_fee', 'DEBIT', int(fee_cents)), ('fee_payable', 'CREDIT', int(fee_cents))])
        self._post_journal(merchant, intent_id, 'CAPTURE', lines)
        return dict(intent)

    def refund(self, merchant: str, intent_id: str, amount_cents: int) -> dict:
        # Sample: Refund 1500 cents on captured 5000 cents intent. Return refunded_cents 1500. Refund 4000 more. Raise OverRefundError.
        intent = self._intents.get(intent_id)
        if not intent or intent['merchant'] != merchant:
            raise AccessDeniedError('Payment intent unavailable for merchant')
        amount = int(amount_cents)
        if amount <= 0:
            raise CheckoutError('Refund amount must be positive')
        if intent['refunded_cents'] + amount > intent['captured_cents']:
            raise OverRefundError('Refund exceeds captured amount')
        intent['refunded_cents'] += amount
        intent['status'] = 'REFUNDED' if intent['refunded_cents'] == intent['captured_cents'] else 'PARTIALLY_REFUNDED'
        self._post_journal(merchant, intent_id, 'REFUND', [('merchant_revenue', 'DEBIT', amount), ('receivable', 'CREDIT', amount)])
        return dict(intent)

    def list_intents(self, merchant: str) -> List[dict]:
        # Sample: Return all payment intents belonging to merchant M-1.
        return [dict(i) for i in self._intents.values() if i['merchant'] == merchant]

    def list_ledger(self, merchant: str) -> List[dict]:
        # Sample: Return all double-entry journal rows belonging to merchant M-1.
        return [dict(e) for e in self._ledger if e['merchant'] == merchant]
