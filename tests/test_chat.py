import json
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

    async def run_async(self, *, user_id, session_id, new_message):
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
            patch.object(server.settings, 'google_api_key', 'test-key'),
            patch.object(server, 'verify_token', side_effect=lambda token: {'uid': token.removeprefix('Bearer ')}),
        ):
            mock_patch.start()
            self.addCleanup(mock_patch.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)

    def send(self, uid='alice', message='Hello'):
        return self.client.post('/chat', headers={'Authorization': f'Bearer {uid}'},
                                json={'session_id': 'browser-session', 'message': message})

    def test_new_session_is_created_and_reused(self):
        self.assertEqual(self.send().json()['reply'], 'Hello from Noor')
        self.assertEqual(self.send(message='Again').status_code, 200)
        self.assertEqual(FakeRunner.calls, [
            ('alice', 'alice-browser-session', 'Hello'),
            ('alice', 'alice-browser-session', 'Again')])
        self.assertEqual(len(server._session_service.sessions[server.APP_NAME]['alice']), 1)

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
                                    json={'session_id': 'stream-session', 'message': 'Hello'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/event-stream', response.headers['content-type'])
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual(events[0], {'text': 'Hello from Noor', 'done': True})
        self.assertEqual(FakeRunner.calls, [('alice', 'alice-stream-session', 'Hello')])

    def test_stream_reports_provider_errors(self):
        async def failing_runner(*args, **kwargs):
            raise RuntimeError('provider credential details')
            yield
        with patch.object(FakeRunner, 'run_async', failing_runner), self.assertLogs(server.logger, level='ERROR'):
            response = self.client.post('/chat/stream', headers={'Authorization': 'Bearer alice'},
                                        json={'session_id': 'stream-session', 'message': 'Hello'})
        self.assertIn('Noor could not respond right now.', response.text)
        self.assertNotIn('credential', response.text)


if __name__ == '__main__':
    unittest.main()
