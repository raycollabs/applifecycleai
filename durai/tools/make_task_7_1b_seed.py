"""Sample: Build seed SQL with generic packs G-3 and G-4, trading packs A-1 and A-2, foreign pack F-1, and five bindings."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.common import digest
from durai.packs import generic_pack, make_record

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'templates/task-7-1b-policy-store/protected/tests/seed/001_packs.sql'


def application_pack(pack_id, policies):
    # Sample: Build trading pack A-1 with one DESIGN KNOWLEDGE record that holds rule trading-shares-unit.
    record = make_record('design.knowledge', 'DESIGN', 'KNOWLEDGE', {'layer': 'DESIGN', 'policies': policies})
    return {'format': 'durai-ontology-pack/v1', 'id': pack_id, 'version': '1', 'kind': 'APPLICATION',
            'reviewState': 'SYNTHETIC_FIXTURE', 'records': [record], 'edges': [], 'provenance': {'fixture': 'task-7-1b'}}


def literal(value):
    # Sample: Quote pack G-4 as one dollar-quoted jsonb literal. Refuse text that contains the quote tag.
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if '$seed$' in text:
        raise ValueError('Seed quote tag occurs in the data')
    return '$seed$' + text + '$seed$::jsonb'


def main():
    # Sample: Write 001_packs.sql with five packs, their records, and five bindings. Print the pack digests.
    packs = [('*', generic_pack('3')), ('*', generic_pack('4')),
             ('trading', application_pack('trading.application', [
                 {'id': 'trading-shares-unit', 'rule': 'DO NOT store quantities in lots. Store shares.', 'audience': ['coding-worker']}])),
             ('trading', application_pack('trading.conflict', [
                 {'id': 'do-not-edit-unrequested', 'rule': 'DO NOT edit tests.', 'audience': ['coding-worker']}])),
             ('foreign', application_pack('foreign.application', [
                 {'id': 'foreign-only-rule', 'rule': 'DO NOT share foreign data.', 'audience': ['coding-worker']}]))]
    lines, references = ['-- Sample: Insert generic packs 3 and 4, packs A-1, A-2, and F-1, and five bindings for apps trading and foreign.'], {}
    for scope, pack in packs:
        checksum = digest(pack)
        references[(scope, pack['id'], pack['version'])] = {'scope': scope, 'id': pack['id'], 'version': pack['version'], 'digest': checksum}
        lines.append("INSERT INTO durai_ontology.pack(scope,id,version,kind,digest,document) VALUES('%s','%s','%s','%s','%s',%s);"
                     % (scope, pack['id'], pack['version'], pack['kind'], checksum, literal(pack)))
        for record in pack['records']:
            lines.append("INSERT INTO durai_ontology.record VALUES('%s','%s','%s','%s','%s','%s','%s',%s);"
                         % (scope, pack['id'], pack['version'], record['id'], record['layer'], record['type'], record['digest'], literal(record)))
    generic3 = references[('*', 'durai.software-generation', '3')]
    generic4 = references[('*', 'durai.software-generation', '4')]
    bindings = [('trading', 'foundation-generic-v4', [generic4]), ('trading', 'generic-v3', [generic3]),
                ('trading', 'trading-app-v1', [generic4, references[('trading', 'trading.application', '1')]]),
                ('trading', 'trading-conflict', [generic4, references[('trading', 'trading.conflict', '1')]]),
                ('foreign', 'foreign-app-v1', [generic4, references[('foreign', 'foreign.application', '1')]])]
    for application, identity, refs in bindings:
        document = {'packs': refs, 'mode': 'SYNTHETIC_LOCAL', 'releaseAuthority': False}
        lines.append("INSERT INTO durai_ontology.binding(application_id,id,revision,document,digest) VALUES('%s','%s',1,%s,'%s');"
                     % (application, identity, literal(document), digest(document)))
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'/'.join(key): value['digest'][:12] for key, value in references.items()}))


if __name__ == '__main__':
    main()
