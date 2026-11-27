"""Sample: Run run_blind_build.py with --workspace W-1 --components S-TRADE-BOOK --label smoke --fake-model. Read receipts/blind-build-smoke-S-TRADE-BOOK.json."""
import argparse
import json
import os
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
    parser.add_argument('--provider', default='reflection')
    parser.add_argument('--fallback-provider', help='Optional failover provider when primary rate-limits or fails')
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
    parser.add_argument('--rl-dsn', help='Optional PostgreSQL DSN to record execution episodes and tickets')
    parser.add_argument('--rl-local', action='store_true', help='Record execution episodes and tickets into durai_local via durai.db.connect')
    parser.add_argument('--rl-source-mode', choices=('LOCAL_DURAI', 'PUBLIC_SHELL'), default='LOCAL_DURAI')
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
    fb_model = fb_name = fb_provider = None
    if arguments.fallback_provider and not arguments.fake_model:
        # Sample: Build fallback with thinking disabled so mid-stream failover does not require prior reasoning_content fields.
        fb_model, fb_name = live_model(arguments.fallback_provider, thinking=False)
        fb_provider = arguments.fallback_provider
    enhancer = None
    if arguments.enhance == 'on' and arguments.prompt_database:
        from durai.prompt_enhancer import Enhancer
        enhancer = Enhancer.for_database(arguments.prompt_database)
    tools = BuildTools(arguments.workspace, enhance=arguments.enhance == 'on', enhancer=enhancer, build_label=arguments.label, bare=arguments.bare, samples=samples)
    build = BlindBuild(tools, model, arguments.label, provider, model_name, arguments.receipts,
                       arguments.calls_per_component, arguments.calls_total, arguments.cost_cap,
                       keep_recent=arguments.keep_recent, bare=arguments.bare,
                       fallback_model=fb_model, fallback_provider=fb_provider, fallback_model_name=fb_name)
    summary = build.run([item.strip() for item in arguments.components.split(',') if item.strip()])
    rl_dsn = arguments.rl_dsn or os.environ.get('DURAI_RL_DSN')
    if rl_dsn or arguments.rl_local:
        import psycopg
        from psycopg.rows import dict_row
        from durai.adapters.rl_store.postgres import PostgresRLStore
        from durai.ticket_collector import collect_run_feedback
        if arguments.rl_local:
            from durai.db import connect as db_connect
            rl_store = PostgresRLStore(lambda: db_connect('durai_runtime', 'durai_local'))
        else:
            rl_store = PostgresRLStore(lambda: psycopg.connect(rl_dsn, row_factory=dict_row, autocommit=False))
        rl_store.ensure_schema()
        for comp in summary.get('components', []):
            receipt_file = Path(arguments.receipts) / f"blind-build-{arguments.label}-{comp['sampleId']}.json"
            if receipt_file.is_file():
                comp_receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
                collect_run_feedback(rl_store, comp_receipt, source_mode=arguments.rl_source_mode)
    print(json.dumps(summary, indent=2))
    return 0 if all(item['stopReason'] == 'FINISHED' for item in summary['components']) else 1


if __name__ == '__main__':
    sys.exit(main())
