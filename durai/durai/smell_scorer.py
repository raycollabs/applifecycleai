"""Sample: Analyze workspace code for smells like broad exceptions or SQL injection and adjust review score."""
import ast
import re
from pathlib import Path

MAX_FUNCTION_LINES = 80
MAX_FILE_LINES = 500

SECRET_PATTERN = re.compile(r'(?i)(password|secret|api_key|token)\s*=\s*[\'"][A-Za-z0-9_.~-]{8,}[\'"]')
SQL_KEYWORDS = ('select', 'insert', 'update', 'delete', 'drop')


def is_raw_sql_format(line: str) -> bool:
    # Sample: Check if line has SQL keyword with string formatting or f-string interpolation.
    lower = line.lower()
    if not any(k in lower for k in SQL_KEYWORDS):
        return False
    if '%s' in lower or '.format(' in lower:
        return True
    if ('f"' in lower or "f'" in lower) and ('{' in lower and '}' in lower):
        return True
    return False


def find_python_smells(filename: str, source: str) -> list:
    # Sample: Scan source text with except Exception pass. Return one BROAD_EXCEPTION smell.
    smells = []
    lines = source.splitlines()
    if len(lines) > MAX_FILE_LINES:
        smells.append({
            'file': filename,
            'line': 1,
            'kind': 'EXCESSIVE_FILE_LENGTH',
            'message': f'File length {len(lines)} exceeds max limit {MAX_FILE_LINES}',
            'severity': 'LOW',
        })

    for idx, line in enumerate(lines, start=1):
        if SECRET_PATTERN.search(line):
            smells.append({
                'file': filename,
                'line': idx,
                'kind': 'HARDCODED_SECRET',
                'message': 'Potential hardcoded secret or token detected in source line',
                'severity': 'HIGH',
            })
        if is_raw_sql_format(line):
            smells.append({
                'file': filename,
                'line': idx,
                'kind': 'RAW_SQL_FORMAT',
                'message': 'Potential raw string interpolation in SQL query instead of parameterized arguments',
                'severity': 'HIGH',
            })

    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return smells

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_len = getattr(node, 'end_lineno', node.lineno) - node.lineno
            if func_len > MAX_FUNCTION_LINES:
                smells.append({
                    'file': filename,
                    'line': node.lineno,
                    'kind': 'EXCESSIVE_FUNCTION_LENGTH',
                    'message': f'Function {node.name} length {func_len} exceeds max limit {MAX_FUNCTION_LINES}',
                    'severity': 'LOW',
                })

        if isinstance(node, ast.ExceptHandler):
            # Check if exception type is broad (None or Exception) and body is just pass
            is_broad = node.type is None or (isinstance(node.type, ast.Name) and node.type.id in ('Exception', 'BaseException'))
            is_silent = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
            if is_broad and is_silent:
                smells.append({
                    'file': filename,
                    'line': node.lineno,
                    'kind': 'BROAD_EXCEPTION',
                    'message': 'Broad exception caught silently with pass statement',
                    'severity': 'MEDIUM',
                })

    return smells


def analyze_code_smells(files: dict) -> dict:
    # Sample: Analyze dict of paths and contents. Return all smells and total severity penalty.
    all_smells = []
    for path, content in files.items():
        if path.endswith('.py'):
            all_smells.extend(find_python_smells(path, content))

    high_count = sum(1 for s in all_smells if s['severity'] == 'HIGH')
    med_count = sum(1 for s in all_smells if s['severity'] == 'MEDIUM')
    low_count = sum(1 for s in all_smells if s['severity'] == 'LOW')

    penalty = min(2, (high_count * 2) + med_count + (1 if low_count >= 3 else 0))
    return {
        'smells': all_smells,
        'penalty': penalty,
        'clean': len(all_smells) == 0,
        'summary': {'high': high_count, 'medium': med_count, 'low': low_count},
    }


def score_with_smell_guard(base_score: int, executed_checks_pass: bool, smells_report: dict) -> int:
    # Sample: Base score 4 with executed checks passing and 1 medium smell. Return score 3. Without passing checks return 0.
    if not executed_checks_pass:
        return 0
    if base_score <= 0:
        return 0
    penalty = smells_report.get('penalty', 0)
    # Ensure score stays bounded between 1 and 4 when executed checks pass
    return max(1, min(4, base_score - penalty))
