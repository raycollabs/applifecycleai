"""Sample: Apply a generated graph package to an isolated database. Check owner rights and restore exact views."""
import json
import subprocess
import tempfile
import test_graph_tenancy_fixture as subject

evidence = {}

class GraphCandidateReviewTests(subject.GraphTenancyFixtureTests):
    def test_operator_privileges_are_narrow(self):
        # Sample: Apply C-1 to an isolated database. Check its owner, function ACL, registry row security, and runtime write denial.
        with subject.EphemeralDatabase(self.temporary.name, owned=['core_0012.sql']) as database:
            setup = database.apply(subject.REFERENCE, subject.SUITE, self.evaluator.database_image)
            self.assertEqual(setup['exitCode'], 0, setup['output'])
            query = """SELECT json_build_object(
             'functionOwner',r.rolname,'securityDefiner',p.prosecdef,'functionSettings',p.proconfig,
             'ownerLogin',r.rolcanlogin,'ownerSuperuser',r.rolsuper,'ownerBypassRls',r.rolbypassrls,
             'ownerCreateDb',r.rolcreatedb,'ownerCreateRole',r.rolcreaterole,
             'runtimeOwnerMembership',pg_has_role('durai_runtime',r.oid,'MEMBER'),
             'foreignOwnerMembership',pg_has_role('durai_foreign',r.oid,'MEMBER'),
             'publicExecute',EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner)))
               WHERE grantee=0 AND privilege_type='EXECUTE'),
             'registryRls',c.relrowsecurity,'registryForceRls',c.relforcerowsecurity,
             'runtimeRegistryWrites',has_table_privilege('durai_runtime',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE'),
             'foreignRegistryWrites',has_table_privilege('durai_foreign',c.oid,'INSERT,UPDATE,DELETE,TRUNCATE'),
             'wrongPrivateObjectOwners',(SELECT count(*) FROM durai_ontology.application_graph a
               JOIN pg_namespace n ON n.nspname=a.graph_name
               JOIN pg_class object ON object.relnamespace=n.oid
               WHERE object.relkind IN ('r','S') AND (object.relowner<>r.oid OR n.nspowner<>r.oid)))
             FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
             JOIN pg_class c ON c.oid='durai_ontology.application_graph'::regclass
             WHERE p.oid='durai_ontology.project_application_binding(text)'::regprocedure;"""
            result = subprocess.run(['docker', 'exec', database.name, 'psql', '-U', 'supabase_admin',
                                     '-v', 'ON_ERROR_STOP=1', '-At', '-d', 'durai_test', '-c', query],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            metadata = json.loads(result.stdout)
            self.assertEqual(metadata['functionOwner'], 'durai_graph_owner')
            self.assertEqual(metadata['wrongPrivateObjectOwners'], 0)
            for name in ('securityDefiner', 'registryRls', 'registryForceRls'):
                self.assertTrue(metadata[name], name)
            for name in ('ownerLogin', 'ownerSuperuser', 'ownerBypassRls', 'ownerCreateDb', 'ownerCreateRole',
                         'runtimeOwnerMembership', 'foreignOwnerMembership', 'publicExecute',
                         'runtimeRegistryWrites', 'foreignRegistryWrites'):
                self.assertFalse(metadata[name], name)
            settings = [item.replace(' ', '') for item in metadata['functionSettings'] or []]
            self.assertTrue(any(item.startswith('search_path=pg_catalog,') and item.endswith(',pg_temp') for item in settings), settings)
            evidence['operatorMetadata'] = metadata


def check_candidate(files, suite, evaluator):
    # Sample: Check generated Python P-1 and SQL S-1. Return narrow operator rights and a successful restore.
    previous = subject.REFERENCE, subject.SUITE
    evidence.clear()
    try:
        subject.REFERENCE, subject.SUITE = files, suite
        with tempfile.TemporaryDirectory(prefix='durai-generated-review-') as scratch:
            for method in ('test_operator_privileges_are_narrow', 'test_restore_preserves_grants_and_digests'):
                check = GraphCandidateReviewTests(method)
                check.temporary = type('Scratch', (), {'name': scratch})()
                check.evaluator = evaluator
                getattr(check, method)()
    finally:
        subject.REFERENCE, subject.SUITE = previous
    return dict(evidence, restore='PASS_WITH_EXPLICIT_AGE_OID_REPAIR')
