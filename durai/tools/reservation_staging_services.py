"""Sample: Run the API, relay, or consumer of the staging stack against the broker named in the environment."""
import argparse
import os
from pathlib import Path
import signal
import sys
from threading import Event, Thread
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools'))

from reservation_pilot_api import serve_forever
from reservation_pilot_broker import consume_entries, make_broker, relay


def read_secret(variable):
    # Sample: Read the token from the file named by DURAI_TOKEN_FILE. Strip the line end.
    return Path(os.environ[variable]).read_text(encoding='utf-8').strip()


def broker_from_environment():
    # Sample: Set DURAI_BROKER=redis-stream and DURAI_REDIS_HOST=broker. Return a connected broker with its group.
    kind = os.environ.get('DURAI_BROKER','redis-stream')
    password = read_secret('DURAI_REDIS_PASSWORD_FILE') if os.environ.get('DURAI_REDIS_PASSWORD_FILE') else None
    broker = make_broker(kind,host=os.environ['DURAI_REDIS_HOST'],port=int(os.environ.get('DURAI_REDIS_PORT','6379')),
                         password=password)
    broker.start()
    return broker


def guarded(step, stop, pause=0.5):
    # Sample: Run one step. Print the error class on a failure and wait 0.5 seconds before the next step.
    try:
        return step()
    except Exception as error:
        print('step failed: '+type(error).__name__,file=sys.stderr,flush=True)
        stop.wait(pause)
        return 0


def relay_loop(broker, stop, *, lease_seconds=3, idle=0.05, rounds=None):
    # Sample: Publish 2 pending events, then wait 0.05 seconds when the outbox is empty.
    count = 0
    while not stop.is_set() and (rounds is None or count<rounds):
        count += 1
        if not guarded(lambda:relay(broker,lease_seconds=lease_seconds),stop):
            stop.wait(idle)


def relay_workers(broker, stop, count, **options):
    # Sample: Run 4 relay loops together. Each loop claims different stream heads with SKIP LOCKED.
    workers = [Thread(target=relay_loop,args=(broker,stop),kwargs=options) for _ in range(count)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()


def consumer_loop(broker, owner, stop, *, idle_ms=3000, idle=0.05, rounds=None):
    # Sample: Consume 2 new entries. Every second claim entries idle for 3000 ms from a stopped owner.
    count = 0
    next_recover = 0
    while not stop.is_set() and (rounds is None or count<rounds):
        count += 1
        done = 0
        if time.monotonic()>=next_recover:
            done += len(guarded(lambda:consume_entries(broker,owner,recover=True,min_idle_ms=idle_ms),stop) or [])
            next_recover = time.monotonic()+1
        done += len(guarded(lambda:consume_entries(broker,owner),stop) or [])
        if not done:
            stop.wait(idle)


def main():
    # Sample: Run "relay" until a stop signal. Run "relay-stop" once and exit 71 after Redis accepts the event.
    parser = argparse.ArgumentParser()
    parser.add_argument('role',choices=['api','relay','consumer','relay-stop','consumer-stop'])
    args = parser.parse_args()
    stop = Event()
    signal.signal(signal.SIGTERM,lambda *_:(stop.set(),sys.exit(0)))
    if args.role=='api':
        serve_forever(read_secret('DURAI_TOKEN_FILE'),'0.0.0.0',8080)
        return
    broker = broker_from_environment()
    if args.role=='relay-stop':
        relay(broker,lose_ack=True)
    elif args.role=='consumer-stop':
        consume_entries(broker,'stopped-'+str(os.getpid()),lose_ack=True)
    elif args.role=='relay':
        relay_workers(broker,stop,int(os.environ.get('DURAI_RELAY_WORKERS','4')))
    else:
        consumer_loop(broker,'consumer-'+str(os.getpid()),stop)


if __name__=='__main__':
    main()
