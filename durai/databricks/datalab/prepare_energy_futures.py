import argparse
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path


SEED = Path(__file__).with_name('energy_futures_seed.json')
TABLES = ('contracts', 'traders', 'bookings', 'price_events', 'settlements')


def unique(rows, key):
    # Sample: Read two contract rows with the same code. Refuse the input.
    index = {row[key]: row for row in rows}
    if len(index) != len(rows):
        raise ValueError(f'Duplicate {key}')
    return index


def check_price(value, contract):
    # Sample: Check 74.32 with tick 0.01. Refuse 74.321.
    price = Decimal(value)
    tick = Decimal(contract['minimum_tick'])
    if not price.is_finite() or price <= 0 or price % tick:
        raise ValueError(f"Price does not match the tick for {contract['instrument_code']}")
    return price


def validate_booking(row, contracts, traders):
    # Sample: Book two CLF27 contracts at 74.32. Check the trader, lot count, and tick.
    contract = contracts[row['instrument_code']]
    if row['trader_code'] not in traders or row['side'] not in {'BUY', 'SELL'}:
        raise ValueError('Invalid trader or side')
    if type(row['quantity']) is not int or row['quantity'] < 1:
        raise ValueError('Quantity must be a positive number of whole contracts')
    if date.fromisoformat(row['trade_date']) >= date.fromisoformat(contract['delivery_month']):
        raise ValueError('Trade date must be before the delivery month')
    check_price(row['price'], contract)


def validate_replay(case, bookings, contracts, traders):
    # Sample: Read K-1. Make an equal request and one with a changed side. Keep K-1 on both.
    original = bookings[case['booking_key']]
    changed = original | case['changed_fields']
    validate_booking(changed, contracts, traders)
    if (changed == original or changed['idempotency_key'] != original['idempotency_key']
            or case['same_request_result'] != 'replay' or case['changed_request_result'] != 'conflict'):
        raise ValueError('The replay example needs equal and changed requests under the same key')
    return [{'request': original, 'expected': 'replay'}, {'request': changed, 'expected': 'conflict'}]


def validate_price_correction(events, contracts):
    # Sample: Add P-2 after P-1 for the same price time. Link P-2 to P-1.
    seen, corrected = {}, set()
    for event in events:
        code = event['instrument_code']
        check_price(event['price'], contracts[code])
        if event['source'] != 'SYNTHETIC':
            raise ValueError('Use a synthetic price source')
        seen_at = datetime.fromisoformat(event['known_at'])
        price_time = datetime.fromisoformat(event['price_time'])
        if seen_at < price_time:
            raise ValueError('A price must be known after its price time')
        parent_key = event['supersedes_message_key']
        if parent_key is not None:
            parent = seen.get(parent_key)
            if (parent is None or parent_key in corrected or parent['instrument_code'] != code
                    or parent['price_time'] != event['price_time']
                    or datetime.fromisoformat(parent['known_at']) >= seen_at):
                raise ValueError('A correction needs one earlier price for the same instrument and time')
            corrected.add(parent_key)
        seen[event['message_key']] = event


def validate_settlement(row, contracts, bookings, traders):
    # Sample: Settle 2,000 long barrels from 74.32 to 74.47. Check 300 USD.
    contract = contracts[row['instrument_code']]
    prior = [trade for trade in bookings
             if trade['instrument_code'] == row['instrument_code']
             and traders[trade['trader_code']]['desk'] == row['desk']
             and trade['trade_date'] < row['settlement_date']]
    lots = sum(trade['quantity'] if trade['side'] == 'BUY' else -trade['quantity'] for trade in prior)
    opened = lots * contract['contract_size']
    reference = check_price(row['reference_price'], contract)
    price = check_price(row['settlement_price'], contract)
    if not prior or (price - reference) * opened != Decimal(row['expected_cash_usd']):
        raise ValueError('Settlement cash does not match open units and price change')


def json_bytes(value):
    # Sample: Write one row as one line of UTF-8 JSON.
    return (json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')


def prepare(destination, source=SEED):
    # Sample: Export four contracts and two settlements. Check the file row counts and digests.
    source_bytes = source.read_bytes()
    data = json.loads(source_bytes)
    if data['version'] != 'durai-energy-futures/v1' or data['data_class'] != 'synthetic':
        raise ValueError('Use the declared synthetic energy futures seed')
    contracts = unique(data['contracts'], 'instrument_code')
    traders = unique(data['traders'], 'trader_code')
    bookings = unique(data['bookings'], 'idempotency_key')
    unique(data['price_events'], 'message_key')
    unique(data['settlements'], 'settlement_key')
    for row in bookings.values():
        validate_booking(row, contracts, traders)
    replays = validate_replay(data['replay'], bookings, contracts, traders)
    validate_price_correction(data['price_events'], contracts)
    for row in data['settlements']:
        validate_settlement(row, contracts, bookings.values(), traders)

    destination.mkdir(parents=True, exist_ok=False)
    receipt = {'source_sha256': sha256(source_bytes).hexdigest(), 'output_kind': 'durai_synthetic_seed',
               'version': data['version'], 'files': {}}
    inputs = {name: data[name] for name in TABLES}
    for name, rows in (inputs | {'replay_cases': replays}).items():
        body = b''.join(json_bytes(row) for row in rows)
        path = destination / (name + '.jsonl')
        path.write_bytes(body)
        receipt['files'][path.name] = {'rows': len(rows), 'sha256': sha256(body).hexdigest()}
    (destination / 'receipt.json').write_bytes(json_bytes(receipt))
    return receipt


def main():
    # Sample: Run the exporter with a new output path. Read the receipt.
    parser = argparse.ArgumentParser(description='Prepare synthetic energy futures inputs')
    parser.add_argument('destination', type=Path)
    values = parser.parse_args()
    print(json.dumps(prepare(values.destination), indent=2))


if __name__ == '__main__':
    main()
