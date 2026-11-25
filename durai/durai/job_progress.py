"""Sample: Claim J-1. Report 25 percent. Cancel J-1 and refuse later progress."""
from uuid import uuid4

from .common import Conflict
from .db import transaction
from .idempotency import execute_request
from .outbox import check_name, enqueue


def queue_job(request_key, database='durai_test', role='durai_runtime'):
    # Sample: Queue J-1. Repeat the request and keep the same queued job.
    def queue(db, _body):
        # Sample: Store J-1 and its queued event together.
        job_id = str(uuid4())
        db.execute('INSERT INTO durai_coordination.job(application_id,id) VALUES(durai_core.application_id(),%s)',(job_id,))
        enqueue(db,'job.'+job_id,'job.queued',{'jobId':job_id,'state':'QUEUED'})
        return {'jobId':job_id,'state':'QUEUED'}
    return execute_request('job.queue',request_key,{},queue,database,role)


def status(job_id, database='durai_test', role='durai_runtime'):
    # Sample: Read J-1, its state, and the last retained progress event.
    check_name(job_id, maximum=120)
    with transaction(role,database) as db:
        row = db.execute("""
            SELECT j.state,j.generation,j.result,(
                SELECT e.payload FROM durai_events.outbox e
                WHERE e.application_id=j.application_id AND e.stream_id=%s
                  AND e.event_type='job.progress'
                ORDER BY e.sequence DESC LIMIT 1
            ) AS progress
            FROM durai_coordination.job j WHERE j.application_id=durai_core.application_id() AND j.id=%s
        """,('job.'+job_id,job_id)).fetchone()
    if row is None:
        raise KeyError('Job unavailable in this tenant')
    progress = row['progress']
    if progress and progress['generation'] != row['generation']:
        row['progress'] = None
    return row


def report_progress(job_id, owner, generation, percent, database='durai_test', role='durai_runtime'):
    # Sample: Report 25 percent with the current lease. Refuse an older generation.
    check_name(job_id, maximum=120)
    check_name(owner)
    if type(generation) is not int or not 1 <= generation < 2**63 or type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError('Invalid job generation or percent')
    with transaction(role,database) as db:
        row = db.execute("""
            SELECT lease_until FROM durai_coordination.job
            WHERE application_id=durai_core.application_id() AND id=%s AND state='RUNNING'
                AND owner=%s AND generation=%s FOR UPDATE
        """,(job_id,owner,generation)).fetchone()
        if row is None or not db.execute('SELECT %s > clock_timestamp() AS live',(row['lease_until'],)).fetchone()['live']:
            raise Conflict('Job lease is stale or expired')
        previous = db.execute("""
            SELECT payload FROM durai_events.outbox WHERE stream_id=%s AND event_type='job.progress'
            ORDER BY sequence DESC LIMIT 1
        """,('job.'+job_id,)).fetchone()
        result = {'jobId':job_id,'generation':generation,'percent':percent}
        if previous is not None and previous['payload']['generation'] == generation:
            old = previous['payload']['percent']
            if percent < old:
                raise Conflict('Job progress cannot decrease in one generation')
            if percent == old:
                return result
        enqueue(db,'job.'+job_id,'job.progress',result)
        return result


def cancel_job(job_id, request_key, database='durai_test', role='durai_runtime'):
    # Sample: Cancel J-1 at generation 1. Check that its previous owner cannot report progress.
    check_name(job_id, maximum=120)

    def cancel(db, body):
        # Sample: Cancel a queued or running job and write its event.
        identity = body['jobId']
        row = db.execute("""
            UPDATE durai_coordination.job SET state='CANCELLED',generation=generation+1,owner=NULL,lease_until=NULL
            WHERE application_id=durai_core.application_id() AND id=%s AND state IN ('QUEUED','RUNNING')
            RETURNING generation
        """,(identity,)).fetchone()
        if row is None:
            raise Conflict('Job is unavailable or terminal')
        result = {'jobId':identity,'state':'CANCELLED','generation':row['generation']}
        enqueue(db,'job.'+identity,'job.cancelled',result)
        return result
    return execute_request('job.cancel',request_key,{'jobId':job_id},cancel,database,role)
