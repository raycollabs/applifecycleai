"""Sample: Keep a legacy comment. Add a comment about the fix. Report the added comment only."""
import ast
from collections import Counter
import io
import re
import tokenize
from .comment_policy import RATIONALE

SAMPLE = re.compile(r'#\s*(Sample:\s+\S|\d+\.\s+\S)')


def comments(text):
    # Sample: Read a file with "# Sample: Book 2 lots." twice. Return that comment with count 2.
    return Counter(token.string.strip() for token in tokenize.generate_tokens(io.StringIO(text).readline)
                   if token.type == tokenize.COMMENT)


def docstrings(tree):
    # Sample: Read a module docstring and one function docstring. Return both strings.
    return Counter(node.value.value.strip() for node in ast.walk(tree)
                   if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))


def functions(text, tree):
    # Sample: Read validate_change at line 4 with a "# Sample:" line. Return its segment, True, and line 4.
    samples = [token.start for token in tokenize.generate_tokens(io.StringIO(text).readline)
               if token.type == tokenize.COMMENT and token.string.startswith('# Sample:')]
    found = {}

    def visit(node, prefix):
        # Sample: Visit class Book and method add. Record Book.add.
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                if not isinstance(child, ast.ClassDef):
                    first = child.body[0].lineno
                    documented = (ast.get_docstring(child) or '').startswith('Sample:') or any(
                        child.lineno < line <= first and column > child.col_offset for line, column in samples)
                    found[name] = (ast.get_source_segment(text, child), documented, child.lineno)
                visit(child, name + '.')

    visit(tree, '')
    return found


def missing_sample(name, line):
    # Sample: Read create_operation.handler at line 42. Return the finding with line 42, the body placement, and "outer sample of create_operation does not cover it".
    text = ('added or changed function has no sample walkthrough: ' + name + ' (def at line ' + str(line) +
            '); put a "# Sample:" comment as the first line inside its body, one level deeper than its def')
    if '.' in name:
        text += '; the sample of ' + name.rsplit('.', 1)[0] + ' does not cover this inner function'
    return text


def comment_findings(before, after):
    # Sample: Add the comment "# Fixed the strict check." to validate_change. Return one added-comment finding.
    old_text = before.decode('utf-8-sig') if before is not None else ''
    new_text = after.decode('utf-8-sig')
    old_tree, new_tree = ast.parse(old_text), ast.parse(new_text)
    findings = []
    for comment in (comments(new_text) - comments(old_text)):
        if not SAMPLE.match(comment) or RATIONALE.search(comment):
            findings.append('added comment is not a sample walkthrough: ' + comment[:80])
    for value in (docstrings(new_tree) - docstrings(old_tree)):
        if not value.startswith('Sample:') or RATIONALE.search(value):
            findings.append('added docstring is not a sample walkthrough: ' + value[:80])
    old_functions = functions(old_text, old_tree)
    for name, (segment, documented, line) in sorted(functions(new_text, new_tree).items()):
        if (name not in old_functions or old_functions[name][0] != segment) and not documented:
            findings.append(missing_sample(name, line))
    return findings


def sql_comment_findings(before, after):
    # Sample: Add "-- Sample: Create table policy." and "-- Store rules here." to core_0010.sql. Return one finding.
    def lines(source):
        # Sample: Read two SQL lines with one comment. Return that comment text.
        found = Counter()
        for line in (source or b'').decode('utf-8-sig').splitlines():
            if '--' in line and line.count("'", 0, line.index('--')) % 2 == 0:
                found[line[line.index('--'):].strip()] += 1
        return found
    findings = []
    for comment in (lines(after) - lines(before)):
        if not re.match(r'--\s*Sample:\s+\S', comment) or RATIONALE.search(comment):
            findings.append('added SQL comment is not a sample walkthrough: ' + comment[:80])
    return findings
