"""Sample: Write manifest of a baseline tree. Load trade-pilot E6 with 1 component and 2 samples into durai_test. Show its matrix."""
import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from psycopg.types.json import Jsonb  # noqa: E402
from durai.common import Conflict  # noqa: E402
from durai.db import transaction  # noqa: E402

SECRET_NAMES = ('githubkey*', '*.pem', '*.key', '.env*', '*credential*', '*secret*')
SECRET_FOLDERS = {'private', 'node_modules', '.git', '.next', 'dist', 'build', 'logs', '__pycache__', 'third_party'}
KEY_JSON = re.compile(r'-[0-9a-f]{10,}', re.IGNORECASE)
APP_FIELDS = ('app_key', 'app_version', 'display_name', 'domain', 'builder_model', 'durai_ranking')
COMPONENT_FIELDS = ('component_key', 'category', 'goal', 'design', 'built', 'status', 'sample_id', 'sample_revision')


def is_secret_name(name, extra=()):
    # Sample: Check githubkey, app-sa-0123456789ab.json and service-account.json are secret. Check index.tsx is not.
    lower = name.lower()
    if any(fnmatch.fnmatch(lower, pattern) for pattern in SECRET_NAMES + tuple(item.lower() for item in extra)):
        return True
    return lower.endswith('.json') and (bool(KEY_JSON.search(lower)) or 'firebase-adminsdk' in lower or 'service-account' in lower)


def file_digest(path):
    # Sample: Hash a 16 byte file in 1 MiB chunks. Return 64 hex characters.
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_digest(files):
    # Sample: Join "pages/index.tsx", 12 and a SHA-256 with tabs. Hash the sorted lines.
    lines = ''.join('{}\t{}\t{}\n'.format(item['path'], item['size'], item['sha256']) for item in sorted(files, key=lambda item: item['path']))
    return hashlib.sha256(lines.encode('utf-8')).hexdigest()


def build_manifest(root, extra=()):
    # Sample: Walk a tree with githubkey, private/runtime.json and pages/index.tsx. Return 1 file, pages/index.tsx.
    root = Path(root).resolve()
    files = []
    for folder, names, filenames in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name.lower() not in SECRET_FOLDERS and not is_secret_name(name, extra)
                          and not (Path(folder, name).is_symlink()))
        for name in sorted(filenames):
            full = Path(folder, name)
            relative = full.relative_to(root).as_posix()
            if is_secret_name(name, extra) or any(fnmatch.fnmatch(relative.lower(), item.lower()) for item in extra) or full.is_symlink():
                continue
            files.append({'path': relative, 'size': full.stat().st_size, 'sha256': file_digest(full)})
    files.sort(key=lambda item: item['path'])
    return {'root': str(root), 'files': files, 'digest': manifest_digest(files)}


def read_manifest(path):
    # Sample: Read manifest M-1 with 3 files. Recompute its digest. Refuse when the digest differs.
    manifest = json.loads(Path(path).read_text(encoding='utf-8'))
    if manifest_digest(manifest['files']) != manifest['digest']:
        raise ValueError('Manifest digest does not match its files')
    return manifest


def normal(name):
    return re.sub(r'[^a-z0-9]', '', name.lower())


