"""Durai local command surface. Sensitive settings stay under private/."""
import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import sys
import time
import unittest

from durai.common import digest
from durai.db import prepare, transaction
from durai.registry import Registry
from durai.files import NativeFixtureRunner
from durai.evaluation_consumer import LocalProtectedEvaluator
from durai.release import ReleaseController
from durai.coordination import Coordination
from durai.trading import seed, discover

ROOT = Path(__file__).resolve().parent


def save(name, data):
    path = ROOT / 'receipts' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + '\n', encoding='utf-8')


def components(database='durai_local'):
    registry = Registry(database)
    from durai.sandbox import DockerRunner
    runner = DockerRunner(ROOT / 'private/docker-workspaces')
    return registry, runner, LocalProtectedEvaluator(registry, runner), ReleaseController(registry, runner)


def demo(run_id=None, database='durai_local'):
    receipt_path = ROOT / 'receipts/local-demo.json'
    if not run_id and database == 'durai_local' and receipt_path.exists():
        run_id = json.loads(receipt_path.read_text(encoding='utf-8'))['runId']
    seed(database)
    from durai.ontology import project_trading_rule
    projection = project_trading_rule(database)
    registry, runner, evaluator, controller = components(database)
    controller.initialize_target('trading-local' if database == 'durai_local' else 'trading-test',
                                 'LOCAL_DEMO' if database == 'durai_local' else 'TEST')
    run_id, finding_id = discover(database, run_id)
    existing = Coordination(database).run(run_id)
    if existing['state'] == 'SUCCEEDED':
        return {'state': 'SUCCEEDED', 'runId': run_id, 'findingId': finding_id,
                'message': 'The verified run remains complete. Use rollback to restore its previous release.'}
    rejected = evaluator.evaluate('quantity-only-v2')
    accepted = evaluator.evaluate('repaired-v3')
    if rejected['verdict'] != 'FAIL' or accepted['verdict'] != 'PASS':
        raise ValueError('Fixture gate results differ')
    Coordination(database).checkpoint(run_id, {'findingId': finding_id, 'stage': 'EVALUATED',
                                              'candidateVersion': 'repaired-v3',
                                              'candidateDigest': accepted['digest']}, 'WAITING_FOR_REVIEW')
    value = {'state': 'WAITING_FOR_REVIEW', 'runId': run_id, 'findingId': finding_id,
             'expectedShares': 200, 'observedShares': 2,
             'rejected': rejected, 'candidate': accepted,
             'generationMethod': 'authored-synthetic-fixtures; no LLM repair is claimed',
             'executionProvider': 'docker-linux',
             'ontologyProjection': projection,
             'nextAction': 'Review the candidate and approve its exact digest through the local operator command.',
             'approvalCreated': False, 'database': database}
    if database == 'durai_local':
        save('local-demo.json', value)
    return value


