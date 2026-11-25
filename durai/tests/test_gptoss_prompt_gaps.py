"""Sample: Render the events, seats, and settlement prompts with pack revision 3. Check the new example, rule, finish rule, tool list, and tool evidence."""
import json
import unittest
from unittest.mock import patch

from durai.prompt_quality import PINS, audit_prompt
from durai.qualification import TEMPLATES
from durai.runtime_deepagents import instructions
from tools.check_prompt_quality import audit_fixture, candidate_framework, matching_revision, render_fixture
from test_dev_loop import DATABASE, LoopHarness, loop_spec
from test_workspace_worker import task


def render(name):
    # Sample: Render task-tx-events-v14 with the pack revision 3 pin. Return its task, prompt, pack, and protected suite.
    with patch('durai.prompt_quality.ACTIVE_REVISION', matching_revision()):
        return render_fixture(TEMPLATES / name, candidate_framework())


def examples_of(prompt):
    # Sample: Read a prompt with two public worked examples. Return the two example lines.
    return prompt.split('## Public worked examples\n')[1].split('\n\n', 1)[0].splitlines()


class TestPromptFacts(unittest.TestCase):
    def test_events_redelivery_example_in_prompt(self):
        # Sample: Render task-tx-events-v14. Check the prompt has "claimed again after the lease ends" and example E-1 consumed once with replayed False then True.
        for name in ('task-tx-events-v14', 'task-tx-events-v14-plain'):
            with self.subTest(name=name):
                spec, prompt, _, suite = render(name)
                self.assertIn('claimed again after the lease ends', prompt)
                self.assertTrue(any('Enqueue E-1' in line and 'attempt 2' in line and 'one local effect row' in line
                                    for line in examples_of(prompt)))
                self.assertIn('CLAIMED with lease_until at or before now', prompt)
                self.assertIn('(%s::text IS NULL OR stream_id = %s::text)', prompt)
                self.assertEqual(audit_prompt(spec, prompt, suite)['state'], 'PASS')
                cut = prompt.replace('Stop the relay and do not settle.', '')
                self.assertIn('events-redelivery-example', audit_prompt(spec, cut, suite)['missing'])

    def test_seats_race_example_in_prompt(self):
        # Sample: Render task-tx-seats-v14. Check order K-1 HELD and K-2 SEAT_UNAVAILABLE for seat A-12 and the bare ON CONFLICT DO NOTHING form.
        for name in ('task-tx-seats-v14', 'task-tx-seats-v14-plain'):
            with self.subTest(name=name):
                spec, prompt, _, suite = render(name)
                self.assertTrue(any('Race two holds for seat A-12' in line and 'SEAT_UNAVAILABLE' in line and 'key K-3' in line
                                    for line in examples_of(prompt)))
                self.assertIn('Do not write ON CONFLICT ON CONSTRAINT', prompt)
                self.assertEqual(audit_prompt(spec, prompt, suite)['state'], 'PASS')
                cut = prompt.replace('Race two holds for seat A-12', 'Hold seat A-12')
                self.assertIn('seats-race-example', audit_prompt(spec, cut, suite)['missing'])

    def test_settlement_difference_rule_in_prompt(self):
        # Sample: Render task-tx-settlement-v14. Check amountMinor 480 against local 500 gives MISMATCH, captured stays 500, and providerReference proof.C-1 is text.
        for name in ('task-tx-settlement-v14', 'task-tx-settlement-v14-plain'):
            with self.subTest(name=name):
                spec, prompt, _, suite = render(name)
                self.assertIn('A difference never changes money.', prompt)
                self.assertIn('providerReference is a text string', prompt)
                self.assertTrue(any('amountMinor 480' in line and 'MISMATCH' in line and 'captured 500 before and after' in line
                                    for line in examples_of(prompt)))
                self.assertEqual(audit_prompt(spec, prompt, suite)['state'], 'PASS')
                cut = prompt.replace('is never converted to int', '')
                self.assertIn('settlement-reference-text', audit_prompt(spec, cut, suite)['missing'])


