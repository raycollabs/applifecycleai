"""Sample: Publish a held order to Redis Streams and acknowledge it after one local effect."""
import json
import re
import subprocess
import time

from durai.common import uid
from durai.db_sandbox import docker
from durai.inbox import consume
from durai.outbox import claim_batch, settle

STREAM = 'durai-reservation-staging'
GROUP = 'projection'


class Broker:
    def __init__(self, name):
        # Sample: Open the named disposable broker with its private stream key.
        if not re.fullmatch(r'durai-stage-redis-[0-9a-f]{12}',name):
            raise ValueError('Refuse a shared Redis broker')
        self.name = name

    def run(self,*args):
        # Sample: Read Redis PING or a JSON stream reply.
        command = ['docker','exec',self.name,'redis-cli','-2','--json',*args]
        result = subprocess.run(command,capture_output=True,text=True,check=True,timeout=20)
        return json.loads(result.stdout)

    def wait_ready(self):
        # Sample: Wait for the private broker to return PONG before using its stream.
        end = time.monotonic()+15
        while time.monotonic()<end:
            try:
                if self.run('PING')=='PONG':
                    return
            except (subprocess.CalledProcessError, json.JSONDecodeError):
                pass
            time.sleep(.2)
        details = docker('logs',self.name)
        raise ValueError('Redis did not become ready: '+details[-600:])

    def start(self, image, network):
        # Sample: Start a broker with append-only persistence and no published port.
        docker('run','-d','--pull','never','--name',self.name,'--network',network,
               '--label','com.durai.owner=reservation-staging',image,'redis-server',
               '--appendonly','yes','--appendfsync','always','--save','')
        self.wait_ready()
        self.run('XGROUP','CREATE',STREAM,GROUP,'0','MKSTREAM')

    def publish(self,event):
        # Sample: Append E-1 and return its Redis stream entry ID.
        return self.run('XADD',STREAM,'*','body',json.dumps(event,separators=(',',':')))

    def receive(self,owner,recover=False,limit=32,min_idle_ms=0):
        # Sample: Read new messages or reclaim one that was not acknowledged.
        if recover:
            rows = self.run('XAUTOCLAIM',STREAM,GROUP,owner,str(min_idle_ms),'0-0','COUNT',str(limit))[1]
        else:
            value = self.run('XREADGROUP','GROUP',GROUP,owner,'COUNT',str(limit),'STREAMS',STREAM,'>')
            rows = value[0][1] if value else []
        return [(entry,json.loads(fields[1])) for entry,fields in rows]

    def ack(self,entry):
        # Sample: Acknowledge one stream entry after PostgreSQL commits.
        if self.run('XACK',STREAM,GROUP,entry) != 1:
            raise ValueError('Stream acknowledgment failed')

    def pending(self):
        # Sample: Count messages that still need acknowledgment.
        return self.run('XPENDING',STREAM,GROUP)[0]


def relay(broker, *, stream=None, lose_ack=False, lease_seconds=120):
    # Sample: Commit an event to Redis and stop before settling its PostgreSQL claim.
    claims = claim_batch(limit=1 if lose_ack else 32,lease_seconds=1 if lose_ack else lease_seconds,stream_id=stream)
    for claim in claims:
        entry = broker.publish(claim['event'])
        if not isinstance(entry,str):
            raise ValueError('Redis did not accept the event')
        if lose_ack:
            raise SystemExit(71)
        settle(claim,True)
    return len(claims)


def projection(db, event):
    # Sample: Record a single effect for the confirmed order event.
    db.execute('INSERT INTO durai_staging.effect (id,event_id,order_id,sequence,state) VALUES (%s,%s,%s,%s,%s)',
               (uid(),event['eventId'],event['payload']['orderId'],event['sequence'],event['payload']['orderStatus']))
    return {'eventId':event['eventId']}


def consume_entries(broker, owner, *, recover=False, lose_ack=False, min_idle_ms=0):
    # Sample: Commit the inbox effect and stop before acknowledging the Redis entry.
    results = []
    for entry,event in broker.receive(owner,recover=recover,limit=1 if lose_ack else 32,min_idle_ms=min_idle_ms):
        result = consume(GROUP,event,projection)
        if lose_ack:
            raise SystemExit(72)
        broker.ack(entry)
        results.append(result)
    return results


class RedisStreamBroker:
    def __init__(self, host, port=6379, password=None, *, stream=STREAM, group=GROUP):
        # Sample: Open broker host "broker" with its private password. Create no stream entry yet.
        import redis
        self.name = host+':'+str(port)
        self.stream = stream
        self.group = group
        self.client = redis.Redis(host=host,port=port,password=password,decode_responses=True,
                                  socket_timeout=10,socket_connect_timeout=5)

    def wait_ready(self, seconds=15):
        # Sample: Wait up to 15 seconds for PING to return True.
        end = time.monotonic()+seconds
        while time.monotonic()<end:
            try:
                if self.client.ping():
                    return
            except Exception:
                pass
            time.sleep(.2)
        raise ValueError('Redis did not become ready')

    def start(self):
        # Sample: Create group "projection" on stream S-1 and keep an existing group.
        self.wait_ready()
        try:
            self.client.xgroup_create(self.stream,self.group,id='0',mkstream=True)
        except Exception as error:
            if 'BUSYGROUP' not in str(error):
                raise

    def publish(self, event):
        # Sample: Append E-1 and return its Redis stream entry ID.
        return self.client.xadd(self.stream,{'body':json.dumps(event,separators=(',',':'))})

    def receive(self, owner, recover=False, limit=32, min_idle_ms=0):
        # Sample: Read new messages as C-1 or reclaim entries idle for 2000 ms.
        if recover:
            rows = self.client.xautoclaim(self.stream,self.group,owner,min_idle_ms,'0-0',count=limit)[1]
        else:
            value = self.client.xreadgroup(self.group,owner,{self.stream:'>'},count=limit)
            rows = value[0][1] if value else []
        return [(entry,json.loads(fields['body'])) for entry,fields in rows if fields]

    def ack(self, entry):
        # Sample: Acknowledge one stream entry after PostgreSQL commits.
        if self.client.xack(self.stream,self.group,entry)!=1:
            raise ValueError('Stream acknowledgment failed')

    def pending(self):
        # Sample: Count messages that still need acknowledgment.
        return self.client.xpending(self.stream,self.group)['pending']

    def length(self):
        # Sample: Count stream entries, including duplicates of one event.
        return self.client.xlen(self.stream)

    def rewind(self):
        # Sample: Move group "projection" to the first entry so every event is delivered again.
        self.client.xgroup_setid(self.stream,self.group,'0')


def make_broker(kind, **options):
    # Sample: Return the redis-stream broker for host "broker". Refuse the name "kafka".
    if kind=='redis-stream':
        return RedisStreamBroker(options['host'],options.get('port',6379),options.get('password'))
    if kind=='redis-cli':
        return Broker(options['name'])
    raise ValueError('Unknown broker: '+str(kind))
