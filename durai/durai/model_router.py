"""Sample: Select route for ui on py-fastapi-next with min_finish_rate 0.90. Pick highest mean_reward, then lowest cost, or fall back to model_pricing."""
from typing import Any, Dict, List

from .model_pricing import LIST_RATES, estimate_costs

FALLBACK_MODELS: Dict[str, str] = {
    'reflection': 'Beam-1.0-501B-A23B',
    'deepseek': 'deepseek-flash',
    'gemini': 'gemini-2.5-flash',
    'deepinfra-deepseek': 'DeepSeek-V4.1-Flash',
    'deepinfra': 'DeepSeek-V4.1-Flash',
    'claude-haiku': 'claude-haiku',
    'openai': 'gpt-4o',
    'anthropic': 'claude-sonnet',
    'claude-sonnet': 'claude-sonnet',
    'claude-opus': 'claude-opus',
}
DEFAULT_INPUT_TOKENS = 100_000
DEFAULT_OUTPUT_TOKENS = 20_000


def fallback_routes(input_tokens: int = DEFAULT_INPUT_TOKENS, output_tokens: int = DEFAULT_OUTPUT_TOKENS) -> List[dict]:
    # Sample: Rank non-fake routes from model_pricing.LIST_RATES by estimated list cost ascending.
    candidates: List[dict] = []
    for provider, model_id in FALLBACK_MODELS.items():
        if provider == 'fake' or provider not in LIST_RATES:
            continue
        estimate = estimate_costs(provider, model_id, input_tokens, output_tokens)
        candidates.append({
            'provider': provider,
            'model_id': model_id,
            'pack_version': 'default',
            'estimated_list_cost_usd': estimate['list'],
            'rates': estimate['rates'],
        })
    candidates.sort(key=lambda c: (float(c['estimated_list_cost_usd']), c['provider'], c['model_id']))
    return candidates


def _route_cost(row: dict) -> float:
    # Sample: Return numeric avg_list_cost_usd or estimate default list cost from model_pricing.
    if row.get('avg_list_cost_usd') is not None:
        return float(row['avg_list_cost_usd'])
    estimate = estimate_costs(row.get('provider', ''), row.get('model_id', ''), DEFAULT_INPUT_TOKENS, DEFAULT_OUTPUT_TOKENS)
    return float(estimate['list'])


def select_route(
    store: Any,
    component_category: str,
    shell_profile: str = 'py-fastapi-next',
    min_finish_rate: float = 0.90,
) -> dict:
    # Sample: Query model_route_stats for ui and py-fastapi-next. Return best eligible route or lowest-cost model_pricing fallback.
    rows = list(store.get_route_stats(component_category, shell_profile=shell_profile)) if store is not None else []
    eligible = [
        dict(row) for row in rows
        if float(row.get('finish_rate', 0.0)) >= float(min_finish_rate)
        and int(row.get('episodes_count', 1)) > 0
    ]
    if eligible:
        eligible.sort(
            key=lambda r: (
                -round(float(r.get('mean_reward', 0.0)), 6),
                _route_cost(r),
                -float(r.get('finish_rate', 0.0)),
                str(r.get('model_id', '')),
                str(r.get('provider', '')),
            )
        )
        best = eligible[0]
        return {
            'component_category': component_category,
            'shell_profile': shell_profile,
            'model_id': best['model_id'],
            'provider': best['provider'],
            'pack_version': best.get('pack_version', '1.0.0'),
            'mean_reward': float(best.get('mean_reward', 0.0)),
            'finish_rate': float(best.get('finish_rate', 0.0)),
            'avg_list_cost_usd': _route_cost(best),
            'episodes_count': int(best.get('episodes_count', 1)),
            'source': 'model_route_stats',
        }
    fallback = fallback_routes()[0]
    return {
        'component_category': component_category,
        'shell_profile': shell_profile,
        'model_id': fallback['model_id'],
        'provider': fallback['provider'],
        'pack_version': fallback['pack_version'],
        'mean_reward': None,
        'finish_rate': None,
        'avg_list_cost_usd': fallback['estimated_list_cost_usd'],
        'episodes_count': 0,
        'source': 'model_pricing_fallback',
    }
