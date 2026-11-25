"""Sample: Verify the staging canary, race two holds, and measure a named-seat workload."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from threading import Barrier, Lock
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


def digest(value):
    # Sample: Hash the retained result for request K-1.
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def identifier(value):
    # Sample: Accept the synthetic seat name fixture.A1.
    return isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}',value)


def uuid_text(value):
    # Sample: Check the canonical order ID from the staging fixture.
    return isinstance(value,str) and str(UUID(value))==value


def number(value, minimum, maximum):
    # Sample: Accept 20 requests per second within the stated bounds.
    return type(value) in (int,float) and minimum<=value<=maximum and math.isfinite(value)


def validate_profile(profile):
    # Sample: Validate two load seats, one race seat, and an HTTPS staging target.
    required = {'baseUrl','canaryOrderId','canarySeatId','raceSeatId','loadSeats','runId','clients',
                'requestsPerSecond','p95LimitMs','queueAgeLimitSeconds','replicaLagLimitBytes','holdSeconds'}
    if not isinstance(profile,dict) or profile.keys()!=required:
        raise ValueError('Use the specified staging profile fields')
    raw = profile['baseUrl']
    if not isinstance(raw,str) or re.search(r'\s',raw):
        raise ValueError('Use a staging HTTPS base URL')
    base = urlsplit(raw)
    if (base.scheme!='https' or not base.hostname or base.username is not None or base.password is not None
            or base.query or base.fragment or '\\' in raw):
        raise ValueError('Use a staging HTTPS base URL without credentials, query, or fragment')
    if not uuid_text(profile['runId']) or not uuid_text(profile['canaryOrderId']):
        raise ValueError('Use canonical UUIDs')
    seats = profile['loadSeats']
    if not isinstance(seats,list) or not 1<=len(seats)<=10000:
        raise ValueError('Use 1 to 10000 approved load seats')
    all_seats = [profile['canarySeatId'],profile['raceSeatId'],*seats]
    if any(not identifier(value) for value in all_seats) or len(set(all_seats))!=len(all_seats):
        raise ValueError('Every synthetic seat must have a distinct ID')
    if type(profile['clients']) is not int or not 1<=profile['clients']<=64:
        raise ValueError('Use 1 to 64 clients')
    if type(profile['holdSeconds']) is not int or not 1<=profile['holdSeconds']<=86400:
        raise ValueError('Use a hold duration from 1 to 86400 seconds')
    for field, lower, upper in (
        ('requestsPerSecond',0.1,500),('p95LimitMs',0.001,60000),
        ('queueAgeLimitSeconds',0,86400),('replicaLagLimitBytes',0,2**53),
    ):
        if not number(profile[field],lower,upper):
            raise ValueError('Invalid rate or threshold: '+field)
    return profile


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Sample: Receive an HTTP redirect and stop before a second request.
        return None


def transport(base, token):
    # Sample: Read order O-1 through the verified staging URL.
    opener = build_opener(NoRedirect())

    def send(path, body=None):
        # Sample: Send one bounded JSON request. Stop on a redirect or HTTP error.
        data = json.dumps(body).encode() if body is not None else None
        request = Request(base.rstrip('/')+path,data=data,
                          headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with opener.open(request,timeout=15) as response:
                raw = response.read(65537)
                if len(raw)>65536:
                    raise ValueError('Staging response exceeds its size limit')
                return response.status,json.loads(raw)
        except HTTPError as error:
            error.close()
            return error.code,{}
    return send


def preflight(profile, send):
    # Sample: Read the known order. Stop before a write if its seat differs.
    validate_profile(profile)
    order = profile['canaryOrderId']
    status,result = send('/orders/'+order)
    if status!=200 or not isinstance(result,dict) or result.get('orderId')!=order or result.get('seatId')!=profile['canarySeatId']:
        raise ValueError('Staging canary did not match the approved fixture')
    return {'state':'CANARY_VERIFIED','profileDigest':digest(profile)}


class RequestPacer:
    def __init__(self, rate):
        # Sample: Set a shared request pace across all workload clients.
        self.interval = 1/rate
        self.next = 0
        self.lock = Lock()

    def wait(self):
        # Sample: Delay one request until its scheduled start time.
        with self.lock:
            now = time.monotonic()
            delay = max(0,self.next-now)
            if delay:
                time.sleep(delay)
            self.next = time.monotonic()+self.interval


def run_workload(profile, send, on_ack=None):
    # Sample: Race the approved seat. Hold and close one order per load seat.
    checked = preflight(profile,send)
    run = profile['runId']
    receipts = {}
    receipts_lock = Lock()

    def submit(path, body):
        # Sample: Retain the acknowledged outcome and its event ID under the request key.
        status,response = send(path,body)
        if status!=200 or not isinstance(response,dict) or not isinstance(response.get('outcome'),dict):
            raise ValueError('Reservation command failed on '+path+' with status '+str(status))
        result = response['outcome']
        event = result.get('event')
        event_id = event.get('eventId') if isinstance(event,dict) else None
        if event is not None and not uuid_text(event_id):
            raise ValueError('Reservation event ID differs')
        record = {'outcomeDigest':digest(result),'eventId':event_id,'effectCount':1 if event_id else 0}
        with receipts_lock:
            previous = receipts.setdefault(body['requestKey'],record)
            if previous!=record:
                raise ValueError('Request replay changed its retained result')
            if on_ack is not None:
                on_ack(body['requestKey'],record)
        return response

    def hold(seat, key):
        # Sample: Submit the same request key on a retry.
        return {'seatId':seat,'ttlSeconds':profile['holdSeconds'],'requestKey':run+':'+key}

    def finish(order, action, key):
        # Sample: Confirm or release the returned order at its acknowledged revision.
        if not isinstance(order,dict) or not uuid_text(order.get('orderId')) or order.get('revision')!=1:
            raise ValueError('Held order identity or revision differs')
        result = submit('/holds/'+action,{'orderId':order['orderId'],'revision':order['revision'],
                                        'requestKey':run+':'+key})['outcome']
        updated = result.get('order',{})
        expected = 'CONFIRMED' if action=='confirm' else 'CANCELLED'
        if (updated.get('orderStatus')!=expected or updated.get('orderId')!=order['orderId']
                or updated.get('revision')!=2 or not result.get('event')):
            raise ValueError('Reservation terminal result differs')

    gate = Barrier(2)

    def race(index):
        # Sample: Release both contenders for the same seat.
        body = hold(profile['raceSeatId'],'race:'+str(index))
        gate.wait(timeout=10)
        return body,submit('/holds',body)

    with ThreadPoolExecutor(2) as pool:
        races = list(pool.map(race,range(2)))
    if sorted(response['outcome'].get('result','') for _,response in races)!=['HELD','SEAT_UNAVAILABLE']:
        raise ValueError('Last-seat race did not produce exactly one winner')
    body,winner = next((body,response) for body,response in races if response['outcome']['result']=='HELD')
    replay = submit('/holds',body)
    if replay.get('replayed') is not True or replay['outcome']!=winner['outcome'] or not winner['outcome'].get('event'):
        raise ValueError('Last-seat request did not replay its result')
    finish(winner['outcome'].get('order'),'release','race:release')

    pace = RequestPacer(profile['requestsPerSecond'])

    def work(index):
        # Sample: Hold one assigned seat and time its terminal command separately.
        pace.wait()
        started = time.perf_counter()
        result = submit('/holds',hold(profile['loadSeats'][index],'load:hold:'+str(index)))['outcome']
        first = (time.perf_counter()-started)*1000
        if result.get('result')!='HELD' or not result.get('event'):
            raise ValueError('Assigned load seat was unavailable')
        pace.wait()
        started = time.perf_counter()
        finish(result.get('order'),'confirm' if index%2==0 else 'release','load:close:'+str(index))
        return first,(time.perf_counter()-started)*1000

    started = time.perf_counter()
    with ThreadPoolExecutor(profile['clients']) as pool:
        samples = sorted(t for pair in pool.map(work,range(len(profile['loadSeats']))) for t in pair)
    elapsed = time.perf_counter()-started
    p95 = samples[math.ceil(len(samples)*0.95)-1]
    return {**checked,'state':'HTTP_PASS' if p95<=profile['p95LimitMs'] else 'HTTP_LIMIT_EXCEEDED',
            'commands':len(samples),'clients':profile['clients'],'requestedRate':profile['requestsPerSecond'],
            'achievedRate':round((len(samples)-1)/elapsed,2),'p95Ms':round(p95,3),'lastSeatWinners':1,
            'acknowledgedWrites':receipts}


def compare_rollback(expected, restored, final):
    # Sample: Compare K-1 in all three snapshots and report any changed outcome or duplicate effect.
    for snapshot in (expected,restored,final):
        if not isinstance(snapshot,dict):
            raise ValueError('Use a JSON object of request keys')
        for record in snapshot.values():
            if (not isinstance(record,dict) or record.keys()!={'outcomeDigest','eventId','effectCount'}
                    or not isinstance(record['outcomeDigest'],str) or not re.fullmatch('[0-9a-f]{64}',record['outcomeDigest'])
                    or record['eventId'] is not None and not uuid_text(record['eventId'])
                    or type(record['effectCount']) is not int or record['effectCount']<0):
                raise ValueError('Invalid request snapshot')
    if not expected:
        raise ValueError('Expected writes are required')
    if any(row['effectCount']!=(1 if row['eventId'] else 0) for row in expected.values()):
        raise ValueError('Expected effects must match the recorded event')
    result = {'preserved':[],'recoveredAfterReplay':[],'missing':[],'different':[],'unexpected':[]}
    for key,row in sorted(expected.items()):
        before, after = restored.get(key),final.get(key)
        before_valid = (before is None or (before['outcomeDigest']==row['outcomeDigest']
                        and before['eventId']==row['eventId'] and before['effectCount']<=row['effectCount']))
        if not before_valid or (after is not None and after!=row):
            result['different'].append(key)
        elif after is None:
            result['missing'].append(key)
        else:
            result['preserved' if before==row else 'recoveredAfterReplay'].append(key)
    result['unexpected'] = sorted((restored.keys()|final.keys())-expected.keys())
    return {'state':'PASS' if not any(result[k] for k in ('missing','different','unexpected')) else 'FAIL',**result}


def read_json(path):
    # Sample: Read one staging profile from a private JSON file.
    return Path(path).read_text(encoding='utf-8-sig')


def main():
    # Sample: Read the canary or run the bounded workload and retain its receipt.
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command',required=True)
    for name in ('preflight','run'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--profile',required=True)
        cmd.add_argument('--output',required=True)
    compare = sub.add_parser('compare-rollback')
    for name in ('expected','restored','final','output'):
        compare.add_argument('--'+name,required=True)
    args = parser.parse_args()
    with Path(args.output).open('x',encoding='utf-8',newline='\n') as output:
        if args.command=='compare-rollback':
            result = compare_rollback(*(json.loads(read_json(path)) for path in (args.expected,args.restored,args.final)))
        else:
            profile = validate_profile(json.loads(read_json(args.profile)))
            token = os.environ.get('DURAI_RESERVATION_STAGING_TOKEN','')
            if not token or re.search(r'\s',token):
                raise ValueError('Set the staging bearer token in the approved environment')
            send = transport(profile['baseUrl'],token)
            if args.command=='preflight':
                result = preflight(profile,send)
            else:
                journal_path = Path(args.output).with_suffix(Path(args.output).suffix+'.ack.jsonl')
                with journal_path.open('x',encoding='utf-8',newline='\n') as journal:
                    def record(key, value):
                        # Sample: Retain K-1 immediately after it returns an acknowledgment.
                        journal.write(json.dumps({'requestKey':key,**value})+'\n')
                        journal.flush()
                    result = run_workload(profile,send,on_ack=record)
        output.write(json.dumps(result,indent=2)+'\n')
    print(result['state'])
    return 1 if result['state'] in ('FAIL','HTTP_LIMIT_EXCEEDED') else 0


if __name__=='__main__':
    raise SystemExit(main())
