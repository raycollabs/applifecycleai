"""Sample: Permit changes to validate_change only. Delete the module docstring. Report one unrequested edit."""
import ast
from .common import Conflict


def outline(source):
    # Sample: Read a module with a docstring, import hashlib, and two functions. Return the docstring, imports, and each top-level segment.
    text = source.decode('utf-8-sig')
    tree = ast.parse(text)
    imports, symbols, other = set(), {}, []
    for node in tree.body:
        segment = ast.get_source_segment(text, node)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.update(ast.dump(alias) + ':' + str(getattr(node, 'module', None)) for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols[node.name] = segment
        elif not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node is tree.body[0]):
            other.append(segment)
    return {'docstring': ast.get_docstring(tree, clean=False), 'imports': imports, 'symbols': symbols, 'other': other}


def unrequested_edits(task, before, after):
    # Sample: Declare validate_change for change_manifest.py. Change file_digest and remove the docstring. Return two findings.
    findings = []
    for path, names in sorted((task.get('editScope') or {}).items()):
        if path not in after:
            findings.append(path + ': file removed')
            continue
        if path not in before:
            continue
        if not path.endswith('.py'):
            if before[path] != after[path]:
                findings.append(path + ': no scope check for this file type')
            continue
        old, new = outline(before[path]), outline(after[path])
        if old['docstring'] != new['docstring']:
            findings.append(path + ': module docstring changed')
        if old['imports'] - new['imports']:
            findings.append(path + ': import removed')
        if old['other'] != new['other']:
            findings.append(path + ': module-level statement changed')
        hint = ('; allowed: ' + ', '.join(names) + '; restore it exactly as in the sealed source '
                'and put the change in an allowed function')
        for name in sorted(set(old['symbols']) | set(new['symbols'])):
            if name in names:
                continue
            if name not in new['symbols']:
                findings.append(path + ': ' + name + ' removed' + hint)
            elif name not in old['symbols']:
                findings.append(path + ': ' + name + ' added without scope' + hint)
            elif old['symbols'][name] != new['symbols'][name]:
                findings.append(path + ': ' + name + ' changed without scope' + hint)
    return findings


def check_scope(task, before, after):
    # Sample: Find one unrequested edit in change_manifest.py. Raise a refusal that names it.
    findings = unrequested_edits(task, before, after)
    if findings:
        raise Conflict('Unrequested edit: ' + '; '.join(findings))
    return {'scope': 'PASS', 'files': sorted(task.get('editScope') or {})}
