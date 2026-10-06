"""Time a generic Gemini greeting without patient data or a live database.

Pass --compare to compare default thinking with the current low-thinking
configuration. Calls Gemini using private settings.
"""
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from noor.agent import build_agent
from noor.config import settings
from noor import db
from noor_database.config import DatabaseSettings
from noor_database.factory import create_appointment_service
from noor_database.testing import MemoryFirestore


async def measure(label, agent):
    sessions = InMemorySessionService()
    identity = uuid.uuid4().hex
    await sessions.create_session(app_name='latency-check', user_id=identity, session_id=identity)
    runner = Runner(agent=agent, app_name='latency-check', session_service=sessions)
    started = time.perf_counter()
    first_text = None
    tool_calls = 0
    async for event in runner.run_async(
        user_id=identity, session_id=identity,
        new_message=types.Content(role='user', parts=[types.Part(text='Hello')]),
        run_config=RunConfig(streaming_mode=StreamingMode.SSE),
    ):
        tool_calls += len(event.get_function_calls())
        if first_text is None and event.content and any(
            part.text and not part.thought for part in event.content.parts):
            first_text = time.perf_counter() - started
    print(json.dumps({'configuration': label, 'model': settings.gemini_model,
                      'first_text_seconds': round(first_text, 3) if first_text is not None else None,
                      'total_seconds': round(time.perf_counter() - started, 3),
                      'tool_calls': tool_calls}), flush=True)


async def main():
    config = DatabaseSettings(redis_url='', booking_mode='firestore',
                              calendar_invitations_enabled=False, clinic_data_path='')
    service = create_appointment_service(config, db=MemoryFirestore())
    with patch.object(db, 'get_service', return_value=service):
        if '--compare' in sys.argv:
            agent = build_agent(uid='diagnostic', session_id='diagnostic')
            agent.generate_content_config = None
            await asyncio.wait_for(measure('default_thinking', agent), timeout=55)
        await asyncio.wait_for(measure('optimized', build_agent(uid='diagnostic', session_id='diagnostic')),
                               timeout=55)


if __name__ == '__main__':
    asyncio.run(main())