def baseline_terms(manifest, root=None):
    # Sample: Read paths booking.go and migrations/001.sql with CREATE TABLE trade_lot. Return file booking.go, stem booking, table trade_lot.
    root = Path(root or manifest['root'])
    names, stems, tables = set(), set(), set()
    for item in manifest['files']:
        name = item['path'].rsplit('/', 1)[-1]
        if '.' in name.strip('.'):
            names.add(name.lower())
            stems.add(normal(name.split('.')[0]))
        parts = item['path'].lower().split('/')
        if item['path'].lower().endswith('.sql') and 'migrations' in parts[:-1]:
            text = (root / item['path']).read_text(encoding='utf-8', errors='replace')
            for found in re.findall(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w."]+)', text, re.IGNORECASE):
                tables.add(found.replace('"', '').lower())
    return names, stems, tables


def blind_violations(text, terms):
    # Sample: Check "Extend internal/service/booking.go with FIFO lots". Return booking.go and a path. Check "book trades once" returns nothing.
    names, stems, tables = terms
    lower, found = text.lower(), []
    boundary = r'(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])'
    for name in sorted(names):
        if re.search(boundary.format(re.escape(name)), lower):
            found.append('file ' + name)
    for match in re.findall(r'[\w][\w.-]*[/\\][\w./\\-]*', text):
        found.append('path ' + match)
    for token in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', text):
        shaped = '_' in token.strip('_') or re.search(r'[a-z0-9][A-Z]', token) or re.match(r'[A-Z][a-z0-9]+[A-Z]', token)
        if shaped and normal(token) in stems:
            found.append('identifier ' + token)
    for table in sorted(tables):
        short = table.rsplit('.', 1)[-1]
        needle = table if '.' in table else short
        if ('_' in short or '.' in table) and re.search(boundary.format(re.escape(needle)), lower):
            found.append('table ' + needle)
    return found


def check_components(components, manifest, base):
    # Sample: Check booking with evidence booking.go at digest a1 against the manifest. Pass. Change the file and read a refusal naming booking.go.
    by_path = {item['path']: item for item in manifest['files']}
    for component in components:
        for field in COMPONENT_FIELDS[:-1]:
            if component.get(field) in (None, ''):
                raise ValueError('Component field is missing: ' + field)
        for evidence in component.get('evidence', []):
            entry = by_path.get(evidence.get('path'))
            if not entry:
                raise ValueError('Evidence path is not in the manifest: ' + str(evidence.get('path')))
            if entry['sha256'] != evidence.get('sha256'):
                raise ValueError('Evidence digest differs from the manifest: ' + evidence['path'])
            if file_digest(base / evidence['path']) != entry['sha256']:
                raise ValueError('Evidence file changed after the manifest: ' + evidence['path'])


def load_benchmark(path, manifest_path, database='durai_local', root=None):
    # Sample: Load trade-pilot E6 with evidence booking.go. Return 1 app, 1 component, 2 samples. Load it again and read a refusal.
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    manifest = read_manifest(manifest_path)
    base = Path(root or manifest['root'])
    app, components, samples = data['app'], data['components'], data['samples']
    for field in APP_FIELDS:
        if app.get(field) in (None, ''):
            raise ValueError('App field is missing: ' + field)
    check_components(components, manifest, base)
    terms = baseline_terms(manifest, base)
    for sample in samples:
        found = blind_violations(sample['request_text'], terms)
        if found:
            raise ValueError('Sample {} names baseline terms: {}'.format(sample['sample_id'], ', '.join(found)))
    known = {(sample['sample_id'], sample.get('revision', 1)) for sample in samples}
    for component in components:
        if (component['sample_id'], component.get('sample_revision', 1)) not in known:
            raise ValueError('Component sample is not in the file: ' + component['sample_id'])
    with transaction('durai_evaluator', database) as db:
        if db.execute('SELECT 1 FROM durai_bench.app_benchmark WHERE app_key=%s AND app_version=%s', (app['app_key'], app['app_version'])).fetchone():
            raise Conflict('App version is already loaded: {} {}'.format(app['app_key'], app['app_version']))
        db.execute('INSERT INTO durai_bench.app_benchmark(app_key,app_version,display_name,domain,builder_model,builder_version,model_profile_id,durai_ranking,'
                   'source_root,source_manifest_digest,live_url,notes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                   (app['app_key'], app['app_version'], app['display_name'], app['domain'], app['builder_model'], app.get('builder_version'),
                    app.get('model_profile_id'), app['durai_ranking'], str(base), manifest['digest'], app.get('live_url'), app.get('notes')))
        for sample in samples:
            db.execute('INSERT INTO durai_samples.sample_request(sample_id,revision,domain,scope,parent_sample_id,sort_order,title,request_text,tags,language,status,author,reviewer)'
                       ' VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                       (sample['sample_id'], sample.get('revision', 1), sample['domain'], sample['scope'], sample.get('parent_sample_id'),
                        sample.get('sort_order', 0), sample['title'], sample['request_text'], sample.get('tags', []), sample.get('language', 'en'),
                        sample['status'], sample['author'], sample.get('reviewer')))
        for component in components:
            db.execute('INSERT INTO durai_bench.app_component(app_key,app_version,component_key,category,goal,design,built,status,evidence,test_goal_count,limits)'
                       ' VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                       (app['app_key'], app['app_version'], component['component_key'], component['category'], component['goal'], component['design'],
                        component['built'], component['status'], Jsonb(component.get('evidence', [])), component.get('test_goal_count', 0), component.get('limits', [])))
            revision = component.get('sample_revision', 1)
            digest = hashlib.sha256(next(item['request_text'] for item in samples if item['sample_id'] == component['sample_id']
                                         and item.get('revision', 1) == revision).encode('utf-8')).hexdigest()
            stored = db.execute('SELECT text_digest FROM durai_samples.sample_request WHERE sample_id=%s AND revision=%s', (component['sample_id'], revision)).fetchone()
            if stored['text_digest'] != digest:
                raise ValueError('Sample digest differs: ' + component['sample_id'])
            db.execute('INSERT INTO durai_bench.component_goal(app_key,app_version,component_key,sample_id,sample_revision,sample_digest,hidden_goals)'
                       ' VALUES(%s,%s,%s,%s,%s,%s,%s)',
                       (app['app_key'], app['app_version'], component['component_key'], component['sample_id'], revision, digest,
                        Jsonb(component.get('hidden_goals', []))))
    return {'app': app['app_key'], 'version': app['app_version'], 'components': len(components), 'samples': len(samples)}


