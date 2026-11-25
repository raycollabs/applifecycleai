"""Sample: Decide for a task that writes journal.py and location_search.py. Check SUBAGENTS and one DECISION_RECORDED."""
import unittest
from durai.common import digest, uid
from durai.db import prepare
from durai.execution_events import redact, replay
from durai.packs import PackStore, generic_pack
from durai.policy_store import record_policies
from durai.strategy_select import DEFAULT_RULES, choose, features, rules_for
from durai.worker_contract import resolve_contract

DATABASE = 'durai_test'
V14_DIGEST = 'e1c5eeff8b414dfa16f10357431f04d876f14c9330a81a379d91ec5a6b52d332'
JOURNAL = '/workspace/journal.py'
SEARCH = '/workspace/location_search.py'
OUTBOX = '/workspace/outbox.py'
FILES = {JOURNAL: b'import json\n', SEARCH: b'import math\n', OUTBOX: b'def enqueue():\n    return 1\n'}


def task_of(*paths, **extra):
    # Sample: Build a task that writes journal.py and location_search.py.
    return dict({'writePaths': list(paths)}, **extra)


def choice_of(task, files=None, budget=None, step=None):
    # Sample: Decide for the task with 40 model calls left. Return the choice and the recorded events.
    seen = []
    result = choose(task, step or {'id': 'S-1'}, FILES if files is None else files, budget, lambda kind, payload: seen.append((kind, payload)))
    return result['choice'], seen


def bound(version):
    # Sample: Install generic pack G-15 in durai_test and bind it as B-1. Return a task pinned to B-1.
    prepare(DATABASE)
    store = PackStore(DATABASE)
    reference = store.install(generic_pack(version))
    record_policies(reference, DATABASE)
    identity = 'strategy-' + version + '-' + uid()[:8]
    store.bind(identity, [reference], 'SYNTHETIC_LOCAL')
    return {'writePaths': [JOURNAL, SEARCH], 'binding': {'bindingId': identity},
            'pins': {'contractDigest': resolve_contract(identity, database=DATABASE)['digest']}}


class StrategyTests(unittest.TestCase):
    def test_single_module_selects_single(self):
        # Sample: Task writes journal.py only. Check SINGLE.
        self.assertEqual(choice_of(task_of(JOURNAL))[0], 'SINGLE')

    def test_loose_modules_select_subagents(self):
        # Sample: Task writes journal.py and location_search.py with no import between them. Check SUBAGENTS.
        self.assertEqual(choice_of(task_of(JOURNAL, SEARCH))[0], 'SUBAGENTS')

    def test_coupled_modules_select_single(self):
        # Sample: Task writes journal.py and outbox.py where journal.py has "from .outbox import enqueue". Check COUPLED and SINGLE.
        files = dict(FILES, **{JOURNAL: b'from .outbox import enqueue\n'})
        choice, seen = choice_of(task_of(JOURNAL, OUTBOX), files)
        self.assertEqual(choice, 'SINGLE')
        self.assertEqual(seen[0][1]['features']['moduleShape'], 'COUPLED')

    def test_budget_low_selects_single(self):
        # Sample: Task writes 2 loose modules with 15 model calls left and median 8. Check budgetLow and SINGLE. Use 16 calls. Check SUBAGENTS.
        task = task_of(JOURNAL, SEARCH)
        choice, seen = choice_of(task, budget={'modelCalls': 15})
        self.assertEqual((choice, seen[0][1]['features']['budgetLow']), ('SINGLE', True))
        self.assertEqual(choice_of(task, budget={'modelCalls': 16})[0], 'SUBAGENTS')

    def test_role_split_selects_team(self):
        # Sample: Task writes journal.py and test_journal.py and asks for implementer, test writer, reviewer. Check TEAM.
        task = task_of(JOURNAL, '/workspace/test_journal.py', roles=['implementer', 'test-writer', 'reviewer'])
        self.assertEqual(choice_of(task)[0], 'TEAM')

    def test_decision_recorded(self):
        # Sample: Decide for task T-1 with 2 write paths. Check DECISION_RECORDED with writePathCount 2 and the rule digest.
        task = task_of(JOURNAL, SEARCH)
        choice, seen = choice_of(task)
        kind, payload = seen[0]
        self.assertEqual((len(seen), kind, payload['point'], payload['choice']), (1, 'DECISION_RECORDED', 'decision.execution-strategy', 'SUBAGENTS'))
        self.assertEqual((payload['features']['writePathCount'], payload['features']['riskClass']), (2, 'MONEY'))
        self.assertEqual(payload['ruleDigest'], digest(DEFAULT_RULES))
        self.assertEqual(payload['reasons'], ['loose-modules'])
        self.assertEqual(features(task, {}, FILES, None)['budgetLow'], False)

    def test_v14_binding_uses_local_rules(self):
        # Sample: Bind generic pack v14. Read rules_for. Check DEFAULT_RULES and median 8.
        found = rules_for(bound('14'), DATABASE, 'durai_runtime')
        self.assertEqual((found['rules'], found['singleRunMedianModelCalls']), (DEFAULT_RULES, 8))

    def test_v15_pack_record_pinned(self):
        # Sample: Build generic pack v15 and v14. Check v14 digest unchanged. Bind v15. Check rules_for returns the pinned record.
        self.assertEqual(digest(generic_pack('14')), V14_DIGEST)
        record = next(item for item in generic_pack('15')['records'] if item['id'] == 'decision.execution-strategy')
        self.assertEqual((record['type'], record['body']['audience'], record['body']['releaseAuthority']), ('PROCEDURE', ['control-plane'], False))
        self.assertNotIn('decision.execution-strategy', [item['id'] for item in generic_pack('14')['records']])
        found = rules_for(bound('15'), DATABASE, 'durai_runtime')
        self.assertEqual((found['rules'], found['singleRunMedianModelCalls']), (record['body']['rules'], 8))


class RedactTests(unittest.TestCase):
    def test_run_id_not_redacted(self):
        # Sample: Redact the run ID task-tx-funds-v14. Check it is unchanged.
        self.assertEqual(redact('run task-tx-funds-v14 done', set()), 'run task-tx-funds-v14 done')

    def test_secret_still_redacted(self):
        # Sample: Redact "key sk-abcdefgh12" and "Bearer abc.def". Check both become [REDACTED].
        self.assertEqual(redact('key sk-abcdefgh12 and Bearer abc.def', set()), 'key [REDACTED] and [REDACTED]')


if __name__ == '__main__':
    unittest.main()
