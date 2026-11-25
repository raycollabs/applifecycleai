"""Sample: Run ExportTests. Check the export holds only public files, starts one commit and refuses unsafe input."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import boundary_scan  # noqa: E402
import export_open_tree  # noqa: E402

FAKE_GOOGLE = 'AIza' + 'B' * 35


def make_repo(folder, files):
    # Sample: Make a git repository in the folder with the given path and text pairs. Commit them.
    Path(folder).mkdir(parents=True, exist_ok=True)
    run = lambda *args: subprocess.run(['git', '-C', str(folder), '-c', 'user.name=t', '-c', 'user.email=t@example.org', *args],
                                       check=True, capture_output=True)
    run('init', '-q')
    for path, text in files.items():
        target = Path(folder, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    run('add', '-A')
    run('commit', '-q', '-m', 'first')
    run('commit', '-q', '--allow-empty', '-m', 'second')


class ExportTests(unittest.TestCase):
    def setUp(self):
        # Sample: Make a temporary folder for the repository and one for the export.
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name, 'repo')
        self.out = Path(self.folder.name, 'out')
        self.addCleanup(self.folder.cleanup)

    def test_export_copies_only_public_files(self):
        # Sample: Repository holds durai/durai/a.py (OPEN), durai/blueprints/x.sql (PAID_PACK) and durai/private/k.json (NEVER). Check only a.py is exported.
        make_repo(self.root, {'durai/durai/a.py': 'x = 1\n', 'durai/blueprints/x.sql': 'select 1;\n', 'durai/private/k.json': '{}\n'})
        result = export_open_tree.export(self.root, self.out)
        self.assertEqual(1, result['copied'])
        self.assertTrue((self.out / 'durai/durai/a.py').is_file())
        self.assertFalse((self.out / 'durai/blueprints').exists())
        self.assertFalse((self.out / 'durai/private').exists())

    def test_export_starts_one_commit(self):
        # Sample: Export a repository that has 2 commits. Check the export has exactly 1 commit.
        make_repo(self.root, {'durai/durai/a.py': 'x = 1\n'})
        export_open_tree.export(self.root, self.out)
        count = subprocess.run(['git', '-C', str(self.out), 'rev-list', '--count', 'HEAD'], capture_output=True, text=True).stdout.strip()
        self.assertEqual('1', count)

    def test_free_pack_file_moves_to_starter_folder(self):
        # Sample: Export durai/design/engineering-philosophy-v15.json. Check the file is at packs/starter/design/engineering-philosophy-v15.json.
        make_repo(self.root, {'durai/design/engineering-philosophy-v15.json': '{}\n'})
        export_open_tree.export(self.root, self.out)
        self.assertTrue((self.out / 'packs/starter/design/engineering-philosophy-v15.json').is_file())

    def test_free_pack_files_with_one_name_do_not_overwrite_each_other(self):
        # Sample: Export durai/templates/trading-demo/a/x.py and durai/templates/position-app/b/x.py. Check both files exist with their own text.
        make_repo(self.root, {'durai/templates/trading-demo/a/x.py': 'one\n', 'durai/templates/position-app/b/x.py': 'two\n'})
        export_open_tree.export(self.root, self.out)
        self.assertEqual('one', (self.out / 'packs/starter/templates/trading-demo/a/x.py').read_text(encoding='utf-8').strip())
        self.assertEqual('two', (self.out / 'packs/starter/templates/position-app/b/x.py').read_text(encoding='utf-8').strip())

    def test_unclassified_file_is_refused(self):
        # Sample: Repository holds new/file.txt with no class. Check ValueError and no export folder content.
        make_repo(self.root, {'new/file.txt': 'x\n'})
        with self.assertRaises(ValueError):
            export_open_tree.export(self.root, self.out)

    def test_secret_in_public_file_is_refused(self):
        # Sample: Repository holds durai/durai/a.py with a fake Google key. Check ValueError and the key is not in the message.
        make_repo(self.root, {'durai/durai/a.py': 'key = "' + FAKE_GOOGLE + '"\n'})
        with self.assertRaises(ValueError) as caught:
            export_open_tree.export(self.root, self.out)
        self.assertNotIn(FAKE_GOOGLE, str(caught.exception))

    def test_non_empty_destination_is_refused(self):
        # Sample: Destination already holds a file. Check ValueError.
        make_repo(self.root, {'durai/durai/a.py': 'x = 1\n'})
        self.out.mkdir()
        (self.out / 'keep.txt').write_text('x', encoding='utf-8')
        with self.assertRaises(ValueError):
            export_open_tree.export(self.root, self.out)


if __name__ == '__main__':
    unittest.main()
