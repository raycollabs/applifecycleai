import json
import html
from pathlib import Path
from .common import canonical, digest, Conflict
from .engineering import Engineering, validate_domain, check_walkthroughs
from .packs import PackStore
from .files import export_package, safe_path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = {'applicationName': 'Exchange positions', 'quantityUnit': 'shares',
                'signedLots': True, 'correctionDirection': 'REMOVE_LOTS'}


def render_application(specification):
    # Sample: Supply signed-lot domain semantics. Produce source, UI, tests, and docs.
    rules = validate_domain(specification)
    from .domain_profiles import profile_id
    if profile_id(specification) != 'trading-position':
        raise Conflict('The position renderer requires the trading-position profile')
    template = ROOT / 'templates/position-app'
    files = {path.name: path.read_bytes() for path in sorted(template.iterdir()) if path.is_file()}
    files['index.html'] = files['index.html'].decode('utf-8').replace('Exchange positions', html.escape(specification['applicationName'])).encode('utf-8')
    for name in ('program.py', 'server.py', 'test_business.py'):
        check_walkthroughs(files[name])
        compile(files[name], name, 'exec')
    files['domain-specification.json'] = canonical({'specification': specification, 'rules': rules})
    return files


def scaffold_source(candidate):
    # Sample: Build a runnable raw-lot capture shell before implementing share conversion.
    text = candidate.decode('utf-8')
    needle = '    return lots * lot_size'
    if text.count(needle) != 1:
        raise ValueError('Qualified scaffold template differs')
    return text.replace(needle, '    return lots  # Sample: Capture 2 lots. Keep 2 before conversion.', 1).encode('utf-8')


def write_bundle(folder, files):
    # Sample: Write program.py to a new run folder. Verify its sealed file digest.
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {'format': 'durai-application/v1', 'files': {name: digest(data) for name, data in sorted(files.items())}}
    for name, data in files.items():
        safe_path(name)
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (folder / 'manifest.json').write_bytes(canonical(manifest))
    verify_bundle(folder)
    return manifest


def verify_bundle(folder):
    # Sample: Change one source byte or add an extra file. Refuse the changed bundle.
    folder = Path(folder).resolve()
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 'durai-application/v1':
        raise Conflict('Unsupported application manifest')
    actual = {path.relative_to(folder).as_posix() for path in folder.rglob('*') if path.is_file()}
    if actual != set(manifest['files']) | {'manifest.json'}:
        raise Conflict('Application file path set differs')
    for name, checksum in manifest['files'].items():
        safe_path(name)
        path = folder / name
        if path.is_symlink() or not path.resolve().is_relative_to(folder) or digest(path.read_bytes()) != checksum:
            raise Conflict('Application file digest or containment differs')
    return manifest


