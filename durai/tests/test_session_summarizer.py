"""Sample: Check a fake DeepSeek reply and refuse a missing source reference."""
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from durai.common import Conflict
from durai.session_summarizer import (
    DEEPSEEK_ENDPOINT, DEEPSEEK_FLASH, DeepSeekSummaryProvider, PROMPT, SummaryCompletion, check_summary,
)


def summary(**changes):
    # Sample: Build a result for turn 7 with an exact commit reference.
    return dict({'goal': 'Review a change.', 'decisions': [], 'corrections': [], 'openQuestions': [],
                 'nextAction': 'Run the proof.', 'sourceTurns': [7], 'refs': ['commit:abc123']}, **changes)


class SessionSummarizerTests(unittest.TestCase):
    def test_deepseek_request_uses_the_profile(self):
        # Sample: Build a client and send one fake request for turn 7. Read the visible JSON and stop result.
        with patch('openai.OpenAI') as client:
            instance = client.return_value
            instance.base_url = DEEPSEEK_ENDPOINT + '/'
            instance.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{}', reasoning_content='Hidden text.'),
                finish_reason='stop',
            )])
            provider = DeepSeekSummaryProvider.from_key('fake-value')
            client.assert_called_once_with(api_key='fake-value', base_url=DEEPSEEK_ENDPOINT, max_retries=0, timeout=45)
            self.assertEqual(SummaryCompletion('{}', 'stop'), provider.complete(PROMPT, '{}', DEEPSEEK_FLASH))
            request = instance.chat.completions.create.call_args.kwargs
            self.assertEqual('deepseek-flash', request['model'])
            self.assertEqual(8000, request['max_tokens'])
            self.assertEqual({'reasoning_effort': 'low', 'thinking': {'type': 'enabled'}}, request['extra_body'])
            self.assertNotIn('temperature', request)
            with self.assertRaises(ValueError):
                provider.complete(PROMPT, '{}', replace(DEEPSEEK_FLASH, model='different-model'))
            instance.base_url = 'https://another.example/'
            with self.assertRaises(ValueError):
                provider.complete(PROMPT, '{}', DEEPSEEK_FLASH)
            instance.chat.completions.create.assert_called_once()

    def test_validation_checks_output_and_sources(self):
        # Sample: Accept turn 7 and the commit. Refuse altered, foreign, oversized, and truncated output.
        accepted = SummaryCompletion(json.dumps(summary()), 'stop')
        self.assertEqual(summary(), check_summary(accepted, DEEPSEEK_FLASH, len, {7}, {'commit:abc123'}))
        for bad in (summary(refs=[]), summary(refs=['commit:ABC123']), summary(refs=['commit:abc123'] * 2),
                    summary(sourceTurns=[8]), summary(sourceTurns=[7, 7]), summary(sourceTurns=[True]),
                    summary(sourceTurns=[]), summary(nextAction='x' * 13000), summary(corrections=[1]),
                    summary(goal='Bearer private-test-credential')):
            with self.subTest(fields=list(bad)), self.assertRaises(Conflict):
                check_summary(SummaryCompletion(json.dumps(bad), 'stop'), DEEPSEEK_FLASH, len, {7}, {'commit:abc123'})
        for incomplete in (SummaryCompletion('{', 'length'), SummaryCompletion('{', 'stop'),
                           SummaryCompletion(None, 'stop')):
            with self.assertRaises(Conflict):
                check_summary(incomplete, DEEPSEEK_FLASH, len, {7}, {'commit:abc123'})
        with self.assertRaises(Conflict):
            check_summary(accepted, DEEPSEEK_FLASH, lambda text: 801, {7}, {'commit:abc123'})

    def test_stored_form_is_checked_against_the_limit(self):
        # Sample: Send compact JSON of 150 characters for turn 7. The stored form with sort_keys and spaces is longer. Set the limit to the compact length. Check refusal.
        compact = json.dumps(summary(), separators=(',', ':'))
        profile = replace(DEEPSEEK_FLASH, max_visible_tokens=len(compact))
        with self.assertRaises(Conflict):
            check_summary(SummaryCompletion(compact, 'stop'), profile, len, {7}, {'commit:abc123'})
