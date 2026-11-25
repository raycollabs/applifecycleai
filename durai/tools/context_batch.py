"""Sample: Batch plan P-1 with a declared limit of 2 provider calls. Keep writes free of embedding calls."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from durai.context_embeddings import NvidiaEmbeddings
from durai.context_search import BatchedVectorSearch
from durai.context_store import ContextStore


def main():
    # Sample: Omit --accept-provider-calls. Refuse before sending any context to the provider.
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True, choices=['durai_test', 'durai_local'])
    parser.add_argument('--plan', required=True)
    parser.add_argument('--max-calls', required=True, type=int)
    parser.add_argument('--accept-provider-calls', action='store_true')
    args = parser.parse_args()
    if not args.accept_provider_calls:
        parser.error('An explicit provider-call approval is required')
    result = BatchedVectorSearch(NvidiaEmbeddings()).build(ContextStore(args.database, 'durai_evaluator'), args.plan, args.max_calls)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
