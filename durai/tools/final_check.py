"""Summarize public evidence and check that local secrets are absent from artifacts."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.db import settings, transaction
from durai.common import digest
from durai.plans_dir import plans_dir


def main():
    secrets = []
    def collect(value):
        if isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, str) and len(value) >= 16:
            secrets.append(value.encode())
    collect(settings())
    from durai.models import configuration
    model_key = configuration()['apiKey']
    if model_key and model_key != 'local-no-auth':
        secrets.append(model_key.encode())
    scanned = 0
    for path in ROOT.rglob('*'):
        if not path.is_file() or any(part in ('private', 'logs', '__pycache__') for part in path.relative_to(ROOT).parts):
            continue
        if any(value in path.read_bytes() for value in secrets):
            raise ValueError('Configured secret found in public file: ' + str(path.relative_to(ROOT)))
        scanned += 1
    tests = json.loads((ROOT / 'receipts/tests.json').read_text(encoding='utf-8'))
    browser = json.loads((ROOT / 'receipts/browser.json').read_text(encoding='utf-8'))
    sandbox = json.loads((ROOT / 'receipts/sandbox.json').read_text(encoding='utf-8'))
    juicefs = json.loads((ROOT / 'receipts/juicefs.json').read_text(encoding='utf-8'))
    factory = json.loads((ROOT / 'receipts/factory-demo.json').read_text(encoding='utf-8'))
    original = json.loads(plans_dir() / 'livebuild_docker_before_2026-11-14.json'.read_text(encoding='utf-8-sig'))
    expected = {row['Name'].lstrip('/'): row['Id'] for row in original}
    result = subprocess.run(['docker', 'inspect', '--format',
        '{{.Id}}|{{.Name}}|{{.State.Running}}|{{json (index .State "Health")}}',
        *expected.keys()], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError('Original container inspection failed: ' + result.stderr.strip())
    for line in result.stdout.splitlines():
        identity, name, running, health_json = line.split('|', 3)
        health = (json.loads(health_json) or {}).get('Status', 'none')
        if identity != expected[name.lstrip('/')] or running != 'true' or health not in ('healthy', 'none'):
            raise ValueError('An original container identity or health changed')
    with transaction() as db:
        approvals = db.execute('SELECT count(*) AS n FROM durai_governance.approval').fetchone()['n']
        run = db.execute('SELECT state FROM durai_agents.run WHERE id=%s', (factory['runId'],)).fetchone()['state']
        rule = db.execute("SELECT document FROM dev_trading.rule WHERE id='quantity-unit'").fetchone()['document']
    if tests['state'] != 'PASS' or browser['state'] != 'PASS' or sandbox['state'] != 'PASS' or juicefs['state'] != 'BASIC_CONTRACT_PASS':
        raise ValueError('A required milestone receipt did not pass')
    if approvals != 0 or run != 'WAITING_FOR_REVIEW':
        raise ValueError('The local demonstration review state changed; inspect it before finalizing')
    value = {'state': 'LOCAL_MILESTONE_VERIFIED', 'checkedAt': datetime.now(timezone.utc).isoformat(),
             'testsPassed': tests['testsRun'], 'testsSkipped': tests['skipped'],
             'browser': browser['state'], 'sandbox': sandbox['state'],
             'juicefs': juicefs['state'], 'factory': factory['executionMode'],
             'localRunState': run, 'localApprovalCount': approvals,
             'sourceSecretScan': {'state': 'PASS', 'files': scanned},
             'originalContainers': {'state': 'IDENTITY_AND_HEALTH_PRESERVED', 'count': len(expected)},
             'ontologyRuleDigest': digest(rule),
             'modelGeneratedRepair': ({'state': 'EVALUATED', 'verdict': factory['candidate']['verdict'],
                                       'versionId': factory['candidate']['versionId']}
                                      if factory.get('useModel') else 'NOT_EXECUTED'),
             'productionQualified': False}
    (ROOT / 'receipts/final.json').write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
