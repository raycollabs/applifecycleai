from pathlib import Path
from shutil import copyfile, copytree

ROOT = Path(__file__).resolve().parent
DURAI = ROOT.parent


def prepare(destination=ROOT / 'staged'):
    # Sample: Copy a shell to a new staging folder. Check an API app and a web app.
    destination.mkdir(parents=True, exist_ok=False)
    for name in ('api', 'web'):
        source = DURAI / 'apps' / 'shell' / name
        target = destination / name
        copytree(source, target, ignore=lambda path, files: {n for n in files
                 if n.startswith('.') or n in ('__pycache__', 'node_modules')})
    copyfile(ROOT / 'launch_api.py', destination / 'api/launch_api.py')
    copyfile(ROOT / 'launch_web.mjs', destination / 'web/launch_web.mjs')
    requirements = (DURAI / 'docker/eval-python/requirements.txt').read_text(encoding='utf-8')
    (destination / 'api/requirements.txt').write_text(requirements.splitlines()[0].split()[0]
        + '\npsycopg-binary==3.3.9\nfastapi==0.165.0\npydantic==2.14.4\nuvicorn==0.54.0\n'
        + 'databricks-sdk==0.81.0\n', encoding='utf-8', newline='\n')
    return destination


if __name__ == '__main__':
    prepare()
