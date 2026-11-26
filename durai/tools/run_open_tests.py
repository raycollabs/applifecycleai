"""Sample: Run run_open_tests.py in a clean export. A test that fails because a pack file or a private settings file is missing counts as a skip with the file name. Any other failure stays a failure. Exit 1 when a failure remains."""
import json
import re
import sys
import unittest
from pathlib import Path

MISSING_DATA = re.compile(r'(?:private|design|templates|blueprints|qualification|packs)[\\/]')
DATA_ERRORS = ('FileNotFoundError', 'NotADirectoryError')
DB_ERRORS = ('OperationalError', 'ConnectionRefusedError')


def classify_error(exc_info):
    # Sample: FileNotFoundError for private/local.json returns "missing data: private/local.json". KeyError returns None. OperationalError returns "database not available".
    exc_type, exc, _ = exc_info
    name = exc_type.__name__
    text = str(exc)
    if (name in DATA_ERRORS or isinstance(exc, (FileNotFoundError, NotADirectoryError))) and MISSING_DATA.search(text):
        flat = text.replace('\\\\', '/').replace('\\', '/')
        return 'missing data: ' + flat.split('durai/')[-1].strip("'")[:80]
    if name in DB_ERRORS:
        return 'database not available'
    return None


NEEDS_FILE = Path(__file__).resolve().parents[1] / 'tests' / 'needs_packs.json'
PACK_FOLDER = Path(__file__).resolve().parents[1] / 'templates'


def needs_packs():
    # Sample: The templates folder is missing (clean export). Return the ids listed in tests/needs_packs.json. With the folder present, return an empty set.
    if PACK_FOLDER.exists() or not NEEDS_FILE.exists():
        return set()
    return set(json.loads(NEEDS_FILE.read_text(encoding='utf-8'))['tests'])


class OpenResult(unittest.TextTestResult):
    skipped_for_data = 0
    listed = None

    def listed_skip(self, test):
        if OpenResult.listed is None:
            OpenResult.listed = needs_packs()
        if test.id() in OpenResult.listed:
            OpenResult.skipped_for_data += 1
            self.addSkip(test, 'needs paid pack')
            return True
        return False

    def addError(self, test, err):
        if self.listed_skip(test):
            return
        reason = classify_error(err)
        if reason:
            OpenResult.skipped_for_data += 1
            self.addSkip(test, reason)
            return
        super().addError(test, err)

    def addFailure(self, test, err):
        if self.listed_skip(test):
            return
        super().addFailure(test, err)


def main(argv=None):
    # Sample: Discover tests in the tests folder. Run them. Print one JSON line with run, failures, errors, skipped and skippedForData.
    folder = Path(argv[0]) if argv else Path(__file__).resolve().parents[1] / 'tests'
    sys.path.insert(0, str(folder.parent))
    sys.path.insert(0, str(folder))
    suite = unittest.defaultTestLoader.discover(str(folder), pattern='test_*.py')
    runner = unittest.TextTestRunner(resultclass=OpenResult, verbosity=0, stream=open('NUL' if sys.platform == 'win32' else '/dev/null', 'w'))
    result = runner.run(suite)
    print(json.dumps({'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                      'skipped': len(result.skipped), 'skippedForData': OpenResult.skipped_for_data}))
    for test, trace in (result.failures + result.errors):
        print('FAILED', test.id(), '|', trace.strip().splitlines()[-1][:140])
    return 1 if result.failures or result.errors else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
