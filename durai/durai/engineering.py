import ast
import io
import tokenize
from .comment_policy import validate_comments
from psycopg.types.json import Jsonb
from .common import digest, uid, Conflict, audit
from .db import transaction
from .packs import PackStore, ORDER


def validate_domain(specification):
    # Sample: Omit correction direction for 2 lots of 100. Request clarification.
    from .domain_profiles import validate_domain as validate_profile
    return validate_profile(specification)


def check_walkthroughs(source):
    # Sample: Remove the 2-lot example from book(). Report the qualified symbol.
    text = source.decode('utf-8') if isinstance(source, bytes) else source
    validate_comments(text)
    tree = ast.parse(text)
    samples = [token.start for token in tokenize.generate_tokens(io.StringIO(text).readline)
               if token.type == tokenize.COMMENT and token.string.startswith('# Sample:')]
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0].lineno
            if not (ast.get_docstring(node) or '').startswith('Sample:') and not any(node.lineno < line <= first and column > node.col_offset for line, column in samples):
                missing.append(node.name)
    if missing:
        raise Conflict('Missing sample walkthrough: ' + ', '.join(missing))
    return {'functions': sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree)),
            'policy': 'sample-only-asd-ste100/v2; language review required'}


class Engineering:
    def __init__(self, database='durai_local'):
        # Sample: Store a synthetic workflow in durai_local. Leave the old factory unchanged.
        self.database = database
        self.store = PackStore(database)

    def create(self, binding_id, specification, context, mode='GREENFIELD'):
        # Sample: Pin G-1 and T-1 with source A. Create an immutable run identity.
        rules = validate_domain(specification)
        binding = self.store.resolve(binding_id)
        if binding['document']['mode'] != 'SYNTHETIC_LOCAL':
            raise Conflict('Reference-only ontology cannot execute a workflow')
        domain_sources = []
        for reference in binding['document']['packs']:
            pack = self.store.get(reference)
            for record in pack['records']:
                from .domain_profiles import profile_id
                if pack['kind'] == 'APPLICATION' and record['id'] == 'sample.position-domain' and profile_id(specification) == 'trading-position':
                    for key in ('quantityUnit', 'signedLots', 'correctionDirection'):
                        if record['body'][key] != specification[key]:
                            raise Conflict('NEEDS_CLARIFICATION: domain specification contradicts the application ontology')
                    domain_sources.append({'pack': reference, 'recordId': record['id'], 'recordDigest': record['digest']})
        from .worker_contract import resolve_contract
        contract = resolve_contract(binding_id, specification, self.database)
        run_id = uid()
        document = {'bindingId': binding_id, 'bindingRevision': binding['revision'],
                    'specification': specification, 'domainRules': rules, 'context': context,
                    'domainSources': domain_sources or [{'kind': 'USER_DEFINED', 'specificationDigest': digest(specification)}],
                    'packs': binding['document']['packs'], 'releaseAuthority': False,
                    'workerContract': contract, 'workerContractDigest': contract['digest']}
        with transaction(database=self.database) as db:
            db.execute('INSERT INTO durai_engineering.run VALUES(durai_core.application_id(),%s,%s,%s,%s,%s,DEFAULT)',
                       (run_id, mode, binding['digest'], digest(context), Jsonb(document)))
            audit(db, 'ENGINEERING_CREATE', {'runId': run_id, 'mode': mode, 'contextDigest': digest(context)})
        return run_id

    def get(self, run_id):
        # Sample: Reload run R-1. Return its pinned specification and ordered evidence.
        with transaction(database=self.database) as db:
            run = db.execute('SELECT * FROM durai_engineering.run WHERE id=%s', (run_id,)).fetchone()
            evidence = db.execute('SELECT stage,digest,document,actor FROM durai_engineering.evidence WHERE run_id=%s ORDER BY sequence', (run_id,)).fetchall()
        if not run:
            raise KeyError('Engineering run unavailable')
        run['evidence'] = evidence
        run['state'] = evidence[-1]['stage'] if evidence else 'DRAFT'
        return run

    def record(self, run_id, stage, evidence, role='durai_runtime'):
        # Sample: Supply smoke evidence before red evidence. Reject reversed order.
        run = self.get(run_id)
        binding = self.store.resolve(run['document']['bindingId'], run['document']['bindingRevision'])
        if binding['digest'] != run['binding_digest'] or digest(run['document']['context']) != run['context_digest']:
            raise Conflict('Pinned design context differs')
        if evidence.get('contextDigest') != run['context_digest'] or evidence.get('bindingDigest') != run['binding_digest']:
            raise Conflict('Evidence is not bound to the pinned context and policy')
        if run['state'] in ('WAITING_FOR_REVIEW', 'FAILED'):
            raise Conflict('Engineering run is terminal')
        if stage != 'FAILED' and run['document'].get('workerContract'):
            from .worker_contract import verify_contract
            verify_contract(run['document']['workerContract'], self.database)
        index = len(run['evidence'])
        if stage != 'FAILED' and (index >= len(ORDER) or stage != ORDER[index]):
            raise Conflict('Engineering gate order differs')
        if stage == 'DESIGN_READY' and not evidence.get('briefDigest'):
            raise Conflict('Reviewed implementation brief is required')
        if stage == 'EXECUTION_SHELL_READY' and not all(evidence.get(k) for k in ('compiled', 'smokePassed', 'baselineDigest')):
            raise Conflict('Compile and smoke evidence is required')
        if stage == 'BASELINE_TEST_FAILURE_CONFIRMED':
            shell = run['evidence'][-1]['document']
            if (role != 'durai_evaluator' or evidence.get('category') != 'BUSINESS_ASSERTION' or
                evidence.get('expected') == evidence.get('observed') or
                not evidence.get('assertion') or not evidence.get('suiteDigest') or
                evidence.get('baselineDigest') != shell['baselineDigest']):
                raise Conflict('Independent meaningful business-red evidence is required')
        if stage == 'IMPLEMENTATION_READY' and evidence.get('suiteDigest') != run['evidence'][-1]['document']['suiteDigest']:
            raise Conflict('Implementation must retain the sealed test suite')
        if stage == 'WAITING_FOR_REVIEW':
            red = run['evidence'][2]['document']
            if (role != 'durai_evaluator' or evidence.get('verdict') != 'PASS' or
                evidence.get('suiteDigest') != red['suiteDigest'] or not evidence.get('candidateDigest') or
                not evidence.get('checks') or not all(item.get('passed') is True for item in evidence['checks'])):
                raise Conflict('Passing independent evaluation of the sealed suite is required')
        with transaction(role, self.database) as db:
            db.execute('SELECT id FROM durai_engineering.run WHERE id=%s FOR UPDATE', (run_id,))
            count = db.execute('SELECT count(*) AS n FROM durai_engineering.evidence WHERE run_id=%s', (run_id,)).fetchone()['n']
            if count != index:
                raise Conflict('Concurrent engineering transition; reload the run')
            db.execute('INSERT INTO durai_engineering.evidence(application_id,run_id,sequence,stage,digest,document) VALUES(durai_core.application_id(),%s,%s,%s,%s,%s)',
                       (run_id, index + 1, stage, digest(evidence), Jsonb(evidence)))
            audit(db, 'ENGINEERING_' + stage, {'runId': run_id, 'evidenceDigest': digest(evidence)})
        return self.get(run_id)

    def evidence(self, run_id, **values):
        # Sample: Bind an observed 2-share result to the exact context and policy.
        run = self.get(run_id)
        return dict(values, contextDigest=run['context_digest'], bindingDigest=run['binding_digest'])