def append_benchmark(path, manifest_path, database='durai_local', root=None):
    # Sample: Append components 6 to 17 to trade-pilot e6 with 12 new rows. Append the same file again and read a refusal naming group-valuation.
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    manifest = read_manifest(manifest_path)
    base = Path(root or manifest['root'])
    app, components, samples = data['app'], data['components'], data.get('samples', [])
    for field in ('app_key', 'app_version'):
        if app.get(field) in (None, ''):
            raise ValueError('App field is missing: ' + field)
    check_components(components, manifest, base)
    terms = baseline_terms(manifest, base)
    for sample in samples:
        found = blind_violations(sample['request_text'], terms)
        if found:
            raise ValueError('Sample {} names baseline terms: {}'.format(sample['sample_id'], ', '.join(found)))
    with transaction('durai_evaluator', database) as db:
        if not db.execute('SELECT 1 FROM durai_bench.app_benchmark WHERE app_key=%s AND app_version=%s', (app['app_key'], app['app_version'])).fetchone():
            raise ValueError('App version is not loaded: {} {}'.format(app['app_key'], app['app_version']))
        for component in components:
            if db.execute('SELECT 1 FROM durai_bench.app_component WHERE app_key=%s AND app_version=%s AND component_key=%s',
                          (app['app_key'], app['app_version'], component['component_key'])).fetchone():
                raise Conflict('Component is already loaded: ' + component['component_key'])
        for sample in samples:
            revision = sample.get('revision', 1)
            if db.execute('SELECT 1 FROM durai_samples.sample_request WHERE sample_id=%s AND revision=%s', (sample['sample_id'], revision)).fetchone():
                continue
            db.execute('INSERT INTO durai_samples.sample_request(sample_id,revision,domain,scope,parent_sample_id,sort_order,title,request_text,tags,language,status,author,reviewer)'
                       ' VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                       (sample['sample_id'], revision, sample['domain'], sample['scope'], sample.get('parent_sample_id'),
                        sample.get('sort_order', 0), sample['title'], sample['request_text'], sample.get('tags', []), sample.get('language', 'en'),
                        sample['status'], sample['author'], sample.get('reviewer')))
        for component in components:
            db.execute('INSERT INTO durai_bench.app_component(app_key,app_version,component_key,category,goal,design,built,status,evidence,test_goal_count,limits)'
                       ' VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                       (app['app_key'], app['app_version'], component['component_key'], component['category'], component['goal'], component['design'],
                        component['built'], component['status'], Jsonb(component.get('evidence', [])), component.get('test_goal_count', 0), component.get('limits', [])))
            revision = component.get('sample_revision', 1)
            stored = db.execute('SELECT text_digest FROM durai_samples.sample_request WHERE sample_id=%s AND revision=%s',
                                (component['sample_id'], revision)).fetchone()
            if not stored:
                raise ValueError('Component sample is not stored: ' + component['sample_id'])
            db.execute('INSERT INTO durai_bench.component_goal(app_key,app_version,component_key,sample_id,sample_revision,sample_digest,hidden_goals)'
                       ' VALUES(%s,%s,%s,%s,%s,%s,%s)',
                       (app['app_key'], app['app_version'], component['component_key'], component['sample_id'], revision,
                        stored['text_digest'], Jsonb(component.get('hidden_goals', []))))
    return {'app': app['app_key'], 'version': app['app_version'], 'components_added': len(components), 'samples_in_file': len(samples)}


