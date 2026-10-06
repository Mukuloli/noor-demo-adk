"""Terminal ADK uses the shared session lifecycle without live model or DB calls."""
import asyncio
import unittest
from unittest.mock import Mock, patch

from firebase_admin import firestore
from google.adk.events import Event
from google.genai import types

import main as cli
from noor_database.adk_sessions import CachedAdkSessionService
from noor_database.config import DatabaseSettings
from noor_database.factory import create_appointment_service
from noor_database.testing import MemoryFirestore, transaction_wrapper
from noor_database.users import UserDataService


class TerminalChatTests(unittest.TestCase):
    def test_start_warms_user_and_turns_persist_history(self):
        config = DatabaseSettings(redis_url='', booking_mode='firestore',
                                  calendar_invitations_enabled=False)
        with patch.object(firestore, 'transactional', transaction_wrapper):
            service = create_appointment_service(config, db=MemoryFirestore())
            warm = Mock(side_effect=UserDataService(service).warm)
            history = []
            session_ids = []

            class Runner:
                def __init__(self, *, agent, app_name, session_service):
                    self.app_name, self.sessions = app_name, session_service

                async def run_async(self, *, user_id, session_id, new_message):
                    session_ids.append(session_id)
                    session = await self.sessions.get_session(
                        app_name=self.app_name, user_id=user_id, session_id=session_id)
                    history.append([event.content.parts[0].text for event in session.events])
                    await self.sessions.append_event(session, Event(author='user', content=new_message))
                    event = Event(author='noor', content=types.Content(
                        role='model', parts=[types.Part(text='Hello from Noor')]))
                    await self.sessions.append_event(session, event)
                    yield event

            with (patch.object(cli, 'settings', config),
                  patch.object(cli, 'get_service', return_value=service),
                  patch.object(cli, 'warm_user', warm),
                  patch.object(cli, 'build_agent', return_value=object()),
                  patch.object(cli, 'Runner', Runner),
                  patch('builtins.input', side_effect=['First', 'Second', 'quit']),
                  patch('builtins.print')):
                asyncio.run(cli.run_chat('alice', 'Alice'))

            warm.assert_called_once_with('alice')
            self.assertEqual(history, [[], ['First', 'Hello from Noor']])
            restored = asyncio.run(CachedAdkSessionService(config, lambda: service).get_session(
                app_name='noor-adk', user_id='alice', session_id=session_ids[0]))
            self.assertEqual([event.content.parts[0].text for event in restored.events],
                             ['First', 'Hello from Noor', 'Second', 'Hello from Noor'])
