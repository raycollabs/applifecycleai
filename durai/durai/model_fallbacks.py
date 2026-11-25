import tempfile
from pathlib import Path
from .common import digest

ROOT = Path(__file__).resolve().parents[1]


class ModelAttemptsFailed(RuntimeError):
    def __init__(self, attempts, reason):
        # Sample: Exhaust five models. Retain safe attempt metadata without provider bodies.
        self.attempts = attempts
        super().__init__(reason + '; attempted ' + str(len(attempts)) + ' model routes')


def transient_provider_error(error):
    # Sample: Continue after 429 or a timeout. Stop after 401 or an invalid request.
    from openai import APIConnectionError, APITimeoutError
    if isinstance(error, (APIConnectionError, APITimeoutError, TimeoutError)):
        return True
    return getattr(error, 'status_code', None) in (404, 408, 429, 500, 502, 503, 504)


def propose(source, task, role='coding', routes=None, model_factory=None, worker=None, contract=None, database='durai_local'):
    # Sample: Fail P-1/M-1 after an edit. Give P-1/M-2 the original source in a new folder.
    if role != 'coding':
        raise ValueError('Code proposal execution supports coding only; other roles require separate adapters')
    from .coding_worker import configured_model, repair_workspace
    from .models import require_configuration
    from .model_routes import candidates
    routes = routes if routes is not None else candidates(role, require_configuration())
    model_factory = model_factory or configured_model
    worker = worker or repair_workspace
    if not routes or len(routes) > 25:
        raise ValueError('The model route budget is empty or exceeds 25 attempts')
    attempts = []
    (ROOT / 'private').mkdir(parents=True, exist_ok=True)
    for route in routes:
        attempt = {'provider': route['id'], 'model': route['model'], 'role': role,
                   'baselineDigest': digest(source), 'attempt': len(attempts) + 1}
        try:
            with tempfile.TemporaryDirectory(prefix='model-attempt-', dir=ROOT / 'private') as folder:
                program = Path(folder, 'workspace/program.py')
                program.parent.mkdir()
                program.write_bytes(source)
                model = model_factory(route['model'], profile=route)
                if contract is None:
                    result = worker(folder, model, task)
                else:
                    from .worker_contract import verify_contract
                    verify_contract(contract, database)
                    result = worker(folder, model, task, contract=contract)
            if not result.get('source') or digest(result['source']) != result['digest']:
                raise ValueError('Candidate bytes do not match the provider proposal')
            if contract is not None:
                verify_contract(contract, database)
            from .comment_policy import validate_comments
            validate_comments(result['source'])
            attempt.update(outcome='PROPOSED', candidateDigest=result['digest'])
            attempts.append(attempt)
            return dict(result, model=route['model'], provider=route['id'], modelAttempts=attempts,
                        fallbackUsed=len(attempts) > 1, role=role)
        except Exception as error:
            attempt.update(outcome='PROVIDER_FAILED' if transient_provider_error(error) else 'STOPPED',
                           errorType=type(error).__name__, statusCode=getattr(error, 'status_code', None))
            attempts.append(attempt)
            if not transient_provider_error(error):
                raise ModelAttemptsFailed(attempts, 'Non-transient provider or coding failure') from None
    raise ModelAttemptsFailed(attempts, 'Provider/model fallback chain exhausted') from None
