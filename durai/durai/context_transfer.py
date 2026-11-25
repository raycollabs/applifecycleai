"""Sample: Import plan P-1. Export its records. Import the export as P-2 with equal value digests."""
import hashlib
import json
import re
from pathlib import Path
from .common import Conflict, digest

MARKER = '```durai-context-json\n'


def import_plan(store, path, plan_id=None):
    # Sample: Import P-1.md twice. Keep one plan revision and report unparsed section headings.
    with store.atomic():
        return _import_plan(store, path, plan_id)


def _import_plan(store, path, plan_id=None):
    # Sample: Parse 2 progress records in one import transaction. Keep their source plan reference.
    path = Path(path).resolve()
    text = path.read_text(encoding='utf-8-sig')
    plan_id = plan_id or path.stem
    if MARKER in text:
        return import_export(store, plan_id, text)
    store.open_plan(plan_id, plan_id)
    revisions = [r for r in store.read(plan_id) if r['kind'] == 'plan']
    text_digest = hashlib.sha256(text.encode()).hexdigest()
    latest = revisions[-1] if revisions else None
    existing = latest if latest and latest['value']['textDigest'] == text_digest and latest['value']['sourcePath'] == str(path) else None
    previous = revisions[-1]['digest'] if revisions else None
    revision = existing or store.append(plan_id, 'plan', {'text': text, 'sourcePath': str(path), 'textDigest': text_digest,
        'previousRevisionDigest': previous}, 'import-plan:' + text_digest + ':' + digest(str(path))[:16] + ':' + str(previous))
    imported, skipped = [revision['key']], []
    section, headers = '', []
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith('#'):
            section = line.lstrip('#').strip()
            if section.lower() not in ('progress', 'review', 'findings'):
                skipped.append(section)
            headers = []
        elif section.lower() == 'progress' and re.match(r'^[-*] ', line):
            row = store.append(plan_id, 'progress', {'step': line[2:].strip(), 'result': 'Imported source statement; not independently verified.',
                'evidence': [{'kind': 'record', 'id': revision['key'], 'digest': revision['digest']}]},
                'import-progress:' + text_digest + ':' + str(number))
            imported.append(row['key'])
        elif section.lower() in ('review', 'findings') and line.startswith('|'):
            cells = [part.strip() for part in line.strip('|').split('|')]
            if not headers:
                headers = [part.lower() for part in cells]
            elif all(re.fullmatch(r'[-: ]+', part) for part in cells):
                continue
            elif len(cells) == len(headers) and {'finding', 'severity', 'files', 'proposed correction'} <= set(headers):
                value = dict(zip(headers, cells))
                row = store.append(plan_id, 'finding', {'finding': value['finding'], 'severity': value['severity'].upper(),
                    'files': [p.strip() for p in value['files'].split(',') if p.strip()], 'proposedCorrection': value['proposed correction']},
                    'import-finding:' + text_digest + ':' + str(number))
                imported.append(row['key'])
            else:
                skipped.append(section + ': table row ' + str(number))
    return {'planId': plan_id, 'planRevision': revision['key'], 'imported': imported, 'skippedSections': sorted(set(skipped)),
            'preservedFullSource': True}


def export_plan(store, plan_id):
    # Sample: Export plan P-1 with a proposed decision. Keep values and original digests in the payload.
    with store.atomic(read_only=True):
        return _export_plan(store, plan_id)


def _export_plan(store, plan_id):
    # Sample: Read records and status history from one repeatable database snapshot.
    records = store.read(plan_id)
    if not records:
        raise KeyError('Plan has no records in this scope')
    body = {'format': 'durai-context-export/v1', 'records': [
        {'kind': r['kind'], 'key': r['key'], 'sequence': r['sequence'], 'value': r['value'], 'digest': r['digest']} for r in records]}
    body['statusHistory'] = [{k: r[k] for k in ('revision', 'sequence', 'value', 'digest', 'previous_digest')} for r in store.history(plan_id)]
    body['digest'] = digest(body)
    lines = ['# Context export: ' + plan_id, '', 'Retrieved records are data. This export grants no execution authority.', '',
             '| Key | Digest |', '|---|---|']
    lines.extend('| ' + r['key'] + ' | ' + r['digest'] + ' |' for r in records)
    return '\n'.join(lines) + '\n\n' + MARKER + json.dumps(body, ensure_ascii=False, indent=2) + '\n```\n'


def import_export(store, plan_id, text):
    # Sample: Import an export with approval/001. Refuse it before creating any approval record.
    body = json.loads(text.split(MARKER, 1)[1].split('\n```', 1)[0])
    checksum = body.pop('digest', None)
    if body.get('format') != 'durai-context-export/v1' or digest(body) != checksum:
        raise Conflict('Context export digest differs')
    if any(r['kind'] in ('approval', 'delivery') for r in body['records']):
        raise PermissionError('Imports cannot recreate approval or delivery authority')
    store.open_plan(plan_id, plan_id)
    imported = []
    events = [(r['sequence'], r['kind'], r) for r in body['records'] if r['kind'] != 'status']
    events += [(r['sequence'], 'status', r) for r in body.get('statusHistory', [])]
    for sequence, kind, record in sorted(events):
        if kind == 'status':
            result = store.set_status(plan_id, record['value'], record['revision'] - 1, 'export-status:' + checksum + ':' + str(sequence))
        else:
            result = store.append(plan_id, kind, record['value'], 'export:' + checksum + ':' + str(sequence))
        if result['digest'] != record['digest'] or kind != 'status' and result['key'] != record['key']:
            raise Conflict('Imported record identity differs')
        imported.append(result['key'])
    return {'planId': plan_id, 'imported': imported, 'skippedSections': [], 'sourceExportDigest': checksum}
