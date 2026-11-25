"""Sample: Run python tools/durai_build_mcp.py --workspace W-1. Send JSON-RPC lines on stdin. Read replies on stdout."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.build_mcp import serve  # noqa: E402
from durai.build_tools import BuildTools  # noqa: E402


def main():
    # Sample: Start with --workspace W-1. Serve one build workspace until stdin closes.
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--enhance', choices=('on', 'off'), default='off')
    parser.add_argument('--prompt-database')
    arguments = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    enhancer = None
    if arguments.enhance == 'on' and arguments.prompt_database:
        from durai.prompt_enhancer import Enhancer
        enhancer = Enhancer.for_database(arguments.prompt_database)
    serve(BuildTools(arguments.workspace, enhance=arguments.enhance == 'on', enhancer=enhancer))


if __name__ == '__main__':
    main()
