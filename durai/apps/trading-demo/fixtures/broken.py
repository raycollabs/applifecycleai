"""F-101 synthetic producer: quantities incorrectly keep lot units."""
import json
import sys


def book(lots, lot_size):
    return lots


def correct(shares, lots, lot_size):
    return shares - lots


if __name__ == '__main__':
    request = json.load(sys.stdin)
    result = book(request['lots'], request['lot_size'])
    if 'correction_lots' in request:
        result = correct(result, request['correction_lots'], request['lot_size'])
    print(json.dumps({'shares': result}))
