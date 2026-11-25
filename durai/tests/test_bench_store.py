"""Sample: Import synthetic funds receipts into durai_test. Read runs, cost, scorecard, and experiment pairs."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
import psycopg
from durai.bench_store import add_experiment, add_price, add_profile, backfill_provider_usage, compare, import_receipt, prepare_bench, scorecard
from durai.common import Conflict, uid
from durai.db import connect, prepare, transaction

DATABASE = 'durai_test'
START = {'started': datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc), 'finished': datetime(2026, 11, 20, 10, 2, tzinfo=timezone.utc), 'workers': {}}


def make_receipt(folder, fixture, label, provider, model, counts, verdict='PASS', ledger=None, tokens=None, advisor=None, extra=None):
    # Sample: Write dev-loop-task-tx-funds-v14-deepseek-r1.json with attempts of 7 and 21 calls. Return the path and the receipt.
    run_id = fixture + '-' + uid()
    attempts = []
    for number, calls in enumerate(counts, 1):
        last = number == len(counts)
        metrics = {'inputCount': (tokens or {}).get('input', 1000) if last else 0, 'outputCount': (tokens or {}).get('output', 100) if last else 0,
                   'reasoningCount': 0, 'modelCalls': calls, 'toolCalls': calls * 2, 'readCalls': 3, 'queryCalls': 1, 'editCalls': 1,
                   'refusedWrites': 0, 'callsBeforeFirstWrite': 5, 'packCharacters': 4000}
        if tokens and 'cached' in tokens and last:
            metrics['cachedInputCount'] = tokens['cached']
        attempts.append({'attemptId': run_id + ':' + str(number), 'verdict': 'PASS' if last and verdict == 'PASS' else 'FAIL',
                         'usage': {'attempts': len(counts), 'modelCalls': ledger or sum(counts), 'toolCalls': sum(counts) * 2},
                         'efficiency': {'metrics': metrics}})
    receipt = {'runId': run_id, 'outcome': 'PASSED' if verdict == 'PASS' else 'FAILED', 'attempts': attempts, 'model': model, 'provider': provider,
               'fixture': fixture, 'stop': None, 'taskDigest': 'a' * 64, 'clientRetries': 0,
               'verdict': {'verdict': verdict, 'results': [{'failedGoals': [] if verdict == 'PASS' else ['refund-limit'],
                                                            'fault': None if verdict == 'PASS' else {'origin': 'MODEL', 'reasonCode': 'GOAL_FAILED'}}]}}
    if advisor:
        receipt['advisor'] = advisor
    receipt.update(extra or {})
    path = Path(folder) / ('dev-loop-' + fixture + '-' + label + '.json')
    path.write_text(json.dumps(receipt), encoding='utf-8')
    return path, receipt


class BenchBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Prepare durai_test and bench_0001. Open a temp folder for receipts.
        prepare(DATABASE)
        prepare_bench(DATABASE)
        cls.folder = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        # Sample: Remove the temp folder with 4 synthetic receipts.
        cls.folder.cleanup()

    def profile(self, provider='deepseek', price=(0.30, 0.006, 1.20), start='2026-11-01'):
        # Sample: Add profile p-1 for provider P-1 and a price of 0.30 input from 2026-11-01. Return the profile id and model.
        token = uid()
        model = 'model-' + token[:8]
        profile_id = 'p-' + token[:12]
        add_profile(profile_id, provider + '-' + token[:8], model, 'direct', database=DATABASE)
        add_price({'provider': provider + '-' + token[:8], 'model': model, 'effective_from': start, 'verification': 'AGGREGATOR',
                   'input_per_million': price[0], 'cached_input_per_million': price[1], 'output_per_million': price[2],
                   'sources': ['https://example.test/price'], 'checked_at': '2026-11-20'}, DATABASE)
        return profile_id, provider + '-' + token[:8], model

    def load(self, profile_id, fixture='task-tx-funds-v14', label=None, **kwargs):
        # Sample: Write a funds receipt for profile P-1 and import it. Return the import result, the receipt, and the path.
        label = label or 'deepseek-' + uid()[:8]
        path, receipt = make_receipt(self.folder.name, fixture, label, 'deepseek', 'deepseek-flash', **kwargs)
        result = import_receipt(path, profile_id, DATABASE, None, timings=START)
        return result, receipt, path

    def rows(self, query, params):
        # Sample: Read the run rows of run R-1 with the evaluator login. Return a list of dictionaries.
        with transaction('durai_evaluator', DATABASE) as db:
            return db.execute(query, params).fetchall()


class BenchStoreTests(BenchBase):
    def test_import_receipt_creates_run_and_attempts(self):
        # Sample: Import a funds receipt with 2 attempts (7 and 21 calls). Check 1 run with outcome PASSED and 2 attempt rows.
        profile_id, _, _ = self.profile()
        result, receipt, _ = self.load(profile_id, counts=[7, 21])
        self.assertEqual(result['state'], 'IMPORTED')
        run = self.rows('SELECT outcome,pass_attempt,model_calls,wall_seconds,task_digest FROM durai_bench.run WHERE run_id=%s', (receipt['runId'],))
        attempts = self.rows('SELECT number,model_calls FROM durai_bench.attempt WHERE run_id=%s ORDER BY number', (receipt['runId'],))
        self.assertEqual([(row['outcome'], row['pass_attempt'], row['model_calls']) for row in run], [('PASSED', 2, 28)])
        self.assertEqual(float(run[0]['wall_seconds']), 120.0)
        self.assertEqual([(row['number'], row['model_calls']) for row in attempts], [(1, 7), (2, 21)])

    def test_reimport_same_digest_is_noop(self):
        # Sample: Import the funds receipt twice. Check 1 run row.
        profile_id, _, _ = self.profile()
        _, receipt, path = self.load(profile_id, counts=[7, 21])
        again = import_receipt(path, profile_id, DATABASE, None, timings=START)
        self.assertEqual(again['state'], 'UNCHANGED')
        self.assertEqual(len(self.rows('SELECT 1 FROM durai_bench.run WHERE run_id=%s', (receipt['runId'],))), 1)

    def test_changed_receipt_refused(self):
        # Sample: Change one count in the funds receipt. Import it again. Check a Conflict.
        profile_id, _, _ = self.profile()
        _, receipt_data, path = self.load(profile_id, counts=[7, 21])
        receipt_data['attempts'][0]['efficiency']['metrics']['modelCalls'] = 8
        path.write_text(json.dumps(receipt_data), encoding='utf-8')
        with self.assertRaises(Conflict):
            import_receipt(path, profile_id, DATABASE, None, timings=START)

    def test_crlf_copy_has_same_digest(self):
        # Sample: Write the same receipt with LF and with CRLF. Import both paths with the same run ID. Check UNCHANGED, not Conflict.
        profile_id, _, _ = self.profile()
        path, receipt = make_receipt(self.folder.name, 'task-tx-funds-v14', 'deepseek-' + uid()[:8], 'deepseek', 'deepseek-flash', [7])
        crlf = Path(self.folder.name) / 'crlf'
        crlf.mkdir(exist_ok=True)
        copy = crlf / path.name
        text = json.dumps(receipt, indent=1)
        path.write_bytes(text.encode('utf-8'))
        copy.write_bytes(text.replace(chr(10), chr(13) + chr(10)).encode('utf-8'))
        self.assertEqual(import_receipt(path, profile_id, DATABASE, None, timings=START)['state'], 'IMPORTED')
        self.assertEqual(import_receipt(copy, profile_id, DATABASE, None, timings=START)['state'], 'UNCHANGED')

    def test_import_from_other_root(self):
        # Sample: Write a receipt under a temporary root with templates/task-tx-funds-v14/task.json. Import with that root. Check case funds and receipt_path relative to that root.
        profile_id, _, _ = self.profile()
        with tempfile.TemporaryDirectory() as other:
            template = Path(other) / 'templates/task-tx-funds-v14'
            template.mkdir(parents=True)
            (template / 'task.json').write_text(json.dumps({'case': 'funds', 'variant': 'contract'}), encoding='utf-8')
            (Path(other) / 'receipts').mkdir()
            path, receipt = make_receipt(Path(other) / 'receipts', 'task-tx-funds-v14', 'deepseek-' + uid()[:8], 'deepseek', 'deepseek-flash', [5])
            result = import_receipt(path, profile_id, DATABASE, None, root=Path(other), timings=START)
            self.assertEqual(result['state'], 'IMPORTED')
            row = self.rows('SELECT r.receipt_path,t.case_name FROM durai_bench.run r JOIN durai_bench.task_case t ON t.id=r.task_case_id WHERE r.run_id=%s',
                            (receipt['runId'],))[0]
            self.assertEqual((row['receipt_path'], row['case_name']), ('receipts/' + path.name, 'funds'))

    def test_invalid_receipt_reported(self):
        # Sample: Remove "attempts" from a receipt. Check the import reports it and adds no row.
        profile_id, _, _ = self.profile()
        path, receipt = make_receipt(self.folder.name, 'task-tx-funds-v14', 'deepseek-' + uid()[:8], 'deepseek', 'deepseek-flash', [7])
        del receipt['attempts']
        path.write_text(json.dumps(receipt), encoding='utf-8')
        result = import_receipt(path, profile_id, DATABASE, None)
        self.assertEqual(result['state'], 'INVALID')
        self.assertIn('attempts', result['reason'])
        self.assertEqual(self.rows('SELECT 1 FROM durai_bench.run WHERE run_id=%s', (receipt['runId'],)), [])


class BenchCostTests(BenchBase):
    def cost(self, run_id):
        # Sample: Read the estimated cost of run R-1. Return it as a float.
        return float(self.rows('SELECT estimated_cost FROM durai_bench.run_cost WHERE run_id=%s', (run_id,))[0]['estimated_cost'])

    def test_cost_uses_price_in_effect(self):
        # Sample: Price 0.30 from 2026-11-01 and 0.40 from 2026-11-21. A run on 2026-11-20 with 1,000,000 input tokens costs 0.30 USD.
        profile_id, provider, model = self.profile(price=(0.30, 0.006, 1.20), start='2026-11-01')
        add_price({'provider': provider, 'model': model, 'effective_from': '2026-11-21', 'verification': 'AGGREGATOR', 'input_per_million': 0.40,
                   'cached_input_per_million': 0.006, 'output_per_million': 1.20, 'sources': ['https://example.test/later'], 'checked_at': '2026-11-20'}, DATABASE)
        _, receipt, _ = self.load(profile_id, counts=[5], tokens={'input': 1000000, 'output': 0})
        self.assertAlmostEqual(self.cost(receipt['runId']), 0.30, places=6)

    def test_cached_tokens_use_cached_price(self):
        # Sample: 1,000,000 input tokens with 800,000 cached at 0.006 and the rest at 0.30. Check 0.0648 USD.
        profile_id, _, _ = self.profile()
        _, receipt, _ = self.load(profile_id, counts=[5], tokens={'input': 1000000, 'cached': 800000, 'output': 0})
        self.assertAlmostEqual(self.cost(receipt['runId']), 0.0648, places=6)


    def test_advisor_tokens_in_cost(self):
        # Sample: Run with 1,000,000 worker input tokens and 100,000 advisor output tokens at 0.30/1.20. Check estimated_cost 0.42 and advisor_cost 0.12.
        profile_id, _, _ = self.profile()
        advisor = {'profile': {'id': profile_id}, 'binding': 'foundation-exchange-v2', 'cap': 3, 'calls': 2,
                   'tokens': {'input': 0, 'output': 100000, 'reasoning': 80000}, 'latencies': [17.5, 18.25], 'states': ['ADVICE', 'ADVICE']}
        _, receipt, _ = self.load(profile_id, counts=[5], tokens={'input': 1000000, 'output': 0}, advisor=advisor)
        row = self.rows('SELECT estimated_cost,advisor_cost FROM durai_bench.run_cost WHERE run_id=%s', (receipt['runId'],))[0]
        stored = self.rows('SELECT binding,advisor_profile,advisor_calls,advisor_seconds,advisor_states FROM durai_bench.run WHERE run_id=%s',
                           (receipt['runId'],))[0]
        self.assertEqual((float(row['estimated_cost']), float(row['advisor_cost'])), (0.42, 0.12))
        self.assertEqual((stored['binding'], stored['advisor_profile'], stored['advisor_calls'], float(stored['advisor_seconds'])),
                         ('foundation-exchange-v2', profile_id, 2, 35.75))
        self.assertEqual(stored['advisor_states'], ['ADVICE', 'ADVICE'])


class BenchProviderTests(BenchBase):
    REQUESTS = [{'inputTokens': 600000, 'cachedInputTokens': 500000, 'outputTokens': 6000, 'reasoningTokens': 1000},
                {'inputTokens': 400000, 'cachedInputTokens': 300000, 'outputTokens': 4000, 'reasoningTokens': 500}]

    def provider_run(self, **kwargs):
        # Sample: Profile at 2.00/0.10/10.00 and a receipt with 2 providerRequests. Return the profile id, receipt, and path.
        profile_id, _, _ = self.profile(price=(2.00, 0.10, 10.00))
        _, receipt, path = self.load(profile_id, counts=[5], extra={'providerRequests': self.REQUESTS}, **kwargs)
        return profile_id, receipt, path

    def admin(self, query, params):
        # Sample: Run an UPDATE or SELECT as the postgres admin login on durai_test. Return the rows.
        with connect('postgres', DATABASE) as db:
            return db.execute(query, params).fetchall() if query.lstrip().upper().startswith('SELECT') else db.execute(query, params)

    def test_provider_requests_priced_with_cache(self):
        # Sample: Receipt with providerRequests 1,000,000 input of which 800,000 cached at 2.00/0.10/10.00 and 10,000 output. Check estimated_cost 0.58.
        _, receipt, _ = self.provider_run()
        row = self.rows('SELECT estimated_cost FROM durai_bench.run_cost WHERE run_id=%s', (receipt['runId'],))[0]
        stored = self.rows('SELECT provider_input_tokens,provider_cached_input_tokens,provider_output_tokens,provider_reasoning_tokens '
                           'FROM durai_bench.run WHERE run_id=%s', (receipt['runId'],))[0]
        self.assertEqual(float(row['estimated_cost']), 0.58)
        self.assertEqual(tuple(stored.values()), (1000000, 800000, 10000, 1500))

    def test_backfill_fills_provider_usage(self):
        # Sample: Import a receipt with providerRequests into a bench_0002-shaped row with NULL provider columns, run backfill. Check the columns filled and cost changed; second backfill changes nothing.
        _, receipt, _ = self.provider_run()
        self.admin('UPDATE durai_bench.run SET provider_input_tokens=NULL,provider_cached_input_tokens=NULL,provider_output_tokens=NULL,'
                   'provider_reasoning_tokens=NULL WHERE run_id=%s', (receipt['runId'],))
        before = float(self.rows('SELECT estimated_cost FROM durai_bench.run_cost WHERE run_id=%s', (receipt['runId'],))[0]['estimated_cost'])
        first = [item for item in backfill_provider_usage(DATABASE, [self.folder.name]) if item['runId'] == receipt['runId']]
        after = float(self.rows('SELECT estimated_cost FROM durai_bench.run_cost WHERE run_id=%s', (receipt['runId'],))[0]['estimated_cost'])
        second = [item for item in backfill_provider_usage(DATABASE, [self.folder.name]) if item['runId'] == receipt['runId']]
        self.assertEqual([item['state'] for item in first], ['FILLED'])
        self.assertEqual((before, after), (0.003, 0.58))
        self.assertEqual(second, [])

    def test_backfill_refuses_changed_receipt(self):
        # Sample: Change one byte in the receipt. Check the backfill skips it and reports the digest mismatch.
        _, receipt, path = self.provider_run()
        self.admin('UPDATE durai_bench.run SET provider_input_tokens=NULL WHERE run_id=%s', (receipt['runId'],))
        path.write_bytes(path.read_bytes().replace(b'600000', b'600001'))
        report = [item for item in backfill_provider_usage(DATABASE, [self.folder.name]) if item['runId'] == receipt['runId']]
        self.assertEqual([item['state'] for item in report], ['DIGEST_MISMATCH'])
        stored = self.rows('SELECT provider_input_tokens FROM durai_bench.run WHERE run_id=%s', (receipt['runId'],))[0]
        self.assertIsNone(stored['provider_input_tokens'])

    def test_strategy_defaults_single(self):
        # Sample: Import one receipt without strategy and one with strategy TEAM and endpoint responses. Check SINGLE with no endpoint, then TEAM with responses.
        profile_id, _, _ = self.profile()
        _, plain, _ = self.load(profile_id, counts=[5])
        _, team, _ = self.load(profile_id, counts=[5], extra={'strategy': 'TEAM', 'endpoint': 'responses'})
        query = 'SELECT strategy,endpoint FROM durai_bench.run WHERE run_id=%s'
        self.assertEqual(tuple(self.rows(query, (plain['runId'],))[0].values()), ('SINGLE', None))
        self.assertEqual(tuple(self.rows(query, (team['runId'],))[0].values()), ('TEAM', 'responses'))
        self.assertTrue(any(row['strategy'] == 'TEAM' for row in self.rows('SELECT strategy FROM durai_bench.strategy_scorecard', ())))


class BenchScoreTests(BenchBase):
    def card(self, profile_id):
        # Sample: Read the scorecard row of profile P-1 in family transaction-v14. Return it.
        return [row for row in scorecard(DATABASE, 'transaction-v14') if row['model_profile_id'] == profile_id][0]

    def test_scorecard_per_model(self):
        # Sample: DeepSeek 2 runs (1 pass), GLM 1 run (1 pass). Check pass rates 0.5 and 1.0 and cost per pass.
        deepseek, _, _ = self.profile(price=(0.30, 0.006, 1.20))
        glm, _, _ = self.profile(provider='z.ai', price=(1.40, 0.26, 4.40))
        tokens = {'input': 1000000, 'output': 0}
        self.load(deepseek, counts=[10], tokens=tokens)
        self.load(deepseek, fixture='task-tx-funds-v14-plain', counts=[30], verdict='FAIL', tokens=tokens)
        self.load(glm, counts=[6], tokens=tokens)
        first, second = self.card(deepseek), self.card(glm)
        self.assertEqual((float(first['pass_rate']), float(second['pass_rate'])), (0.5, 1.0))
        self.assertEqual(float(first['median_calls']), 20.0)
        self.assertAlmostEqual(float(first['cost_per_pass']), 0.60, places=6)
        self.assertAlmostEqual(float(second['cost_per_pass']), 1.40, places=6)

    def test_incomplete_usage_flagged(self):
        # Sample: Ledger 10 calls, metrics 9 calls. Check usage_complete false and 1 incomplete run in the scorecard.
        profile_id, _, _ = self.profile()
        _, receipt, _ = self.load(profile_id, counts=[9], ledger=10)
        flags = self.rows('SELECT usage_complete FROM durai_bench.attempt WHERE run_id=%s', (receipt['runId'],))
        self.assertEqual([row['usage_complete'] for row in flags], [False])
        self.assertEqual(self.card(profile_id)['incomplete_usage_runs'], 1)

    def test_experiment_pairs_by_case(self):
        # Sample: Baseline r2 and treatment order-r1 on 3 cases. Check 1 treatment-only, 0 baseline-only, 2 both pass.
        profile_id, _, _ = self.profile()
        suffix = uid()[:8]
        base, treat = 'deepseek-base-' + suffix, 'deepseek-treat-' + suffix
        for fixture, base_verdict in (('task-tx-funds-v14', 'PASS'), ('task-tx-funds-v14-plain', 'PASS'), ('task-tx-inventory-v14', 'FAIL')):
            self.load(profile_id, fixture=fixture, label=base, counts=[5], verdict=base_verdict)
            self.load(profile_id, fixture=fixture, label=treat, counts=[5])
        experiment = 'exp-' + suffix
        add_experiment({'id': experiment, 'question': 'Does order help?', 'plan_file': 'plan_x.md', 'baseline_labels': [base],
                        'treatment_labels': [treat], 'family': 'transaction-v14'}, DATABASE)
        result = compare(experiment, DATABASE)
        self.assertEqual((result['pairs'], result['bothPass'], result['treatmentOnly'], result['baselineOnly']), (3, 2, 1, 0))


class BenchVariantTests(BenchBase):
    def test_contract_vs_plain_pairs(self):
        # Sample: One label, case funds, contract PASS and plain FAIL. Check treatment only 1.
        profile_id, _, _ = self.profile()
        label = 'deepseek-cp-' + uid()[:8]
        self.load(profile_id, fixture='task-tx-funds-v14', label=label, counts=[5])
        self.load(profile_id, fixture='task-tx-funds-v14-plain', label=label, counts=[5], verdict='FAIL')
        experiment = 'exp-' + uid()[:8]
        add_experiment({'id': experiment, 'question': 'Contract beats plain?', 'plan_file': 'plan_x.md', 'baseline_labels': [label],
                        'treatment_labels': [label], 'family': 'transaction-v14', 'baseline_variants': ['plain'],
                        'treatment_variants': ['contract']}, DATABASE)
        result = compare(experiment, DATABASE)
        self.assertEqual((result['pairs'], result['treatmentOnly'], result['baselineOnly'], result['bothPass']), (1, 1, 0, 0))


class BenchAccessTests(BenchBase):
    def test_runtime_cannot_write(self):
        # Sample: Insert a run with the runtime login. Check an insufficient-privilege error.
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with transaction('durai_runtime', DATABASE) as db:
                db.execute("INSERT INTO durai_bench.task_case(id,family,case_name,variant,template) VALUES('x','x','x','x','x')")


if __name__ == '__main__':
    unittest.main()
