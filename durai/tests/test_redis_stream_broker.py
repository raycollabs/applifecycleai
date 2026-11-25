"""Sample: Publish E-1 to a disposable Redis container, receive it as C-1, and acknowledge it once."""
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))

try:
    import redis
except ImportError:
    redis = None

REDIS_IMAGE = 'sha256:e7b0b929eb5b1e7b18f4662e190ceeac481b72e35dfede063b09a4ce8beac79f'


def docker_ready():
    # Sample: Run docker image inspect on the pinned Redis image. Return False when Docker or the image is absent.
    try:
        subprocess.run(['docker','image','inspect',REDIS_IMAGE],capture_output=True,check=True,timeout=20)
        return True
    except (OSError,subprocess.SubprocessError):
        return False


@unittest.skipUnless(redis is not None and docker_ready(),'Redis client or pinned Redis image is unavailable')
class RedisStreamBrokerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Start redis container R-1 on a free loopback port with append-only persistence.
        from reservation_pilot_broker import RedisStreamBroker
        cls.name = 'durai-stage-redis-test-'+uuid4().hex[:12]
        subprocess.run(['docker','run','-d','--pull','never','--name',cls.name,'--label','com.durai.owner=redis-broker-test',
                        '-p','127.0.0.1::6379',REDIS_IMAGE,'redis-server','--appendonly','yes','--save',''],
                       capture_output=True,check=True,timeout=60)
        port = subprocess.check_output(['docker','port',cls.name,'6379/tcp'],text=True,timeout=20).splitlines()[0].rsplit(':',1)[1]
        cls.factory = lambda stream: RedisStreamBroker('127.0.0.1',int(port),stream=stream)

    @classmethod
    def tearDownClass(cls):
        # Sample: Remove container R-1 and its anonymous volume.
        subprocess.run(['docker','rm','-fv',cls.name],capture_output=True,timeout=60)

    def broker(self):
        # Sample: Open an empty stream named for this test and create its group.
        broker = type(self).factory('test-'+uuid4().hex[:12])
        broker.start()
        return broker

    def test_publish_receive_ack_round_trip(self):
        # Sample: Publish event E-1 for order O-1. Receive as owner C-1. Ack. Check 0 pending entries and 1 effect.
        broker = self.broker()
        event = {'eventId':'E-1','payload':{'orderId':'O-1'}}
        broker.publish(event)
        effects = []
        for entry,body in broker.receive('C-1'):
            effects.append(body['payload']['orderId'])
            broker.ack(entry)
        self.assertEqual(effects,['O-1'])
        self.assertEqual(broker.pending(),0)
        self.assertEqual(broker.receive('C-1'),[])

    def test_pending_entry_recovered_after_crash(self):
        # Sample: Receive E-2 as owner C-1 and do not ack. Wait past the idle time. Recover as C-2. Check E-2 delivered to C-2 and 1 effect after consume.
        broker = self.broker()
        broker.publish({'eventId':'E-2','payload':{'orderId':'O-2'}})
        self.assertEqual(len(broker.receive('C-1')),1)
        self.assertEqual(broker.pending(),1)
        self.assertEqual(broker.receive('C-2',recover=True,min_idle_ms=60000),[])
        time.sleep(0.3)
        effects = []
        for entry,body in broker.receive('C-2',recover=True,min_idle_ms=200):
            effects.append(body['eventId'])
            broker.ack(entry)
        owners = broker.client.xpending(broker.stream,broker.group)
        self.assertEqual(effects,['E-2'])
        self.assertEqual(owners['pending'],0)


if __name__=='__main__':
    unittest.main()
