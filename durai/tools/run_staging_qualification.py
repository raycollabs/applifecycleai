"""Sample: Start the Docker staging stack, qualify it over HTTPS, archive one receipt, and remove the stack."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tools'))

from durai.common import digest
import staging_stack as stack
from qualify_reservation_target import transport

SCOPE = ('This receipt proves one Docker staging stack on one host with synthetic traffic over a loopback HTTPS proxy. '
         'It is not proof of cloud network latency, cross-host failover, or production capacity.')


def read(path):
    # Sample: Read one JSON file written by the qualification tool.
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def tool(arguments, env):
    # Sample: Run "preflight" with the staging token. Return its exit code and keep stderr short.
    result = subprocess.run([sys.executable,str(ROOT/'tools'/'qualify_reservation_target.py'),*arguments],env=env,
                            capture_output=True,text=True,timeout=900)
    return result.returncode,result.stdout.strip(),result.stderr.strip()[-600:]


def rehearse_restart(info, profile, send, seat):
    # Sample: Hold seat R-1. Stop the relay and consumer after commit. Check one effect after redelivery.
    project = info['project']
    key = 'rehearsal:'+profile['runId']
    stack.service(project,'stop','relay')
    status,held = send('/holds',{'seatId':seat,'ttlSeconds':profile['holdSeconds'],'requestKey':key+':hold'})
    event = held.get('outcome',{}).get('event') if status==200 else None
    if not event:
        raise ValueError('Rehearsal hold did not return an event')
    before = stack.redis(project,'XLEN',stack.STREAM)
    relay_exit = stack.one_off(project,'relay-stop')
    stack.service(project,'restart','broker')
    stack.wait_until(lambda:stack.redis(project,'PING')=='PONG',60,'broker restart')
    kept = stack.redis(project,'XLEN',stack.STREAM)
    stack.service(project,'start','relay')
    stack.wait_until(lambda:stack.psql(project,"SELECT count(*) FROM durai_events.outbox WHERE state<>'PUBLISHED'")=='0',60,'relay to publish')
    entries = stack.redis(project,'XLEN',stack.STREAM)
    consumer_exit = stack.one_off(project,'consumer-stop')
    waiting = stack.pending(project)
    return {'eventId':event['eventId'],'relayExit':relay_exit,'brokerRestartKeptEntries':kept>=before+1,
            'duplicateEntries':entries-kept,'consumerExit':consumer_exit,'pendingAfterConsumerStop':waiting}


def effects_of(project, event_id):
    # Sample: Count effects for event E-1. Read 1 after redelivery.
    return int(stack.psql(project,"SELECT count(*) FROM durai_staging.effect WHERE event_id='"+event_id+"'"))


def qualify(receipt, load_seats=300, port=8443, keep=False):
    # Sample: Run all qualification steps on a new stack. Write receipt R-1. Remove the stack.
    if Path(receipt).exists():
        raise ValueError('Refuse to overwrite an archived receipt')
    info = stack.up(load_seats=load_seats,port=port)
    project = info['project']
    folder = stack.STAGING/project
    remaining = None
    try:
        token = Path(info['token']).read_text(encoding='utf-8').strip()
        profile = read(info['profile'])
        env = {**os.environ,'SSL_CERT_FILE':info['ca'],'DURAI_RESERVATION_STAGING_TOKEN':token}
        os.environ['SSL_CERT_FILE'] = info['ca']
        send = transport(profile['baseUrl'],token)
        steps = {}
        code,text,error = tool(['preflight','--profile',info['profile'],'--output',str(folder/'preflight.json')],env)
        if code:
            raise ValueError('Preflight failed: '+error)
        steps['preflight'] = read(folder/'preflight.json')
        stack.service(project,'stop','consumer')
        with stack.Sampler(project) as sampler:
            code,text,error = tool(['run','--profile',info['profile'],'--output',str(folder/'run.json')],env)
            if not (folder/'run.json').exists() or code not in (0,1):
                raise ValueError('Workload failed: '+error)
            stack.wait_until(lambda:stack.psql(project,"SELECT count(*) FROM durai_events.outbox WHERE state<>'PUBLISHED'")=='0',60,'relay to publish')
        run = read(folder/'run.json')
        expected = run.pop('acknowledgedWrites')
        (folder/'expected.json').write_text(json.dumps(expected),encoding='utf-8',newline='\n')
        metrics = sampler.result()
        rehearsal = rehearse_restart(info,profile,send,read(folder/'stack.json')['rehearsalSeat'])
        archive = stack.snapshot(project,'rehearsal')
        stack.service(project,'start','consumer')
        drained = stack.drain(project)
        stack.wait_until(lambda:stack.pending(project)==0,60,'pending entries to clear')
        rehearsal['effectsAfterRedelivery'] = effects_of(project,rehearsal['eventId'])
        restored_run = stack.restore(project,archive['path'])
        restored = stack.export_outcomes(project,profile['runId'])
        stack.rewind(project)
        for name in ('api','relay','consumer'):
            stack.service(project,'start',name)
        replayed = stack.drain(project)
        stack.wait_until(lambda:stack.pending(project)==0,60,'pending entries to clear')
        final = stack.export_outcomes(project,profile['runId'])
        for name,value in (('restored',restored),('final',final)):
            (folder/(name+'.json')).write_text(json.dumps(value),encoding='utf-8',newline='\n')
        code,text,error = tool(['compare-rollback','--expected',str(folder/'expected.json'),'--restored',str(folder/'restored.json'),
                                '--final',str(folder/'final.json'),'--output',str(folder/'comparison.json')],env)
        comparison = read(folder/'comparison.json')
        rate_met = run['achievedRate']>=0.9*run['requestedRate']
        checks = {'preflight':steps['preflight']['state']=='CANARY_VERIFIED','http':run['state']=='HTTP_PASS',
                  'trafficRateMet':rate_met,'queueAge':metrics['maxQueueAgeSeconds']<=profile['queueAgeLimitSeconds'],
                  'replicaLag':0<=metrics['maxReplicaLagBytes']<=profile['replicaLagLimitBytes'],
                  'restartRehearsal':rehearsal['relayExit']==71 and rehearsal['consumerExit']==72
                                    and rehearsal['brokerRestartKeptEntries'] and rehearsal['effectsAfterRedelivery']==1,
                  'rollbackCompare':comparison['state']=='PASS'}
        result = {'state':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'scope':SCOPE,
                  'imageIds':stack.image_ids(),'profileDigest':steps['preflight']['profileDigest'],
                  'limits':{key:profile[key] for key in ('p95LimitMs','queueAgeLimitSeconds','replicaLagLimitBytes')},
                  'traffic':{key:profile[key] for key in ('clients','requestsPerSecond','holdSeconds')}|{'loadSeats':len(profile['loadSeats'])},
                  'preflight':steps['preflight'],
                  'http':{key:run[key] for key in ('state','commands','clients','requestedRate','achievedRate','p95Ms','lastSeatWinners')}
                         |{'acknowledgedWrites':len(expected),'acknowledgedDigest':digest(expected)},
                  'operations':{'metrics':metrics,'restartRehearsal':rehearsal,'drainSeconds':drained},
                  'rollback':{'snapshotBytes':archive['bytes'],'snapshotDigest':archive['digest'],'restoreSeconds':restored_run['seconds'],
                              'replayDrainSeconds':replayed,'comparison':{key:(len(value) if isinstance(value,list) else value) for key,value in comparison.items()}},
                  'schemaDigests':read(folder/'stack.json')['schemas'],
                  'fileDigests':{name:digest((ROOT/name).read_bytes()) for name in (
                      'tools/staging_stack.py','tools/run_staging_qualification.py','tools/qualify_reservation_target.py',
                      'tools/reservation_staging_services.py','tools/reservation_pilot_broker.py','tools/reservation_pilot_api.py')}}
        Path(receipt).parent.mkdir(parents=True,exist_ok=True)
        with Path(receipt).open('x',encoding='utf-8',newline='\n') as output:
            output.write(json.dumps(result,indent=2)+'\n')
        return result
    finally:
        if not keep:
            remaining = stack.down(project)
            if remaining:
                print('Resources remain: '+' '.join(remaining),file=sys.stderr)


def main():
    # Sample: Run the full qualification and write receipts/reservation-docker-staging-20261122.json.
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipt',required=True)
    parser.add_argument('--load-seats',type=int,default=300)
    parser.add_argument('--port',type=int,default=8443)
    parser.add_argument('--keep',action='store_true')
    args = parser.parse_args()
    result = qualify(args.receipt,args.load_seats,args.port,args.keep)
    print(json.dumps({'state':result['state'],'checks':result['checks'],'p95Ms':result['http']['p95Ms'],
                      'achievedRate':result['http']['achievedRate'],'metrics':result['operations']['metrics']}))
    return 0 if result['state']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
