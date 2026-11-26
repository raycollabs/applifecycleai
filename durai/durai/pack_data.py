"""Sample: Read design/engineering-philosophy-v15.json from design/ or from packs/starter/design/. Name the pack that supplies a missing file."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPLIERS = (
    ('engineering-philosophy-v15.json', 'durai.starter (free)'),
    ('architecture-defaults-v1.json', 'durai.starter (free)'),
    ('engineering-philosophy-v', 'durai.software-generation (paid)'),
    ('transaction-blueprints-v1.json', 'durai.blueprints.transactions (paid)'),
    ('samples/', 'durai.samples (paid)'),
    ('prompt/', 'durai.prompt-seed (paid)'),
    ('reference-packs/', 'durai.reference-packs (paid)'),
)


class PackDataMissing(FileNotFoundError):
    """Sample: Raise for design/engineering-philosophy-v7.json. The message names the file and the pack that supplies it."""


def design_folders(root=None):
    # Sample: Root R. Return [R/design, R/../packs/starter/design]. The full tree has the first. The public export has the second.
    base = Path(root) if root else ROOT
    return [base / 'design', base.parent / 'packs' / 'starter' / 'design']


def supplier(name):
    # Sample: Name samples/trading-v1.json returns durai.samples (paid). Name notes.txt returns an unknown pack.
    for prefix, pack in SUPPLIERS:
        if name == prefix or (prefix.endswith(('/', '-v')) and name.startswith(prefix)):
            return pack
    return 'an unknown pack'


def design_path(name, root=None):
    # Sample: Name engineering-philosophy-v15.json. Return the first existing file in design/ or packs/starter/design/. Raise PackDataMissing when neither has it.
    for folder in design_folders(root):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    raise PackDataMissing('design/' + name + ' is not installed. It is supplied by pack ' + supplier(name))


DEFAULT_POLICY_VERSION = '7'
FALLBACK_POLICY_VERSION = '15'


def default_policy_version(root=None):
    # Sample: design/engineering-philosophy-v7.json exists. Return 7. Only the free v15 file exists. Return 15.
    for folder in design_folders(root):
        if (folder / ('engineering-philosophy-v' + DEFAULT_POLICY_VERSION + '.json')).is_file():
            return DEFAULT_POLICY_VERSION
    return FALLBACK_POLICY_VERSION


def read_design(name, root=None):
    # Sample: Read design/architecture-defaults-v1.json. Return the parsed catalog with 7 patterns.
    return json.loads(design_path(name, root).read_text(encoding='utf-8'))


class PolicySource:
    def __init__(self, view=None, root=None):
        # Sample: Hold an effective view V-1, or None. Hold the root used to find design files.
        self.view = view
        self.root = root

    def policies(self):
        # Sample: View V-1 holds record design.knowledge with 26 policies. Return those 26. No view. Return the 26 policies of generic_pack. A view without the record raises PackDataMissing.
        if self.view is None:
            from .packs import generic_pack
            return generic_pack(root=self.root)['records'][0]['body']['policies']
        record = self.view.get_record('design.knowledge')
        if record is None:
            raise PackDataMissing('design.knowledge is not in the effective view. It is supplied by pack durai.starter (free)')
        return record['body']['policies']
