"""F-101 accepted fixture: book and correct in canonical shares."""
import json
import sys


def book(lots, lot_size):
    return lots * lot_size


def correct(shares, lots, lot_size):
    return shares - lots * lot_size


if __name__ == '__main__':
    request = json.load(sys.stdin)
    result = book(request['lots'], request['lot_size'])
    if 'correction_lots' in request:
        result = correct(result, request['correction_lots'], request['lot_size'])
    print(json.dumps({'shares': result}))
