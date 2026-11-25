"""Compare business sources without changing either workspace copy."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.idea', '.gradle', '.proxyai', 'node_modules', 'vendor', 'private',
        'build', '.next', '.sites-runtime', '__pycache__', 'test-results',
        'playwright-report', 'receipts', 'logs', 'durai'}
EXTENSIONS = {'.go', '.mod', '.sum', '.py', '.sql', '.java', '.kt', '.kts',
              '.ts', '.tsx', '.js', '.mjs', '.css', '.json', '.yaml', '.yml', '.toml'}


def sources(root):
    result = {}
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in SKIP and not (
            Path(folder, name).lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)]
        for name in names:
            path = Path(folder, name)
            if path.suffix not in EXTENSIONS or name.startswith('.env'):
                continue
            if path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                continue
            result[path.relative_to(root).as_posix()] = path
    return result


def main():
    left = sources(Path('C:/livebuild/q1-fintech-checkout-flow'))
    right = sources(ROOT.parent)
    common = sorted(left.keys() & right.keys())

    def compare(name):
        a = hashlib.sha256(left[name].read_bytes()).hexdigest()
        b = hashlib.sha256(right[name].read_bytes()).hexdigest()
        return None if a == b else {'path': name, 'cSha256': a, 'dSha256': b}

    with ThreadPoolExecutor(max_workers=4) as pool:
        changed = [value for value in pool.map(compare, common) if value]
    receipt = {'state': 'SOURCE_MATCH' if not changed and left.keys() == right.keys()
               else 'SOURCE_DIFFERENCES', 'comparedFiles': len(common),
               'onlyC': sorted(left.keys() - right.keys()),
               'onlyD': sorted(right.keys() - left.keys()), 'changed': changed,
               'policy': 'Preserve both trees. Build new Durai work in active D: workspace. '
                         'Generated data and private runtime state are excluded.'}
    target = ROOT / 'receipts/workspace-source-comparison.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
