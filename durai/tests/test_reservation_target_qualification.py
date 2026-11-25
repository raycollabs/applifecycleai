"""Sample: Qualify synthetic HTTP commands and compare the rollback snapshots."""
import sys
from pathlib import Path
from threading import Lock
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from qualify_reservation_target import compare_rollback, preflight, run_workload, transport, validate_profile
import reservation_pilot_api as api


class TargetTests(unittest.TestCase):
    def profile(self):
        # Sample: Approve one race seat and two load seats for a controlled HTTP test.
        return {'baseUrl':'https://reservation-staging.example/api','canaryOrderId':str(uuid4()),
                'canarySeatId':'fixture.canary','raceSeatId':'fixture.race','loadSeats':['fixture.a','fixture.b'],
                'runId':str(uuid4()),'clients':2,'requestsPerSecond':100,'p95LimitMs':60000,
                'queueAgeLimitSeconds':2,'replicaLagLimitBytes':100,'holdSeconds':60}

    def test_workload_and_replay(self):
        # Sample: Race for one seat through HTTP. Run four more commands and retain seven write acknowledgments.
        profile = self.profile()
        state = {}
        used = set()
        saved = {}
        lock = Lock()

        def hold(seat, ttl, key):
            # Sample: Replay the prior outcome for K-1. Permit one hold for the seat.
            with lock:
                if key in saved:
                    return {'outcome':saved[key],'replayed':True}
                if seat in used:
                    outcome = {'result':'SEAT_UNAVAILABLE','seatId':seat}
                else:
                    used.add(seat)
                    order = {'orderId':str(uuid4()),'seatId':seat,'revision':1}
                    state[order['orderId']] = order
                    outcome = {'result':'HELD','order':order,'event':{'eventId':str(uuid4())}}
                saved[key] = outcome
                return {'outcome':outcome,'replayed':False}

        def change(action, order, revision, key):
            # Sample: Confirm or release the held test order at revision 1.
            with lock:
                self.assertEqual(revision,1)
                result = dict(state[order],revision=2,orderStatus='CONFIRMED' if action=='confirm' else 'CANCELLED')
                return {'outcome':{'order':result,'event':{'eventId':str(uuid4())}},'replayed':False}

        canary = {'orderId':profile['canaryOrderId'],'seatId':profile['canarySeatId']}
        ack = {}
        with patch.object(api,'read_order',return_value=canary), patch.object(api,'hold_seat',side_effect=hold), \
                patch.object(api,'change_hold',side_effect=change), api.serve('fixture-token') as endpoint:
            send = transport(endpoint,'fixture-token')
            self.assertEqual(preflight(profile,send)['state'],'CANARY_VERIFIED')
            result = run_workload(profile,send,on_ack=ack.__setitem__)
        self.assertEqual(result['state'],'HTTP_PASS')
        self.assertEqual(result['commands'],4)
        self.assertEqual(result['lastSeatWinners'],1)
        self.assertEqual(len(ack),7)
        self.assertEqual(result['acknowledgedWrites'],ack)

    def test_preflight_blocks_wrong_canary(self):
        # Sample: Return the wrong seat from the canary. Check that the runner sends only its read.
        profile = self.profile()
        calls = []

        def send(path, body=None):
            # Sample: Record the requested path and return an unrelated synthetic seat.
            calls.append((path,body))
            return 200,{'orderId':profile['canaryOrderId'],'seatId':'fixture.other'}
        with self.assertRaisesRegex(ValueError,'canary'):
            run_workload(profile,send)
        self.assertEqual(calls,[('/orders/'+profile['canaryOrderId'],None)])

    def test_profile_limits(self):
        # Sample: Refuse an HTTP target, a repeated seat, a nonpositive rate, or an invalid concurrency.
        profile = self.profile()
        for change in ({'clients':0},{'loadSeats':[profile['raceSeatId']]},
                       {'baseUrl':'http://stage.example'},{'baseUrl':'https://user@stage.example'},
                       {'p95LimitMs':float('nan')},{'requestsPerSecond':0},{'replicaLagLimitBytes':-1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_profile(dict(profile,**change))

    def test_rollback_classifies_writes(self):
        # Sample: Preserve K-1, recover K-2 and K-3, lose K-4, and duplicate the effect of K-5.
        event = {'outcomeDigest':'a'*64,'eventId':str(uuid4()),'effectCount':1}
        absent_effect = dict(event,effectCount=0)
        keys = ('K-1','K-2','K-3','K-4','K-5')
        expected = dict.fromkeys(keys,event)
        restored = {'K-1':event,'K-3':absent_effect,'K-5':event}
        final = {'K-1':event,'K-2':event,'K-3':event,'K-5':dict(event,effectCount=2)}
        result = compare_rollback(expected,restored,final)
        self.assertEqual(result['state'],'FAIL')
        self.assertEqual(result['preserved'],['K-1'])
        self.assertEqual(result['recoveredAfterReplay'],['K-2','K-3'])
        self.assertEqual(result['missing'],['K-4'])
        self.assertEqual(result['different'],['K-5'])
        self.assertEqual(compare_rollback(expected,restored,expected)['state'],'PASS')