def run_factory(run_id=None, use_model=False, binding_id=None):
    # Sample: Run the repair chain. Retain provider attempts without approving a target.
    if use_model:
        from durai.models import require_configuration
        require_configuration()  # Refuse missing configuration before creating durable work.
    from durai.factory import factory_graph, fixture_proposer
    from durai.harness import prepare_checkpoints
    from durai.common import uid
    from durai.coding_worker import model_proposer
    seed()
    prepare_checkpoints('durai_local')
    registry, runner, evaluator, controller = components()
    controller.initialize_target()
    path = ROOT / 'receipts/factory-demo.json'
    if not run_id and path.exists() and not use_model:
        prior = json.loads(path.read_text(encoding='utf-8'))
        if prior['state'] == 'WAITING_FOR_REVIEW' and not prior.get('useModel'):
            run_id = prior['runId']
    run_id = run_id or uid()
    config = {'configurable': {'thread_id': 'factory:' + run_id}}
    proposer = (lambda state: model_proposer(registry, state)) if use_model else fixture_proposer
    with factory_graph(registry, evaluator, controller, proposer) as graph:
        prior = graph.get_state(config)
        if prior.values and binding_id and prior.values.get('binding_id') != binding_id:
            raise ValueError('The resumed factory must retain its pinned binding')
        try:
            if not prior.values:
                graph.invoke({'run_id': run_id, 'target_id': 'trading-local', 'use_model': use_model, 'binding_id': binding_id}, config)
            elif prior.next and prior.next != ('review',):
                graph.invoke(None, config)
        except Exception as error:
            failed = graph.get_state(config).values
            if failed.get('finding_id'):
                Coordination().checkpoint(run_id, {'findingId': failed['finding_id'],
                    'stage': 'PROVIDER_OR_WORKER_FAILED', 'errorType': type(error).__name__}, 'FAILED')
            save('factory-failure.json', {'runId': run_id, 'state': 'FAILED',
                 'errorType': type(error).__name__, 'modelAttempts': getattr(error, 'attempts', []), 'approvalCreated': False})
            raise RuntimeError('Factory worker failed (' + type(error).__name__ +
                               '). Resume with --run-id ' + run_id + ' after correcting the provider configuration.') from None
        state = graph.get_state(config)
    values = state.values
    value = {'state': 'WAITING_FOR_REVIEW' if state.next == ('review',) else values['state'],
             'runId': run_id, 'findingId': values['finding_id'], 'candidate': values['verdict'],
             'attempts': values['attempt'], 'useModel': use_model,
             'executionMode': 'langgraph-postgres-factory', 'executionProvider': 'docker-linux',
             'generationMethod': 'deepagents-model' if use_model else 'authored-synthetic-fixtures',
             'approvalCreated': False}
    save('factory-demo.json', value)
    save('runs/factory-' + run_id + '.json', value)
    if value['state'] == 'WAITING_FOR_REVIEW':
        save('local-demo.json', value)
    return value


def resume_factory(run_id, approval_id):
    from durai.factory import factory_graph, fixture_proposer
    from langgraph.types import Command
    registry, runner, evaluator, controller = components()
    config = {'configurable': {'thread_id': 'factory:' + run_id}}
    with factory_graph(registry, evaluator, controller, fixture_proposer) as graph:
        state = graph.get_state(config)
        if not state.values or state.values['run_id'] != run_id:
            raise ValueError('The factory checkpoint is unavailable')
        if not state.next:
            return state.values.get('result', {})
        result = graph.invoke(Command(resume={'approvalId': approval_id}), config)
    save('local-activation.json', result['result'])
    return result['result']


def status(database='durai_local'):
    with transaction(database=database) as db:
        value = {'database': database,
                 'versions': db.execute('SELECT id,digest,manifest FROM durai_agents.version ORDER BY created_at').fetchall(),
                 'runs': db.execute('SELECT id,state,checkpoint,revision FROM durai_agents.run ORDER BY created_at DESC LIMIT 20').fetchall(),
                 'findings': db.execute('SELECT * FROM durai_improvement.finding ORDER BY id').fetchall(),
                 'targets': db.execute('SELECT * FROM durai_governance.target').fetchall(),
                 'evaluations': db.execute('SELECT * FROM durai_governance.evaluation').fetchall(),
                 'releases': db.execute('SELECT * FROM durai_agents.release').fetchall(),
                 'approvals': db.execute('SELECT id,version_id,digest,target,authority,reviewer FROM durai_governance.approval').fetchall(),
                 'trades': db.execute('SELECT * FROM app_trading.trade').fetchall()}
    return value


