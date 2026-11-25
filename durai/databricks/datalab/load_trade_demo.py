from decimal import Decimal
import json
import sys


def load(data, conn):
    # Sample: Load four bookings. Load the batch again. Check zero new bookings.
    from src.tradebook import booking, prices, settlement

    results = {'bookings': 0, 'price_events': 0, 'settlements': 0}
    traders = {row['desk']: row['trader_code'] for row in data['traders']}
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('durai_energy_futures_v1'))")
        for row in data['contracts']:
            expected = (row['product'], 'FUTURES', Decimal(row['contract_size']), row['currency'], True)
            conn.execute(
                'INSERT INTO trade_book.instrument(code,name,kind,contract_size,currency) '
                'VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (row['instrument_code'], *expected[:4]),
            )
            actual = conn.execute(
                'SELECT name,kind,contract_size,currency,enabled FROM trade_book.instrument WHERE code=%s',
                (row['instrument_code'],),
            ).fetchone()
            if actual != expected:
                raise ValueError('Instrument differs from the synthetic seed')
        for row in data['traders']:
            code, desk = row['trader_code'], row['desk']
            conn.execute(
                'INSERT INTO trade_book.trader(code,name,desk) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                (code, code, desk),
            )
            if conn.execute('SELECT name,desk,enabled FROM trade_book.trader WHERE code=%s', (code,)).fetchone() != (code, desk, True):
                raise ValueError('Trader differs from the synthetic seed')
            conn.execute(
                "INSERT INTO trade_book.trader_permission(trader_code,desk,instrument_kind) "
                "VALUES (%s,%s,'FUTURES') ON CONFLICT DO NOTHING", (code, desk),
            )
        for row in data['bookings']:
            results['bookings'] += booking.book_trade(conn, row).created
        for row in data['price_events']:
            result = prices.record_price(
                conn, row | {'currency': 'USD', 'corrects': row['supersedes_message_key']},
                recorded_by=traders['SYN-OIL'],
            )
            results['price_events'] += result.created
        for row in data['settlements']:
            result = settlement.settle_day(conn, row | {
                'idempotency_key': row['settlement_key'], 'trader_code': traders[row['desk']],
            })
            if Decimal(result.settlement['cash_amount']) != Decimal(row['expected_cash_usd']):
                raise ValueError('Trade-book settlement differs from the synthetic seed')
            results['settlements'] += result.created
    return results


def main():
    # Sample: Run inside the trading demo container. Load the synthetic seed from stdin.
    from psycopg.conninfo import conninfo_to_dict
    from src.tradebook.db import connect, database_url

    target = conninfo_to_dict(database_url())
    if target.get('host') != 'db' or target.get('dbname') != 'postgres':
        raise ValueError('Use the Docker trading demo database')
    data = json.load(sys.stdin)
    with connect() as conn:
        result = load(data, conn)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
