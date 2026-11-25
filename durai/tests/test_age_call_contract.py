"""Sample: Return parameter policy-1 through the complete Python and PL/pgSQL AGE call forms."""
import tempfile
import unittest
from pathlib import Path
from durai.db_sandbox import EphemeralDatabase
from durai.qualification import TEMPLATES, fixture_files, load
from durai.task_evaluator import SandboxEvaluator
from durai.workspace_manifest import materialize


class AgeCallContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply the reference graph schema to one isolated database for two call-form checks.
        cls.temporary = tempfile.TemporaryDirectory(prefix='durai-age-call-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.evaluator = SandboxEvaluator(Path(cls.temporary.name))
        folder = TEMPLATES / 'task-graph-tenancy-module-v8'
        files, suite = dict(fixture_files(folder), **load(folder / 'reference')), load(folder / 'protected')
        materialize(Path(cls.temporary.name) / 'source', files)
        cls.database = EphemeralDatabase(cls.temporary.name, owned=['core_0012.sql'])
        cls.database.__enter__()
        cls.addClassCleanup(cls.database.__exit__, None, None, None)
        setup = cls.database.apply(files, suite, cls.evaluator.database_image)
        if setup['exitCode']:
            raise AssertionError(setup['output'])

    def check_program(self, body):
        # Sample: Read the login graph from its registry. Run one bound-map call and check exit code 0.
        program = ("import json\nfrom psycopg import sql\nfrom durai.db import connect\n"
                   "with connect('durai_runtime','durai_test') as connection:\n"
                   " connection.execute('SET search_path=ag_catalog,pg_catalog')\n"
                   " graph=connection.execute('SELECT graph_name FROM durai_ontology.application_graph').fetchone()['graph_name']\n" + body)
        result = self.evaluator.run(Path(self.temporary.name) / 'source/workspace', ['python', '-c', program],
                                    self.evaluator.database_image, self.database.candidate_args())
        self.assertEqual(result['exitCode'], 0, result['output'])

    def test_python_call_shape(self):
        # Sample: Compose the registry graph as sql.Literal and bind key policy-1. Check the returned key.
        self.check_program(" statement=sql.SQL('SELECT * FROM ag_catalog.cypher({}, $q$ RETURN $key $q$, %s::ag_catalog.agtype) AS (key ag_catalog.agtype)').format(sql.Literal(graph))\n"
                           " row=connection.execute(statement,(json.dumps({'key':'policy-1'}),)).fetchone()\n"
                           " assert json.loads(row['key'])=='policy-1'\n")

    def test_plpgsql_call_shape(self):
        # Sample: Compose the registry graph with %L and bind the map with USING. Check policy-1.
        self.check_program(''' connection.execute("""DO $body$
 DECLARE graph_name text; parameters jsonb := '{"key":"policy-1"}'; value ag_catalog.agtype;
 BEGIN
 SELECT g.graph_name INTO STRICT graph_name FROM durai_ontology.application_graph g;
 EXECUTE format($sql$SELECT * FROM ag_catalog.cypher(%L, $q$ RETURN $key $q$, $1::ag_catalog.agtype) AS (key ag_catalog.agtype)$sql$, graph_name)
 INTO value USING parameters::text::ag_catalog.agtype;
 IF value::text <> 'policy-1' THEN RAISE EXCEPTION 'Unexpected key'; END IF;
 END $body$""", prepare=False)
''')
