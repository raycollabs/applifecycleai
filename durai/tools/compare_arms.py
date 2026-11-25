"""Sample: Compare Arm 1 (Durai Small Model) and Arm 2 (Bare Small Model) receipts and calculate the Durai lift."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ('S-TRADE-BOOK', 'S-TRADE-ACCESS', 'S-TRADE-PRICES', 'S-TRADE-PNL', 'S-TRADE-CASH')


def load_receipt(receipts_dir: Path, prefix: str, sample_id: str) -> dict:
    # Sample: Load receipt for S-TRADE-BOOK with prefix blind-build-arm2-bare. Return dict or None.
    matches = list(receipts_dir.glob(f"{prefix}*{sample_id}*.json"))
    if not matches:
        return None
    try:
        return json.loads(matches[0].read_text(encoding='utf-8'))
    except Exception:
        return None


def calculate_arm_metrics(receipts: list) -> dict:
    # Sample: Aggregate metrics across list of component receipts. Return calls, pass counts, costs and tool errors.
    total_calls = sum(r.get('calls', 0) for r in receipts)
    total_in_tokens = sum(r.get('inputTokens', 0) for r in receipts)
    total_out_tokens = sum(r.get('outputTokens', 0) for r in receipts)
    total_ent_cost = sum(r.get('estimatedEnterpriseCost', 0.0) for r in receipts)
    total_list_cost = sum(r.get('estimatedListCost', 0.0) for r in receipts)
    
    tests_passed = sum((r.get('lastRunTests') or {}).get('passed', 0) for r in receipts)
    tests_failed = sum((r.get('lastRunTests') or {}).get('failed', 0) for r in receipts)
    
    total_tool_errors = sum(sum((r.get('toolErrors') or {}).values()) for r in receipts)
    finished_count = sum(1 for r in receipts if r.get('stopReason') in ('FINISHED', 'STOP_COMPLETED'))

    return {
        'componentCount': len(receipts),
        'finishedCount': finished_count,
        'totalCalls': total_calls,
        'inputTokens': total_in_tokens,
        'outputTokens': total_out_tokens,
        'enterpriseCost': round(total_ent_cost, 4),
        'listCost': round(total_list_cost, 4),
        'testsPassed': tests_passed,
        'testsFailed': tests_failed,
        'toolErrors': total_tool_errors,
    }


def compare_arms(receipts_folder: Path = ROOT / 'receipts') -> dict:
    # Sample: Read Arm 1 and Arm 2 receipts from receipts folder. Return comparison dict with Durai lift metrics.
    folder = Path(receipts_folder)
    
    # Arm 1: Small Model with Durai (Round 3 / Diagnostic receipts)
    arm1_receipts = []
    for c in COMPONENTS:
        r = (load_receipt(folder, 'blind-build-deepseek-flash-r3-diag-', c) or
             load_receipt(folder, 'blind-build-deepseek-flash-r3-', c))
        if r:
            arm1_receipts.append(r)

    # Arm 2: Small Model Bare Control
    arm2_receipts = []
    for c in COMPONENTS:
        r = load_receipt(folder, 'blind-build-arm2-bare-deepseek-flash-', c)
        if r:
            arm2_receipts.append(r)

    m1 = calculate_arm_metrics(arm1_receipts)
    m2 = calculate_arm_metrics(arm2_receipts)

    # Calculate lift
    test_total_1 = m1['testsPassed'] + m1['testsFailed']
    rate_1 = (m1['testsPassed'] / test_total_1) if test_total_1 > 0 else 0.0
    
    test_total_2 = m2['testsPassed'] + m2['testsFailed']
    rate_2 = (m2['testsPassed'] / test_total_2) if test_total_2 > 0 else 0.0

    pass_rate_lift = round((rate_1 - rate_2) * 100, 2)
    error_reduction = 0.0
    if m2['toolErrors'] > 0:
        error_reduction = round(((m2['toolErrors'] - m1['toolErrors']) / m2['toolErrors']) * 100, 2)

    return {
        'arm1_durai': m1,
        'arm2_bare': m2,
        'lift': {
            'passRateLiftPercent': pass_rate_lift,
            'arm1PassRate': round(rate_1 * 100, 2),
            'arm2PassRate': round(rate_2 * 100, 2),
            'toolErrorReductionPercent': error_reduction,
            'arm1Finished': m1['finishedCount'],
            'arm2Finished': m2['finishedCount'],
        }
    }


def render_markdown_table(comp: dict) -> str:
    # Sample: Format comparison dict into GitHub markdown table with lift percentages.
    a1, a2, l = comp['arm1_durai'], comp['arm2_bare'], comp['lift']
    return (
        "| Metric | Arm 1 (DeepSeek Flash + Durai) | Arm 2 (DeepSeek Flash Bare) | Durai Lift Delta |\n"
        "|---|---|---|---|\n"
        f"| Finished Components | {a1['finishedCount']} / {len(COMPONENTS)} | {a2['finishedCount']} / {len(COMPONENTS)} | +{a1['finishedCount'] - a2['finishedCount']} |\n"
        f"| Tests Passed | {a1['testsPassed']} | {a2['testsPassed']} | +{a1['testsPassed'] - a2['testsPassed']} |\n"
        f"| Test Pass Rate | {l['arm1PassRate']}% | {l['arm2PassRate']}% | +{l['passRateLiftPercent']}% |\n"
        f"| Tool / Rework Errors | {a1['toolErrors']} | {a2['toolErrors']} | {l['toolErrorReductionPercent']}% reduction |\n"
        f"| Model Calls | {a1['totalCalls']} | {a2['totalCalls']} | {a1['totalCalls'] - a2['totalCalls']} calls |\n"
        f"| Enterprise Cost | ${a1['enterpriseCost']:.2f} | ${a2['enterpriseCost']:.2f} | - |\n"
        f"| List Cost | ${a1['listCost']:.3f} | ${a2['listCost']:.3f} | - |\n"
    )


def main(argv=None):
    # Sample: Run comparison across receipts and print markdown report to stdout.
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipts', default=str(ROOT / 'receipts'))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    comparison = compare_arms(Path(args.receipts))
    if args.json:
        print(json.dumps(comparison, indent=2))
    else:
        print(render_markdown_table(comparison))
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
