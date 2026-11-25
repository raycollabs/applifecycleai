"""Sample: Copy psycopg 3.3.9 hashes from requirements.lock. Build durai-eval-python:1. Lock its image ID."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('psycopg', 'psycopg-binary', 'typing-extensions')
TAG = 'durai-eval-python:1'
BASE = 'python:3.13.7-slim-bookworm'


def pinned_requirements():
    # Sample: Read requirements.lock. Return the psycopg, psycopg-binary, and typing-extensions blocks with every hash.
    text = (ROOT / 'requirements.lock').read_text(encoding='utf-8')
    blocks = []
    for name in PACKAGES:
        match = re.search(r'^' + re.escape(name) + r'==[^\n]*(?:\n    --hash=sha256:[a-f0-9]{64}[^\n]*)+', text, re.M)
        if not match:
            raise ValueError('Pinned package is missing from requirements.lock: ' + name)
        blocks.append(match.group(0).rstrip(' \\'))
    return '\n'.join(blocks) + '\n'


def main():
    # Sample: Write the Dockerfile and requirements. Build the image. Save tag, base, image ID, and packages to eval-image.lock.json.
    folder = ROOT / 'docker/eval-python'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'requirements.txt').write_text(pinned_requirements(), encoding='utf-8', newline='\n')
    (folder / 'Dockerfile').write_text('FROM ' + BASE + '\nCOPY requirements.txt /tmp/requirements.txt\n'
                                       'RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt\n',
                                       encoding='utf-8', newline='\n')
    subprocess.run(['docker', 'build', '-t', TAG, str(folder)], check=True, timeout=900)
    image = subprocess.check_output(['docker', 'image', 'inspect', TAG, '--format', '{{.Id}}'], text=True).strip()
    lock = {'tag': TAG, 'imageId': image, 'base': BASE, 'packages': list(PACKAGES),
            'execution': 'non-root, read-only root, internal network to one ephemeral database, no capabilities'}
    (ROOT / 'eval-image.lock.json').write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(lock))


if __name__ == '__main__':
    main()
