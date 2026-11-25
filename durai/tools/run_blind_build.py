"""Sample: Run run_blind_build.py with --workspace W-1 --components S-TRADE-BOOK --label smoke --fake-model. Read receipts/blind-build-smoke-S-TRADE-BOOK.json."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.build_driver import BlindBuild, ScriptedModel, live_model  # noqa: E402
from durai.build_tools import BuildTools  # noqa: E402


def main(argv=None):
    # Sample: Run two components with the fake model. Print the summary JSON. Exit 0 when every component FINISHED, else 1.
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--components', required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--provider', default='deepseek')
    parser.add_argument('--fake-model', action='store_true')
    parser.add_argument('--enhance', choices=('on', 'off'), default='off')
    parser.add_argument('--prompt-database')
    parser.add_argument('--receipts', default=str(ROOT / 'receipts'))
    parser.add_argument('--calls-per-component', type=int, default=80)
    parser.add_argument('--calls-total', type=int, default=400)
    parser.add_argument('--cost-cap', type=float, default=10.0)
    parser.add_argument('--keep-recent', type=int, default=10)
    parser.add_argument('--bare', action='store_true', help='Run in bare control mode (Arm 2)')
    parser.add_argument('--samples', help='Sample file to use instead of the default trading file')
    parser.add_argument('--sample-status', choices=('REVIEWED', 'PROPOSED'), default='REVIEWED')
    arguments = parser.parse_args(argv)
    samples = None
    if arguments.samples:
        from durai.build_samples import JsonSampleSource
        samples = JsonSampleSource(arguments.samples, statuses=(arguments.sample_status,))
    if arguments.fake_model:
        model, model_name, provider = ScriptedModel(), 'fake-model', 'fake'
    else:
        model, model_name = live_model(arguments.provider)
        provider = arguments.provider
    enhancer = None
    if arguments.enhance == 'on' and arguments.prompt_database:
        from durai.prompt_enhancer import Enhancer
        enhancer = Enhancer.for_database(arguments.prompt_database)
    tools = BuildTools(arguments.workspace, enhance=arguments.enhance == 'on', enhancer=enhancer, build_label=arguments.label, bare=arguments.bare, samples=samples)
    build = BlindBuild(tools, model, arguments.label, provider, model_name, arguments.receipts,
                       arguments.calls_per_component, arguments.calls_total, arguments.cost_cap,
                       keep_recent=arguments.keep_recent, bare=arguments.bare)
    summary = build.run([item.strip() for item in arguments.components.split(',') if item.strip()])
    print(json.dumps(summary, indent=2))
    return 0 if all(item['stopReason'] == 'FINISHED' for item in summary['components']) else 1


if __name__ == '__main__':
    sys.exit(main())
