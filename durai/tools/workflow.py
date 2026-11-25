"""Sample: Run python tools/workflow.py start --definition W-1.json. Check JSON with workflowId W-1 and status SUCCEEDED or a waiting gate."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.common import Conflict  # noqa: E402
from durai.workflow_definition import validate  # noqa: E402
from durai.workflow_registry import default_providers  # noqa: E402
from durai.execution_events import replay  # noqa: E402
from durai.workflow_runner import build_workflow, recover_workflow, resume_workflow, start_workflow, sweep_gates  # noqa: E402


def load(path):
    # Sample: Read W-1.json. Return the validated definition. Refuse a missing file or an unknown field.
    return validate(json.loads(Path(path).read_text(encoding='utf-8')))


def summary(result):
    # Sample: Read the graph state of W-1 after S-1 and an open gate. Return workflowId, status RUNNING, steps, and waitingAtGate True.
    return {'workflowId': result.get('workflow_id'), 'status': result.get('status'), 'steps': result.get('steps'),
            'waitingAtGate': '__interrupt__' in result}


def providers_for(arguments, model_factory, evaluator):
    # Sample: Read --accept-spend absent with no injected model. Refuse before any model call. Read the flag. Build the registry.
    if model_factory is None and not arguments.accept_spend:
        raise SystemExit('Live model calls need --accept-spend. Add it to confirm the run may use the configured coding model.')
    settings = {key: value for key, value in (('scratch', arguments.scratch), ('maxParallel', arguments.max_parallel),
                                              ('leaseSeconds', arguments.lease_seconds)) if value is not None}
    return default_providers(arguments.database, settings, model_factory=model_factory, evaluator=evaluator)


def recover_command(arguments, model_factory, evaluator, out):
    # Sample: Run recover W-1 with W-1.json in the folder. Run W-1 from its last checkpoint. Print status SUCCEEDED and exit 0. Run recover W-404 with no events. Print an error and exit 2.
    if not replay(arguments.workflow_id, database=arguments.database):
        print(json.dumps({'error': 'Unknown workflow: ' + arguments.workflow_id}), file=out)
        return 2
    path = Path(arguments.definitions or '.') / (arguments.workflow_id + '.json')
    if not path.is_file():
        print(json.dumps({'error': 'Definition file not found: ' + str(path)}), file=out)
        return 1
    definition = load(path)
    providers = providers_for(arguments, model_factory, evaluator)
    result = recover_workflow(build_workflow(definition, providers, arguments.database), definition['id'], arguments.database)
    print(json.dumps(dict(summary(result), workflowId=definition['id']), default=str), file=out)
    return 0


def main(argv=None, model_factory=None, evaluator=None, out=None):
    # Sample: Parse start --definition W-1.json --database durai_test. Run W-1 from step S-1. Print one JSON line. Parse sweep --definitions DIR. Resume every workflow whose gate is no longer waiting. Parse recover W-1 --definitions DIR. Run W-1 from its last checkpoint.
    parser = argparse.ArgumentParser(description='Start, resume, or sweep a Durai workflow with the default providers.')
    parser.add_argument('--database', default='durai_local')
    parser.add_argument('--accept-spend', action='store_true', help='Allow the configured paid coding model')
    parser.add_argument('--scratch', help='Folder for candidates and workspaces')
    parser.add_argument('--max-parallel', type=int, help='Subagents run at the same time')
    parser.add_argument('--lease-seconds', type=int, help='File lease length for TEAM members')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('start', 'resume'):
        command = commands.add_parser(name)
        command.add_argument('--definition', required=True, help='Workflow definition JSON file')
    commands.add_parser('sweep').add_argument('--definitions', required=True, help='Folder with one <workflowId>.json per workflow')
    recover = commands.add_parser('recover')
    recover.add_argument('workflow_id', help='Workflow ID whose process died')
    recover.add_argument('--definitions', help='Folder with <workflowId>.json')
    arguments = parser.parse_args(argv)
    out = out or sys.stdout
    try:
        if arguments.command == 'recover':
            return recover_command(arguments, model_factory, evaluator, out)
        providers = providers_for(arguments, model_factory, evaluator)
        if arguments.command == 'sweep':
            folder = Path(arguments.definitions)

            def builder_for(workflow_id):
                # Sample: Read W-1. Return its builder from W-1.json in the folder, or None when the file is absent.
                path = folder / (workflow_id + '.json')
                return build_workflow(load(path), providers, arguments.database) if path.is_file() else None

            result = {'resumed': sweep_gates(builder_for, arguments.database)}
        else:
            definition = load(arguments.definition)
            builder = build_workflow(definition, providers, arguments.database)
            run = start_workflow if arguments.command == 'start' else resume_workflow
            result = dict(summary(run(builder, definition['id'], arguments.database)), workflowId=definition['id'])
    except (Conflict, ValueError, OSError) as error:
        print(json.dumps({'error': type(error).__name__ + ': ' + str(error)}), file=out)
        return 1
    print(json.dumps(result, default=str), file=out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
