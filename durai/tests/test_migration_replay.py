"""Sample: Add an undeclared future migration that raises an error. Replay the sealed graph fixture without that migration."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from durai.qualification import ROOT,TEMPLATES,fixture_files,fixture_spec,load
from durai.task_contract import build_task
from durai.task_evaluator import SandboxEvaluator,deferred_migrations


class MigrationReplayTests(unittest.TestCase):
    def test_future_operating_migration_is_not_run(self):
        # Sample: Add core_9999 with a raised exception. Check the old graph fixture still passes all 12 goals.
        name = 'task-graph-tenancy-module-v9'
        folder = TEMPLATES/name
        files = dict(fixture_files(folder),**load(folder/'reference'))
        with tempfile.TemporaryDirectory(prefix='durai-migration-replay-') as scratch:
            scratch = Path(scratch)
            evaluator = SandboxEvaluator(scratch/'evaluation')
            task = build_task(fixture_spec(name,'durai_test',evaluator.image,evaluator.database_image))['task']
            trusted = scratch/'trusted'
            shutil.copytree(ROOT/'migrations/core',trusted/'migrations/core')
            (trusted/'durai').mkdir()
            shutil.copyfile(ROOT/'durai/db.py',trusted/'durai/db.py')
            (trusted/'migrations/core/core_9999.sql').write_text("DO $$ BEGIN RAISE EXCEPTION 'Undeclared future migration ran'; END $$;")
            with patch('durai.db.ROOT',trusted),patch('durai.task_evaluator.ROOT',trusted):
                self.assertIn('core_9999.sql',deferred_migrations(task,files))
                verdict = evaluator.evaluate(task,files,load(folder/'protected'),baseline=fixture_files(folder))
            self.assertEqual(verdict['verdict'],'PASS',verdict['results'])
