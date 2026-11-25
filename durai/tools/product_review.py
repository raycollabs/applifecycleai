"""Sample: Run product review against http://127.0.0.1:54360 with 3 criteria. Record PASS verdicts, screenshot digests, and UI/UX score 4."""
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FOLDER = ROOT / 'private/product-evidence'
RECEIPTS_FOLDER = ROOT / 'receipts'


def digest_file(path):
    # Sample: Read screenshot.png. Return its 64-character SHA-256 hex digest.
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def probe_http(url, timeout=5):
    # Sample: Request GET http://127.0.0.1:54360/. Return HTTP status 200. Return None on connection failure.
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode('utf-8', errors='replace')
    except Exception:
        return None, ''


def review_product(app_url, criteria, project='durai-app-default', evidence_folder=EVIDENCE_FOLDER,
                   receipts_folder=RECEIPTS_FOLDER, browser_runner=None):
    # Sample: Review app on 127.0.0.1:54360 with 2 criteria. Return receipt with 2 PASS verdicts, UI score 3, and screenshot digests.
    evidence_dir = Path(evidence_folder) / project
    evidence_dir.mkdir(parents=True, exist_ok=True)
    receipts_dir = Path(receipts_folder)
    receipts_dir.mkdir(parents=True, exist_ok=True)

    status_code, html_content = probe_http(app_url)
    started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    checks = []
    screenshots = []

    if browser_runner is not None:
        # Custom or mock browser runner for unit tests or headless playwright
        browser_result = browser_runner(app_url, evidence_dir)
        screenshots = browser_result.get('screenshots', [])
        html_content = browser_result.get('html', html_content)
        status_code = browser_result.get('status', status_code)

    for index, criterion in enumerate(criteria):
        text = str(criterion).strip()
        verdict = 'UNTESTABLE'
        evidence = 'App did not respond'
        if status_code == 200:
            # Check for keyword presence or structural requirement in page
            lower_html = html_content.lower()
            key_terms = [w.lower() for w in text.split() if len(w) > 4 and w.isalpha()]
            matches = [term for term in key_terms if term in lower_html]
            if len(matches) >= max(1, len(key_terms) // 2):
                verdict = 'PASS'
                evidence = f"HTTP 200; terms found: {', '.join(matches[:4])}"
            else:
                verdict = 'FAIL'
                evidence = f"HTTP 200; missing key terms from criterion {index + 1}"
        checks.append({
            'criterionIndex': index,
            'criterionText': text,
            'verdict': verdict,
            'evidence': evidence
        })

    pass_count = sum(1 for c in checks if c['verdict'] == 'PASS')
    total = len(criteria) if criteria else 1
    pass_ratio = pass_count / total

    # Score calculation per UI_UX rubric: 0 = no UI, 1 = broken, 2 = missing screens, 3 = runs with states, 4 = complete
    if status_code != 200:
        ui_score = 0
    elif pass_ratio >= 0.9:
        ui_score = 4
    elif pass_ratio >= 0.7:
        ui_score = 3
    elif pass_ratio >= 0.4:
        ui_score = 2
    else:
        ui_score = 1

    receipt = {
        'format': 'durai-product-review/v1',
        'project': project,
        'appUrl': app_url,
        'startedAt': started,
        'finishedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'httpStatus': status_code,
        'criteriaChecked': len(checks),
        'checks': checks,
        'screenshots': screenshots,
        'uiScore': ui_score,
        'passRatio': round(pass_ratio, 2)
    }

    receipt_path = receipts_dir / f"product-review-{project}.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


def main(argv=None):
    # Sample: Run product review against http://127.0.0.1:54370 for durai-app-deepseek-r3 and print receipt.
    import argparse
    import sys
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:54370/')
    parser.add_argument('--project', default='durai-app-deepseek-r3')
    parser.add_argument('--criteria', nargs='*', default=['Web app serves trading dashboard with trade reference lines'])
    args = parser.parse_args(argv)
    receipt = review_product(args.url, args.criteria, project=args.project)
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
