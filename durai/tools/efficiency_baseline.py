"""Sample: Read blind build traces from worker evidence. Print one metrics row for each attempt with stage progression and dual-basis costs."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from durai.efficiency_metrics import load_trace, trace_metrics

COLUMNS = ('modelCalls', 'stageCount', 'resubmissions', 'readCalls', 'distinctFilesRead',
           'editCalls', 'refusedWrites', 'inputCount', 'outputCount', 'costEnterprise', 'costList')


def rows(folder, prefix):
    # Sample: Read folder F with blind-T-1-S-TRADE-BOOK-D.json. Return attempt 1 with its metrics.
    for path in sorted(Path(folder).glob(prefix + '*.json'), key=lambda item: item.stat().st_mtime):
        trace = load_trace(path)
        yield dict(trace_metrics(trace), attemptId=trace.get('attemptId', path.stem), trace=path.name)


if __name__ == '__main__':
    folder = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else str(ROOT / 'private/worker-evidence')
    prefix = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else 'blind-'
    found = list(rows(folder, prefix))
    print(json.dumps(found, indent=2) if '--json' in sys.argv else
          '\n'.join(['| attempt | ' + ' | '.join(COLUMNS) + ' |', '|---' * (len(COLUMNS) + 1) + '|'] +
                    ['| ' + row['attemptId'][-35:] + ' | ' + ' | '.join(str(row.get(c, '')) for c in COLUMNS) + ' |' for row in found]))
