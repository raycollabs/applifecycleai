"""Sample: Record receipt blind-build-deepseek-flash-r3-S-TRADE-BOOK as attempt blind-deepseek-flash-r3-trade-booking of trade-pilot. Return 1 attempt and 4 stages."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from psycopg.types.json import Jsonb  # noqa: E402
from durai.common import Conflict  # noqa: E402
from durai.db import transaction  # noqa: E402

FRONT_ENDS = ('NEXTJS_TS', 'OTHER', 'NONE')
MODEL_PROFILE = 'deepseek-flash-direct'
WRITER = 'durai_evaluator'


def read_receipt(path):
    # Sample: Read receipts/blind-build-deepseek-flash-r3-S-TRADE-BOOK.json. Return the sample id S-TRADE-BOOK and 4 stages.
    return json.loads(Path(path).read_text(encoding='utf-8'))


def record_receipt(receipt, attempt_id, app_key, app_version, component_key, frontend_stack, git_commit,
                   pack='build-guidance-r3', database='durai_local'):
    # Sample: Record the booking receipt as attempt A-1 with 4 DONE stages. Record it again and read a refusal naming A-1.
    if frontend_stack not in FRONT_ENDS:
        raise ValueError('frontend_stack must be one of ' + ', '.join(FRONT_ENDS))
    if receipt.get('stopReason') != 'FINISHED':
        raise ValueError('Only a FINISHED receipt is recorded: ' + str(receipt.get('stopReason')))
    with transaction(WRITER, database) as db:
        if db.execute('SELECT 1 FROM durai_bench.build_attempt WHERE attempt_id=%s', (attempt_id,)).fetchone():
            raise Conflict('Attempt is already recorded: ' + attempt_id)
        db.execute('INSERT INTO durai_bench.build_attempt(attempt_id,app_key,app_version,scope,component_key,model_profile_id,pack,git_commit,'
                   'sample_id,sample_revision,request_digest,cost,started_at,finished_at,frontend_stack)'
                   ' VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                   (attempt_id, app_key, app_version, 'COMPONENT', component_key, MODEL_PROFILE, pack, git_commit,
                    receipt['sampleId'], receipt['revision'], receipt['digest'], receipt['estimatedEnterpriseCost'],
                    receipt['startedAt'], receipt['finishedAt'], frontend_stack))
        for stage in receipt['stages']:
            artifacts = [{'path': path, 'sha256': digest} for path, digest in zip(stage['paths'], stage['sha256s'])]
            db.execute('INSERT INTO durai_bench.build_stage(attempt_id,stage,state,artifacts) VALUES(%s,%s,%s,%s)',
                       (attempt_id, stage['stage'], 'DONE', Jsonb(artifacts)))
    return {'attempt': attempt_id, 'stages': len(receipt['stages']), 'frontendStack': frontend_stack}


def main(argv=None):
    # Sample: Run the tool with the booking receipt, attempt blind-deepseek-flash-r3-trade-booking and component trade-booking. Print the attempt and stage count.
    parser = argparse.ArgumentParser(description='Record one FINISHED blind build receipt in durai_bench')
    parser.add_argument('receipt')
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--app-key', default='trade-pilot')
    parser.add_argument('--app-version', default='e6-e7-local-2026-11')
    parser.add_argument('--component', required=True)
    parser.add_argument('--frontend-stack', choices=FRONT_ENDS, default='NONE')
    parser.add_argument('--git-commit', required=True)
    parser.add_argument('--database', default='durai_local')
    args = parser.parse_args(argv)
    try:
        result = record_receipt(read_receipt(args.receipt), args.attempt_id, args.app_key, args.app_version, args.component,
                                args.frontend_stack, args.git_commit, database=args.database)
    except (ValueError, Conflict) as error:
        print('Refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
