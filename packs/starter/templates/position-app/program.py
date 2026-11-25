import json
import sqlite3
import sys


def integer(value, positive=False):
    # Sample: Read a lot size of 100. Accept 100. Reject True, zero, and 1.5.
    if type(value) is not int or abs(value) > 1000000000 or (positive and value <= 0):
        raise ValueError('Use bounded integer lots and positive integer lot sizes')
    return value


def quantity(lots, lot_size):
    # Sample: Convert 2 lots of 100. Return 200 shares. Keep -2 lots negative.
    return lots * lot_size


def book(db, command):
    # Sample: Book T-101 with 2 lots of 100. Record 200 shares. Reject duplicate IDs.
    identity = command['id']
    if not isinstance(identity, str) or not identity.strip() or len(identity) > 100:
        raise ValueError('Trade ID must contain 1 to 100 characters')
    lots, size = integer(command['lots']), integer(command['lot_size'], True)
    if lots == 0:
        raise ValueError('An opening trade must have nonzero lots')
    db.execute('INSERT INTO trade VALUES(?,?,?)', (identity, size, quantity(lots, size)))


def correct(db, command):
    # Sample: Remove 1 lot from T-101 at size 100. Change 200 shares to 100 shares.
    row = db.execute('SELECT lot_size,shares FROM trade WHERE id=?', (command['id'],)).fetchone()
    if row is None:
        raise ValueError('Correction requires an existing trade')
    delta = integer(command['delta_lots'])
    db.execute('UPDATE trade SET shares=? WHERE id=?', (row[1] - quantity(delta, row[0]), command['id']))


def positions(db):
    # Sample: Read T-101 at 200 shares and T-102 at -100. Return netShares 100.
    rows = [{'id': row[0], 'lot_size': row[1], 'shares': row[2]}
            for row in db.execute('SELECT id,lot_size,shares FROM trade ORDER BY id')]
    return {'trades': rows, 'netShares': sum(row['shares'] for row in rows)}


def execute(value):
    # Sample: Book T-101, then correct by 1 lot. Return one trade with 100 shares.
    if not isinstance(value, dict):
        return {'error': 'Use a JSON object with a commands list', 'category': 'INVALID_INPUT'}
    commands = value.get('commands', [])
    if not isinstance(commands, list) or len(commands) > 100:
        return {'error': 'Command budget is 100', 'category': 'INVALID_INPUT'}
    db = sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TABLE trade(id TEXT PRIMARY KEY,lot_size INTEGER NOT NULL,shares INTEGER NOT NULL)')
        for command in commands:
            action = command['action']
            if action == 'book':
                book(db, command)
            elif action == 'correct':
                correct(db, command)
            else:
                raise ValueError('Unsupported trade action')
        return positions(db)
    except sqlite3.IntegrityError:
        return {'error': 'Trade ID already exists', 'category': 'INVALID_INPUT'}
    except OverflowError:
        return {'error': 'Position exceeds the local integer limit', 'category': 'INVALID_INPUT'}
    except (ValueError, KeyError, TypeError) as error:
        return {'error': str(error), 'category': 'INVALID_INPUT'}
    finally:
        db.close()


def main():
    # Sample: Read a JSON booking from stdin. Write the resulting positions as JSON.
    print(json.dumps(execute(json.load(sys.stdin))))


if __name__ == '__main__':
    main()
