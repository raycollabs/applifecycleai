"""Sample: Hold 4 level-0 nodes for turns 1 to 48. Choose one merge and build a head with no model call."""
import json

MERGE_FANOUT = 4
FIELDS = ('goal', 'decisions', 'corrections', 'openQuestions', 'nextAction', 'sourceTurns', 'refs')


def top_nodes(nodes):
    # Sample: Hold level-0 nodes 1 to 12 and 13 to 24 and a level-1 node 1 to 24. Return only the level-1 node.
    ordered = sorted(nodes, key=lambda node: (node['first'], -node['level']))
    tops = []
    for node in ordered:
        if tops and node['last'] <= tops[-1]['last']:
            continue
        tops.append(node)
    return tops


def compose_head(nodes):
    # Sample: Join nodes for turns 1 to 12 and 13 to 24. Return one body with both goals and the union of refs.
    bodies = [node['body'] for node in sorted(nodes, key=lambda node: node['first'])]
    if len(bodies) == 1:
        return dict(bodies[0])
    return {'goal': ' '.join(body['goal'] for body in bodies if body['goal']),
            'decisions': [item for body in bodies for item in body['decisions']],
            'corrections': [item for body in bodies for item in body['corrections']],
            'openQuestions': [item for body in bodies for item in body['openQuestions']],
            'nextAction': next((body['nextAction'] for body in reversed(bodies) if body['nextAction']), ''),
            'sourceTurns': sorted({turn for body in bodies for turn in body['sourceTurns']}),
            'refs': sorted({ref for body in bodies for ref in body['refs']})}


def head_tokens(nodes, count_tokens):
    # Sample: Count the estimated tokens of the joined head for 3 nodes.
    return count_tokens(json.dumps(compose_head(nodes), sort_keys=True)) if nodes else 0


def choose_merge(nodes, count_tokens, head_limit):
    # Sample: Hold 4 adjacent level-0 nodes. Return all 4. Hold 2 nodes over 800 tokens. Return both.
    tops = top_nodes(nodes)
    runs, run = [], []
    for node in tops:
        if run and (node['level'] != run[-1]['level'] or node['first'] != run[-1]['last'] + 1):
            runs.append(run)
            run = []
        run.append(node)
    runs.append(run)
    runs = [run for run in runs if run]
    for run in sorted(runs, key=lambda item: item[0]['level']):
        if len(run) >= MERGE_FANOUT:
            return run[:MERGE_FANOUT]
    if head_tokens(tops, count_tokens) > head_limit:
        for run in sorted(runs, key=lambda item: item[0]['level']):
            if len(run) >= 2:
                return run[:MERGE_FANOUT]
    return None
