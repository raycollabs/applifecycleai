"""Sample: Make a registry for workspace W-1. Call get_sample S-TRADE-BOOK, write docs/design.md, submit DESIGN. Check one stage with a SHA-256."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from .build_guidance import guidance_text, topic_names
from .build_samples import JsonSampleSource

STAGES = ('DESIGN', 'PLAN', 'IMPLEMENTATION', 'TESTS', 'DEPLOYED')
REQUIRED_STAGES = STAGES[:4]
CODE_SUFFIXES = {'.py', '.sql', '.sh', '.js', '.ts', '.go', '.java', '.rs', '.c', '.cpp', '.rb', '.php'}
MAX_WRITE = 200_000
MAX_READ = 60_000
MAX_LIST = 500


class ToolError(Exception):
    pass


def now():
    # Sample: Return the current UTC time as 2026-11-22T10:00:00+00:00.
    return datetime.now(timezone.utc).isoformat()


def sha256_of(path):
    # Sample: File holds the bytes abc. Return ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad.
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class BuildTools:
    def __init__(self, workspace, samples=None, runner=None, archive=None, app_runner=None, enhance=False, enhancer=None, build_label=None, bare=False):
        # Sample: Open workspace W-1 with the trading samples. Start with no component and no stage. Use archive W-1-artifacts. With enhance on, offer get_enhanced_request and 12 tools. With enhance off, offer 11 tools. With bare on, offer raw tools without guidance or stage gates.
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.archive = Path(archive).resolve() if archive else self.root.parent / (self.root.name + '-artifacts')
        self.samples = samples or JsonSampleSource()
        self.runner = runner
        self.app_runner = app_runner
        self.enhance_on, self.enhancer, self.build_label = bool(enhance), enhancer, build_label
        self.bare = bool(bare)
        self.current = None
        self.components = {}
        self.errors = {}
        self.finished = False
        self.table = {
            'list_samples': (self.list_samples, 'List the user requests you can build.',
                             {'domain': {'type': 'string'}}, []),
            'get_sample': (self.get_sample, 'Get one user request by sampleId and start work on it.',
                           {'sampleId': {'type': 'string'}}, ['sampleId']),
            'list_files': (self.list_files, 'List files in the workspace, or in one folder.',
                           {'path': {'type': 'string'}}, []),
            'read_file': (self.read_file, 'Read one text file from the workspace.',
                          {'path': {'type': 'string'}}, ['path']),
            'write_file': (self.write_file, 'Write one text file in the workspace.' if self.bare else 'Write one text file in the workspace. Code files need an accepted PLAN.',
                           {'path': {'type': 'string'}, 'content': {'type': 'string'}}, ['path', 'content']),
            'edit_file': (self.edit_file, 'Replace one exact text in a workspace file. The old text must occur once.',
                          {'path': {'type': 'string'}, 'old': {'type': 'string'}, 'new': {'type': 'string'}},
                          ['path', 'old', 'new']),
            'run_tests': (self.run_tests, 'Run the workspace tests against a disposable database. Returns passed and failed counts.',
                          {}, []),
            'check_app': (self.check_app, 'Start python -m src.app in the sandbox. Wait up to 60 seconds for HTTP 200 on /. Returns status and the first 2000 characters.',
                          {}, []),
            'finish': (self.finish, 'End the work on this component.', {}, []),
        }
        if not self.bare:
            self.table['get_guidance'] = (self.get_guidance, 'Get the Durai engineering rules. Call it first. Optional topic.',
                                          {'topic': {'type': 'string', 'enum': topic_names(bool(enhance))}}, [])
            self.table['submit_stage'] = (self.submit_stage, 'Submit a stage in order: DESIGN, PLAN, IMPLEMENTATION, TESTS, DEPLOYED. DEPLOYED is for the screens component: submit src/app.py.',
                                          {'stage': {'type': 'string', 'enum': list(STAGES)},
                                           'paths': {'type': 'array', 'items': {'type': 'string'}}}, ['stage', 'paths'])
            if self.enhance_on:
                self.table['get_enhanced_request'] = (
                    self.get_enhanced_request, 'Get the request of the current sample with open questions, concepts to apply and an answer format for each stage.', {}, [])

    def tool_list(self):
        # Sample: List tools. Return 11 records with name, description and inputSchema.
        return [{'name': name, 'description': description,
                 'inputSchema': {'type': 'object', 'properties': properties, 'required': required}}
                for name, (_, description, properties, required) in self.table.items()]

    def call(self, name, arguments=None):
        # Sample: Call write_file for ../x.py. Return isError true and no file outside. Call an unknown tool. Return isError true.
        arguments = arguments or {}
        try:
            if name not in self.table:
                raise ToolError('Unknown tool ' + str(name))
            function, _, properties, required = self.table[name]
            if not isinstance(arguments, dict):
                raise ToolError('Arguments must be an object')
            for field in required:
                if field not in arguments:
                    raise ToolError('Missing argument ' + field)
            unknown = sorted(set(arguments) - set(properties))
            if unknown:
                raise ToolError('Unknown argument ' + ', '.join(unknown))
            return {'text': function(**arguments), 'isError': False}
        except ToolError as error:
            self.errors[name] = self.errors.get(name, 0) + 1
            return {'text': 'ERROR: ' + str(error), 'isError': True}
        except Exception as error:
            self.errors[name] = self.errors.get(name, 0) + 1
            return {'text': 'ERROR: ' + type(error).__name__ + ': ' + str(error)[:500], 'isError': True}

    def resolve(self, path):
        # Sample: Resolve docs/a.md. Return W-1/docs/a.md. Resolve ../x.py, C:/x.py or a link. Refuse each.
        if not isinstance(path, str) or not path.strip() or '\x00' in path:
            raise ToolError('Path must be a non-empty text')
        if PureWindowsPath(path).is_absolute() or PureWindowsPath(path).drive or PurePosixPath(path).is_absolute() \
                or path.startswith(('/', '\\')):
            raise ToolError('Absolute paths are refused. Use a path inside the workspace')
        parts = [part for part in path.replace('\\', '/').split('/') if part not in ('', '.')]
        if not parts or '..' in parts:
            raise ToolError('Paths with .. or no name are refused')
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                raise ToolError('Links are refused: ' + part)
        resolved = Path(os.path.realpath(current))
        if not resolved.is_relative_to(self.root):
            raise ToolError('Path is outside the workspace')
        return current

    def relative(self, path):
        # Sample: Return docs/a.md for W-1/docs/a.md.
        return path.relative_to(self.root).as_posix()

    def state(self):
        # Sample: Return the stage record of the current component. Raise before get_sample.
        if self.current is None:
            raise ToolError('Call get_sample first')
        return self.components[self.current]

    def accepted(self):
        # Sample: After DESIGN and PLAN return the list DESIGN, PLAN.
        return [item['stage'] for item in self.state()['stages']]

    def list_samples(self, domain=None):
        # Sample: List domain trading. Return six lines with sampleId and title.
        found = self.samples.list(domain)
        return '\n'.join(item['sampleId'] + ': ' + item['title'] for item in found) or 'No samples'

    def get_sample(self, sampleId):
        # Sample: Get S-TRADE-BOOK. Return the request text. Make S-TRADE-BOOK the current component.
        try:
            sample = self.samples.get(sampleId)
        except KeyError as error:
            raise ToolError(str(error.args[0]))
        self.current = sampleId
        self.components.setdefault(sampleId, {'sample': sample, 'stages': [], 'lastRunTests': None})
        return ('Sample ' + sample['sampleId'] + ' revision ' + str(sample['revision']) + ' (' + sample['title'] + ')\n'
                'Request: ' + sample['requestText'])

    def get_enhanced_request(self):
        # Sample: Current sample S-TRADE-BOOK. Return its words first, then questions, concepts and stage formats. With a database add the line "recorded enhancement E-1". Without one add "not recorded: no database".
        sample = self.state()['sample']
        if self.enhancer is None:
            from .prompt_enhancer import Enhancer
            self.enhancer = Enhancer.offline()
        from .prompt_enhancer import BlindCheckError, SkillError
        record = self.enhancer.recorder is not None
        try:
            result = self.enhancer.enhance(sample['requestText'], record=record, sample_id=self.current, build_label=self.build_label)
        except (BlindCheckError, SkillError) as error:
            raise ToolError('Enhanced request refused: ' + str(error)[:300])
        note = ('Durai recorded enhancement ' + result['enhancementId']) if record else 'Durai did not record this enhancement: no database'
        return result['text'] + '\n[' + note + ']'

    def get_guidance(self, topic=None):
        # Sample: Get topic tests. Return the tests rules. Get no topic. Return every topic.
        try:
            return guidance_text(topic, self.enhance_on)
        except KeyError as error:
            raise ToolError(str(error.args[0]))

    def list_files(self, path=None):
        # Sample: Workspace holds docs/a.md and src/x.py. List. Return both paths.
        base = self.root if path in (None, '', '.') else self.resolve(path)
        if not base.is_dir():
            raise ToolError('Not a folder: ' + str(path))
        found = []
        for item in sorted(base.rglob('*')):
            hidden = any(part.startswith('.') or part == '__pycache__' for part in item.relative_to(self.root).parts)
            if item.is_file() and not item.is_symlink() and not hidden:
                found.append(self.relative(item))
            if len(found) >= MAX_LIST:
                break
        return '\n'.join(found) or 'No files'

    def read_file(self, path):
        # Sample: Read docs/a.md that holds hello. Return hello. Read a missing file. Return an error.
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError('File not found: ' + path)
        text = target.read_text(encoding='utf-8', errors='replace')
        return text if len(text) <= MAX_READ else text[:MAX_READ] + '\n[truncated]'

    def _shell_manifest_paths(self):
        # Sample: Workspace has shell-manifest.json listing app/Menu.tsx. Return set of normalized read-only paths.
        manifest = self.root / 'shell-manifest.json'
        if not manifest.is_file():
            return set()
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
        except Exception:
            return set()
        files = data.get('files') if isinstance(data, dict) else None
        if isinstance(files, dict):
            return {p.replace('\\', '/') for p in files}
        if isinstance(files, list):
            return {str(p).replace('\\', '/') for p in files}
        return set()

    def _workspace_digest(self):
        # Sample: Workspace has src/api.py and app/page.tsx. Return one SHA-256 digest over all non-hidden files.
        hasher = hashlib.sha256()
        for item in sorted(self.root.rglob('*')):
            if not item.is_file():
                continue
            rel = self.relative(item)
            if rel.startswith('.durai/') or '/.durai/' in rel or rel.startswith('node_modules/') or rel.startswith('.next/'):
                continue
            hasher.update(rel.encode('utf-8'))
            hasher.update(b'\0')
            hasher.update(item.read_bytes())
        return hasher.hexdigest()

    def gate(self, target):
        # Sample: In bare mode accept code files. In Durai mode, if PLAN is not accepted, refuse booking.py.
        rel = self.relative(target)
        if rel in self._shell_manifest_paths() or rel == 'shell-manifest.json':
            raise ToolError('Shell manifest path is read-only: ' + rel)
        if self.bare:
            return
        if target.suffix.lower() in CODE_SUFFIXES and 'PLAN' not in self.accepted():
            raise ToolError('Code files are refused. Submit PLAN first (order: DESIGN, then PLAN)')

    def write_file(self, path, content):
        # Sample: Write docs/design.md with x. Return Wrote docs/design.md. Write booking.py before PLAN. Refuse.
        if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_WRITE:
            raise ToolError('Content must be text of at most ' + str(MAX_WRITE) + ' bytes')
        target = self.resolve(path)
        self.gate(target)
        if target.is_dir():
            raise ToolError('Path is a folder')
        target.parent.mkdir(parents=True, exist_ok=True)
        self.resolve(path)
        target.write_text(content, encoding='utf-8', newline='\n')
        self.state()['lastCheckApp'] = None
        return 'Wrote ' + self.relative(target) + ' (' + str(len(content.encode('utf-8'))) + ' bytes)'

    def edit_file(self, path, old, new):
        # Sample: File holds a=1. Replace a=1 with a=2. Check a=2. Replace text that occurs twice. Refuse.
        target = self.resolve(path)
        self.gate(target)
        if not target.is_file():
            raise ToolError('File not found: ' + path)
        text = target.read_text(encoding='utf-8')
        if not old or text.count(old) != 1:
            raise ToolError('Old text must occur exactly once; it occurs ' + str(text.count(old) if old else 0) + ' times')
        target.write_text(text.replace(old, new), encoding='utf-8', newline='\n')
        self.state()['lastCheckApp'] = None
        return 'Edited ' + self.relative(target)

    def submit_stage(self, stage, paths):
        # Sample: Submit PLAN first. Refuse. Submit DESIGN with docs/design.md. Return its SHA-256. Submit PLAN. Return its SHA-256.
        state = self.state()
        if stage not in STAGES:
            raise ToolError('Stage must be one of ' + ', '.join(STAGES))
        done = self.accepted()
        expected = STAGES[len(done)] if len(done) < len(STAGES) else None
        if stage != expected:
            raise ToolError('Stage ' + stage + ' refused. Next stage is ' + str(expected) + '. Accepted: ' + (', '.join(done) or 'none'))
        if not isinstance(paths, list) or not paths:
            raise ToolError('Give at least one path')
        check_evidence = None
        if stage == 'DEPLOYED':
            from .build_web_rules import check_web_rules, describe
            rules = check_web_rules(self.root)
            if rules:
                raise ToolError('DEPLOYED refused.\n' + describe(rules))
            last_check = state.get('lastCheckApp')
            current_digest = self._workspace_digest()
            if not last_check or not last_check.get('passed') or last_check.get('digest') != current_digest:
                raise ToolError('DEPLOYED refused. Run check_app after the last edit and pass sign-in and HTTP 200 checks.')
            check_evidence = {'status': last_check.get('status'), 'signIn': last_check.get('signIn'), 'digest': current_digest}
        files = []
        for path in paths:
            target = self.resolve(path)
            if not target.is_file():
                raise ToolError('File not found: ' + str(path))
            files.append(target)
        record = {'stage': stage, 'paths': [self.relative(item) for item in files],
                  'sha256s': [sha256_of(item) for item in files], 'at': now()}
        if check_evidence is not None:
            record['checkApp'] = check_evidence
        self.archive.mkdir(parents=True, exist_ok=True)
        for item, digest in zip(files, record['sha256s']):
            kept = self.archive / digest
            if not kept.exists():
                kept.write_bytes(item.read_bytes())
        state['stages'].append(record)
        return 'Accepted ' + stage + '\n' + '\n'.join(p + ' ' + h for p, h in zip(record['paths'], record['sha256s']))

    def run_tests(self):
        # Sample: Workspace has one passing and one failing test. Return passed 1, failed 1, and the output tail.
        state = self.state()
        runner = self.runner
        if runner is None:
            from .build_run_tests import run_workspace_tests as runner
        result = runner(self.root)
        state['lastRunTests'] = {'passed': result['passed'], 'failed': result['failed']}
        return ('passed: ' + str(result['passed']) + '\nfailed: ' + str(result['failed']) +
                ('\nTIMED OUT' if result.get('timedOut') else '') + '\n--- output ---\n' + result.get('output', ''))

    def check_app(self):
        # Sample: Workspace src/app.py serves ok. Return status 200 and the text ok. App crashes at start. Return status none and the log tail.
        state = self.state()
        runner = self.app_runner
        if runner is None:
            from .build_run_tests import check_workspace_app as runner
        result = runner(self.root)
        from .build_web_rules import check_web_rules, describe
        rules = check_web_rules(self.root)
        sign_in = result.get('signIn')
        sign_in_ok = True if sign_in is None else bool(sign_in.get('ok'))
        passed = (result.get('status') == 200) and (not rules) and sign_in_ok
        state['lastCheckApp'] = {
            'status': result.get('status'),
            'rules': [item['rule'] for item in rules],
            'signIn': sign_in,
            'passed': passed,
            'digest': self._workspace_digest(),
        }
        text = 'status: ' + str(result.get('status')) + '\n--- body (first 2000 characters) ---\n' + (result.get('body') or '')
        if result.get('status') != 200:
            text += '\n--- app log ---\n' + (result.get('output') or '')
        text += '\n--- ' + describe(rules)
        if not passed:
            text += '\nDEPLOYED is refused until check_app passes (status 200, web rules ok, sign-in ok).'
        return text

    def finish(self):
        # Sample: Accepted DESIGN and PLAN only. Finish. Return the missing stages IMPLEMENTATION and TESTS. Skip DEPLOYED without a message.
        missing = [stage for stage in REQUIRED_STAGES if stage not in self.accepted()]
        self.finished = True
        return 'Finished' + (' with missing stages: ' + ', '.join(missing) if missing else '')
