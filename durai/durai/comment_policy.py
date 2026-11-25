"""Sample: Check a two-lot walkthrough. Reject a forbidden comment."""
import ast
import io
import re
import tokenize
from .common import Conflict

RATIONALE = re.compile(r'\b(because|rationale|easy|easier|hard|harder|why|reasoning|reason|purpose)\b', re.I)


def validate_comments(source):
    # Sample: Accept a 200-share walkthrough. Reject a comment about difficulty.
    text = source.decode('utf-8-sig') if isinstance(source, bytes) else source
    tree = ast.parse(text)
    previous = None
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.COMMENT:
            continue
        value = token.string.lstrip('#').strip()
        numbered = previous == (token.start[0] - 1, token.start[1]) and re.match(r'\d+\.\s+\S', value)
        if (not re.match(r'Sample:\s+\S', value) and not numbered) or RATIONALE.search(value):
            raise Conflict('Only ASD-STE100 sample walkthrough comments are permitted')
        previous = token.start
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            value = node.value.value.strip()
            if not re.match(r'Sample:\s+\S', value) or RATIONALE.search(value):
                raise Conflict('Only sample walkthrough docstrings are permitted')
    return {'policy': 'sample-only-asd-ste100/v2', 'languageReviewRequired': True}
