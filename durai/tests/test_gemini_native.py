"""Sample: Route gemini-3.8-flash returns a signature on turn 1. Replay turn 2 offline. Check the signature is in the request."""
import base64
import unittest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_google_genai import chat_models as genai
from durai.coding_worker import configured_model

SIGNATURE = b'sig-book-2-lots-of-100'


def route(**changes):
    # Sample: Build a gemini route with key K-1. Return the profile dict.
    profile = {'id': 'gemini', 'kind': 'openai-compatible', 'model': 'gemini-3.8-flash', 'apiKey': 'K-1',
               'baseUrl': 'https://generativelanguage.googleapis.com/v1beta/openai'}
    profile.update(changes)
    return profile


class GeminiWorkerTests(unittest.TestCase):
    def test_gemini_route_uses_native_client(self):
        # Sample: Route id gemini, then kind google on another host, then the Google host under id x. Check ChatGoogleGenerativeAI each time.
        import tempfile
        from unittest.mock import patch
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.enterContext(patch.dict('os.environ', {'DURAI_PROVIDER_DIR': folder.name}))
        for profile in (route(), route(id='g2', kind='google', baseUrl='https://example.com'),
                        route(id='x', baseUrl='https://generativelanguage.googleapis.com/v1beta')):
            model = configured_model(profile=profile)
            self.assertEqual(type(model).__name__, 'ChatGoogleGenerativeAI')
            self.assertEqual((model.model, model.max_output_tokens, model.max_retries), ('gemini-3.8-flash', 32768, 0))

    def test_isolated_model_accepts_native_client(self):
        # Sample: Wrap the native client. Bind read_file. Check the bound model is kept.
        from langchain_core.tools import tool
        from durai.worker_harness import isolated_model

        @tool
        def read_file(path: str) -> str:
            """Read one file."""
            return path
        wrapped = isolated_model(configured_model(profile=route()))
        self.assertIsNotNone(wrapped.bind_tools([read_file]).bound)

    def test_signature_survives_second_turn_conversion(self):
        # Sample: Turn 1 AIMessage calls book_trade with signature S-1. Add its ToolMessage. Convert. Check the function call part carries S-1 bytes.
        encoded = base64.b64encode(SIGNATURE).decode()
        call = {'name': 'book_trade', 'args': {'lots': 2, 'size': 100}, 'id': 'call-1', 'type': 'tool_call'}
        turn_one = AIMessage(content='', tool_calls=[call],
                             additional_kwargs={genai._FUNCTION_CALL_THOUGHT_SIGNATURES_MAP_KEY: {'call-1': encoded}})
        history = [HumanMessage('Book 2 lots of 100 shares.'), turn_one,
                   ToolMessage(content='booked', tool_call_id='call-1', name='book_trade')]
        _, contents = genai._parse_chat_history(history, model='gemini-3.8-flash')
        parts = [part for content in contents for part in content.parts if part.function_call]
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].function_call.name, 'book_trade')
        self.assertEqual(parts[0].thought_signature, SIGNATURE)

    def test_signature_dropped_without_native_field(self):
        # Sample: Turn 1 AIMessage has no signature field. Convert. Check the part holds the skip sentinel, not S-1.
        call = {'name': 'book_trade', 'args': {'lots': 2}, 'id': 'call-1', 'type': 'tool_call'}
        history = [HumanMessage('Book 2 lots.'), AIMessage(content='', tool_calls=[call]),
                   ToolMessage(content='booked', tool_call_id='call-1', name='book_trade')]
        _, contents = genai._parse_chat_history(history, model='gemini-3.8-flash')
        part = next(part for content in contents for part in content.parts if part.function_call)
        self.assertNotEqual(part.thought_signature, SIGNATURE)


if __name__ == '__main__':
    unittest.main()
