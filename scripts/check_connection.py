"""Check Gemini/ADK conversation and Firestore access without making bookings."""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from noor.agent import build_agent
from noor.config import settings
from noor.db import get_db


async def main():
    assert settings.google_api_key, 'Configure GOOGLE_API_KEY in .env first.'
    sessions = InMemorySessionService()
    session_id = str(uuid.uuid4())
    uid = 'noor-connection-check'
    await sessions.create_session(app_name='noor-adk', user_id=uid, session_id=session_id)
    runner = Runner(agent=build_agent(uid, session_id), app_name='noor-adk', session_service=sessions)
    for message in ('Hello. Introduce yourself briefly; do not book anything.',
                    'What is your name? Do not book anything.'):
        reply = []
        async for event in runner.run_async(user_id=uid, session_id=session_id,
                new_message=types.Content(role='user', parts=[types.Part(text=message)])):
            if event.is_final_response() and event.content:
                reply.extend(part.text for part in event.content.parts if part.text)
        assert reply, 'ADK returned no final reply.'
        print('ADK reply:', ' '.join(reply))
    get_db().collection('appointments').limit(1).get()
    print('Firestore read connection passed; no booking records were written.')


if __name__ == '__main__':
    asyncio.run(main())