def main():
    # Sample: Run greenfield with an ontology binding. Save its reviewable proof package.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'status', 'demo', 'factory', 'test', 'serve', 'approve', 'rollback', 'juicefs', 'sandbox', 'repair', 'model-status', 'model-setup', 'model-fallbacks', 'model-provider-setup', 'model-provider-key', 'model-catalog', 'model-anthropic-setup', 'ontology-install', 'ontology-status', 'ontology-bind', 'ontology-export', 'ontology-import', 'greenfield', 'replica', 'five-phase'])
    parser.add_argument('--run-id')
    parser.add_argument('--port', type=int, default=54420)
    parser.add_argument('--spec')
    parser.add_argument('--binding')
    parser.add_argument('--model', action='store_true')
    parser.add_argument('--with-trading', action='store_true')
    parser.add_argument('--capture-live', action='store_true')
    parser.add_argument('--pack-file')
    parser.add_argument('--archive')
    parser.add_argument('--digest')
    parser.add_argument('--output')
    parser.add_argument('--fallback-model', action='append', default=[])
    parser.add_argument('--provider')
    parser.add_argument('--base-url')
    parser.add_argument('--model-id', action='append', default=[])
    parser.add_argument('--role', choices=['coding', 'evaluation', 'design', 'context'], default='coding')
    parser.add_argument('--key-env')
    parser.add_argument('--disabled', action='store_true')
    args = parser.parse_args()
    if args.action == 'model-anthropic-setup':
        from durai.model_routes import configure_anthropic
        value = configure_anthropic(args.model_id or None, args.key_env)
        save('model-routing.json', value)
    elif args.action in ('model-fallbacks', 'model-provider-setup', 'model-provider-key', 'model-catalog'):
        from durai.model_routes import configure_fallbacks, configure_provider, configure_provider_key, catalog_status
        if args.action == 'model-fallbacks':
            value = configure_fallbacks(args.fallback_model)
            save('model-routing.json', value)
        elif args.action == 'model-provider-key':
            if not args.provider:
                parser.error('model-provider-key requires --provider')
            value = configure_provider_key(args.provider)
            save('model-routing.json', value)
        elif args.action == 'model-provider-setup':
            if not args.provider or not args.base_url or (not args.model_id and not args.disabled):
                parser.error('model-provider-setup requires --provider, --base-url, and one to five --model-id values unless disabled')
            value = configure_provider(args.provider, args.base_url, args.model_id, args.role, args.key_env, not args.disabled)
            save('model-routing.json', value)
        else:
            value = catalog_status(args.provider)
            save('model-catalog.json' if not args.provider or args.provider == 'openrouter' else 'model-catalog-' + value['provider'] + '.json', value)
    elif args.action in ('ontology-install', 'ontology-status', 'ontology-bind', 'ontology-export', 'ontology-import', 'greenfield', 'replica', 'five-phase'):
        from durai.local_factory import install_ontology, ontology_status, five_phase, save_receipt
        from durai.generation import generate, DEFAULT_SPEC
        from durai.replica import evolve
        from durai.packs import PackStore
        pack_document = json.loads(Path(args.pack_file).read_text(encoding='utf-8')) if args.pack_file else None
        if args.action == 'ontology-install':
            value = (PackStore().install(pack_document, 'trading') if pack_document else
                     install_ontology(capture_live=args.capture_live, with_trading=args.with_trading))
        elif args.action == 'ontology-bind':
            if not pack_document or not args.binding:
                parser.error('ontology-bind requires --binding and --pack-file with packs and mode')
            value = PackStore().bind(args.binding, pack_document['packs'], pack_document.get('mode', 'REFERENCE_ONLY'))
        elif args.action == 'ontology-export':
            if not pack_document or not args.output:
                parser.error('ontology-export requires --pack-file with an exact reference and --output')
            value = {'archiveDigest': PackStore().export(pack_document, args.output), 'output': args.output}
        elif args.action == 'ontology-import':
            if not args.archive or not args.digest:
                parser.error('ontology-import requires --archive and --digest')
            value = PackStore().import_archive(args.archive, args.digest)
        elif args.action == 'ontology-status':
            value = ontology_status()
        elif args.action == 'five-phase':
            value = five_phase(use_model=args.model)
        elif args.action == 'greenfield':
            specification = json.loads(Path(args.spec).read_text(encoding='utf-8')) if args.spec else dict(DEFAULT_SPEC)
            value = generate(args.binding or 'exchange-synthetic', specification, use_model=args.model)
            save_receipt('greenfield.json', value)
        else:
            value = evolve(args.binding or 'exchange-synthetic')
            save_receipt('replica.json', value)
    elif args.action == 'model-status':
        from durai.models import readiness
        value = readiness()
    elif args.action == 'model-setup':
        from durai.models import setup
        value = setup()
    elif args.action == 'prepare':
        migrations = prepare()
        versions = {name: importlib.metadata.version(name) for name in
                    ['psycopg', 'langgraph', 'langgraph-checkpoint-postgres', 'deepagents', 'langchain-openai']}
        with transaction('postgres') as db:
            database_version = db.execute('SHOW server_version').fetchone()['server_version']
            age = db.execute("SELECT extversion FROM pg_extension WHERE extname='age'").fetchone()['extversion']
        value = {'state': 'READY', 'database': 'durai_local', 'port': 54410,
                 'postgresVersion': database_version, 'ageVersion': age,
                 'imageId': 'sha256:7c2e75b32428228f33541f6312cf126feef4943fbbd5c30b3af667d70f8d3523',
                 'dependencies': versions, 'requirementsDigest': digest((ROOT / 'requirements.lock').read_bytes()),
                 'migrations': migrations, 'createdAt': datetime.now(timezone.utc).isoformat()}
        save('environment.json', value)
    elif args.action == 'demo':
        value = demo(args.run_id)
    elif args.action == 'factory':
        value = run_factory(args.run_id)
    elif args.action == 'status':
        value = status()
    elif args.action == 'test':
        prepare('durai_test')
        suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_*.py')
        goals = []
        class RecordedResult(unittest.TextTestResult):
            def startTest(self, test):
                self.started = time.monotonic()
                super().startTest(test)
            def addSuccess(self, test):
                goals.append({'function': test.id(), 'state': 'PASS', 'seconds': round(time.monotonic()-self.started, 4)})
                super().addSuccess(test)
            def addError(self, test, err):
                goals.append({'function': test.id(), 'state': 'ERROR', 'errorType': err[0].__name__})
                super().addError(test, err)
            def addFailure(self, test, err):
                goals.append({'function': test.id(), 'state': 'FAIL', 'errorType': err[0].__name__})
                super().addFailure(test, err)
        result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
        value = {'testsRun': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                 'skipped': len(result.skipped), 'state': 'PASS' if result.wasSuccessful() else 'FAIL',
                 'database': 'durai_test', 'scope': 'Synthetic local tests; approvals use TEST_ONLY authority',
                 'goals': goals}
        save('tests.json', value)
        print(json.dumps(value, indent=2))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    elif args.action == 'approve':
        path = ROOT / 'receipts/local-demo.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        print('Candidate:', value['candidate']['digest'])
        print('Review the source, protected results, and target before continuing.')
        entered = input('Type the full reviewed digest to approve this local target: ').strip()
        reviewer = input('Reviewer name: ').strip()
        _, _, _, controller = components()
        approval = controller.approve(value['candidate']['versionId'], entered, 'trading-local', reviewer)
        value = (resume_factory(value['runId'], approval) if value.get('executionMode') == 'langgraph-postgres-factory'
                 else controller.activate(approval, value['findingId'], value['runId']))
        save('local-activation.json', value)
    elif args.action == 'rollback':
        value = components()[3].rollback('trading-local')
    elif args.action == 'serve':
        from durai.server import serve
        serve(args.port)
        return
    elif args.action == 'juicefs':
        from durai.juicefs import qualify
        value = qualify()
    elif args.action == 'sandbox':
        from durai.sandbox import prepare_image, qualify
        prepare_image()
        value = qualify()
    elif args.action == 'repair':
        value = run_factory(args.run_id, use_model=True, binding_id=args.binding)
    print(json.dumps(value, indent=2, default=str))


if __name__ == '__main__':
    main()