def generate(binding_id, specification=None, database='durai_local', use_model=False, runner=None):
    # Sample: Generate a 200-share application. Prove business-red, then independent green.
    from .sandbox import DockerRunner
    from .protected.position_suite import cases, evaluate
    specification = specification or dict(DEFAULT_SPEC)
    files = render_application(specification)
    baseline = scaffold_source(files['program.py'])
    binding = PackStore(database).resolve(binding_id)
    context = {'baselineDigest': digest(baseline), 'specificationDigest': digest(specification),
               'adapter': 'synthetic-position-app/v1', 'data': 'authored synthetic trades only'}
    engine = Engineering(database)
    run_id = engine.create(binding_id, specification, context)
    runner = runner or DockerRunner(ROOT / 'private/docker-workspaces')
    try:
        brief = {'goal': 'Generate signed-lot booking, lot correction, and share positions',
                 'domainRules': validate_domain(specification), 'binding': binding['document'],
                 'domainSources': engine.get(run_id)['document']['domainSources'],
                 'context': context, 'moduleMap': {'Book trade': 'book', 'Correct trade': 'correct', 'Positions': 'positions'},
                 'designReview': 'Validated against the user-approved local adapter contract; no hidden reasoning transcript',
                 'failurePolicy': 'Reject invalid inputs and preserve release review', 'releaseAuthority': False}
        files['implementation-brief.json'] = canonical(brief)
        engine.record(run_id, 'DESIGN_READY', engine.evidence(run_id, briefDigest=digest(brief)))
        compile(baseline, 'scaffold.py', 'exec')
        smoke = runner.execute(baseline, digest(baseline), {'commands': []})
        if smoke != {'trades': [], 'netShares': 0}:
            raise Conflict('Runnable shell smoke differs')
        engine.record(run_id, 'EXECUTION_SHELL_READY', engine.evidence(run_id,
                      compiled=True, smokePassed=True, baselineDigest=digest(baseline)))
        suite = cases()
        observed = runner.execute(baseline, digest(baseline), suite[0]['input'])['netShares']
        if observed == suite[0]['expected']:
            raise Conflict('The intended baseline business assertion did not fail')
        engine.record(run_id, 'BASELINE_TEST_FAILURE_CONFIRMED', engine.evidence(run_id,
            category='BUSINESS_ASSERTION', baselineDigest=digest(baseline), suiteDigest=digest(suite),
            assertion='2 lots * 100 shares per lot == 200 shares', expected=200, observed=observed), 'durai_evaluator')
        engine.record(run_id, 'IMPLEMENTATION_READY', engine.evidence(run_id, suiteDigest=digest(suite)))
        from .worker_contract import verify_contract
        contract = engine.get(run_id)['document']['workerContract']
        verify_contract(contract, database)
        method = 'deterministic-domain-adapter'
        proposal = None
        if use_model:
            from .model_fallbacks import propose
            proposal = propose(baseline,
                    'Complete the share conversion in the runnable greenfield scaffold /workspace/program.py. '
                    'The approved rule is signed shares = lots * lot_size. Positive correction lots remove shares. '
                    'Preserve the existing SQLite/JSON interface, validation, and all source sample walkthrough comments. '
                    'Keep every function executable. Do not change tests or request release authority. '
                    'Implementation brief: ' + json.dumps(brief), contract=contract, database=database)
            files['program.py'] = proposal['source']
            method = 'deepagents-openrouter' if proposal['provider'] == 'openrouter' else 'deepagents-compatible-provider'
        check_walkthroughs(files['program.py'])
        compile(files['program.py'], 'candidate.py', 'exec')
        verdict = evaluate(files['program.py'], runner)
        if verdict['verdict'] != 'PASS':
            raise Conflict('Generated candidate failed independent evaluation')
        result = {'state': 'WAITING_FOR_REVIEW', 'runId': run_id, 'generationMethod': method,
                  'database': database, 'bindingDigest': binding['digest'], 'workerContractDigest': contract['digest'], 'baselineDigest': digest(baseline),
                  'candidateDigest': digest(files['program.py']), 'evaluation': verdict, 'approvalCreated': False,
                  'scope': 'Small synthetic position application; no broker or production deployment'}
        if use_model:
            result['model'] = proposal['model']
            result['modelProvider'] = proposal['provider']
            result['modelAttempts'] = proposal['modelAttempts']
            result['fallbackUsed'] = proposal['fallbackUsed']
        files['evaluation.json'] = canonical(result)
        output = ROOT / 'private/generated' / run_id
        result['manifest'] = write_bundle(output, files)
        result['outputDirectory'] = str(output)
        result['packageDigest'] = export_package(output.parent / (run_id + '.sqlite'), files,
                                                 {'runId': run_id, 'bindingDigest': binding['digest']})
        engine.record(run_id, 'WAITING_FOR_REVIEW', engine.evidence(run_id, **verdict), 'durai_evaluator')
        return result
    except Exception as error:
        current = engine.get(run_id)
        if current['state'] not in ('FAILED', 'WAITING_FOR_REVIEW'):
            engine.record(run_id, 'FAILED', engine.evidence(run_id, errorType=type(error).__name__))
        if database == 'durai_local':
            failure = {'state': 'FAILED', 'runId': run_id, 'stageAtFailure': current['state'],
                       'errorType': type(error).__name__, 'modelRequested': use_model,
                       'modelAttempts': getattr(error, 'attempts', []),
                       'fallbackUsed': False, 'approvalCreated': False}
            (ROOT / 'receipts/greenfield-failure.json').write_bytes(canonical(failure))
        raise RuntimeError('Greenfield run ' + run_id + ' failed (' + type(error).__name__ +
                           '). Its terminal evidence is retained; no fallback or approval was created.') from None
