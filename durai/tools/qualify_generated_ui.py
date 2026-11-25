"""Sample: Start the reviewed trade-pilot UI server on port 54420 and verify browser rendering with Playwright."""
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.generation import verify_bundle


def main():
    # Sample: Start the authored app for a browser proof. Stop only this child afterward.
    receipt = json.loads((ROOT / 'receipts/five-phase.json').read_text())['applicationGreenfield']
    if receipt['generationMethod'] != 'deterministic-domain-adapter':
        raise ValueError('Host UI proof accepts only the reviewed deterministic template')
    folder = Path(receipt['outputDirectory'])
    verify_bundle(folder)
    if (folder / 'program.py').read_bytes() != (ROOT / 'templates/position-app/program.py').read_bytes():
        raise ValueError('Host template bytes differ')
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    process = subprocess.Popen([sys.executable, '-B', str(folder / 'server.py')], cwd=folder,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=flags)
    try:
        for _ in range(40):
            if process.poll() is not None:
                raise RuntimeError('Owned generated UI server failed to start; preserve any existing listener')
            try:
                with urllib.request.urlopen('http://127.0.0.1:54430/positions', timeout=1) as response:
                    if json.load(response) != {'trades': [], 'netShares': 0}:
                        raise ValueError('Generated UI state is not empty')
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Generated UI readiness timed out')
        subprocess.run(['node', str(ROOT / 'tools/generated-ui-proof.cjs')], check=True, timeout=90)
    finally:
        process.terminate()
        process.communicate(timeout=5)
    verify_bundle(folder)


if __name__ == '__main__':
    main()
