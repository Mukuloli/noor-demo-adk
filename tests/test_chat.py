import json
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService

import server


class FakeRunner:
    calls = []

    def __init__(self, *, agent, app_name, session_service):
        self.app_name = app_name
        self.session_service = session_service

    async def run_async(self, *, user_id, session_id, new_message, run_config=None):
        session = await self.session_service.get_session(
            app_name=self.app_name, user_id=user_id, session_id=session_id)
        if session is None:
            raise ValueError('Session not found')
        self.calls.append((user_id, session_id, new_message.parts[0].text))
        yield SimpleNamespace(
            is_final_response=lambda: True,
            content=SimpleNamespace(parts=[SimpleNamespace(text='Hello from Noor')]))


class ChatTests(unittest.TestCase):
    def setUp(self):
        FakeRunner.calls = []
        for mock_patch in (
            patch.object(server, '_session_service', InMemorySessionService()),
            patch.object(server, 'Runner', FakeRunner),
            patch.object(server, 'build_agent', return_value=object()),
            patch.object(server, 'warm_user', return_value={'uid': 'test-patient'}),
            patch.object(server.settings, 'google_api_key', 'test-key'),
            patch.object(server, 'verify_token', side_effect=lambda token: {'uid': token.removeprefix('Bearer ')}),
        ):
            mock_patch.start()
            self.addCleanup(mock_patch.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)

    def send(self, uid='alice', message='How can you help me?'):
        return self.client.post('/chat', headers={'Authorization': f'Bearer {uid}'},
                                json={'session_id': 'browser-session', 'message': message})

    def test_new_session_is_created_and_reused(self):
        self.assertEqual(self.send().json()['reply'], 'Hello from Noor')
        self.assertEqual(self.send(message='Again').status_code, 200)
        self.assertEqual(FakeRunner.calls, [
            ('alice', 'alice-browser-session', 'How can you help me?'),
            ('alice', 'alice-browser-session', 'Again')])
        self.assertEqual(len(server._session_service.sessions[server.APP_NAME]['alice']), 1)

    def test_user_data_is_warmed_only_when_conversation_starts(self):
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.send(message='Again').status_code, 200)
        server.warm_user.assert_called_once_with('alice', '', '')

    def test_stream_warms_verified_identity_once(self):
        with patch.object(server, 'verify_token', return_value={
                'uid': 'alice', 'name': 'Alice', 'email': 'alice@example.com'}):
            for message in ('Hello', 'Again'):
                response = self.client.post('/chat/stream', headers={'Authorization': 'Bearer token'},
                                            json={'session_id': 'stream-session', 'message': message})
                self.assertEqual(response.status_code, 200)
        server.warm_user.assert_called_once_with('alice', 'Alice', 'alice@example.com')

    def test_shared_browser_session_is_scoped_to_firebase_user(self):
        self.send('alice')
        self.send('bob')
        self.assertEqual([call[1] for call in FakeRunner.calls],
                         ['alice-browser-session', 'bob-browser-session'])
        self.assertEqual([call.kwargs['session_id'] for call in server.build_agent.call_args_list],
                         ['alice-browser-session', 'bob-browser-session'])

    def test_missing_configuration_is_service_error(self):
        with patch.object(server.settings, 'google_api_key', ''):
            self.assertEqual(self.send().status_code, 503)
        self.assertEqual(FakeRunner.calls, [])

    def test_invalid_firebase_token_is_rejected(self):
        from fastapi import HTTPException
        with patch.object(server, 'verify_token', side_effect=HTTPException(401, 'Please sign in again.')):
            self.assertEqual(self.send().status_code, 401)
        self.assertEqual(FakeRunner.calls, [])

    def test_model_error_is_safe_service_error(self):
        async def failing_runner(*args, **kwargs):
            raise RuntimeError('provider credential details')
            yield
        with patch.object(FakeRunner, 'run_async', failing_runner), self.assertLogs(server.logger, level='ERROR'):
            response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('credential', response.json()['detail'])

    def test_browser_preflight_allows_bearer_authorization(self):
        response = self.client.options('/chat', headers={
            'Origin': 'http://localhost:3000',
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'authorization,content-type'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:3000')

    def test_stream_creates_session_and_returns_final_reply(self):
        response = self.client.post('/chat/stream', headers={'Authorization': 'Bearer alice'},
                                    json={'session_id': 'stream-session', 'message': 'How can you help me?'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/event-stream', response.headers['content-type'])
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual(events[0], {'text': 'Hello from Noor', 'done': False})
        self.assertEqual(events[-1], {'text': '', 'done': True})
        self.assertEqual(FakeRunner.calls, [('alice', 'alice-stream-session', 'How can you help me?')])

    def test_stream_reports_provider_errors(self):
        async def failing_runner(*args, **kwargs):
            raise RuntimeError('provider credential details')
            yield
        with patch.object(FakeRunner, 'run_async', failing_runner), self.assertLogs(server.logger, level='ERROR'):
            response = self.client.post('/chat/stream', headers={'Authorization': 'Bearer alice'},
                                        json={'session_id': 'stream-session', 'message': 'How can you help me?'})
        self.assertIn('Noor could not respond right now.', response.text)
        self.assertNotIn('credential', response.text)

    def test_stream_reports_session_preparation_errors(self):
        with patch.object(server, '_ensure_session', side_effect=RuntimeError('private setup details')):
            with self.assertLogs(server.logger, level='ERROR'):
                response = self.client.post('/chat/stream', headers={'Authorization': 'Bearer alice'},
                                            json={'session_id': 'stream-session', 'message': 'How can you help me?'})
        self.assertIn('Noor could not respond right now.', response.text)
        self.assertNotIn('private setup details', response.text)

    def test_stream_enables_model_streaming_and_does_not_repeat_answer(self):
        async def streaming_runner(*args, run_config, **kwargs):
            self.assertEqual(run_config.streaming_mode, server.StreamingMode.SSE)
            for text, partial in [('Hello ', True), ('from Noor', True), ('Hello from Noor', False)]:
                yield SimpleNamespace(
                    partial=partial, is_final_response=lambda: not partial,
                    content=SimpleNamespace(parts=[SimpleNamespace(text=text)]))
        with patch.object(FakeRunner, 'run_async', streaming_runner):
            response = self.client.post('/chat/stream', headers={'Authorization': 'Bearer alice'},
                                        json={'session_id': 'stream-session', 'message': 'How can you help me?'})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual([e['text'] for e in events], ['Hello ', 'from Noor', ''])
        self.assertEqual(events[-1]['done'], True)

    def test_greeting_chunk_precedes_session_and_profile_reads(self):
        async def check():
            with patch.object(server, '_ensure_session') as prepare:
                stream = server._reply_chunks(server.ChatRequest(message='hii'), {'uid': 'alice'})
                first = await anext(stream)
                self.assertIn("I'm Noor", first['text'])
                prepare.assert_not_called()
                server.warm_user.assert_not_called()
                server.build_agent.assert_not_called()
                await stream.aclose()
        asyncio.run(check())

    def test_quick_greeting_is_kept_in_conversation_history(self):
        response = self.send(message='Hello')
        self.assertEqual(response.status_code, 200)
        self.assertIn("I'm Noor", response.json()['reply'])
        self.assertFalse(FakeRunner.calls)
        session = server._session_service.sessions[server.APP_NAME]['alice']['alice-browser-session']
        self.assertEqual([event.author for event in session.events], ['user', 'noor'])
        self.assertEqual(session.events[0].content.parts[0].text, 'Hello')

    def test_profile_warmup_does_not_block_first_model_text(self):
        started, release = threading.Event(), threading.Event()
        def slow_profile(*args):
            started.set()
            if not release.wait(3):
                raise AssertionError('Generation waited on an unrelated profile read')
        async def check():
            with patch.object(server, 'warm_user', side_effect=slow_profile):
                stream = server._reply_chunks(server.ChatRequest(message='Which services do you offer?'), {'uid': 'alice'})
                try:
                    first = await asyncio.wait_for(anext(stream), timeout=1)
                    self.assertEqual(first['text'], 'Hello from Noor')
                    self.assertTrue(await asyncio.to_thread(started.wait, 1))
                    self.assertFalse(release.is_set())
                finally:
                    release.set()
                    await stream.aclose()
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
