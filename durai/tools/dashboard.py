"""Sample: Run python tools/dashboard.py --port 54430. Check the printed URL http://127.0.0.1:54430 and no credentials."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from durai.dashboard_server import serve  # noqa: E402


def main():
    # Sample: Start the dashboard on durai_local port 54430. Print the URL. Stop with Ctrl+C.
    parser = argparse.ArgumentParser(description='Start the read-only Durai dashboard on 127.0.0.1.')
    parser.add_argument('--port', type=int, default=54430)
    parser.add_argument('--database', default='durai_local')
    arguments = parser.parse_args()
    try:
        serve(arguments.port, database=arguments.database)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
