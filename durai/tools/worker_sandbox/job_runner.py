"""Sample: Run inside the sandbox as python3 job_runner.py. Read /tmp/work/job. Call repair_workspace with the DeepSeek model. Print one JSON line with the digest and the message count."""
import json
import os
import sys

sys.path.insert(0, "/opt/durai")

from durai.coding_worker import repair_workspace
from durai.common import digest
from langchain_openai import ChatOpenAI

contract = {'policies': {}, 'allowedFiles': ['/workspace/program.py'], 'releaseAuthority': False}
contract['digest'] = digest(contract)
model = ChatOpenAI(model=sys.argv[1], base_url='https://api.deepinfra.com/v1/openai', api_key=os.environ['DEEPINFRA_API_KEY'], max_retries=5, timeout=120)
task = 'Under each def line in program.py add one comment line in exactly this form: # Sample: Call total with [10, 20]. Check 30. Use concrete sample inputs and results for that function. Add no other comments or docstrings. Do not change the code.'
result = repair_workspace('/tmp/work/job', model, task, contract)
print(json.dumps({'digest': result['digest'], 'messageCount': result['messageCount'], 'authority': result['authority']}))
