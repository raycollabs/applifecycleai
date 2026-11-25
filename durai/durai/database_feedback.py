"""Sample: Read a database error with hidden value 912345. Return its fixed parser diagnosis without that value."""
import re

DIAGNOSES = (
    'a name constant is expected',
    'a dollar-quoted string constant is expected',
    'third argument of cypher function must be a parameter',
)
ERROR_CODES = {'SyntaxError': '42601', 'InvalidParameterValue': '22023',
               'InsufficientPrivilege': '42501', 'UndefinedFunction': '42883',
               'UndefinedColumn': '42703', 'UndefinedTable': '42P01', 'UndefinedObject': '42704', 'InvalidSchemaName': '3F000',
               'IndeterminateDatatype': '42P18', 'UndefinedParameter': '42P02'}
PARAMETER_FACTS = (r'could not determine data type of parameter \$\d+', r'there is no parameter \$\d+')


def candidate_location(output):
    # Sample: Read a traceback with File "/workspace/durai/events.py", line 42 and File "/workspace/tests/test_events.py", line 9. Return "durai/events.py:42".
    found = re.findall(r'File "/workspace/(durai/[A-Za-z0-9_./-]+)", line (\d+)', output)
    return found[-1][0] + ':' + found[-1][1] if found else None


def safe_database_feedback(output, phase):
    # Sample: Read InvalidParameterValue with a quoted binding ID. Return error class, SQLSTATE 22023, and a fixed diagnosis.
    match = re.search(r'^psycopg\.errors\.([A-Za-z]+):', output, re.M)
    if not match:
        return 'Database ' + phase + ' failed. Inspect the retained operator receipt.'
    name = match.group(1)
    if name not in ERROR_CODES:
        return 'Database ' + phase + ' failed. Inspect the retained operator receipt.'
    diagnosis = next((item for item in DIAGNOSES if item in output), None)
    if diagnosis is None and name == 'UndefinedObject' and 'graphid_ops' in output:
        diagnosis = 'AGE provisioning requires ag_catalog in the trusted search_path'
    if diagnosis is None and name == 'UndefinedObject' and 'type "agtype" does not exist' in output:
        diagnosis = 'AGE types require ag_catalog in the search_path of each SQL or PL/pgSQL function that uses agtype'
    if diagnosis is None and name == 'UndefinedFunction' and 'ag_catalog.agtype' in output and 'operator does not exist' in output:
        diagnosis = 'AGE queries require ag_catalog in the trusted transaction-local search_path'
    if diagnosis is None and name == 'UndefinedColumn' and 'record ' in output and 'has no field' in output:
        diagnosis = 'A PL/pgSQL record field is absent; use separate loop variables and explicit column aliases'
    if diagnosis is None:
        diagnosis = next((found.group(0) for found in (re.search(item, output) for item in PARAMETER_FACTS) if found), None)
    where = candidate_location(output)
    text = 'Database ' + phase + ' failed: ' + name + ' SQLSTATE ' + ERROR_CODES[name] + '.' + (' Candidate line ' + where + '.' if where else '')
    return text + (' ' + diagnosis + '.' if diagnosis else ' Check the declared database API contract.')
