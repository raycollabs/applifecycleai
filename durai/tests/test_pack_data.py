"""Sample: Run ResolverTests. Check the search order, the free pack folder and the named missing-file error."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import run_open_tests  # noqa: E402
import json  # noqa: E402
from durai.effective_view import EffectiveView  # noqa: E402
from durai.pack_data import PackDataMissing, PolicySource, default_policy_version, design_path, read_design, supplier  # noqa: E402
from durai.pack_registry import INSTALLED, PackRegistry  # noqa: E402
from durai.pack_signing import TrustedKeyList  # noqa: E402
from durai.packs import generic_pack  # noqa: E402


class ResolverTests(unittest.TestCase):
    def setUp(self):
        # Sample: Make folders T/durai/design and T/packs/starter/design. Root is T/durai.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / 'durai'
        self.full = self.root / 'design'
        self.free = Path(folder.name) / 'packs' / 'starter' / 'design'
        self.full.mkdir(parents=True)
        self.free.mkdir(parents=True)

    def test_design_folder_is_searched_first(self):
        # Sample: Put {"v": "full"} in design/a.json and {"v": "free"} in packs/starter/design/a.json. Read a.json. Check full.
        (self.full / 'a.json').write_text('{"v": "full"}', encoding='utf-8')
        (self.free / 'a.json').write_text('{"v": "free"}', encoding='utf-8')
        self.assertEqual({'v': 'full'}, read_design('a.json', self.root))

    def test_free_pack_folder_is_the_fallback(self):
        # Sample: Put b.json only in packs/starter/design. Read b.json. Check the free file path is returned.
        (self.free / 'b.json').write_text('{"v": "free"}', encoding='utf-8')
        self.assertEqual(self.free / 'b.json', design_path('b.json', self.root))

    def test_missing_file_names_file_and_pack(self):
        # Sample: Ask for engineering-philosophy-v7.json with no file present. Check FileNotFoundError that names the file and the paid pack.
        with self.assertRaises(FileNotFoundError) as caught:
            design_path('engineering-philosophy-v7.json', self.root)
        self.assertIsInstance(caught.exception, PackDataMissing)
        self.assertIn('design/engineering-philosophy-v7.json', str(caught.exception))
        self.assertIn('durai.software-generation (paid)', str(caught.exception))

    def test_missing_error_is_a_data_skip_for_the_open_runner(self):
        # Sample: Raise PackDataMissing for samples/trading-v1.json. Check classify_error returns a reason that starts with "missing data".
        try:
            design_path('samples/trading-v1.json', self.root)
        except PackDataMissing:
            reason = run_open_tests.classify_error(sys.exc_info())
        self.assertTrue(reason.startswith('missing data: '))

    def test_supplier_names(self):
        # Sample: Ask the supplier of the v15 file, the v7 file, the blueprints and an unknown file. Check four different answers.
        self.assertEqual('durai.starter (free)', supplier('engineering-philosophy-v15.json'))
        self.assertEqual('durai.software-generation (paid)', supplier('engineering-philosophy-v7.json'))
        self.assertEqual('durai.blueprints.transactions (paid)', supplier('transaction-blueprints-v1.json'))
        self.assertEqual('an unknown pack', supplier('notes.txt'))


class GenericPackVersionTests(unittest.TestCase):
    def setUp(self):
        # Sample: Make an export-like root T/durai with no design folder and T/packs/starter/design holding the real v15 file.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / 'durai'
        self.root.mkdir()
        self.free = Path(folder.name) / 'packs' / 'starter' / 'design'
        self.free.mkdir(parents=True)
        (self.free / 'engineering-philosophy-v15.json').write_bytes(design_path('engineering-philosophy-v15.json').read_bytes())

    def test_default_uses_v7_when_present(self):
        # Sample: Add an empty-policy file design/engineering-philosophy-v7.json to the root. Check the default version is 7.
        (self.root / 'design').mkdir()
        (self.root / 'design' / 'engineering-philosophy-v7.json').write_text('{}', encoding='utf-8')
        self.assertEqual('7', default_policy_version(self.root))

    def test_default_falls_back_to_newest_free(self):
        # Sample: Leave only the v15 file. Check generic_pack with no version returns a pack with version 15.
        self.assertEqual('15', default_policy_version(self.root))
        self.assertEqual('15', generic_pack(root=self.root)['version'])

    def test_explicit_version_never_falls_back(self):
        # Sample: Ask for version 7 with no v7 file. Check PackDataMissing that names the paid pack.
        with self.assertRaises(PackDataMissing) as caught:
            generic_pack('7', root=self.root)
        self.assertIn('durai.software-generation (paid)', str(caught.exception))

    def test_free_layout_gives_the_same_pack(self):
        # Sample: Build v15 from the free layout and from the real design folder. Check the two packs are equal.
        self.assertEqual(generic_pack('15'), generic_pack('15', root=self.root))


HERE = Path(__file__).resolve().parents[1]
PACKS = next(folder for folder in (HERE / 'packs', HERE.parent / 'packs' / 'starter' / 'packs') if (folder / 'durai.starter-v1.0.0.json').is_file())


class PolicySourceTests(unittest.TestCase):
    def make_view(self, install=True):
        # Sample: Make a registry that trusts durai-test-signer-2026. Install the signed starter pack when asked. Return an effective view.
        keys = TrustedKeyList()
        keys.add_key('durai-test-signer-2026', (PACKS / 'test_public_key.hex').read_text(encoding='ascii').strip())
        registry = PackRegistry(trusted_keys=keys)
        if install:
            entry = registry.install(json.loads((PACKS / 'durai.starter-v1.0.0.json').read_text(encoding='utf-8')))
            self.assertEqual(INSTALLED, entry['state'])
        return EffectiveView(registry)

    def test_view_policies_are_used_when_given(self):
        # Sample: Install the starter pack. Ask PolicySource(view) for policies. Check 26 policies and the first id equals the v15 file.
        policies = PolicySource(self.make_view()).policies()
        self.assertEqual(26, len(policies))
        self.assertEqual(read_design('engineering-philosophy-v15.json')['policies'][0]['id'], policies[0]['id'])

    def test_without_a_view_policies_come_from_generic_pack(self):
        # Sample: Ask PolicySource() for policies. Check they equal the policies of generic_pack with no version.
        self.assertEqual(generic_pack()['records'][0]['body']['policies'], PolicySource().policies())

    def test_view_without_the_record_raises(self):
        # Sample: Make a view with no pack installed. Ask for policies. Check PackDataMissing that names durai.starter.
        with self.assertRaises(PackDataMissing) as caught:
            PolicySource(self.make_view(install=False)).policies()
        self.assertIn('durai.starter (free)', str(caught.exception))

    def test_starter_pack_policies_equal_v15_file(self):
        # Sample: Install the starter pack. Check its policies equal the policies of design/engineering-philosophy-v15.json exactly.
        self.assertEqual(read_design('engineering-philosophy-v15.json')['policies'], PolicySource(self.make_view()).policies())


if __name__ == '__main__':
    unittest.main()
