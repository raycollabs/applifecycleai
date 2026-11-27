import argparse
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json


VERSION = 'usd-futures-book-close/v1'
ZERO = Decimal(0)

FILLS = """
SELECT t.id, t.instrument_code, t.side, t.quantity, t.contract_size, t.price,
       t.currency, t.trade_date, t.recorded_at
FROM trade_book.trade t JOIN trade_book.trader u ON u.code = t.trader_code
WHERE u.desk = %s AND t.instrument_kind = 'FUTURES' AND t.status = 'BOOKED'
  AND t.trade_date <= %s AND t.recorded_at <= %s
ORDER BY t.id
"""
FEES = """
SELECT f.id, t.instrument_code, f.amount, f.currency, f.recorded_at
FROM trade_book.trade_fee f
JOIN trade_book.trade t ON t.id = f.trade_id
JOIN trade_book.trader u ON u.code = t.trader_code
WHERE u.desk = %s AND t.instrument_kind = 'FUTURES' AND t.status = 'BOOKED'
  AND t.trade_date = %s AND t.recorded_at <= %s AND f.recorded_at <= %s
ORDER BY f.id
"""
MARKS = """
SELECT id, instrument_code, settlement_date, settlement_price, cash_amount, currency, recorded_at
FROM trade_book.futures_settlement
WHERE desk = %s AND settlement_date IN (%s, %s) AND recorded_at <= %s
ORDER BY instrument_code, settlement_date, id
"""


def canonical(value):
    # Sample: Write Decimal 80.90 as the string "80.90" in JSON.
    return json.dumps(value, default=str, sort_keys=True, separators=(',', ':')).encode('utf-8')


def calculate(book, business_date, prior_date, known_at, fills, fees, marks):
    # Sample: Mark two opening lots and one new fill. Show position P&L, fill P&L, fees, and cash.
    by_mark = {(row['instrument_code'], row['settlement_date']): row for row in marks}
    instruments = sorted({row['instrument_code'] for row in fills})
    rows, issues = [], []
    for code in instruments:
        opening = ZERO
        todays_fills = []
        all_fills = [row for row in fills if row['instrument_code'] == code]
        for row in all_fills:
            units = row['quantity'] * row['contract_size'] * (1 if row['side'] == 'BUY' else -1)
            if row['trade_date'] < business_date:
                opening += units
            else:
                todays_fills.append((units, row['price']))
        close = by_mark.get((code, business_date))
        prior = by_mark.get((code, prior_date))
        charges = [row for row in fees if row['instrument_code'] == code]
        inputs = all_fills + charges + [row for row in (close, prior) if row is not None]
        if any(row['currency'] != 'USD' for row in inputs):
            issues.append({'instrument': code, 'code': 'UNSUPPORTED_CURRENCY'})
            continue
        if (opening or todays_fills) and close is None:
            issues.append({'instrument': code, 'code': 'MISSING_CLOSE_MARK'})
        if opening and prior is None:
            issues.append({'instrument': code, 'code': 'MISSING_PRIOR_MARK'})
        if any(issue['instrument'] == code for issue in issues):
            continue
        start_pnl = opening * (close['settlement_price'] - prior['settlement_price']) if opening else ZERO
        fill_pnl = sum((units * (close['settlement_price'] - price) for units, price in todays_fills), ZERO)
        fee_total = sum((row['amount'] for row in charges), ZERO)
        rows.append({'instrument': code, 'opening_units': opening,
                     'closing_units': opening + sum((units for units, _ in todays_fills), ZERO),
                     'opening_pnl': start_pnl, 'fill_pnl': fill_pnl, 'fees': fee_total,
                     'net_pnl': start_pnl + fill_pnl - fee_total,
                     'settlement_cash': close['cash_amount'] if close else None})

    identity = {'version': VERSION, 'book': book, 'business_date': business_date,
                'prior_business_date': prior_date, 'known_at': known_at}
    digest = sha256(canonical([identity, fills, fees, marks])).hexdigest()
    return {**identity, 'input_sha256': digest, 'currency': 'USD', 'issues': issues, 'instruments': rows,
            'status': 'INCOMPLETE' if issues else 'COMPLETE' if rows else 'EMPTY',
            'net_pnl': None if issues else sum((row['net_pnl'] for row in rows), ZERO)}


def read_close(conn, book, business_date, prior_date, known_at):
    # Sample: Read the November 3 close and November 2 marks in one read-only snapshot.
    if not book or prior_date >= business_date or known_at.utcoffset() is None:
        raise ValueError('Supply a book, an earlier business date, and a cutoff with a time zone')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        fills = conn.execute(FILLS, (book, business_date, known_at)).fetchall()
        fees = conn.execute(FEES, (book, business_date, known_at, known_at)).fetchall()
        marks = conn.execute(MARKS, (book, prior_date, business_date, known_at)).fetchall()
    return calculate(book, business_date, prior_date, known_at, fills, fees, marks)


def main():
    # Sample: Read the report for book SYN-OIL. Print one JSON receipt.
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    import psycopg
    from src.tradebook.db import database_url

    parser = argparse.ArgumentParser(description='Read one USD futures book close')
    parser.add_argument('--book', required=True)
    parser.add_argument('--business-date', required=True, type=date.fromisoformat)
    parser.add_argument('--prior-business-date', required=True, type=date.fromisoformat)
    parser.add_argument('--known-at', required=True, type=datetime.fromisoformat)
    args = parser.parse_args()
    url = database_url()
    target = conninfo_to_dict(url)
    if target.get('host') != 'db' or target.get('dbname') != 'postgres':
        raise ValueError('Use the Docker trading demo database')
    with psycopg.connect(url, row_factory=dict_row) as conn:
        report = read_close(conn, args.book, args.business_date, args.prior_business_date, args.known_at)
    print(canonical(report).decode('utf-8'))


if __name__ == '__main__':
    main()
