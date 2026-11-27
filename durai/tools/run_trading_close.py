import argparse
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory


PROGRAM = Path(__file__).resolve().parents[1] / 'databricks/datalab/book_close.py'


def close_report(project, book, business_date, prior_date, known_at, directory):
    # Sample: Read the SYN-OIL close. Save it by input digest. Repeat the command and check one receipt.
    if not re.fullmatch(r'durai-app-[a-z0-9-]{3,40}', project):
        raise ValueError('Use the name of a Docker trading demo project')
    container = project + '-app'
    owner = subprocess.check_output(
        ['docker', 'inspect', '--format', '{{index .Config.Labels "com.durai.owner"}}', container],
        encoding='utf-8', timeout=15,
    ).strip()
    if owner != project:
        raise ValueError('Trade demo container owner differs from the configured project')
    result = subprocess.run(
        ['docker', 'exec', '-i', container, 'python', '-', '--book', book,
         '--business-date', business_date, '--prior-business-date', prior_date, '--known-at', known_at],
        input=PROGRAM.read_bytes(), capture_output=True, timeout=45,
    )
    if result.returncode:
        raise RuntimeError(f'Close report process exited with code {result.returncode}')
    report = json.loads(result.stdout)
    digest = report['input_sha256']
    if not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Invalid close receipt digest')
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (digest + '.json')
    body = json.dumps(report, sort_keys=True, indent=2).encode('utf-8') + b'\n'
    with TemporaryDirectory(prefix='close-', dir=directory) as scratch:
        staged = Path(scratch) / 'report.json'
        staged.write_bytes(body)
        if destination.exists():
            if destination.read_bytes() != body:
                raise ValueError('Stored report receipt differs')
        else:
            staged.replace(destination)
    return report, destination


def main():
    # Sample: Run one November 3 close. Read its status and receipt path.
    parser = argparse.ArgumentParser(description='Run one trading close job')
    parser.add_argument('--project', required=True)
    parser.add_argument('--book', required=True)
    parser.add_argument('--business-date', required=True)
    parser.add_argument('--prior-business-date', required=True)
    parser.add_argument('--known-at', required=True)
    parser.add_argument('--receipt-directory', required=True, type=Path)
    args = parser.parse_args()
    report, path = close_report(args.project, args.book, args.business_date, args.prior_business_date,
                                args.known_at, args.receipt_directory)
    print(json.dumps({'status': report['status'], 'net_pnl': report['net_pnl'],
                      'issues': report['issues'], 'receipt': str(path)}))
    return 2 if report['status'] == 'INCOMPLETE' else 0


if __name__ == '__main__':
    raise SystemExit(main())
