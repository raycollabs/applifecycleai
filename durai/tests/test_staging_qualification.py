"""Sample: Start a Docker staging stack, qualify it over HTTPS, and check the archived receipt."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import staging_stack as stack


def stack_ready():
    # Sample: Inspect the four pinned images. Return False when Docker or an image is absent.
    try:
        for image in stack.image_ids().values():
            subprocess.run(['docker','image','inspect',image],capture_output=True,check=True,timeout=30)
        return True
    except (OSError,subprocess.SubprocessError):
        return False


def stack_names():
    # Sample: List the names of all containers whose names start with durai-stage- and 12 hex digits.
    text = subprocess.run(['docker','ps','-a','--format','{{.Names}}'],capture_output=True,text=True,timeout=30).stdout
    return {name for name in text.split() if stack.PROJECT.match(name)}


@unittest.skipUnless(stack_ready(),'Docker or a pinned staging image is unavailable')
class StagingQualificationTests(unittest.TestCase):
    def test_docker_stack_qualifies(self):
        # Sample: Start a stack. Run preflight, run (8 clients, 50 rps), restart relay and consumer, snapshot, restore, replay, compare-rollback. Check all PASS, p95 under 300 ms, queue age under 5 s, replica lag under 1 MB. Remove the stack.
        from run_staging_qualification import qualify
        before = stack_names()
        with tempfile.TemporaryDirectory() as folder:
            receipt = Path(folder)/'receipt.json'
            result = qualify(str(receipt),load_seats=100,port=stack.free_port())
            saved = json.loads(receipt.read_text(encoding='utf-8'))
        self.assertEqual(saved['state'],'PASS')
        self.assertEqual(result['checks'],{name:True for name in result['checks']})
        self.assertLess(saved['http']['p95Ms'],300)
        self.assertLess(saved['operations']['metrics']['maxQueueAgeSeconds'],5)
        self.assertLess(saved['operations']['metrics']['maxReplicaLagBytes'],1048576)
        self.assertEqual(saved['operations']['restartRehearsal']['effectsAfterRedelivery'],1)
        self.assertEqual(stack_names(),before)


if __name__=='__main__':
    unittest.main()