class TestWorkerPrompt(unittest.TestCase):
    def test_finish_rule_present(self):
        # Sample: Build the worker prompt for task-tx-journal-v14. Check "complete only after you write the declared file" and the no-edit failure rule.
        _, prompt, _, _ = render('task-tx-journal-v14')
        self.assertIn('The task is complete only after you write the declared file.', prompt)
        self.assertIn('Do not end your turn before one valid edit.', prompt)

    def test_edit_form_example_present(self):
        # Sample: Build the worker prompt. Read the edit_file JSON example. Replace "    return None" in "def f(items):" and compile the result.
        text = instructions(task(), {'/workspace/a.py': b''})
        start = text.index('Valid edit_file arguments, shown as JSON: ') + len('Valid edit_file arguments, shown as JSON: ')
        example, _ = json.JSONDecoder().raw_decode(text[start:])
        self.assertEqual(sorted(example), ['file_path', 'new_string', 'old_string'])
        source = 'def f(items):\n' + example['old_string']
        self.assertIn(example['old_string'], source)
        compile(source.replace(example['old_string'], example['new_string']), 'a.py', 'exec')

    def test_tool_names_listed(self):
        # Sample: Declare tools read_file and edit_file. Build the prompt. Check both names appear in the tool sentence and write_file does not.
        text = instructions(task(), {'/workspace/a.py': b''}, tools=['read_file', 'edit_file'])
        sentence = text.split('You may call only these tools: ', 1)[1].split('. Call no other tool name.', 1)[0]
        self.assertEqual(sentence, 'read_file, edit_file')
        default = instructions(task(), {'/workspace/a.py': b''})
        from durai.worker_harness import ALLOWED_TOOLS
        self.assertIn('You may call only these tools: ' + ', '.join(sorted(ALLOWED_TOOLS)) + '. Call no other tool name.', default)
        _, prompt, _, _ = render('task-tx-journal-v14')
        self.assertIn('You may call only these tools: describe_table, edit_file, find_function, read_file, search_context, show_lines, write_file. Call no other tool name.', prompt)


class TestWorkerHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Sample: Apply core migrations and the LangGraph checkpoint tables to durai_test.
        from durai.db import prepare
        from durai.harness import prepare_checkpoints
        prepare(DATABASE)
        prepare_checkpoints(DATABASE)

    def test_undeclared_tool_name_recorded(self):
        # Sample: Script a worker that calls tool python. Check ATTEMPT_FAILED evidence has requestedTool python and the unchanged error text.
        from durai.execution_events import replay
        harness = LoopHarness([[('python', {'code': 'print(1)'})]])
        state = harness.run(loop_spec())
        self.assertEqual(state['outcome'], 'FAILED')
        failed = [row['payload'] for row in replay(harness.run_id, database=DATABASE) if row['kind'] == 'ATTEMPT_FAILED']
        self.assertEqual(failed[0]['evidence'], {'error': 'Worker requested an undeclared tool', 'requestedTool': 'python'})


class TestPromptAudit(unittest.TestCase):
    def test_all_22_prompts_pass_audit(self):
        # Sample: Render all 22 v14 prompts with revision 3. Check 22 PROMPT_FACT_AUDIT PASS rows within 60000 pack characters.
        with patch('durai.prompt_quality.ACTIVE_REVISION', matching_revision()):
            framework = candidate_framework()
            rows = [audit_fixture(folder, framework) for folder in sorted(TEMPLATES.glob('task-tx-*-v14*'))]
        self.assertEqual(len(rows), 22)
        self.assertEqual([row['fixture'] for row in rows if row['state'] != 'PASS'], [])
        self.assertTrue(all(row['packCharacters'] <= 60000 for row in rows))
        self.assertIn(matching_revision(), PINS)


if __name__ == '__main__':
    unittest.main()