def show_matrix(app_key, version=None, database='durai_local'):
    # Sample: Read the matrix of trade-pilot. Return one row per component, stage and model.
    with transaction('durai_evaluator', database) as db:
        return db.execute('SELECT * FROM durai_bench.baseline_matrix WHERE app_key=%s AND (%s::text IS NULL OR app_version=%s)'
                          ' ORDER BY app_version, component_key, stage, model_profile_id', (app_key, version, version)).fetchall()


def main(argv=None):
    # Sample: Run "manifest D:/src --out m.json". Print the file count and the digest.
    parser = argparse.ArgumentParser(description='App benchmark manifest, load and show')
    commands = parser.add_subparsers(dest='command', required=True)
    first = commands.add_parser('manifest')
    first.add_argument('root')
    first.add_argument('--out', required=True)
    first.add_argument('--exclude', action='append', default=[])
    second = commands.add_parser('load')
    second.add_argument('file')
    second.add_argument('--manifest', required=True)
    second.add_argument('--root')
    second.add_argument('--database', default='durai_local')
    appended = commands.add_parser('append')
    appended.add_argument('file')
    appended.add_argument('--manifest', required=True)
    appended.add_argument('--root')
    appended.add_argument('--database', default='durai_local')
    third = commands.add_parser('show')
    third.add_argument('app_key')
    third.add_argument('--version')
    third.add_argument('--database', default='durai_local')
    args = parser.parse_args(argv)
    try:
        if args.command == 'manifest':
            result = build_manifest(args.root, args.exclude)
            Path(args.out).write_text(json.dumps(result, indent=1), encoding='utf-8')
            print('files {} digest {}'.format(len(result['files']), result['digest']))
        elif args.command == 'load':
            print(json.dumps(load_benchmark(args.file, args.manifest, args.database, args.root)))
        elif args.command == 'append':
            print(json.dumps(append_benchmark(args.file, args.manifest, args.database, args.root)))
        else:
            for row in show_matrix(args.app_key, args.version, args.database):
                print('\t'.join(str(row[key]) for key in ('app_version', 'component_key', 'category', 'component_status', 'stage', 'model_profile_id', 'baseline_score', 'best_build_score', 'builds')))
    except (ValueError, OSError) as error:
        print('Refused: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
