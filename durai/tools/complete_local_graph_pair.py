"""Sample: Qualify two explicit Codex corrections with no provider request. Keep failed paid-run evidence unchanged."""
import json
from copy import deepcopy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from durai.qualification import ROOT,TEMPLATES,fixture_config,fixture_files,load
from run_p1_p2 import run_job
from graph_pair_review import review_pair


def complete(binding,output,qualified=(),corrections=None):
    # Sample: Apply corrected Python and SQL through edit_file. Retain each source package and its combined review result.
    sys.path.insert(0,str(ROOT/'tests'))
    from test_workspace_worker import SequenceModel
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    jobs = []
    passing = {job['fixture']:job for job in qualified if job['outcome']=='PASSED'}
    for part in ('module','migration'):
        name = 'task-graph-tenancy-'+part+'-v9'
        if name in passing:
            job = deepcopy(passing[name])
            job['sourceOrigin'] = 'DEEPSEEK_FLASH_GENERATED'
            jobs.append(job)
            continue
        folder = TEMPLATES/name
        path = fixture_config(folder)['module']
        base = fixture_files(folder)
        corrected = {path:corrections[part]} if corrections and part in corrections else load(folder/'reference')
        steps = [('edit_file',{'file_path':path,'old_string':base[path].decode(),'new_string':corrected[path].decode()})]
        job = run_job(name,lambda attempt:SequenceModel(steps=steps,replies=[]),output/part,
                      database='durai_local',binding=binding)
        job['sourceOrigin'] = 'CODEX_LOCAL_CORRECTION'
        jobs.append(job)
        (output/'local-source-jobs.json').write_text(json.dumps(jobs,indent=2),encoding='utf-8')
        if job['outcome'] != 'PASSED':
            raise RuntimeError('Local correction failed qualification: '+part)
    origins = {job['sourceOrigin'] for job in jobs}
    origin = 'DEEPSEEK_FLASH_GENERATED' if origins == {'DEEPSEEK_FLASH_GENERATED'} else \
        'MIXED_DEEPSEEK_AND_CODEX_CORRECTION' if len(origins)>1 else 'CODEX_LOCAL_CORRECTION'
    result = review_pair(jobs,binding,output/'combined',origin)
    result['jobs'],result['paidProviderCalls'] = jobs,0
    (output/'review.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__ == '__main__':
    receipt = json.loads((ROOT/'private/p1-p2-20261118/receipt.json').read_text())
    result = complete(receipt['binding'],ROOT/'private/p1-p2-local-correction',receipt['jobs'])
    print(json.dumps({'verdict':result['verdict']['verdict'],'review':result.get('review'),'package':result['package']}))
