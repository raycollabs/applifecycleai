"""Sample: Summarize chk-reflection-v1 and res-reflection-v1 receipts against DeepSeek and Bare runs into a comparison report."""
import argparse
import json
from pathlib import Path
from typing import Dict, List


GROUPS = (
    ('Checkout — Bare (`deepseek-flash`)', 'blind-build-chk-bare-v1-S-*.json', 6),
    ('Checkout — DeepSeek (`relace` `DeepSeek-V4-Flash-0731`)', 'blind-build-chk-durai-v3-S-*.json', 6),
    ('Checkout — Reflection (`chk-reflection-v1`)', 'blind-build-chk-reflection-v1-S-*.json', 6),
    ('Reservations — Bare (`deepseek-flash`)', 'blind-build-res-bare-v1-S-*.json', 6),
    ('Reservations — DeepSeek (`deepinfra->relace`, `v3`)', 'blind-build-res-durai-v3-S-*.json', 6),
    ('Reservations — DeepSeek Screens Fix (`res-durai-v3-s6`)', 'blind-build-res-durai-v3-s6-S-*.json', 1),
    ('Reservations — Reflection (`res-reflection-v1`)', 'blind-build-res-reflection-v1-S-*.json', 6),
)


def _read_group(receipts_dir: Path, pattern: str) -> List[dict]:
    # Sample: Load all JSON receipt dictionaries matching pattern from receipts_dir sorted by name.
    return [json.loads(path.read_text(encoding='utf-8')) for path in sorted(receipts_dir.glob(pattern))]


def build_report(receipts_dir: Path) -> str:
    # Sample: Aggregate calls, costs, finishes, and test counts across Checkout and Reservation arms.
    lines = [
        '# Reflection vs. DeepSeek vs. Bare Control Comparison',
        '',
        'Generated automatically from `receipts/blind-build-*.json`.',
        '',
        '| Arm / Run | Components Attempted | FINISHED | Calls | Enterprise Cost | List Cost | Last-Run Tests (Passed/Failed) | Providers Used |',
        '|---|---|---|---|---|---|---|---|',
    ]
    details: List[str] = []
    for title, pattern, target in GROUPS:
        items = _read_group(receipts_dir, pattern)
        if not items:
            lines.append(f'| {title} | 0/{target} | 0 | 0 | $0.00 | $0.000 | — | pending |')
            continue
        finished = sum(1 for item in items if item.get('stopReason') == 'FINISHED')
        calls = sum(int(item.get('calls') or 0) for item in items)
        ent = sum(float(item.get('estimatedEnterpriseCost') or 0.0) for item in items)
        lst = sum(float(item.get('estimatedListCost') or 0.0) for item in items)
        last_tests = items[-1].get('lastRunTests') or {}
        tests_str = f"{last_tests.get('passed', 0)}/{last_tests.get('failed', 0)}" if last_tests else '—'
        provs = sorted({
            item.get('primaryProvider', '') + ('->' + item.get('provider', '') if item.get('fallbackUsed') else '')
            for item in items
        })
        lines.append(
            f"| {title} | {len(items)}/{target} | **{finished}/{len(items)}** | {calls} | "
            f"${ent:.2f} | ${lst:.3f} | {tests_str} | {', '.join(provs)} |"
        )
        details.extend(['', f'## {title}', '', '| Component | Stop Reason | Calls | Tests (P/F) | Stages | Route | Enterprise | List |', '|---|---|---|---|---|---|---|---|'])
        for item in items:
            t = item.get('lastRunTests') or {}
            t_str = f"{t.get('passed', 0)}/{t.get('failed', 0)}" if t else '—'
            stages = ', '.join(s.get('stage', '') for s in item.get('stages') or []) or '[]'
            route = item.get('primaryProvider', '') + ('->' + item.get('provider', '') if item.get('fallbackUsed') else '')
            details.append(
                f"| `{item.get('sampleId')}` | `{item.get('stopReason')}` | {item.get('calls', 0)} | {t_str} | "
                f"{stages} | `{route}` | ${float(item.get('estimatedEnterpriseCost') or 0.0):.2f} | "
                f"${float(item.get('estimatedListCost') or 0.0):.3f} |"
            )
    return '\n'.join(lines + details) + '\n'


def main(argv=None) -> int:
    # Sample: Write comparison markdown to --output from --receipts.
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipts', default='receipts')
    parser.add_argument('--output', default='docs/benchmark_reflection_vs_deepseek_comparison.md')
    args = parser.parse_args(argv)
    report = build_report(Path(args.receipts))
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding='utf-8', newline='\n')
    print(f'WROTE {out_path} ({len(report)} bytes)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
