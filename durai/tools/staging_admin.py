"""Sample: Apply the Durai schemas to the staging database. Hold one canary seat."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from durai.common import digest
from durai.db import connect, prepare

DB = 'durai_test'
SCHEMAS = ('idempotency/v1.sql','events/v1.sql','reservations/v1.sql','reservations/staging.sql')


def schema():
    # Sample: Apply the core migrations and four blueprints. Return the digest of each blueprint file.
    prepare(DB)
    with connect('postgres',DB) as db:
        for name in SCHEMAS:
            db.execute((ROOT/'blueprints'/name).read_text(),prepare=False)
    return {name:digest((ROOT/'blueprints'/name).read_bytes()) for name in SCHEMAS}


def canary(seat, key):
    # Sample: Hold seat "stg.ab12cd34.canary" for 86400 seconds with key "canary:ab12cd34". Return the order ID.
    from durai.reservations import hold_seat
    outcome = hold_seat(seat,86400,key)['outcome']
    if outcome.get('result')!='HELD':
        raise ValueError('Canary seat was unavailable')
    return {'orderId':outcome['order']['orderId'],'seatId':seat}


def main():
    # Sample: Run "schema" and print its digests as JSON. Run "canary SEAT KEY" and print the order.
    command = sys.argv[1] if len(sys.argv)>1 else ''
    if command=='schema' and len(sys.argv)==2:
        print(json.dumps(schema()))
    elif command=='canary' and len(sys.argv)==4:
        print(json.dumps(canary(sys.argv[2],sys.argv[3])))
    else:
        raise SystemExit('Use: schema | canary SEAT KEY')


if __name__=='__main__':
    main()
