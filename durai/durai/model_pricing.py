"""Sample: Calculate enterprise and list price costs for 1000000 input tokens and 100000 output tokens on deepseek-flash."""

ENTERPRISE_INPUT_RATE = 2.0 / 1_000_000
ENTERPRISE_OUTPUT_RATE = 10.0 / 1_000_000

# Per-million token list prices across verified model routes
LIST_RATES = {
    'deepseek': {'input': 0.14 / 1_000_000, 'output': 0.28 / 1_000_000},
    'deepinfra': {'input': 0.20 / 1_000_000, 'output': 0.60 / 1_000_000},
    'deepinfra-deepseek': {'input': 0.20 / 1_000_000, 'output': 0.60 / 1_000_000},
    'anthropic': {'input': 3.00 / 1_000_000, 'output': 15.00 / 1_000_000},
    'claude-sonnet': {'input': 3.00 / 1_000_000, 'output': 15.00 / 1_000_000},
    'claude-haiku': {'input': 0.80 / 1_000_000, 'output': 4.00 / 1_000_000},
    'claude-opus': {'input': 15.00 / 1_000_000, 'output': 75.00 / 1_000_000},
    'openai': {'input': 2.50 / 1_000_000, 'output': 10.00 / 1_000_000},
    'gemini': {'input': 0.15 / 1_000_000, 'output': 0.60 / 1_000_000},
    'fake': {'input': 0.00, 'output': 0.00},
}


def resolve_list_rate(provider, model=''):
    # Sample: Provider deepinfra-deepseek returns 0.20 input and 0.60 output per 1M tokens.
    key = str(provider).lower()
    model_lower = str(model).lower()
    if key in LIST_RATES:
        return LIST_RATES[key]
    for pattern, rate in LIST_RATES.items():
        if pattern in key or pattern in model_lower:
            return rate
    return LIST_RATES['deepinfra']


def estimate_costs(provider, model, input_tokens, output_tokens):
    # Sample: 1000000 input tokens and 100000 output tokens on deepseek direct cost 3.0 USD enterprise and 0.168 USD list.
    in_tok = int(input_tokens or 0)
    out_tok = int(output_tokens or 0)
    enterprise_cost = round(in_tok * ENTERPRISE_INPUT_RATE + out_tok * ENTERPRISE_OUTPUT_RATE, 6)
    rate = resolve_list_rate(provider, model)
    list_cost = round(in_tok * rate['input'] + out_tok * rate['output'], 6)
    return {
        'enterprise': enterprise_cost,
        'list': list_cost,
        'rates': {
            'enterpriseInputPerMillion': round(ENTERPRISE_INPUT_RATE * 1_000_000, 2),
            'enterpriseOutputPerMillion': round(ENTERPRISE_OUTPUT_RATE * 1_000_000, 2),
            'listInputPerMillion': round(rate['input'] * 1_000_000, 4),
            'listOutputPerMillion': round(rate['output'] * 1_000_000, 4),
        }
    }
