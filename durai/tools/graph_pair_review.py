"""Sample: Combine Python from run P-1 and SQL from run S-1. Check exact bytes, narrow grants, and restored graph views."""
import hashlib
import sys
from pathlib import Path
from durai.candidate_store import CandidateStore
from durai.common import uid
from durai.qualification import ROOT,TEMPLATES,fixture_files,fixture_spec,load
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator


def review_pair(jobs,binding,output,origin):
    # Sample: Take the passing module package and migration package. Refuse a failed source job or a missing package.
    if len(jobs) != 2 or not all(job['outcome']=='PASSED' and job.get('package') for job in jobs):
        raise ValueError('Two passing source packages are required')
    if [job['fixture'] for job in jobs] != ['task-graph-tenancy-module-v9','task-graph-tenancy-migration-v9']:
        raise ValueError('Pair sources must be the writable module job followed by the writable migration job')
    output = Path(output)
    module,sql = '/workspace/durai/application_policy_graph.py','/workspace/migrations/core/core_0012.sql'
    store = CandidateStore(ROOT/'private/candidates')
    restored = [store.restore(**job['package']) for job in jobs]
    base = fixture_files(TEMPLATES/'task-graph-tenancy-module-v9')
    files = dict(base)
    files[module],files[sql] = restored[0][module],restored[1][sql]
    suite = load(TEMPLATES/'task-graph-tenancy-module-v9/protected')
    evaluator = SandboxEvaluator(output/'evaluation')
    spec = fixture_spec('task-graph-tenancy-module-v9','durai_test',evaluator.image,evaluator.database_image)
    spec.update(taskId='TASK-GRAPH-PAIR-'+uid()[:8],binding=binding,writePaths=[module,sql],deliverables=[module,sql])
    spec.pop('editScope',None)
    task = build_task(spec)['task']
    verdict = evaluator.evaluate(task,files,suite,baseline=base)
    result = {'origin':origin,'verdict':verdict,'sourceRuns':[job['runId'] for job in jobs],
              'sourcePackages':[job['package'] for job in jobs],
              'fileDigests':{path:hashlib.sha256(files[path]).hexdigest() for path in (module,sql)},
              'package':store.retain('graph-pair-'+uid(),task,files),'adopted':False}
    if verdict['verdict'] != 'PASS':
        return result
    sys.path.insert(0,str(ROOT/'tests'))
    from graph_candidate_checks import check_candidate
    result['operatorChecks'] = check_candidate(files,suite,evaluator)
    result['review'] = 'WAITING_FOR_HUMAN_REVIEW'
    review_files = output/'files'
    review_files.mkdir(parents=True,exist_ok=True)
    for path in (module,sql):
        (review_files/Path(path).name).write_bytes(files[path])
    result['reviewFiles'] = str(review_files)
    return result
